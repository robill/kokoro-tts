#!/usr/bin/env python3
"""Build an English-first study EPUB and one audiobook for a chapter range.

This orchestrator splits the selected chapter pairs from a prepared bilingual
EPUB, exports a fresh study EPUB using reviewed alignment maps, renders each
chapter in English/original-Chinese/A2 triplets, reuses compatible checkpoints,
and merges the chapter MP3s into one output audiobook.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from bilingual_epub_tts import _chapter_file_pattern, _load_chapters
from english_first_chapter import create_english_first_chapter
from english_first_study_epub import export_english_first_study_epub
from merge_chapter_audio import merge_chapter_mp3s


def _get_split_line_map(epubsplit_script: Path, source_epub: Path) -> dict[int, list[int]]:
    """List EpubSplit points and map each paired XHTML member to its split index.

    @param epubsplit_script: Path to the EpubSplit standalone Python CLI.
    @param source_epub: Prepared paired bilingual EPUB.
    @return: Chapter number to its [split_000, split_001] line indices.
    @raises RuntimeError: If EpubSplit fails or chapter pairs are malformed.
    """
    result = subprocess.run(
        [sys.executable, str(epubsplit_script), str(source_epub)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"EpubSplit could not list source split points: {result.stderr.strip()}")

    mapping: dict[int, dict[str, int]] = {}
    line_number: int | None = None
    for line in result.stdout.splitlines():
        if line.startswith("Line Number:"):
            try:
                line_number = int(line.split(":", 1)[1].strip())
            except ValueError as exc:
                raise RuntimeError(f"Unexpected EpubSplit line number: {line!r}") from exc
        elif line.strip().startswith("href:") and line_number is not None:
            member = line.split(":", 1)[1].strip()
            match = _chapter_file_pattern(member)
            if match:
                chapter_number = int(match.group("number"))
                part = match.group("part")
                if part in mapping.setdefault(chapter_number, {}):
                    raise RuntimeError(f"Duplicate EpubSplit {part} point for chapter {chapter_number}.")
                mapping[chapter_number][part] = line_number
            line_number = None

    malformed = {number: parts for number, parts in mapping.items() if set(parts) != {"000", "001"}}
    if malformed:
        raise RuntimeError(f"EpubSplit did not return paired split points: {malformed}")
    return {number: [parts["000"], parts["001"]] for number, parts in mapping.items()}


def _run_split(
    epubsplit_script: Path,
    source_epub: Path,
    chapter_lines: list[int],
    chapter_number: int,
    output_path: Path,
) -> None:
    """Create a new EPUB containing only selected EpubSplit lines.

    @param epubsplit_script: EpubSplit standalone CLI path.
    @param source_epub: Source paired EPUB (never modified).
    @param chapter_lines: EpubSplit line indices to include.
    @param chapter_number: Logical chapter number for the output title.
    @param output_path: New destination EPUB path.
    @raises RuntimeError: If the split operation fails or output is missing.
    """
    title = f"Chapter {chapter_number} English First Study"
    result = subprocess.run(
        [
            sys.executable,
            str(epubsplit_script),
            "--title",
            title,
            "-o",
            str(output_path),
            str(source_epub),
            *[str(index) for index in chapter_lines],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not output_path.is_file():
        raise RuntimeError(
            f"EpubSplit failed for chapter {chapter_number}: {result.stderr.strip()} {result.stdout.strip()}"
        )


def run_english_first_range(
    source_epub: Path,
    cache_dir: Path,
    alignment_dir: Path,
    previous_audio_root: Path,
    epubsplit_script: Path,
    output_root: Path,
    start: int,
    end: int,
) -> dict[str, object]:
    """Produce a fresh English-first study EPUB and one merged audiobook.

    @param source_epub: Prepared full-range paired Chinese/English EPUB.
    @param cache_dir: Validated per-chapter A2 preprocessing cache root.
    @param alignment_dir: Reviewed English-to-Chinese alignment map root.
    @param previous_audio_root: Existing conventional chapter audio root for exact checkpoint reuse.
    @param epubsplit_script: EpubSplit standalone Python CLI path.
    @param output_root: New dedicated output directory for this English-first edition.
    @param start: First chapter number, inclusive.
    @param end: Last chapter number, inclusive.
    @return: Output paths and aggregate audio-generation/reuse counters.
    @raises FileExistsError: If the destination output root already exists.
    @raises FileNotFoundError: If a requested source chapter, alignment, or cache is missing.
    @raises RuntimeError: If splitting, rendering, or merging fails validation.
    """
    source_epub = source_epub.resolve()
    cache_dir = cache_dir.resolve()
    alignment_dir = alignment_dir.resolve()
    previous_audio_root = previous_audio_root.resolve()
    epubsplit_script = epubsplit_script.resolve()
    output_root = output_root.resolve()
    if start > end:
        raise ValueError("--start must be <= --end")
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"Refusing to overwrite non-empty English-first output directory: {output_root}")
    if not source_epub.is_file() or not epubsplit_script.is_file():
        raise FileNotFoundError("Source paired EPUB or EpubSplit CLI was not found.")

    chapters = _load_chapters(source_epub)
    expected_numbers = list(range(start, end + 1))
    missing = [number for number in expected_numbers if number not in chapters]
    if missing:
        raise ValueError(f"Source paired EPUB is missing requested chapters: {missing}")
    split_lines = _get_split_line_map(epubsplit_script, source_epub)
    missing_points = [number for number in expected_numbers if number not in split_lines]
    if missing_points:
        raise ValueError(f"EpubSplit did not provide chapter split points: {missing_points}")
    for number in expected_numbers:
        alignment_path = alignment_dir / f"chapter_{number:04d}" / "english_chinese_alignment.json"
        if not alignment_path.is_file():
            raise FileNotFoundError(f"Missing reviewed alignment JSON for chapter {number}: {alignment_path}")

    output_root.mkdir(parents=True, exist_ok=True)
    split_dir = output_root / "paired_chapters"
    split_dir.mkdir()
    audio_root = output_root / "chapters"
    audio_root.mkdir()
    combined_study_epub = output_root / f"chapters_{start:04d}-{end:04d}_english_first_study.epub"
    combined_audio = output_root / f"chapters_{start:04d}-{end:04d}_english_first_audiobook.mp3"
    totals = {"blocks": 0, "english": 0, "original_chinese": 0, "a2_chinese": 0, "glossary": 0, "reused_wavs": 0, "synthesized_original_wavs": 0}

    # Build small paired EPUBs for each chapter from the mapped split-line indices.
    for position, number in enumerate(expected_numbers, 1):
        mini_epub = split_dir / f"chapter_{number:04d}.epub"
        _run_split(epubsplit_script, source_epub, split_lines[number], number, mini_epub)
        print(f"({position}/{len(expected_numbers)}) Split chapter {number} -> {mini_epub.name}", flush=True)

    range_epub = split_dir / f"chapters_{start:04d}-{end:04d}.epub"
    range_lines = [line for number in expected_numbers for line in split_lines[number]]
    range_title = f"Chapters {start}-{end} English First Study"
    range_split = subprocess.run(
        [
            sys.executable,
            str(epubsplit_script),
            "--title",
            range_title,
            "-o",
            str(range_epub),
            str(source_epub),
            *[str(index) for index in range_lines],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if range_split.returncode != 0 or not range_epub.is_file():
        raise RuntimeError(f"EpubSplit failed to create selected chapter range EPUB: {range_split.stderr.strip()}")

    export_english_first_study_epub(
        paired_epub=range_epub,
        cache_dir=cache_dir,
        alignment_dir=alignment_dir,
        output_epub=combined_study_epub,
        start=start,
        end=end,
    )

    for position, number in enumerate(expected_numbers, 1):
        chapter_output = audio_root / f"chapter_{number:04d}"
        prior_audio = previous_audio_root / f"chapter_{number:04d}"
        prior_original = None
        if number == 745:
            candidates = (
                output_root.parent / "RMJI_Chapter_0745_EnglishFirst" / "output_final" / "original_chinese_segments_0745",
                output_root.parent.parent / "RMJI_Chapter_0745_EnglishFirst" / "output_final" / "original_chinese_segments_0745",
            )
            prior_original = next((candidate for candidate in candidates if candidate.is_dir()), None)
        _study, chapter_audio, stats = create_english_first_chapter(
            paired_epub=split_dir / f"chapter_{number:04d}.epub",
            cache_dir=cache_dir,
            previous_audio=prior_audio,
            alignment_path=alignment_dir / f"chapter_{number:04d}" / "english_chinese_alignment.json",
            output_dir=chapter_output,
            previous_original_chinese_audio=prior_original,
        )
        standard_name = chapter_output / f"chapter_{number:04d}.mp3"
        shutil.copy2(chapter_audio, standard_name)
        totals["blocks"] += stats.get("triplets", 0)
        for key in totals:
            if key != "blocks":
                totals[key] += stats.get(key, 0)
        print(f"({position}/{len(expected_numbers)}) Rendered English-first chapter {number}: {chapter_audio.name}", flush=True)

    _, duration = merge_chapter_mp3s(audio_root, combined_audio, start, end, overwrite=False)
    totals["duration_seconds"] = duration
    totals["study_epub"] = str(combined_study_epub)
    totals["audiobook"] = str(combined_audio)
    totals["chapter_audio_root"] = str(audio_root)
    print(f"Created English-first audiobook: {combined_audio} ({duration / 60:.2f} minutes)")
    print(f"Created English-first study EPUB: {combined_study_epub}")
    return totals


def main() -> int:
    """Run the selected English-first chapter range and report its outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_epub", type=Path)
    parser.add_argument("cache_dir", type=Path)
    parser.add_argument("alignment_dir", type=Path)
    parser.add_argument("previous_audio_root", type=Path)
    parser.add_argument("epubsplit_script", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--end", type=int, required=True)
    args = parser.parse_args()
    try:
        result = run_english_first_range(
            args.source_epub,
            args.cache_dir,
            args.alignment_dir,
            args.previous_audio_root,
            args.epubsplit_script,
            args.output_root,
            args.start,
            args.end,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
