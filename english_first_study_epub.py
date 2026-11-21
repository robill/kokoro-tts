#!/usr/bin/env python3
"""Export a selected paired bilingual EPUB range in English/original/A2 order."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

from bilingual_epub_tts import _chapter_file_pattern, _load_chapters
from chinese_llm_preprocess import load_preprocessed_chapter
from english_first_chapter import _make_study_epub, _replace_paragraph_sequence, _set_chapter_headings


def export_english_first_study_epub(
    paired_epub: Path,
    cache_dir: Path,
    alignment_dir: Path,
    output_epub: Path,
    start: int,
    end: int,
) -> Path:
    """Export a new chapter-range EPUB with English, original Chinese, and A2 triplets.

    @param paired_epub: Prepared bilingual EPUB restricted to the selected chapter range.
    @param cache_dir: Root containing validated per-chapter A2 caches.
    @param alignment_dir: Root containing reviewed per-chapter alignment JSON files.
    @param output_epub: New destination EPUB path; must not already exist.
    @param start: First chapter number, inclusive.
    @param end: Last chapter number, inclusive.
    @return: Resolved path to the completed study EPUB.
    @raises FileNotFoundError: If a cache or alignment map is missing.
    @raises ValueError: If source/cache/alignment coverage is inconsistent.
    @raises FileExistsError: If the output already exists.
    """
    paired_epub = paired_epub.resolve()
    cache_dir = cache_dir.resolve()
    alignment_dir = alignment_dir.resolve()
    output_epub = output_epub.resolve()
    if output_epub.exists():
        raise FileExistsError(f"Refusing to overwrite study EPUB: {output_epub}")
    if start > end:
        raise ValueError("--start must be <= --end")
    chapters = _load_chapters(paired_epub)
    expected_numbers = list(range(start, end + 1))
    if sorted(chapters) != expected_numbers:
        raise ValueError(f"Paired EPUB must contain exactly chapters {start}-{end}; found {sorted(chapters)}")

    members: dict[int, dict[str, str]] = {number: {} for number in expected_numbers}
    with zipfile.ZipFile(paired_epub, "r") as source:
        names = [entry.filename for entry in source.infolist()]
        if len(names) != len(set(names)):
            raise ValueError("Source EPUB has duplicate ZIP member names.")
        for name in names:
            match = _chapter_file_pattern(name)
            if match and int(match.group("number")) in members:
                members[int(match.group("number"))][match.group("part")] = name
        for number in expected_numbers:
            if set(members[number]) != {"000", "001"}:
                raise ValueError(f"Chapter {number} is missing one paired XHTML member.")

    transformed: dict[str, bytes] = {}
    expected_by_member: dict[str, list[str]] = {}
    total_original = total_a2 = total_english = total_blocks = total_glossary = 0
    for number in expected_numbers:
        chapter = chapters[number]
        rewritten, metadata = load_preprocessed_chapter(chapter, cache_dir)
        alignment_path = alignment_dir / f"chapter_{number:04d}" / "english_chinese_alignment.json"
        if not alignment_path.is_file():
            raise FileNotFoundError(f"Missing alignment map for chapter {number}: {alignment_path}")
        alignment_record = json.loads(alignment_path.read_text(encoding="utf-8"))
        alignment_source_hash = alignment_record.get(
            "source_sha256", alignment_record.get("source_chapter_sha256")
        )
        if alignment_source_hash != metadata.get("source_sha256"):
            raise ValueError(f"Chapter {number} alignment map/source digest mismatch.")
        alignment = alignment_record.get("alignment", alignment_record)
        blocks = alignment.get("blocks")
        if not isinstance(blocks, list) or not blocks:
            raise ValueError(f"Chapter {number} alignment file has no blocks.")

        original = [s.text for s in chapter.segments if s.language == "zh" and s.kind == "paragraph"]
        english = [s.text for s in chapter.segments if s.language == "en" and s.kind == "paragraph"]
        a2 = [s.text for s in rewritten.segments if s.language == "zh" and s.kind == "paragraph"]
        flat_e = [idx for block in blocks for idx in block.get("english_paragraphs", [])]
        flat_z = [idx for block in blocks for idx in block.get("chinese_paragraphs", [])]
        if flat_e != list(range(1, len(english) + 1)) or flat_z != list(range(1, len(original) + 1)):
            raise ValueError(f"Chapter {number} alignment does not cover every source paragraph exactly once in order.")

        # Reuse the single-chapter transformer for each paired XHTML member.
        title_name = members[number]["000"]
        body_name = members[number]["001"]
        with zipfile.ZipFile(paired_epub, "r") as source:
            title_xhtml = source.read(title_name)
            body_xhtml = source.read(body_name)
        title_xhtml, body_xhtml = _set_chapter_headings(
            title_xhtml, body_xhtml, chapter.english_title, chapter.chinese_title
        )
        body_xhtml, records = _replace_paragraph_sequence(
            body_xhtml,
            english,
            original,
            a2,
            blocks,
            metadata["vocabulary"],
            number,
        )
        transformed[title_name] = title_xhtml
        transformed[body_name] = body_xhtml

        expected_text: list[str] = []
        for block in blocks:
            expected_text.extend(english[idx - 1] for idx in block["english_paragraphs"])
            if block.get("translation_notice"):
                expected_text.append("Translation note: " + " ".join(block["translation_notice"].split()))
            for idx in block["chinese_paragraphs"]:
                expected_text.extend(("原文： " + original[idx - 1], "A2改写： " + a2[idx - 1]))
        expected_by_member[body_name] = expected_text
        total_original += len(original)
        total_a2 += len(a2)
        total_english += len(english)
        total_blocks += len(blocks)
        total_glossary += len(metadata["vocabulary"])
        print(
            f"Prepared chapter {number}: {len(english)} English, {len(original)} original Chinese, "
            f"{len(a2)} A2, {len(blocks)} alignment blocks, {len(metadata['vocabulary'])} glossary entries.",
            flush=True,
        )

    output_epub.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{output_epub.stem}_", suffix=".tmp.epub", dir=output_epub.parent, delete=False
    )
    temp_path = Path(handle.name)
    handle.close()
    try:
        with zipfile.ZipFile(paired_epub, "r") as source, zipfile.ZipFile(temp_path, "w") as target:
            target.comment = source.comment
            for info in source.infolist():
                target.writestr(info, transformed.get(info.filename, source.read(info)))
        with zipfile.ZipFile(temp_path, "r") as check:
            if check.testzip() is not None:
                raise ValueError("English-first study EPUB failed ZIP validation.")
            for member, expected in expected_by_member.items():
                soup = BeautifulSoup(check.read(member), "html.parser")
                actual = [" ".join(p.get_text(" ", strip=True).split()) for p in soup.find_all("p")]
                if actual[: len(expected)] != expected:
                    raise ValueError(f"Chapter XHTML order verification failed: {member}")
                if actual[len(expected) :] and not soup.find("div", class_="study-glossary"):
                    raise ValueError(f"Unexpected trailing paragraph content in {member}")
        os.replace(temp_path, output_epub)
    finally:
        temp_path.unlink(missing_ok=True)

    print(
        f"Created {output_epub}: chapters {start}-{end}, {total_blocks} blocks, "
        f"{total_english} English, {total_original} original Chinese, {total_a2} A2 paragraphs, "
        f"{total_glossary} glossary entries."
    )
    return output_epub


def main() -> int:
    """Export the selected range as an English-first triplet study EPUB."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paired_epub", type=Path)
    parser.add_argument("cache_dir", type=Path)
    parser.add_argument("alignment_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    args = parser.parse_args()
    try:
        export_english_first_study_epub(
            args.paired_epub,
            args.cache_dir,
            args.alignment_dir,
            args.output,
            args.start,
            args.end,
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
