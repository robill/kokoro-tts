"""Provide matching Kokoro runtimes for Chinese and English synthesis."""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path
from typing import Any

import numpy as np


class KokoroBilingualBackend:
    """Load Kokoro 0.6.1, Misaki Mandarin G2P, and language-matched models.

    @param zh_model_path: Full-precision v1.1 Chinese ONNX model path.
    @param zh_voices_path: Matching v1.1 Chinese voice bundle path.
    @param en_model_path: Existing English ONNX model path.
    @param en_voices_path: Matching English voice bundle path.
    @param zh_voice: Voice ID available in the Chinese voice bundle.
    @param en_voice: Voice ID available in the English voice bundle.
    @raises RuntimeError: If the required runtime or model files are missing.
    """

    def __init__(
        self,
        zh_model_path: Path,
        zh_voices_path: Path,
        en_model_path: Path,
        en_voices_path: Path,
        zh_voice: str,
        en_voice: str,
    ) -> None:
        try:
            runtime_version = importlib.metadata.version("kokoro-onnx")
            misaki_version = importlib.metadata.version("misaki-fork")
            from kokoro_onnx import Kokoro
            from misaki import zh as misaki_zh
        except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
            raise RuntimeError(
                "Chinese G2P audio requires kokoro-onnx 0.6.1 and misaki-fork[zh] 0.9.6. "
                "Run this generator with the .venv_kokoro_061 environment."
            ) from exc

        if runtime_version != "0.6.1":
            raise RuntimeError(
                f"Expected kokoro-onnx 0.6.1 for Misaki G2P, found {runtime_version}. "
                "Run this generator with the .venv_kokoro_061 environment."
            )

        self.zh_model_path = Path(zh_model_path).resolve()
        self.zh_voices_path = Path(zh_voices_path).resolve()
        self.en_model_path = Path(en_model_path).resolve()
        self.en_voices_path = Path(en_voices_path).resolve()
        for path in (
            self.zh_model_path,
            self.zh_voices_path,
            self.en_model_path,
            self.en_voices_path,
        ):
            if not path.is_file():
                raise RuntimeError(f"Required Kokoro asset does not exist: {path}")
        if ".fp16." in self.zh_model_path.name.lower():
            raise RuntimeError(
                "Use the full-precision v1.1 Chinese model; the tested FP16 model produced NaN audio."
            )

        self.zh_g2p = misaki_zh.ZHG2P(version="1.1")
        self.zh_model = Kokoro(str(self.zh_model_path), str(self.zh_voices_path))
        self.en_model = Kokoro(str(self.en_model_path), str(self.en_voices_path))

        zh_voice_ids = set(self.zh_model.get_voices())
        en_voice_ids = set(self.en_model.get_voices())
        if zh_voice not in zh_voice_ids:
            raise ValueError(
                f"Chinese voice {zh_voice!r} is unavailable. Choose from: "
                + ", ".join(sorted(zh_voice_ids))
            )
        if en_voice not in en_voice_ids:
            raise ValueError(
                f"English voice {en_voice!r} is unavailable. Choose from: "
                + ", ".join(sorted(en_voice_ids))
            )

        self.zh_voice = zh_voice
        self.en_voice = en_voice
        self.metadata: dict[str, Any] = {
            "kokoro_onnx": runtime_version,
            "misaki_fork": misaki_version,
            "zh": {
                "model": str(self.zh_model_path),
                "voices_file": str(self.zh_voices_path),
                "voice": zh_voice,
                "g2p": "misaki-fork.ZHG2P",
                "g2p_version": "1.1",
                "input_is_phonemes": True,
            },
            "en": {
                "model": str(self.en_model_path),
                "voices_file": str(self.en_voices_path),
                "voice": en_voice,
                "language": "en-us",
            },
        }

    @property
    def providers(self) -> dict[str, list[str]]:
        """Return active ONNX execution providers for both language models."""
        return {
            "zh": list(self.zh_model.sess.get_providers()),
            "en": list(self.en_model.sess.get_providers()),
        }

    def synthesize(self, text: str, language: str, voice: str, speed: float) -> tuple[np.ndarray, int]:
        """Synthesize one Chinese or English text request into finite audio.

        @param text: Plain Hanzi for Chinese or ordinary text for English.
        @param language: Either ``zh`` or ``en``.
        @param voice: Voice ID belonging to the selected language model.
        @param speed: Playback rate from 0.5 through 2.0.
        @return: One-dimensional float32 audio and its sample rate.
        @raises ValueError: If the language or speed is invalid.
        @raises FloatingPointError: If the model returns invalid audio samples.
        """
        if language not in {"zh", "en"}:
            raise ValueError(f"Unsupported synthesis language: {language}")
        if not 0.5 <= speed <= 2.0:
            raise ValueError("Speaking speed must be between 0.5 and 2.0.")

        if language == "zh":
            phonemes, _ = self.zh_g2p(text)
            if not isinstance(phonemes, str) or not phonemes.strip():
                raise RuntimeError("Misaki Chinese G2P returned no phonemes.")
            samples, sample_rate = self.zh_model.create(
                phonemes,
                voice=voice,
                speed=speed,
                is_phonemes=True,
            )
        else:
            samples, sample_rate = self.en_model.create(
                text,
                voice=voice,
                speed=speed,
                lang="en-us",
            )

        audio = np.asarray(samples, dtype=np.float32)
        if audio.ndim != 1 or audio.size == 0:
            raise RuntimeError(f"Kokoro returned empty or non-mono {language} audio.")
        if not np.isfinite(audio).all():
            raise FloatingPointError(
                f"Kokoro returned NaN or infinite samples for {language} text."
            )
        return audio, int(sample_rate)

    def synthesize_with_fallback(
        self,
        text: str,
        language: str,
        voice: str,
        speed: float,
        *,
        depth: int = 0,
    ) -> tuple[np.ndarray, int]:
        """Retry a failed long synthesis request using smaller text pieces.

        @param text: Text to synthesize without altering or dropping content.
        @param language: Either ``zh`` or ``en``.
        @param voice: Voice ID belonging to the selected language model.
        @param speed: Playback rate from 0.5 through 2.0.
        @param depth: Current recursive split depth.
        @return: Concatenated float32 audio and its sample rate.
        @raises RuntimeError: If a short request or maximum-depth retry fails.
        """
        try:
            return self.synthesize(text, language, voice, speed)
        except FloatingPointError:
            raise
        except Exception as exc:
            if len(text) <= 24 or depth >= 8:
                raise RuntimeError(
                    f"Kokoro could not synthesize {language} text of {len(text)} characters "
                    f"after {depth} fallback split(s): {text[:100]!r}"
                ) from exc

            left, right = _split_failed_text(text)
            left_audio, left_rate = self.synthesize_with_fallback(
                left, language, voice, speed, depth=depth + 1
            )
            right_audio, right_rate = self.synthesize_with_fallback(
                right, language, voice, speed, depth=depth + 1
            )
            if left_rate != right_rate:
                raise RuntimeError(
                    f"Sample-rate mismatch in {language} fallback: {left_rate} vs {right_rate}"
                )
            return np.concatenate((left_audio, right_audio)), left_rate


def _split_failed_text(text: str) -> tuple[str, str]:
    """Split failed synthesis text near its midpoint at a natural boundary."""
    midpoint = len(text) // 2
    boundaries = [
        match.end()
        for match in re.finditer(r"\s+|(?<=[，,、。！？!?；;])", text)
        if 0 < match.end() < len(text)
    ]
    split_at = min(boundaries, key=lambda pos: abs(pos - midpoint)) if boundaries else midpoint
    left, right = text[:split_at].strip(), text[split_at:].strip()
    if not left or not right:
        split_at = midpoint
        left, right = text[:split_at].strip(), text[split_at:].strip()
    if not left or not right:
        raise RuntimeError("Unable to split failed TTS text into smaller pieces.")
    return left, right