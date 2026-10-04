#!/usr/bin/env python3
"""Merge numbered per-chapter MP3s into one validated audiobook file."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AudioInfo:
    """Describe an MP3 stream and its container duration."""

    codec: str
    sample_rate: int
    channels: int
    duration_seconds: float


def collect_chapter_mp3s(
    chapter_root: Path,
    start: int,
    end: int,
    *,
    chapter_prefix: str = "chapter_",
) -> list[Path]:
    """Collect exactly one existing MP3 for every chapter in the requested range.

    @param chapter_root: Directory containing numbered chapter subfolders.
    @param start: First expected chapter number, inclusive.
    @param end: Last expected chapter number, inclusive.
    @param chapter_prefix: Prefix used by chapter directories.
    @return: Chapter MP3 paths in ascending numeric order.
    @raises FileNotFoundError: If the root or any requested chapter MP3 is missing.
    @raises ValueError: If the range is invalid.
    """
    chapter_root = chapter_root.resolve()
    if start > end:
        raise ValueError("--start must be less than or equal to --end.")
    if not chapter_root.is_dir():
        raise FileNotFoundError(f"Chapter audio directory does not exist: {chapter_root}")
    paths: list[Path] = []
    missing: list[int] = []
    for number in range(start, end + 1):
        chapter_dir = chapter_root / f"{chapter_prefix}{number:04d}"
        chapter_mp3 = chapter_dir / f"{chapter_prefix}{number:04d}.mp3"
        if not chapter_mp3.is_file() or chapter_mp3.stat().st_size == 0:
            missing.append(number)
        else:
            paths.append(chapter_mp3)
    if missing:
        raise FileNotFoundError(f"Missing or empty chapter MP3s for: {missing}")
    return paths


def _probe_audio(path: Path, ffprobe: str) -> AudioInfo:
    """Read MP3 stream properties with ffprobe.

    @param path: MP3 file to inspect.
    @param ffprobe: ffprobe executable path.
    @return: Audio codec, sample rate, channel count, and duration.
    @raises RuntimeError: If the input cannot be probed or has no audio stream.
    """
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {completed.stderr.strip()}")
    try:
        data: dict[str, Any] = json.loads(completed.stdout)
        streams = data["streams"]
        audio = streams[0]
        duration = float(data["format"]["duration"])
        result = AudioInfo(
            codec=str(audio["codec_name"]),
            sample_rate=int(audio["sample_rate"]),
            channels=int(audio["channels"]),
            duration_seconds=duration,
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"ffprobe returned incomplete audio metadata for {path}.") from exc
    if result.duration_seconds <= 0:
        raise RuntimeError(f"Audio duration is invalid for {path}.")
    return result


def _concat_quote(path: Path) -> str:
    """Escape a local path for an ffmpeg concat-demuxer list entry."""
    normalized = path.resolve().as_posix()
    return "'" + normalized.replace("'", "'\\''") + "'"


def merge_chapter_mp3s(
    chapter_root: Path,
    output_path: Path,
    start: int,
    end: int,
    *,
    chapter_prefix: str = "chapter_",
    overwrite: bool = False,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
) -> tuple[Path, float]:
    """Concatenate numbered chapter MP3s without decoding/re-encoding audio.

    The function requires a complete contiguous chapter range and matching
    stream properties. It uses ffmpeg stream-copy mode to avoid quality loss
    and excessive RAM use, writes atomically, then fully decodes the result to
    validate it before moving it to the requested output path.

    @param chapter_root: Directory with chapter_NNNN/chapter_NNNN.mp3 files.
    @param output_path: Destination MP3 path.
    @param start: First chapter number, inclusive.
    @param end: Last chapter number, inclusive.
    @param chapter_prefix: Chapter directory/file prefix.
    @param overwrite: Replace the destination if it already exists.
    @param ffmpeg: Optional ffmpeg executable override.
    @param ffprobe: Optional ffprobe executable override.
    @return: Resolved output path and merged duration in seconds.
    @raises FileNotFoundError: If ffmpeg, ffprobe, or any chapter MP3 is missing.
    @raises FileExistsError: If the output exists and overwrite is false.
    @raises RuntimeError: If chapter formats mismatch or ffmpeg validation fails.
    @raises ValueError: If input and output paths or range are invalid.
    """
    chapter_root = chapter_root.resolve()
    output_path = output_path.resolve()
    if output_path.suffix.lower() != ".mp3":
        raise ValueError("Output filename must use the .mp3 extension.")
    if output_path == chapter_root or chapter_root in output_path.parents:
        raise ValueError("Output MP3 must be outside the per-chapter audio directory.")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists; pass --force to replace it: {output_path}")

    ffmpeg_exe = ffmpeg or shutil.which("ffmpeg")
    ffprobe_exe = ffprobe or shutil.which("ffprobe")
    if not ffmpeg_exe:
        raise FileNotFoundError("ffmpeg was not found on PATH.")
    if not ffprobe_exe:
        raise FileNotFoundError("ffprobe was not found on PATH.")

    inputs = collect_chapter_mp3s(chapter_root, start, end, chapter_prefix=chapter_prefix)
    reference: AudioInfo | None = None
    input_duration = 0.0
    for path in inputs:
        info = _probe_audio(path, ffprobe_exe)
        if info.codec != "mp3":
            raise RuntimeError(f"Expected MP3 audio in {path}, found {info.codec}.")
        if reference is None:
            reference = info
        elif (info.codec, info.sample_rate, info.channels) != (
            reference.codec,
            reference.sample_rate,
            reference.channels,
        ):
            raise RuntimeError(
                f"Audio properties in {path} do not match the first chapter: {info} vs {reference}."
            )
        input_duration += info.duration_seconds
    if reference is None:
        raise RuntimeError("No chapter audio inputs were found.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        suffix=".ffconcat",
        prefix="chapter_audio_",
        dir=output_path.parent,
        delete=False,
    )
    list_path = Path(list_handle.name)
    temp_output = output_path.with_name(f".{output_path.stem}.tmp.mp3")
    try:
        with list_handle:
            list_handle.write("ffconcat version 1.0\n")
            for path in inputs:
                list_handle.write(f"file {_concat_quote(path)}\n")

        if temp_output.exists():
            temp_output.unlink()
        merged = subprocess.run(
            [
                ffmpeg_exe,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-map",
                "0:a:0",
                "-c:a",
                "copy",
                "-id3v2_version",
                "3",
                "-metadata",
                f"title=RMJI Chapters {start}-{end} — Bilingual Study Audiobook",
                "-metadata",
                "album=Record of a Mortal's Journey to Immortality",
                str(temp_output),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if merged.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {merged.stderr.strip()}")
        if not temp_output.is_file() or temp_output.stat().st_size == 0:
            raise RuntimeError("ffmpeg produced no merged output.")

        output_info = _probe_audio(temp_output, ffprobe_exe)
        if (output_info.codec, output_info.sample_rate, output_info.channels) != (
            reference.codec,
            reference.sample_rate,
            reference.channels,
        ):
            raise RuntimeError(f"Merged audiobook stream properties changed: {output_info}.")
        # MP3 encoder delay/padding contributes a small duration offset per source file.
        tolerance = max(1.0, len(inputs) * 0.1)
        if abs(output_info.duration_seconds - input_duration) > tolerance:
            raise RuntimeError(
                f"Merged duration {output_info.duration_seconds:.2f}s differs from input total "
                f"{input_duration:.2f}s by more than {tolerance:.2f}s."
            )

        decoded = subprocess.run(
            [ffmpeg_exe, "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", str(temp_output), "-f", "null", "-"],
            check=False,
            capture_output=True,
            text=True,
        )
        if decoded.returncode != 0:
            raise RuntimeError(f"Merged audiobook failed full decode validation: {decoded.stderr.strip()}")
        if output_path.exists() and not overwrite:
            raise FileExistsError(f"Output appeared during merge; refusing to overwrite: {output_path}")
        os.replace(temp_output, output_path)
    finally:
        list_path.unlink(missing_ok=True)
        temp_output.unlink(missing_ok=True)

    return output_path, output_info.duration_seconds


def build_parser() -> argparse.ArgumentParser:
    """Build command-line options for merging chapter MP3s."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chapter_root", type=Path, help="Directory containing chapter_NNNN/chapter_NNNN.mp3 files")
    parser.add_argument("output", type=Path, help="Destination for the single combined audiobook MP3")
    parser.add_argument("--start", type=int, default=721, help="First chapter, inclusive")
    parser.add_argument("--end", type=int, default=770, help="Last chapter, inclusive")
    parser.add_argument("--chapter-prefix", default="chapter_", help="Numbered chapter folder/file prefix")
    parser.add_argument("--force", action="store_true", help="Replace an existing destination MP3")
    return parser


def main() -> int:
    """Merge and validate the requested chapter range."""
    args = build_parser().parse_args()
    try:
        output_path, duration = merge_chapter_mp3s(
            args.chapter_root,
            args.output,
            args.start,
            args.end,
            chapter_prefix=args.chapter_prefix,
            overwrite=args.force,
        )
        print(f"Merged chapters: {args.start}-{args.end}")
        print(f"Output: {output_path}")
        print(f"Duration: {duration / 60:.2f} minutes")
        print(f"Size: {output_path.stat().st_size:,} bytes")
        print("Merge: MP3 stream copy (no re-encoding)")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
