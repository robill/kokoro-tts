#!/usr/bin/env python3
"""Export reviewed Chinese narration into a copy of the bilingual EPUB."""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

from bilingual_epub_tts import Chapter, _chapter_file_pattern, _language_of, _load_chapters
from chinese_llm_preprocess import load_preprocessed_chapter

_P_TAG_RE = re.compile(
    rb"(?P<open><p\b[^>]*>)(?P<body>.*?)(?P<close></p\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_BODY_CLOSE_RE = re.compile(rb"</body\s*>", re.IGNORECASE)
_CALIBRE_BOOKMARK_MEMBER = "META-INF/calibre_bookmarks.txt"


def _chinese_paragraph_texts(xhtml: bytes, chapter_number: int) -> list[str]:
    """Extract Chinese paragraph text using the audiobook language classifier.

    @param xhtml: UTF-8 XHTML document bytes.
    @param chapter_number: Chapter number for diagnostic messages.
    @return: Chinese paragraph text in document order.
    @raises ValueError: If the XHTML is not UTF-8 or has no paragraph tags.
    """
    try:
        xhtml.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Chapter {chapter_number} XHTML is not UTF-8.") from exc

    paragraphs: list[str] = []
    for match in _P_TAG_RE.finditer(xhtml):
        fragment = BeautifulSoup(match.group("body").decode("utf-8"), "html.parser")
        text = " ".join(fragment.get_text(" ", strip=True).split())
        if _language_of(text) == "zh":
            paragraphs.append(text)
    if not paragraphs:
        raise ValueError(f"Chapter {chapter_number} XHTML contains no Chinese body paragraphs.")
    return paragraphs


def _replace_chinese_paragraphs(
    xhtml: bytes,
    replacements: list[str],
    chapter_number: int,
) -> bytes:
    """Replace plain-text Chinese paragraph bodies without reserializing XHTML.

    @param xhtml: Original UTF-8 XHTML member bytes.
    @param replacements: Simplified Chinese paragraphs in source order.
    @param chapter_number: Chapter number for diagnostic messages.
    @return: XHTML bytes with only selected paragraph bodies replaced.
    @raises ValueError: If paragraph alignment or markup safety checks fail.
    """
    if any(not isinstance(text, str) or not text.strip() for text in replacements):
        raise ValueError(f"Chapter {chapter_number} contains an empty simplified paragraph.")

    chunks: list[bytes] = []
    cursor = 0
    replacement_index = 0
    for match in _P_TAG_RE.finditer(xhtml):
        body = match.group("body")
        fragment = BeautifulSoup(body.decode("utf-8"), "html.parser")
        text = " ".join(fragment.get_text(" ", strip=True).split())
        if _language_of(text) != "zh":
            continue
        if fragment.find(True) is not None:
            raise ValueError(
                f"Chapter {chapter_number} Chinese paragraph {replacement_index + 1} "
                "contains inline XHTML elements; refusing to discard its markup."
            )
        if replacement_index >= len(replacements):
            raise ValueError(f"Chapter {chapter_number} has more Chinese paragraphs than its cache.")
        chunks.append(xhtml[cursor : match.start("body")])
        chunks.append(html.escape(replacements[replacement_index], quote=False).encode("utf-8"))
        cursor = match.end("body")
        replacement_index += 1

    if replacement_index != len(replacements):
        raise ValueError(
            f"Chapter {chapter_number} alignment mismatch: replaced {replacement_index} "
            f"Chinese paragraphs, cache contains {len(replacements)}."
        )
    chunks.append(xhtml[cursor:])
    return b"".join(chunks)


def _append_vocabulary_glossary(
    xhtml: bytes,
    vocabulary: list[dict[str, str]],
    chapter_number: int,
) -> bytes:
    """Append a written Hanzi/Pinyin/English glossary before the body closes.

    @param xhtml: UTF-8 chapter body XHTML bytes.
    @param vocabulary: Validated glossary entries with hanzi, pinyin, and english.
    @param chapter_number: Chapter number for diagnostic messages.
    @return: XHTML bytes with a vocabulary section appended at the chapter end.
    @raises ValueError: If glossary fields or XHTML body structure are invalid.
    """
    if not vocabulary:
        raise ValueError(f"Chapter {chapter_number} has no vocabulary entries to append.")
    if not xhtml.decode("utf-8", errors="strict"):
        raise ValueError(f"Chapter {chapter_number} XHTML is empty.")

    close_tags = list(_BODY_CLOSE_RE.finditer(xhtml))
    if len(close_tags) != 1:
        raise ValueError(
            f"Chapter {chapter_number} XHTML must contain exactly one closing body tag."
        )
    if b'class="study-glossary"' in xhtml or b"class='study-glossary'" in xhtml:
        raise ValueError(f"Chapter {chapter_number} already contains a study glossary.")

    items: list[str] = []
    for index, entry in enumerate(vocabulary, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Chapter {chapter_number} glossary entry {index} is not an object.")
        fields = [entry.get(key) for key in ("hanzi", "pinyin", "english")]
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise ValueError(f"Chapter {chapter_number} glossary entry {index} is incomplete.")
        hanzi, pinyin, english = (html.escape(value.strip(), quote=False) for value in fields)
        items.append(f"<li><strong>{hanzi}</strong> (<em>{pinyin}</em>) — {english}</li>")

    section = (
        '<div class="study-glossary"><h2>Vocabulary 词汇表</h2><ul>'
        + "".join(items)
        + "</ul></div>"
    ).encode("utf-8")
    close_tag = close_tags[0]
    return xhtml[: close_tag.start()] + section + xhtml[close_tag.start() :]


def _read_vocabulary_glossary(xhtml: bytes, chapter_number: int) -> list[str]:
    """Extract the appended glossary rows for post-write validation."""
    soup = BeautifulSoup(xhtml, "html.parser")
    section = soup.find("div", class_="study-glossary")
    if section is None:
        raise ValueError(f"Chapter {chapter_number} has no exported study glossary.")
    listing = section.find("ul", recursive=False)
    if listing is None:
        raise ValueError(f"Chapter {chapter_number} glossary has no list.")
    rows: list[str] = []
    for item in listing.find_all("li", recursive=False):
        text = " ".join(item.get_text(" ", strip=True).split())
        text = re.sub(r"\(\s+", "(", text)
        text = re.sub(r"\s+\)", ")", text)
        rows.append(text)
    return rows


def _selected_chapters(chapters: dict[int, Chapter], start: int | None, end: int | None) -> list[Chapter]:
    """Select a contiguous, complete range of source chapters."""
    if not chapters:
        raise ValueError("The EPUB contains no paired bilingual chapters.")
    first = min(chapters) if start is None else start
    last = max(chapters) if end is None else end
    if first > last:
        raise ValueError("--start must be less than or equal to --end.")
    selected = [chapter for number, chapter in sorted(chapters.items()) if first <= number <= last]
    missing = sorted(set(range(first, last + 1)) - {chapter.number for chapter in selected})
    if missing:
        raise ValueError(f"Requested chapter range has missing chapters: {missing}")
    return selected


def export_simplified_epub(
    epub_path: Path,
    cache_dir: Path,
    output_path: Path,
    *,
    start: int | None = None,
    end: int | None = None,
    overwrite: bool = False,
    include_glossary: bool = False,
) -> Path:
    """Create a copy of a bilingual EPUB with cached Chinese paragraphs replaced.

    The source EPUB, English text, Chinese headings, EPUB package metadata, and
    all non-selected content remain unchanged. Optionally append a written
    Hanzi/Pinyin/English glossary to each selected chapter. Every selected chapter cache is
    checked against its source digest and prompt version before the archive is
    written. Output creation is atomic so a failed export cannot leave a partial
    EPUB at the destination.

    @param epub_path: Source bilingual EPUB path.
    @param cache_dir: Directory holding validated per-chapter preprocessing caches.
    @param output_path: Destination EPUB path; must differ from the source path.
    @param start: First chapter to rewrite, inclusive; defaults to first source chapter.
    @param end: Last chapter to rewrite, inclusive; defaults to last source chapter.
    @param overwrite: Replace an existing destination when true.
    @param include_glossary: Append the cached written glossary to each selected chapter.
    @return: Resolved path to the completed study EPUB.
    @raises FileNotFoundError: If the source or a selected chapter cache is missing.
    @raises ValueError: If the source, cache range, or paragraph alignment is invalid.
    @raises FileExistsError: If the destination exists and overwrite is false.
    """
    epub_path = epub_path.resolve()
    cache_dir = cache_dir.resolve()
    output_path = output_path.resolve()
    if not epub_path.is_file():
        raise FileNotFoundError(f"Source EPUB does not exist: {epub_path}")
    if not cache_dir.is_dir():
        raise FileNotFoundError(f"Preprocessing cache directory does not exist: {cache_dir}")
    if epub_path == output_path:
        raise ValueError("Output EPUB must be a different file from the source EPUB.")
    if output_path.suffix.lower() != ".epub":
        raise ValueError("Output filename must have an .epub extension.")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists; choose another path or pass --force: {output_path}")

    chapters = _load_chapters(epub_path)
    selected = _selected_chapters(chapters, start, end)
    member_by_chapter: dict[int, str] = {}
    with zipfile.ZipFile(epub_path, "r") as source:
        names = [info.filename for info in source.infolist()]
        if len(names) != len(set(names)):
            raise ValueError("Source EPUB has duplicate ZIP member names; refusing ambiguous rewrite.")
        for name in names:
            match = _chapter_file_pattern(name)
            if match and match.group("part") == "001":
                member_by_chapter[int(match.group("number"))] = name

    replacements_by_member: dict[str, list[str]] = {}
    glossaries_by_member: dict[str, list[dict[str, str]]] = {}
    for chapter in selected:
        member = member_by_chapter.get(chapter.number)
        if member is None:
            raise ValueError(f"Could not locate the body XHTML for chapter {chapter.number}.")
        rewritten, metadata = load_preprocessed_chapter(chapter, cache_dir)
        replacements = [
            segment.text
            for segment in rewritten.segments
            if segment.language == "zh" and segment.kind == "paragraph"
        ]
        replacements_by_member[member] = replacements
        glossaries_by_member[member] = metadata["vocabulary"]

    preserved_bookmarks: dict[str, bytes] = {}
    if output_path.exists() and overwrite:
        with zipfile.ZipFile(epub_path, "r") as source, zipfile.ZipFile(output_path, "r") as previous:
            if (
                _CALIBRE_BOOKMARK_MEMBER in source.namelist()
                and _CALIBRE_BOOKMARK_MEMBER in previous.namelist()
            ):
                previous_data = previous.read(_CALIBRE_BOOKMARK_MEMBER)
                if previous_data != source.read(_CALIBRE_BOOKMARK_MEMBER):
                    preserved_bookmarks[_CALIBRE_BOOKMARK_MEMBER] = previous_data

    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output_path.stem}_",
        suffix=".tmp.epub",
        dir=output_path.parent,
        delete=False,
    )
    temp_path = Path(handle.name)
    handle.close()
    try:
        with zipfile.ZipFile(epub_path, "r") as source, zipfile.ZipFile(temp_path, "w") as target:
            target.comment = source.comment
            for info in source.infolist():
                content = preserved_bookmarks.get(info.filename, source.read(info))
                replacements = replacements_by_member.get(info.filename)
                if replacements is not None:
                    chapter_match = _chapter_file_pattern(info.filename)
                    assert chapter_match is not None
                    chapter_number = int(chapter_match.group("number"))
                    content = _replace_chinese_paragraphs(content, replacements, chapter_number)
                    if include_glossary:
                        content = _append_vocabulary_glossary(
                            content,
                            glossaries_by_member[info.filename],
                            chapter_number,
                        )
                target.writestr(info, content)

        with zipfile.ZipFile(epub_path, "r") as source, zipfile.ZipFile(temp_path, "r") as check:
            if check.testzip() is not None:
                raise ValueError("Generated EPUB failed ZIP CRC validation.")
            if [info.filename for info in source.infolist()] != [info.filename for info in check.infolist()]:
                raise ValueError("Generated EPUB did not preserve the source package member order.")
            for member, expected in replacements_by_member.items():
                actual = _chinese_paragraph_texts(check.read(member), int(_chapter_file_pattern(member).group("number")))
                if actual != expected:
                    raise ValueError(f"Generated EPUB failed paragraph verification for {member}.")
                if include_glossary:
                    chapter_number = int(_chapter_file_pattern(member).group("number"))
                    expected_rows = [
                        f"{entry['hanzi']} ({entry['pinyin']}) — {entry['english']}"
                        for entry in glossaries_by_member[member]
                    ]
                    if _read_vocabulary_glossary(check.read(member), chapter_number) != expected_rows:
                        raise ValueError(f"Generated EPUB failed glossary verification for {member}.")

        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)

    paragraph_total = sum(len(paragraphs) for paragraphs in replacements_by_member.values())
    print(
        f"Created {output_path}: rewrote {len(selected)} chapter(s), "
        f"{paragraph_total} Chinese paragraphs; English and unselected chapters were retained."
        + (" Written vocabulary glossaries were appended." if include_glossary else "")
        + (" Existing Calibre bookmarks were preserved." if preserved_bookmarks else "")
    )
    return output_path


def build_parser() -> argparse.ArgumentParser:
    """Build command-line options for exporting a simplified study EPUB."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path, help="Original paired Chinese/English EPUB")
    parser.add_argument("cache_dir", type=Path, help="Validated preprocessing cache directory")
    parser.add_argument("output", type=Path, help="New .epub output path; source is never overwritten")
    parser.add_argument("--start", type=int, default=None, help="First cached chapter, inclusive")
    parser.add_argument("--end", type=int, default=None, help="Last cached chapter, inclusive")
    parser.add_argument("--force", action="store_true", help="Replace an existing output EPUB")
    parser.add_argument(
        "--include-glossary",
        action="store_true",
        help="Append each selected chapter's written Hanzi/Pinyin/English glossary",
    )
    return parser


def main() -> int:
    """Export selected chapters as a separate Simplified Chinese study EPUB."""
    args = build_parser().parse_args()
    try:
        export_simplified_epub(
            args.epub,
            args.cache_dir,
            args.output,
            start=args.start,
            end=args.end,
            overwrite=args.force,
            include_glossary=args.include_glossary,
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
