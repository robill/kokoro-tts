#!/usr/bin/env python3
"""Align existing English translations to source Chinese paragraphs in order."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from bilingual_epub_tts import Chapter, _load_chapters
from chinese_llm_preprocess import _make_client, _source_digest, _source_payload, load_preprocessed_chapter

ALIGNMENT_PROMPT = """You align existing English translation paragraphs with their original Chinese source paragraphs for an English-first language-learning EPUB/audio.

Do not rewrite, translate, summarize, or edit any supplied text. Determine contiguous semantic blocks, each containing one or more 1-based English paragraph numbers and one or more 1-based original Chinese paragraph numbers. A block means the listed English text translates the listed Chinese text as a unit. Paragraphs in source order can be split/combined differently across languages.

Rules:
- Keep English indices increasing and Chinese indices increasing across blocks.
- Include every English and every original Chinese paragraph exactly once; no gaps or duplicate indices.
- Each block must have at least one English and one Chinese paragraph.
- Prefer one-to-one blocks where they truly align; combine neighboring paragraphs only when needed to preserve meaning.
- Use the A2 Chinese only as an additional alignment hint; never use it in place of the original Chinese.
- If a boundary is genuinely ambiguous, choose the most plausible contiguous alignment and explain it in alignment_notes. Do not silently drop text.
- Return only a JSON object with exactly these fields: blocks (array of objects with english_paragraphs and chinese_paragraphs arrays of 1-based integers) and alignment_notes (array of strings).
"""


def _validate_alignment(
    data: dict[str, Any], english_count: int, chinese_count: int
) -> dict[str, Any]:
    """Validate complete, monotonic alignment blocks for one chapter.

    @param data: Parsed LLM alignment JSON.
    @param english_count: Expected number of English paragraphs.
    @param chinese_count: Expected number of original Chinese paragraphs.
    @return: Normalized alignment blocks and notes.
    @raises ValueError: If indices are missing, duplicated, unordered, or invalid.
    """
    blocks = data.get("blocks")
    notes = data.get("alignment_notes", [])
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("Alignment response must contain a non-empty blocks array.")
    if not isinstance(notes, list) or any(not isinstance(note, str) for note in notes):
        raise ValueError("alignment_notes must be an array of strings.")

    english_flat: list[int] = []
    chinese_flat: list[int] = []
    normalized: list[dict[str, list[int]]] = []
    for index, block in enumerate(blocks, 1):
        if not isinstance(block, dict):
            raise ValueError(f"Alignment block {index} is not an object.")
        english = block.get("english_paragraphs")
        chinese = block.get("chinese_paragraphs")
        if not isinstance(english, list) or not english or not isinstance(chinese, list) or not chinese:
            raise ValueError(f"Alignment block {index} needs non-empty English and Chinese index arrays.")
        if any(not isinstance(item, int) or isinstance(item, bool) for item in english + chinese):
            raise ValueError(f"Alignment block {index} indices must be integers.")
        if english != sorted(english) or chinese != sorted(chinese):
            raise ValueError(f"Alignment block {index} indices must be increasing.")
        normalized_block = {"english_paragraphs": english, "chinese_paragraphs": chinese}
        notice = block.get("translation_notice")
        if notice is not None:
            if not isinstance(notice, str) or not notice.strip():
                raise ValueError(f"Alignment block {index} translation_notice must be non-empty text.")
            normalized_block["translation_notice"] = notice.strip()
        normalized.append(normalized_block)
        english_flat.extend(english)
        chinese_flat.extend(chinese)

    if english_flat != list(range(1, english_count + 1)):
        raise ValueError(
            f"English indices must cover 1..{english_count} once in source order; got {english_flat}."
        )
    if chinese_flat != list(range(1, chinese_count + 1)):
        raise ValueError(
            f"Chinese indices must cover 1..{chinese_count} once in source order; got {chinese_flat}."
        )
    return {"blocks": normalized, "alignment_notes": [note.strip() for note in notes if note.strip()]}


def _parse_response(content: str, english_count: int, chinese_count: int) -> dict[str, Any]:
    """Parse and validate a JSON alignment response."""
    text = content.strip()
    if text.startswith("```"):
        text = text.partition("\n")[2].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("VIO response did not contain a JSON object.")
    result = json.loads(text[start : end + 1])
    if not isinstance(result, dict):
        raise ValueError("Alignment JSON must be an object.")
    return _validate_alignment(result, english_count, chinese_count)


def _payload(chapter: Chapter, cache_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect exact source and current A2 paragraphs plus preprocessing metadata."""
    source = _source_payload(chapter)
    rewritten, metadata = load_preprocessed_chapter(chapter, cache_dir)
    a2 = [
        segment.text
        for segment in rewritten.segments
        if segment.language == "zh" and segment.kind == "paragraph"
    ]
    if len(a2) != len(source["chinese_paragraphs"]):
        raise ValueError(f"Chapter {chapter.number} A2 cache paragraph count is misaligned.")
    return {
        "chapter": chapter.number,
        "english_title": chapter.english_title,
        "chinese_title": chapter.chinese_title,
        "english_paragraphs": source["english_reference_paragraphs"],
        "original_chinese_paragraphs": source["chinese_paragraphs"],
        "a2_chinese_paragraphs": a2,
    }, metadata


def _write_alignment_review(chapter: Chapter, payload: dict[str, Any], alignment: dict[str, Any], path: Path) -> None:
    """Write a human-readable mapping audit for reviewer confirmation."""
    lines = [
        f"# Chapter {chapter.number} English–Chinese Alignment Review",
        "",
        f"**English title:** {chapter.english_title}",
        f"**Chinese title:** {chapter.chinese_title}",
        "",
        "Each block is rendered as English translation(s), original Chinese paragraph(s), then aligned A2 paragraph(s).",
        "Review semantic grouping before accepting the English-first output.",
        "",
    ]
    for block_number, block in enumerate(alignment["blocks"], 1):
        lines.extend([f"## Alignment block {block_number}", "", "**English**", ""])
        for index in block["english_paragraphs"]:
            lines.extend([f"E{index}: {payload['english_paragraphs'][index - 1]}", ""])
        if block.get("translation_notice"):
            lines.extend([f"**Translation coverage note:** {block['translation_notice']}", ""])
        lines.extend(["**Original Chinese**", ""])
        for index in block["chinese_paragraphs"]:
            lines.extend([f"C{index}: {payload['original_chinese_paragraphs'][index - 1]}", ""])
        lines.extend(["**A2 Chinese**", ""])
        for index in block["chinese_paragraphs"]:
            lines.extend([f"A2-{index}: {payload['a2_chinese_paragraphs'][index - 1]}", ""])
    lines.extend(["## Alignment notes", ""])
    lines.extend([f"- {note}" for note in alignment["alignment_notes"]] or ["- None reported."])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def align_chapter(chapter: Chapter, cache_dir: Path, output_dir: Path, client: Any, model: str, *, retries: int = 2) -> tuple[Path, Path, bool]:
    """Create or reuse one source-hash-bound English/Chinese paragraph alignment.

    @param chapter: Chapter extracted from the paired bilingual EPUB.
    @param cache_dir: Root containing the exact chapter preprocessing cache.
    @param output_dir: Directory for alignment JSON and review Markdown.
    @param client: Configured VIO-compatible OpenAI client.
    @param model: Model identifier to use for alignment only.
    @param retries: Retry count for malformed alignment JSON.
    @return: Alignment JSON path, review path, and whether a valid cache was reused.
    @raises RuntimeError: If the alignment could not be validated after retries.
    """
    payload, preprocessing = _payload(chapter, cache_dir)
    source_hash = preprocessing["source_sha256"]
    chapter_dir = output_dir / f"chapter_{chapter.number:04d}"
    alignment_path = chapter_dir / "english_chinese_alignment.json"
    review_path = chapter_dir / "english_chinese_alignment_review.md"
    if alignment_path.is_file():
        current = json.loads(alignment_path.read_text(encoding="utf-8"))
        if current.get("source_sha256") == source_hash and current.get("prompt_version") == "en-zh-block-alignment-v1":
            alignment = _validate_alignment(current.get("alignment", {}), len(payload["english_paragraphs"]), len(payload["original_chinese_paragraphs"]))
            _write_alignment_review(chapter, payload, alignment, review_path)
            return alignment_path, review_path, True

    user = "Align the following existing paragraphs. Return indices only, and do not rewrite any source text.\nSOURCE_JSON_BEGIN\n" + json.dumps(payload, ensure_ascii=False) + "\nSOURCE_JSON_END"
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        retry_feedback = ""
        if last_error is not None:
            retry_feedback = (
                "\nYour previous response was rejected: " + str(last_error) + ". "
                f"The exact required counts are {len(payload['english_paragraphs'])} English and "
                f"{len(payload['original_chinese_paragraphs'])} Chinese paragraphs. Do not invent indices; "
                "assign each index from 1 through its exact count once, in source order. Return corrected JSON only."
            )
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[
                {"role": "system", "content": ALIGNMENT_PROMPT},
                {"role": "user", "content": user + retry_feedback},
            ],
        )
        try:
            alignment = _parse_response(response.choices[0].message.content or "", len(payload["english_paragraphs"]), len(payload["original_chinese_paragraphs"]))
            record = {
                "chapter": chapter.number,
                "source_sha256": source_hash,
                "prompt_version": "en-zh-block-alignment-v1",
                "alignment_model": model,
                "source_paragraph_counts": {
                    "english": len(payload["english_paragraphs"]),
                    "original_chinese": len(payload["original_chinese_paragraphs"]),
                    "a2_chinese": len(payload["a2_chinese_paragraphs"]),
                },
                "alignment": alignment,
            }
            chapter_dir.mkdir(parents=True, exist_ok=True)
            alignment_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            _write_alignment_review(chapter, payload, alignment, review_path)
            return alignment_path, review_path, False
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Could not align chapter {chapter.number}: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path, help="Prepared paired Chinese/English EPUB")
    parser.add_argument("cache_dir", type=Path, help="Existing validated A2 preprocessing cache root")
    parser.add_argument("alignment_dir", type=Path, help="Output root for alignment JSON/reviews")
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    parser.add_argument("--model", default=None, help="Override AI_MODEL from local .env")
    parser.add_argument("--retries", type=int, default=2, help="Retries after an invalid alignment response")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue with later chapters after a chapter fails")
    args = parser.parse_args()
    if args.start > args.end:
        print("Error: --start must be <= --end", file=sys.stderr); return 2
    if args.retries < 0:
        print("Error: --retries must be non-negative", file=sys.stderr); return 2
    try:
        chapters = _load_chapters(args.epub.resolve())
        selected = [chapters[n] for n in range(args.start, args.end + 1)]
        client, configured_model = _make_client()
        model = args.model or configured_model
        print(f"Alignment model: {model}; chapters: {args.start}-{args.end}", flush=True)
        failures: list[tuple[int, str]] = []
        for i, chapter in enumerate(selected, 1):
            try:
                json_path, review_path, reused = align_chapter(
                    chapter, args.cache_dir.resolve(), args.alignment_dir.resolve(), client, model, retries=args.retries
                )
                print(f"({i}/{len(selected)}) Chapter {chapter.number}: {'reused' if reused else 'aligned'}; JSON={json_path}; review={review_path}", flush=True)
            except Exception as exc:
                failures.append((chapter.number, str(exc)))
                print(f"({i}/{len(selected)}) Chapter {chapter.number}: FAILED; {exc}", file=sys.stderr, flush=True)
                if not args.continue_on_error:
                    raise
        client.close()
        if failures:
            print("Alignment failures: " + "; ".join(f"{number}: {message}" for number, message in failures), file=sys.stderr)
            return 1
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
