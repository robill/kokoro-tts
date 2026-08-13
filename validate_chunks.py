#!/usr/bin/env python3
"""Validate Kokoro split-output chapters for completeness and audio integrity.

Checks each chapter folder for:
- Missing chunks
- Empty/corrupted audio files
- Mismatches between expected and actual chunks
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import soundfile as sf


@dataclass
class ChapterStatus:
    chapter_num: int
    chapter_dir: str
    expected_chunks: int | None
    actual_chunks: int
    missing_chunks: List[int]
    corrupted_chunks: List[int]
    is_complete: bool
    error_msg: str | None


def _parse_chapter_num(chapter_dir: str, prefix: str = "chapter_") -> int | None:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    match = pattern.match(chapter_dir)
    return int(match.group(1)) if match else None


def validate_chapter(chapter_path: str, chapter_num: int) -> ChapterStatus:
    """Validate a single chapter folder for completeness and audio integrity."""
    
    # Read expected chunk count from info.txt
    info_file = os.path.join(chapter_path, "info.txt")
    expected_chunks = None
    if os.path.exists(info_file):
        try:
            with open(info_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith("Words:"):
                        # Rough estimate: ~150 words per minute, audio chunks ~1000 words each
                        words = int(line.split(":")[1].strip())
                        expected_chunks = max(1, (words + 999) // 1000)
                        break
        except Exception as e:
            pass

    # Collect actual chunks
    pattern = re.compile(r"^chunk_(\d+)\.mp3$")
    chunk_entries: List[Tuple[int, str]] = []
    corrupted = []
    
    for name in os.listdir(chapter_path):
        if not name.endswith(".mp3"):
            continue
        match = pattern.match(name)
        if match:
            chunk_num = int(match.group(1))
            chunk_path = os.path.join(chapter_path, name)
            
            # Validate audio file integrity
            try:
                data, sr = sf.read(chunk_path)
                if data is None or len(data) == 0:
                    corrupted.append(chunk_num)
                else:
                    chunk_entries.append((chunk_num, chunk_path))
            except Exception as e:
                corrupted.append(chunk_num)
    
    chunk_entries.sort(key=lambda x: x[0])
    actual_chunks = len(chunk_entries)
    
    # Detect gaps
    chunk_nums = [num for num, _ in chunk_entries]
    missing = []
    if chunk_nums:
        expected_range = set(range(chunk_nums[0], chunk_nums[-1] + 1))
        missing = sorted(expected_range - set(chunk_nums))
    
    # Determine completion status
    is_complete = not missing and not corrupted
    error_msg = None
    if missing:
        error_msg = f"Missing chunks: {missing}"
    if corrupted:
        if error_msg:
            error_msg += f"; Corrupted: {corrupted}"
        else:
            error_msg = f"Corrupted chunks: {corrupted}"
    
    return ChapterStatus(
        chapter_num=chapter_num,
        chapter_dir=os.path.basename(chapter_path),
        expected_chunks=expected_chunks,
        actual_chunks=actual_chunks,
        missing_chunks=missing,
        corrupted_chunks=corrupted,
        is_complete=is_complete,
        error_msg=error_msg,
    )


def validate_split_output(split_output_dir: str, chapter_prefix: str = "chapter_") -> List[ChapterStatus]:
    """Validate all chapters in a split-output directory."""
    
    if not os.path.isdir(split_output_dir):
        raise FileNotFoundError(f"Split-output directory not found: {split_output_dir}")
    
    statuses: List[ChapterStatus] = []
    
    # Collect chapter directories
    chapter_dirs: List[Tuple[int, str]] = []
    for name in os.listdir(split_output_dir):
        path = os.path.join(split_output_dir, name)
        if not os.path.isdir(path):
            continue
        chapter_num = _parse_chapter_num(name, chapter_prefix)
        if chapter_num is not None:
            chapter_dirs.append((chapter_num, path))
    
    chapter_dirs.sort(key=lambda x: x[0])
    
    for chapter_num, chapter_path in chapter_dirs:
        status = validate_chapter(chapter_path, chapter_num)
        statuses.append(status)
    
    return statuses


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Kokoro split-output chapters for completeness and audio integrity."
    )
    parser.add_argument(
        "split_output_dir",
        help="Directory containing chapter_### folders with chunk_###.mp3 files",
    )
    parser.add_argument(
        "--chapter-prefix",
        default="chapter_",
        help="Chapter folder prefix (default: chapter_)",
    )
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show status of all chapters, not just errors",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    split_output_dir = os.path.abspath(args.split_output_dir)
    
    try:
        statuses = validate_split_output(split_output_dir, args.chapter_prefix)
        
        if not statuses:
            print("No chapter directories found.")
            return 1
        
        # Separate complete and incomplete
        complete = [s for s in statuses if s.is_complete]
        incomplete = [s for s in statuses if not s.is_complete]
        
        # Summary
        print(f"\n=== Validation Report ===")
        print(f"Total chapters: {len(statuses)}")
        print(f"Complete: {len(complete)}")
        print(f"Incomplete: {len(incomplete)}")
        
        # Show incomplete chapters with errors
        if incomplete:
            print(f"\n--- Chapters with errors ---")
            for status in incomplete:
                print(f"\n{status.chapter_dir}:")
                print(f"  Chunks: {status.actual_chunks}", end="")
                if status.expected_chunks:
                    print(f" (expected ~{status.expected_chunks})", end="")
                print()
                if status.error_msg:
                    print(f"  Error: {status.error_msg}")
        
        # Generate retry command
        if incomplete:
            retry_chapters = sorted([s.chapter_num for s in incomplete])
            print(f"\n--- Chapters to retry ---")
            print(f"List: {retry_chapters}")
            print(f"\nTo retry only these chapters, re-run kokoro-tts with:")
            print(f"  python -m kokoro_tts <input.epub> --split-output <chunks_dir> ...")
            print(f"\nThe tool will skip completed chapters and only process chapters: {retry_chapters}")
        
        # Show all chapters if requested
        if args.show_all and complete:
            print(f"\n--- Complete chapters ---")
            for status in complete:
                print(f"{status.chapter_dir}: {status.actual_chunks} chunks ✓")
        
        return 0 if not incomplete else 1
    
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
