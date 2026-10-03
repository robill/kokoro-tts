#!/usr/bin/env python3
"""Create a separate Mandarin/English vocabulary-review track from a cache.

Each glossary item is spoken first in Mandarin, followed by its English
meaning. Pinyin remains in the accompanying written review sheet and is not
sent to speech synthesis.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from chinese_llm_preprocess import PROMPT_VERSION
from kokoro_mandarin_backend import KokoroBilingualBackend


def _valid_audio(path: Path) -> bool:
    """Check that a checkpoint WAV contains decodable, non-empty audio."""
    try:
        info = sf.info(str(path))
        return info.frames > 0 and info.samplerate > 0
    except (RuntimeError, OSError):
        return False


def _load_vocabulary(cache_path: Path) -> tuple[dict, list[dict[str, str]]]:
    """Load and validate a glossary from a current LLM preprocessing cache.

    @param cache_path: Path to the chapter's Chinese simplification JSON cache.
    @return: Cache metadata and normalized Hanzi/Pinyin/English glossary rows.
    @raises ValueError: If the cache version or glossary schema is invalid.
    """
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    if cache.get("prompt_version") != PROMPT_VERSION:
        raise ValueError(
            "This cache was generated with a different prompt version; "
            "preprocess the chapter again before creating its study track."
        )
    result = cache.get("result", {})
    entries = result.get("vocabulary")
    if not isinstance(entries, list) or not entries:
        raise ValueError("The preprocessing cache contains no vocabulary entries.")
    cleaned: list[dict[str, str]] = []
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Vocabulary item {index} is not an object.")
        row = {key: entry.get(key) for key in ("hanzi", "pinyin", "english")}
        if any(not isinstance(value, str) or not value.strip() for value in row.values()):
            raise ValueError(f"Vocabulary item {index} has a missing Hanzi, Pinyin, or English meaning.")
        cleaned.append({key: value.strip() for key, value in row.items()})
    return cache, cleaned


def _save_mp3(samples: np.ndarray, sample_rate: int, output: Path) -> None:
    """Encode a mono sample buffer as a 128-kbps MP3 file."""
    try:
        from pydub import AudioSegment
    except ImportError as exc:
        raise RuntimeError("MP3 export requires pydub and ffmpeg.") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", dir=output.parent, delete=False) as handle:
        temp_wav = Path(handle.name)
    try:
        sf.write(str(temp_wav), samples.astype(np.float32, copy=False), sample_rate)
        AudioSegment.from_wav(str(temp_wav)).export(str(output), format="mp3", bitrate="128k")
    finally:
        temp_wav.unlink(missing_ok=True)


def generate_vocabulary_audio(
    cache_path: Path,
    output_path: Path,
    *,
    zh_voice: str = "zf_003",
    en_voice: str = "af_heart",
    zh_speed: float = 0.75,
    en_speed: float = 1.0,
    zh_model_path: Path = Path("models/kokoro-v1.1-zh.onnx"),
    zh_voices_path: Path = Path("models/voices-v1.1-zh.bin"),
    en_model_path: Path = Path("kokoro-v1.0.onnx"),
    en_voices_path: Path = Path("voices-v1.0.bin"),
    pause_ms: int = 600,
) -> Path:
    """Synthesize a standalone glossary track with resumable WAV checkpoints.

    @param cache_path: Reviewed chapter preprocessing cache with a separate glossary.
    @param output_path: Destination MP3 path for the vocabulary audio.
    @param zh_voice: v1.1 Kokoro Mandarin voice ID.
    @param en_voice: Kokoro US English voice name.
    @param zh_speed: Mandarin speaking rate, from 0.5 to 2.0.
    @param en_speed: English speaking rate, from 0.5 to 2.0.
    @param zh_model_path: Full-precision v1.1 Chinese model path.
    @param zh_voices_path: Matching v1.1 Chinese voices bundle path.
    @param en_model_path: Existing English model path.
    @param en_voices_path: Matching English voices bundle path.
    @param pause_ms: Pause after each English meaning, in milliseconds.
    @return: The completed MP3 path.
    @raises RuntimeError: If Kokoro cannot synthesize a glossary item.
    """
    if not 0.5 <= zh_speed <= 2.0 or not 0.5 <= en_speed <= 2.0:
        raise ValueError("Speaking speeds must be between 0.5 and 2.0.")
    if pause_ms < 0:
        raise ValueError("Pause duration cannot be negative.")
    cache, vocabulary = _load_vocabulary(cache_path)
    backend = KokoroBilingualBackend(
        zh_model_path,
        zh_voices_path,
        en_model_path,
        en_voices_path,
        zh_voice,
        en_voice,
    )
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = output_path.with_suffix("").with_name(output_path.stem + "_segments")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = checkpoint_dir / "manifest.json"
    settings = {
        "chapter": cache.get("chapter"),
        "prompt_version": cache.get("prompt_version"),
        "source_sha256": cache.get("source_sha256"),
        "zh_voice": zh_voice,
        "en_voice": en_voice,
        "zh_speed": zh_speed,
        "en_speed": en_speed,
        "synthesis": backend.metadata,
        "pause_ms": pause_ms,
        "vocabulary_count": len(vocabulary),
    }
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        changed = [key for key, value in settings.items() if previous.get(key) != value]
        if changed:
            raise RuntimeError(
                "Vocabulary audio checkpoints use different settings ("
                + ", ".join(changed)
                + "). Use a fresh output path."
            )
    manifest_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Chapter {settings['chapter']} vocabulary items: {len(vocabulary)}")
    print(f"Mandarin: {zh_voice} at {zh_speed}; English: {en_voice} at {en_speed}")
    print(f"Active ONNX providers: {backend.providers}")

    ordered_paths: list[Path] = []
    for index, item in enumerate(vocabulary, 1):
        for language, text, voice, speed in (
            ("zh", item["hanzi"], zh_voice, zh_speed),
            ("en", item["english"], en_voice, en_speed),
        ):
            checkpoint = checkpoint_dir / f"item_{index:03d}_{language}.wav"
            if not _valid_audio(checkpoint):
                samples, sample_rate = backend.synthesize_with_fallback(
                    text, language, str(voice), speed
                )
                if samples is None or sample_rate is None or len(samples) == 0:
                    raise RuntimeError(
                        f"No audio produced for glossary item {index} ({language}); "
                        "successful WAV checkpoints are retained."
                    )
                sf.write(str(checkpoint), np.asarray(samples, dtype=np.float32), sample_rate)
                print(f"[{index}/{len(vocabulary)}] Saved {language} audio")
            ordered_paths.append(checkpoint)

    buffers: list[np.ndarray] = []
    sample_rate: int | None = None
    for index, checkpoint in enumerate(ordered_paths):
        samples, rate = sf.read(str(checkpoint), dtype="float32")
        if sample_rate is None:
            sample_rate = rate
        elif rate != sample_rate:
            raise RuntimeError(f"Sample-rate mismatch in {checkpoint}: {rate} vs {sample_rate}")
        buffers.append(np.asarray(samples, dtype=np.float32))
        # Leave a short retrieval pause after each English definition.
        if index % 2 == 1 and index < len(ordered_paths) - 1 and pause_ms:
            buffers.append(np.zeros(int(sample_rate * pause_ms / 1000), dtype=np.float32))

    if sample_rate is None or not buffers:
        raise RuntimeError("No vocabulary audio was generated.")
    _save_mp3(np.concatenate(buffers), sample_rate, output_path)
    print(f"Vocabulary MP3: {output_path} ({output_path.stat().st_size:,} bytes)")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    """Build command-line options for a chapter glossary audio track."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path, help="Path to chinese_simplification.json")
    parser.add_argument("output", type=Path, help="Destination MP3 for the vocabulary track")
    parser.add_argument("--zh-voice", default="zf_003")
    parser.add_argument("--en-voice", default="af_heart")
    parser.add_argument("--zh-speed", type=float, default=0.75)
    parser.add_argument("--en-speed", type=float, default=1.0)
    parser.add_argument("--pause-ms", type=int, default=600)
    parser.add_argument("--zh-model", type=Path, default=Path("models/kokoro-v1.1-zh.onnx"))
    parser.add_argument("--zh-voices", type=Path, default=Path("models/voices-v1.1-zh.bin"))
    parser.add_argument("--en-model", "--model", dest="en_model", type=Path, default=Path("kokoro-v1.0.onnx"))
    parser.add_argument("--en-voices", "--voices", dest="en_voices", type=Path, default=Path("voices-v1.0.bin"))
    return parser


def main() -> int:
    """Generate the selected chapter's separate vocabulary MP3."""
    args = build_parser().parse_args()
    try:
        generate_vocabulary_audio(
            args.cache,
            args.output,
            zh_voice=args.zh_voice,
            en_voice=args.en_voice,
            zh_speed=args.zh_speed,
            en_speed=args.en_speed,
            zh_model_path=args.zh_model,
            zh_voices_path=args.zh_voices,
            en_model_path=args.en_model,
            en_voices_path=args.en_voices,
            pause_ms=args.pause_ms,
        )
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
