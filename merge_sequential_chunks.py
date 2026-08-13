#!/usr/bin/env python3
"""Merge Kokoro split-output chunks into one fully ordered audio file.

This script reads chapter folders like chapter_001/chunk_001.mp3 in numeric order,
concatenates all chunks, and writes a single output file.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
import soundfile as sf


@dataclass
class MergeStats:
    chapter_count: int
    chunk_count: int
    duration_seconds: float
    sample_rate: int


def _collect_numbered_dirs(base_dir: str, prefix: str) -> List[Tuple[int, str]]:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    items: List[Tuple[int, str]] = []
    for name in os.listdir(base_dir):
        path = os.path.join(base_dir, name)
        if not os.path.isdir(path):
            continue
        match = pattern.match(name)
        if match:
            items.append((int(match.group(1)), path))
    return sorted(items, key=lambda x: x[0])


def _collect_numbered_files(chapter_dir: str, prefix: str, ext: str) -> List[Tuple[int, str]]:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.{re.escape(ext)}$")
    items: List[Tuple[int, str]] = []
    for name in os.listdir(chapter_dir):
        path = os.path.join(chapter_dir, name)
        if not os.path.isfile(path):
            continue
        match = pattern.match(name)
        if match:
            items.append((int(match.group(1)), path))
    return sorted(items, key=lambda x: x[0])


def _find_gaps(numbers: List[int]) -> List[int]:
    if not numbers:
        return []
    expected = set(range(numbers[0], numbers[-1] + 1))
    return sorted(expected - set(numbers))


def merge_sequential_chunks(
    split_output_dir: str,
    output_file: str,
    audio_format: str,
    chapter_prefix: str = "chapter_",
    chunk_prefix: str = "chunk_",
    strict: bool = True,
    dry_run: bool = False,
) -> MergeStats:
    if not os.path.isdir(split_output_dir):
        raise FileNotFoundError(f"Split-output directory not found: {split_output_dir}")

    chapter_entries = _collect_numbered_dirs(split_output_dir, chapter_prefix)
    if not chapter_entries:
        raise RuntimeError(
            f"No chapter directories found in {split_output_dir} matching {chapter_prefix}<number>"
        )

    chapter_numbers = [num for num, _ in chapter_entries]
    chapter_gaps = _find_gaps(chapter_numbers)
    if chapter_gaps:
        msg = f"Missing chapter directories for numbers: {chapter_gaps}"
        if strict:
            raise RuntimeError(msg)
        print(f"Warning: {msg}")

    chunk_count = 0
    sample_rate = None
    channel_count = None
    all_audio: List[np.ndarray] = []

    for chapter_num, chapter_path in chapter_entries:
        chunk_entries = _collect_numbered_files(chapter_path, chunk_prefix, audio_format)
        if not chunk_entries:
            msg = f"No chunks found in chapter {chapter_num:03d} ({chapter_path})"
            if strict:
                raise RuntimeError(msg)
            print(f"Warning: {msg}")
            continue

        chunk_numbers = [num for num, _ in chunk_entries]
        chunk_gaps = _find_gaps(chunk_numbers)
        if chunk_gaps:
            msg = f"Missing chunks in chapter {chapter_num:03d}: {chunk_gaps}"
            if strict:
                raise RuntimeError(msg)
            print(f"Warning: {msg}")

        for _, chunk_path in chunk_entries:
            data, sr = sf.read(chunk_path)
            if data is None or len(data) == 0:
                msg = f"Empty chunk skipped: {chunk_path}"
                if strict:
                    raise RuntimeError(msg)
                print(f"Warning: {msg}")
                continue

            if sample_rate is None:
                sample_rate = sr
            elif sr != sample_rate:
                raise RuntimeError(
                    f"Sample rate mismatch in {chunk_path}: {sr} vs expected {sample_rate}"
                )

            current_channels = 1 if data.ndim == 1 else data.shape[1]
            if channel_count is None:
                channel_count = current_channels
            elif current_channels != channel_count:
                raise RuntimeError(
                    f"Channel mismatch in {chunk_path}: {current_channels} vs expected {channel_count}"
                )

            if not dry_run:
                all_audio.append(np.asarray(data))
            chunk_count += 1

    if sample_rate is None:
        raise RuntimeError("No readable chunks found.")

    if dry_run:
        return MergeStats(
            chapter_count=len(chapter_entries),
            chunk_count=chunk_count,
            duration_seconds=0.0,
            sample_rate=sample_rate,
        )

    merged_audio = np.concatenate(all_audio)
    sf.write(output_file, merged_audio, sample_rate)
    duration_seconds = len(merged_audio) / sample_rate

    return MergeStats(
        chapter_count=len(chapter_entries),
        chunk_count=chunk_count,
        duration_seconds=duration_seconds,
        sample_rate=sample_rate,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge Kokoro split-output chapter/chunk folders into one sequential audio file."
    )
    parser.add_argument(
        "split_output_dir",
        help="Directory containing chapter_### folders with chunk_###.<format> files",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output audio file path. Default: <split_output_dir>_complete_sequential.<format>",
    )
    parser.add_argument(
        "--format",
        default="mp3",
        choices=["mp3", "wav"],
        help="Audio format extension to read and write (default: mp3)",
    )
    parser.add_argument(
        "--chapter-prefix",
        default="chapter_",
        help="Chapter folder prefix (default: chapter_)",
    )
    parser.add_argument(
        "--chunk-prefix",
        default="chunk_",
        help="Chunk file prefix (default: chunk_)",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Skip missing chapter/chunk entries instead of failing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report stats without writing output",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    split_output_dir = os.path.abspath(args.split_output_dir)
    if args.output:
        output_file = os.path.abspath(args.output)
    else:
        output_file = split_output_dir.rstrip("\\/") + f"_complete_sequential.{args.format}"

    try:
        stats = merge_sequential_chunks(
            split_output_dir=split_output_dir,
            output_file=output_file,
            audio_format=args.format,
            chapter_prefix=args.chapter_prefix,
            chunk_prefix=args.chunk_prefix,
            strict=not args.no_strict,
            dry_run=args.dry_run,
        )

        print(f"Input: {split_output_dir}")
        print(f"Chapters: {stats.chapter_count}")
        print(f"Chunks merged: {stats.chunk_count}")
        print(f"Sample rate: {stats.sample_rate}")
        if args.dry_run:
            print("Dry run only. No output file written.")
        else:
            print(f"Output: {output_file}")
            print(f"Duration: {stats.duration_seconds:.2f} seconds")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
