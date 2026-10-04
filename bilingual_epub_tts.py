#!/usr/bin/env python3
"""Generate language-aware, paragraph-interleaved audio from bilingual EPUBs.

The RMJI bilingual EPUB stores a Chinese heading in split_000 and an English
heading plus alternating Chinese/English paragraphs in split_001. This tool
preserves that order, selects a Kokoro language and voice per paragraph, saves
individual WAV checkpoints for resume, and exports one MP3 per chapter.
Chinese segments use Misaki ZHG2P with Kokoro v1.1-zh; English stays on the
existing v1.0 model and voice bundle.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

import numpy as np
import soundfile as sf
from bs4 import BeautifulSoup

from kokoro_mandarin_backend import KokoroBilingualBackend


@dataclass(frozen=True)
class Segment:
    """Represent one language-tagged, speakable EPUB text segment."""

    language: str
    text: str
    kind: str


@dataclass(frozen=True)
class Chapter:
    """Store titles and ordered bilingual segments for one EPUB chapter."""

    number: int
    chinese_title: str
    english_title: str
    segments: tuple[Segment, ...]


def _chapter_file_pattern(name: str) -> re.Match[str] | None:
    return re.search(
        r"(?:^|/)(?P<prefix>\d{4})_(?P<number>\d+)_.*_split_(?P<part>000|001)\.xhtml$",
        name,
        re.IGNORECASE,
    )


def _clean_heading(soup: BeautifulSoup, language: str) -> str:
    heading = soup.find(["h1", "h2", "h3"])
    if heading is None:
        return ""
    text = " ".join(heading.get_text(" ", strip=True).split())
    if language == "zh":
        text = re.sub(r"^Unknown\s*", "", text, flags=re.IGNORECASE)
    return text


def _language_of(text: str) -> str | None:
    han = sum("\u3400" <= char <= "\u9fff" for char in text)
    latin = sum(char.isascii() and char.isalpha() for char in text)
    if han == 0 and latin == 0:
        return None
    return "zh" if han >= latin else "en"


def _load_chapters(epub_path: Path) -> dict[int, Chapter]:
    grouped: dict[int, dict[str, str]] = {}
    with zipfile.ZipFile(epub_path) as archive:
        for member in archive.namelist():
            match = _chapter_file_pattern(member)
            if match:
                number = int(match.group("number"))
                part = match.group("part")
                if part in grouped.setdefault(number, {}):
                    raise ValueError(f"Duplicate split_{part} file for chapter {number}: {member}")
                grouped[number][part] = member

        chapters: dict[int, Chapter] = {}
        for number, files in sorted(grouped.items()):
            if set(files) != {"000", "001"}:
                raise ValueError(f"Chapter {number} is missing one of its split_000/split_001 XHTML files")

            zh_soup = BeautifulSoup(archive.read(files["000"]), "html.parser")
            body_soup = BeautifulSoup(archive.read(files["001"]), "html.parser")
            zh_title = _clean_heading(zh_soup, "zh")
            en_title = _clean_heading(body_soup, "en")
            segments: list[Segment] = []
            if zh_title:
                segments.append(Segment("zh", zh_title, "heading"))
            if en_title:
                segments.append(Segment("en", en_title, "heading"))

            for paragraph in body_soup.find_all("p"):
                text = " ".join(paragraph.get_text(" ", strip=True).split())
                lang = _language_of(text)
                # The EPUB uses standalone ellipses between some passages;
                # do not synthesize punctuation-only decorations.
                if lang is not None:
                    segments.append(Segment(lang, text, "paragraph"))

            if not segments:
                raise ValueError(f"No speakable content found for chapter {number}")
            chapters[number] = Chapter(number, zh_title, en_title, tuple(segments))
    return chapters


def _split_for_tts(text: str, max_chars: int) -> list[str]:
    """Split long paragraphs near sentence/word boundaries, including CJK text."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return [text] if text else []

    sentences = re.split(r"(?<=[。！？!?；;])\s*", text)
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > max_chars:
            flush()
            # Hard-split very long sentences at a CJK/English punctuation or
            # whitespace boundary when possible; otherwise split by characters.
            while len(sentence) > max_chars:
                boundary = max(
                    (sentence.rfind(mark, 1, max_chars + 1) + 1 for mark in "，,、。！？!?；; "),
                    default=0,
                )
                if boundary < max_chars // 2:
                    boundary = max_chars
                chunks.append(sentence[:boundary].strip())
                sentence = sentence[boundary:].strip()
            current = sentence
            continue

        candidate = f"{current} {sentence}".strip()
        if len(candidate) > max_chars:
            flush()
            current = sentence
        else:
            current = candidate
    flush()
    return [chunk for chunk in chunks if chunk]


def _valid_wav(path: Path) -> bool:
    try:
        info = sf.info(str(path))
        return info.frames > 0 and info.samplerate > 0
    except (RuntimeError, OSError):
        return False


def _export_mp3(samples: np.ndarray, sample_rate: int, output_path: Path) -> None:
    try:
        from pydub import AudioSegment
    except ImportError as exc:
        raise RuntimeError("MP3 export needs pydub and ffmpeg; install/configure them, then rerun.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", dir=output_path.parent, delete=False) as tmp:
        temp_wav = Path(tmp.name)
    try:
        sf.write(str(temp_wav), samples.astype(np.float32, copy=False), sample_rate)
        AudioSegment.from_wav(str(temp_wav)).export(str(output_path), format="mp3", bitrate="128k")
    finally:
        temp_wav.unlink(missing_ok=True)


def _prepare_chapter(chapter: Chapter, output_dir: Path, max_chars: int) -> list[tuple[Segment, int, str]]:
    jobs: list[tuple[Segment, int, str]] = []
    segment_dir = output_dir / f"chapter_{chapter.number:04d}" / "segments"
    for index, segment in enumerate(chapter.segments, 1):
        for part_index, _ in enumerate(_split_for_tts(segment.text, max_chars), 1):
            filename = f"segment_{index:04d}_part_{part_index:02d}_{segment.language}.wav"
            jobs.append((segment, index, filename))
    return jobs


def _synthesize_vocabulary_appendix(
    chapter_dir: Path,
    backend: KokoroBilingualBackend,
    voices: dict[str, str],
    speeds: dict[str, float],
    vocabulary: object,
    pause_ms: int,
) -> tuple[np.ndarray, int]:
    """Synthesize Hanzi terms and English meanings as resumable WAV checkpoints.

    @param chapter_dir: Chapter output directory for vocabulary WAV checkpoints.
    @param backend: Language-matched Kokoro and Misaki synthesis backend.
    @param voices: Chinese and English voice IDs.
    @param speeds: Chinese and English synthesis speeds.
    @param vocabulary: Validated Hanzi/Pinyin/English entries from preprocessing.
    @param pause_ms: Silence after each English definition, in milliseconds.
    @return: Concatenated float32 audio and sample rate.
    @raises ValueError: If the glossary schema is empty or malformed.
    @raises RuntimeError: If any vocabulary audio cannot be synthesized or read.
    """
    if not isinstance(vocabulary, list) or not vocabulary:
        raise ValueError("The selected chapter has no vocabulary entries to append.")
    if pause_ms < 0:
        raise ValueError("Vocabulary pause duration cannot be negative.")

    checkpoint_dir = chapter_dir / "vocabulary_segments"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(vocabulary, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Vocabulary entry {index} is not an object.")
        row = {key: item.get(key) for key in ("hanzi", "pinyin", "english")}
        if any(not isinstance(value, str) or not value.strip() for value in row.values()):
            raise ValueError(f"Vocabulary entry {index} is missing Hanzi, Pinyin, or English text.")
        normalized.append({key: value.strip() for key, value in row.items()})

    ordered_paths: list[tuple[Path, str, int]] = []
    total_parts = len(normalized) * 2
    for index, item in enumerate(normalized, 1):
        for language, text in (("zh", item["hanzi"]), ("en", item["english"])):
            checkpoint = checkpoint_dir / f"item_{index:03d}_{language}.wav"
            if not _valid_wav(checkpoint):
                samples, sample_rate = backend.synthesize_with_fallback(
                    text,
                    language,
                    voices[language],
                    speeds[language],
                )
                if len(samples) == 0 or not np.isfinite(samples).all():
                    raise RuntimeError(f"No valid audio produced for vocabulary item {index} ({language}).")
                sf.write(str(checkpoint), np.asarray(samples, dtype=np.float32), sample_rate)
                print(f"[Vocabulary {len(ordered_paths) + 1}/{total_parts}] Saved {checkpoint.name}")
            ordered_paths.append((checkpoint, language, index))

    audio_parts: list[np.ndarray] = []
    sample_rate: int | None = None
    for checkpoint, language, index in ordered_paths:
        samples, rate = sf.read(str(checkpoint), dtype="float32")
        if not np.isfinite(samples).all() or samples.size == 0:
            raise RuntimeError(f"Vocabulary checkpoint is empty or non-finite: {checkpoint}")
        if sample_rate is None:
            sample_rate = rate
        elif rate != sample_rate:
            raise RuntimeError(f"Sample-rate mismatch in vocabulary checkpoint {checkpoint}: {rate} vs {sample_rate}")
        audio_parts.append(np.asarray(samples, dtype=np.float32))
        if language == "zh":
            # Add a short boundary so each Hanzi term is distinct from its English gloss.
            audio_parts.append(np.zeros(int(rate * 0.25), dtype=np.float32))
        elif index < len(normalized) and pause_ms:
            audio_parts.append(np.zeros(int(rate * pause_ms / 1000), dtype=np.float32))

    if sample_rate is None or not audio_parts:
        raise RuntimeError("No vocabulary audio was generated.")
    return np.concatenate(audio_parts), sample_rate


def _process_chapter(
    chapter: Chapter,
    output_dir: Path,
    backend: KokoroBilingualBackend | None,
    voices: dict[str, str],
    speeds: dict[str, float],
    silence_ms: int,
    max_chars: int,
    dry_run: bool,
    preprocessing: dict[str, object] | None = None,
    include_vocabulary_audio: bool = False,
    vocabulary_pause_ms: int = 600,
) -> None:
    chapter_dir = output_dir / f"chapter_{chapter.number:04d}"
    segment_dir = chapter_dir / "segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    jobs = _prepare_chapter(chapter, output_dir, max_chars)
    total_words = sum(len(segment.text.split()) for segment in chapter.segments)
    english_title = re.sub(rf"^Chapter {chapter.number}:\s*", "", chapter.english_title, flags=re.IGNORECASE)
    print(f"\nChapter {chapter.number}: {english_title}")
    print(f"Chinese title: {chapter.chinese_title}")
    print(f"Segments: {len(chapter.segments)}; audio chunks: {len(jobs)}; approx words: {total_words}")
    print(f"Playback speeds: Chinese={speeds['zh']}, English={speeds['en']}")
    counts = {"zh": 0, "en": 0}
    for segment in chapter.segments:
        counts[segment.language] += 1
    print(f"Language segments: Chinese={counts['zh']}, English={counts['en']}")
    if include_vocabulary_audio:
        vocabulary = preprocessing.get("vocabulary") if preprocessing else None
        if not isinstance(vocabulary, list) or not vocabulary:
            raise ValueError(
                "--include-vocabulary-audio requires a reviewed preprocessing cache with vocabulary entries."
            )
        print(f"Vocabulary audio: {len(vocabulary)} items; Pinyin stays unspoken")
    if dry_run:
        for segment_no, segment in enumerate(chapter.segments, 1):
            print(f"  {segment_no:03d} {segment.language:2s} {segment.kind:9s} {segment.text[:160]}")
        return

    manifest_path = chapter_dir / "manifest.json"
    manifest = {
        "chapter": chapter.number,
        "english_title": chapter.english_title,
        "chinese_title": chapter.chinese_title,
        "voices": {"zh": str(voices["zh"]), "en": str(voices["en"])},
        "languages": {"zh": "cmn", "en": "en-us"},
        "speeds": speeds,
        "synthesis": backend.metadata if backend is not None else None,
        "silence_ms": silence_ms,
        "max_chars": max_chars,
        "preprocessing": preprocessing,
        "include_vocabulary_audio": include_vocabulary_audio,
        "vocabulary_audio": (
            {
                "items": len(preprocessing["vocabulary"]),
                "spoken_fields": ["hanzi", "english"],
                "pinyin_spoken": False,
                "pause_after_hanzi_ms": 250,
                "pause_after_english_ms": vocabulary_pause_ms,
            }
            if include_vocabulary_audio and preprocessing is not None
            else None
        ),
        "segments": [asdict(segment) for segment in chapter.segments],
    }
    if manifest_path.exists():
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        settings = ("voices", "languages", "speeds", "synthesis", "silence_ms", "max_chars", "preprocessing")
        changed = [key for key in settings if previous_manifest.get(key) != manifest[key]]
        if changed:
            raise RuntimeError(
                f"Chapter {chapter.number} has checkpoints from different settings "
                f"({', '.join(changed)}). Use a fresh output directory or remove its "
                "chapter folder before regenerating."
            )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    for job_index, (segment, segment_no, filename) in enumerate(jobs, 1):
        wav_path = segment_dir / filename
        if _valid_wav(wav_path):
            print(f"[{job_index}/{len(jobs)}] Reuse {filename}")
            continue
        text_chunks = _split_for_tts(segment.text, max_chars)
        # A long source paragraph may occupy multiple chunk files.
        matching = [job for job in jobs if job[1] == segment_no]
        part_index = matching.index((segment, segment_no, filename))
        text = text_chunks[part_index]
        try:
            if backend is None:
                raise RuntimeError("Synthesis backend was not initialized.")
            samples, sample_rate = backend.synthesize_with_fallback(
                text,
                segment.language,
                str(voices[segment.language]),
                speeds[segment.language],
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"TTS failed at chapter {chapter.number}, segment {segment_no}, "
                f"part {part_index + 1}: {exc}; successful checkpoint WAVs are retained."
            ) from exc
        if samples is None or sample_rate is None or len(samples) == 0:
            raise RuntimeError(
                f"TTS failed at chapter {chapter.number}, segment {segment_no}, "
                f"part {part_index + 1}; successful checkpoint WAVs are retained."
            )
        sf.write(str(wav_path), np.asarray(samples, dtype=np.float32), sample_rate)
        print(f"[{job_index}/{len(jobs)}] Saved {filename}")

    # Reassemble checkpoint WAVs in source reading order with a short pause
    # between source paragraphs, not between forced sub-chunks.
    ordered_audio: list[np.ndarray] = []
    sample_rate: int | None = None
    last_segment_no: int | None = None
    for segment, segment_no, filename in jobs:
        wav_path = segment_dir / filename
        if not _valid_wav(wav_path):
            raise RuntimeError(f"Missing or invalid checkpoint audio: {wav_path}")
        data, sr = sf.read(str(wav_path), dtype="float32")
        if sample_rate is None:
            sample_rate = sr
        elif sr != sample_rate:
            raise RuntimeError(f"Sample-rate mismatch in {wav_path}: {sr} vs {sample_rate}")
        if last_segment_no is not None and segment_no != last_segment_no and silence_ms:
            ordered_audio.append(np.zeros(int(sample_rate * silence_ms / 1000), dtype=np.float32))
        ordered_audio.append(np.asarray(data, dtype=np.float32))
        last_segment_no = segment_no

    if sample_rate is None or not ordered_audio:
        raise RuntimeError(f"No audio segments were generated for chapter {chapter.number}")
    final_audio = np.concatenate(ordered_audio)
    if include_vocabulary_audio:
        if backend is None or preprocessing is None:
            raise RuntimeError("Vocabulary audio requires the initialized synthesis backend and preprocessing cache.")
        glossary_audio, glossary_rate = _synthesize_vocabulary_appendix(
            chapter_dir,
            backend,
            voices,
            speeds,
            preprocessing.get("vocabulary"),
            vocabulary_pause_ms,
        )
        if glossary_rate != sample_rate:
            raise RuntimeError(
                f"Vocabulary sample-rate mismatch: {glossary_rate} vs chapter rate {sample_rate}."
            )
        if silence_ms:
            final_audio = np.concatenate(
                (final_audio, np.zeros(int(sample_rate * silence_ms / 1000), dtype=np.float32))
            )
        final_audio = np.concatenate((final_audio, glossary_audio))
    output_path = chapter_dir / f"chapter_{chapter.number:04d}.mp3"
    _export_mp3(final_audio, sample_rate, output_path)
    duration = len(final_audio) / sample_rate
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Completed: {output_path} ({duration / 60:.1f} minutes, {output_path.stat().st_size:,} bytes)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path, help="Bilingual EPUB containing paired split_000/split_001 XHTML files")
    parser.add_argument("--output-dir", type=Path, default=None, help="Checkpoint/audio folder (default: <epub_stem>_bilingual_audio)")
    parser.add_argument("--start", type=int, default=721, help="First chapter, inclusive (default: 721)")
    parser.add_argument("--end", type=int, default=721, help="Last chapter, inclusive (default: 721; sample one chapter first)")
    parser.add_argument("--zh-voice", default="zf_003", help="v1.1 Chinese voice ID (default: zf_003)")
    parser.add_argument("--en-voice", default="af_heart", help="English voice ID (default: af_heart)")
    parser.add_argument("--zh-speed", type=float, default=None, help="Mandarin speech speed (default: 0.75)")
    parser.add_argument("--en-speed", type=float, default=None, help="English speech speed (default: 1.0)")
    parser.add_argument("--speed", type=float, default=None, help="Deprecated: set both languages to this speed; overridden by --zh-speed/--en-speed")
    parser.add_argument("--silence-ms", type=int, default=250, help="Pause between source paragraphs (default: 250 ms)")
    parser.add_argument("--max-chars", type=int, default=450, help="Maximum characters per TTS request (default: 450)")
    parser.add_argument(
        "--include-vocabulary-audio",
        action="store_true",
        help="Append cached Hanzi terms and English meanings after the chapter narration",
    )
    parser.add_argument(
        "--vocabulary-pause-ms",
        type=int,
        default=600,
        help="Pause after each English vocabulary meaning (default: 600 ms)",
    )
    parser.add_argument(
        "--preprocessed-dir",
        type=Path,
        default=None,
        help="Validated Chinese LLM cache directory from chinese_llm_preprocess.py",
    )
    parser.add_argument("--zh-model", type=Path, default=Path("models/kokoro-v1.1-zh.onnx"))
    parser.add_argument("--zh-voices", type=Path, default=Path("models/voices-v1.1-zh.bin"))
    parser.add_argument("--en-model", "--model", dest="en_model", type=Path, default=Path("kokoro-v1.0.onnx"))
    parser.add_argument("--en-voices", "--voices", dest="en_voices", type=Path, default=Path("voices-v1.0.bin"))
    parser.add_argument("--dry-run", action="store_true", help="Inspect selected chapter text/language routing without loading the model")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.zh_speed is not None and not 0.5 <= args.zh_speed <= 2.0:
        print("Error: --zh-speed must be between 0.5 and 2.0", file=sys.stderr)
        return 2
    if args.en_speed is not None and not 0.5 <= args.en_speed <= 2.0:
        print("Error: --en-speed must be between 0.5 and 2.0", file=sys.stderr)
        return 2
    legacy_speed = args.speed
    speeds = {
        "zh": args.zh_speed if args.zh_speed is not None else (legacy_speed if legacy_speed is not None else 0.75),
        "en": args.en_speed if args.en_speed is not None else (legacy_speed if legacy_speed is not None else 1.0),
    }
    for language, speed in speeds.items():
        if not 0.5 <= speed <= 2.0:
            print(f"Error: {language} speed must be between 0.5 and 2.0", file=sys.stderr)
            return 2
    if args.vocabulary_pause_ms < 0:
        print("Error: --vocabulary-pause-ms cannot be negative", file=sys.stderr)
        return 2
    if args.include_vocabulary_audio and args.preprocessed_dir is None:
        print("Error: --include-vocabulary-audio requires --preprocessed-dir", file=sys.stderr)
        return 2
    epub_path = args.epub.resolve()
    if not epub_path.is_file():
        print(f"Error: EPUB not found: {epub_path}", file=sys.stderr)
        return 2
    if args.start > args.end:
        print("Error: --start must be less than or equal to --end", file=sys.stderr)
        return 2

    try:
        chapters = _load_chapters(epub_path)
        selected = [chapter for number, chapter in sorted(chapters.items()) if args.start <= number <= args.end]
        if not selected:
            raise ValueError(f"No paired chapters found in requested range {args.start}-{args.end}")
        missing = sorted(set(range(args.start, args.end + 1)) - {chapter.number for chapter in selected})
        if missing:
            raise ValueError(f"Requested chapter range has missing chapters: {missing}")

        preprocessing_by_chapter: dict[int, dict[str, object]] = {}
        if args.preprocessed_dir is not None:
            from chinese_llm_preprocess import load_preprocessed_chapter

            cache_dir = args.preprocessed_dir.resolve()
            prepared = []
            for chapter in selected:
                chapter, metadata = load_preprocessed_chapter(chapter, cache_dir)
                prepared.append(chapter)
                preprocessing_by_chapter[chapter.number] = metadata
            selected = prepared
            print(f"Chinese text: using reviewed preprocessing caches from {cache_dir}")

        output_dir = args.output_dir or epub_path.with_name(f"{epub_path.stem}_bilingual_audio")
        output_dir = output_dir.resolve()
        print(f"EPUB: {epub_path}")
        print(f"Output/checkpoints: {output_dir}")
        print(f"Selected chapters: {args.start}-{args.end} ({len(selected)})")
        print(f"Voices/speeds: Chinese {args.zh_voice} at {speeds['zh']}; English {args.en_voice} at {speeds['en']}")

        if args.dry_run:
            for chapter in selected:
                _process_chapter(
                    chapter,
                    output_dir,
                    None,
                    {"zh": args.zh_voice, "en": args.en_voice},
                    speeds,
                    args.silence_ms,
                    args.max_chars,
                    True,
                    preprocessing_by_chapter.get(chapter.number),
                    args.include_vocabulary_audio,
                    args.vocabulary_pause_ms,
                )
            return 0

        backend = KokoroBilingualBackend(
            args.zh_model,
            args.zh_voices,
            args.en_model,
            args.en_voices,
            args.zh_voice,
            args.en_voice,
        )
        voices = {"zh": args.zh_voice, "en": args.en_voice}
        print(f"Active ONNX providers: {backend.providers}")
        print(f"Chinese: Misaki ZHG2P 1.1 / {args.zh_voice}; English: en-us / {args.en_voice}")

        for chapter in selected:
            _process_chapter(
                chapter,
                output_dir,
                backend,
                voices,
                speeds,
                args.silence_ms,
                args.max_chars,
                False,
                preprocessing_by_chapter.get(chapter.number),
                args.include_vocabulary_audio,
                args.vocabulary_pause_ms,
            )
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
