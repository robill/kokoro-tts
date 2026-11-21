#!/usr/bin/env python3
"""Create an English-first triplet study EPUB and audiobook for one chapter."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from bs4 import BeautifulSoup

from bilingual_epub_tts import (
    Chapter,
    Segment,
    _chapter_file_pattern,
    _export_mp3,
    _language_of,
    _load_chapters,
    _split_for_tts,
)
from chinese_llm_preprocess import load_preprocessed_chapter
from kokoro_mandarin_backend import KokoroBilingualBackend
from simplified_epub_export import _append_vocabulary_glossary

_P_RE = re.compile(rb"(?P<open><p\b[^>]*>)(?P<body>.*?)(?P<close></p\s*>)", re.I | re.S)
_HEADING_RE = re.compile(rb"<h[1-3]\b(?P<attrs>[^>]*)>.*?</h[1-3]\s*>", re.I | re.S)


def _text_of_fragment(raw: bytes) -> str:
    """Return normalized visible text from an XHTML fragment."""
    return " ".join(BeautifulSoup(raw.decode("utf-8"), "html.parser").get_text(" ", strip=True).split())


def _replace_paragraph_sequence(
    xhtml: bytes,
    english: list[str],
    original_chinese: list[str],
    simplified_chinese: list[str],
    alignment_blocks: list[dict[str, list[int]]],
    vocabulary: list[dict[str, str]],
    chapter_number: int,
) -> tuple[bytes, list[dict[str, Any]]]:
    """Reorder chapter body paragraphs into English/original/A2 triplets.

    @param xhtml: Source chapter body XHTML.
    @param english: English translation paragraphs in source order.
    @param original_chinese: Original Hanzi paragraphs in source order.
    @param simplified_chinese: Aligned A2 Hanzi paragraphs.
    @param alignment_blocks: Ordered blocks with 1-based ``english_paragraphs`` and ``chinese_paragraphs`` lists.
    @param vocabulary: Validated glossary data for a written appendix.
    @return: Reordered XHTML bytes and triplet audit records.
    @raises ValueError: If markup, count, or alignment validation fails.
    """
    if len(original_chinese) != len(simplified_chinese) or not alignment_blocks:
        raise ValueError("Original/A2 paragraph counts must match and at least one alignment block is required.")
    if any(not isinstance(block, dict) for block in alignment_blocks):
        raise ValueError("Every alignment block must be an object.")
    used_english = [number for block in alignment_blocks for number in block.get("english_paragraphs", [])]
    used_chinese = [number for block in alignment_blocks for number in block.get("chinese_paragraphs", [])]
    if used_english != list(range(1, len(english) + 1)):
        raise ValueError("Alignment blocks must cover every English paragraph once in source order.")
    if used_chinese != list(range(1, len(original_chinese) + 1)):
        raise ValueError("Alignment blocks must cover every original Chinese paragraph once in source order.")

    matches = list(_P_RE.finditer(xhtml))
    chinese_matches: list[re.Match[bytes]] = []
    english_matches: list[re.Match[bytes]] = []
    other_matches: list[re.Match[bytes]] = []
    for match in matches:
        text = _text_of_fragment(match.group("body"))
        language = _language_of(text)
        if language == "zh":
            if BeautifulSoup(match.group("body").decode("utf-8"), "html.parser").find(True):
                raise ValueError("Chinese paragraph contains inline markup; refusing to discard it.")
            chinese_matches.append(match)
        elif language == "en":
            english_matches.append(match)
        else:
            other_matches.append(match)

    if len(chinese_matches) != len(original_chinese) or len(english_matches) != len(english):
        raise ValueError(
            "Source XHTML paragraph counts differ from the aligned chapter: "
            f"source Chinese={len(chinese_matches)}/{len(original_chinese)}, "
            f"English={len(english_matches)}/{len(english)}."
        )
    for left, right in zip(matches, matches[1:]):
        between = xhtml[left.end() : right.start()]
        if between.strip():
            raise ValueError("Non-whitespace content exists between chapter paragraphs; refusing to reorder.")

    english_blocks = [xhtml[m.start() : m.end()] for m in english_matches]
    chinese_open = [m.group("open") for m in chinese_matches]
    chinese_close = [m.group("close") for m in chinese_matches]
    records: list[dict[str, Any]] = []
    ordered: list[bytes] = []
    for block_index, block in enumerate(alignment_blocks, 1):
        english_group = block.get("english_paragraphs", [])
        chinese_group = block.get("chinese_paragraphs", [])
        if not english_group or not chinese_group:
            raise ValueError(f"Alignment block {block_index} must contain English and Chinese paragraphs.")
        ordered.extend(english_blocks[number - 1] for number in english_group)
        translation_notice = block.get("translation_notice")
        if translation_notice is not None:
            if not isinstance(translation_notice, str) or not translation_notice.strip():
                raise ValueError(f"Alignment block {block_index} has an invalid translation notice.")
            ordered.append(
                b'<p class="translation-note"><strong>Translation note:</strong> '
                + html.escape(translation_notice.strip(), quote=False).encode("utf-8")
                + b"</p>"
            )
        original_texts: list[str] = []
        a2_texts: list[str] = []
        for number in chinese_group:
            if not isinstance(number, int) or number < 1 or number > len(original_chinese):
                raise ValueError(f"Alignment block {block_index} has an out-of-range Chinese paragraph index.")
            original_text = original_chinese[number - 1]
            simplified_text = simplified_chinese[number - 1]
            original_texts.append(original_text)
            a2_texts.append(simplified_text)
            original_block = (
                chinese_open[number - 1]
                + "<strong>原文：</strong> ".encode("utf-8")
                + html.escape(original_text, quote=False).encode("utf-8")
                + chinese_close[number - 1]
            )
            simplified_block = (
                chinese_open[number - 1]
                + "<strong>A2改写：</strong> ".encode("utf-8")
                + html.escape(simplified_text, quote=False).encode("utf-8")
                + chinese_close[number - 1]
            )
            ordered.extend((original_block, simplified_block))
        records.append(
            {
                "triplet": block_index,
                "english_paragraphs": english_group,
                "original_chinese_paragraphs": chinese_group,
                "a2_chinese_paragraphs": chinese_group,
                "english_text": [english[number - 1] for number in english_group],
                "translation_notice": translation_notice,
                "original_chinese": original_texts,
                "a2_chinese": a2_texts,
            }
        )

    # Preserve punctuation-only/source-neutral paragraphs after the aligned text.
    ordered.extend(xhtml[m.start() : m.end()] for m in other_matches)
    if not matches:
        raise ValueError("No body paragraph tags found in chapter XHTML.")
    leading = xhtml[: matches[0].start()]
    trailing = xhtml[matches[-1].end() :]
    body = leading + b"\n" + b"\n".join(ordered) + b"\n" + trailing
    if vocabulary:
        body = _append_vocabulary_glossary(body, vocabulary, chapter_number)
    return body, records


def _set_chapter_headings(zh_xhtml: bytes, body_xhtml: bytes, english_title: str, chinese_title: str) -> tuple[bytes, bytes]:
    """Place English chapter heading first, followed by its Chinese heading."""
    escaped_en = html.escape(english_title, quote=False).encode("utf-8")
    escaped_zh = html.escape(chinese_title, quote=False).encode("utf-8")
    match = _HEADING_RE.search(zh_xhtml)
    if not match:
        raise ValueError("Chapter title XHTML has no h1/h2/h3 heading.")
    attrs = match.group("attrs")
    new_heading = b"<h2" + attrs + b">" + escaped_en + b"</h2><h3" + attrs + b">" + escaped_zh + b"</h3>"
    zh_xhtml = zh_xhtml[: match.start()] + new_heading + zh_xhtml[match.end() :]

    # Remove the duplicate English h1 from the body document; both titles now lead the spine.
    body_match = _HEADING_RE.search(body_xhtml)
    if body_match:
        body_xhtml = body_xhtml[: body_match.start()] + body_xhtml[body_match.end() :]
    return zh_xhtml, body_xhtml


def _make_study_epub(
    paired_epub: Path,
    output_epub: Path,
    chapter: Chapter,
    english: list[str],
    original_chinese: list[str],
    a2_chinese: list[str],
    alignment_blocks: list[dict[str, list[int]]],
    vocabulary: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Build a one-chapter study EPUB in English-first, original-then-A2 order."""
    if output_epub.exists():
        raise FileExistsError(f"Refusing to overwrite an existing study EPUB: {output_epub}")
    output_epub.parent.mkdir(parents=True, exist_ok=True)
    names_by_part: dict[str, str] = {}
    with zipfile.ZipFile(paired_epub, "r") as source:
        for name in source.namelist():
            match = _chapter_file_pattern(name)
            if match and int(match.group("number")) == chapter.number:
                names_by_part[match.group("part")] = name
        if set(names_by_part) != {"000", "001"}:
            raise ValueError("Chapter-only input EPUB must contain exactly the paired split_000/split_001 XHTML members.")

        title_xhtml = source.read(names_by_part["000"])
        body_xhtml = source.read(names_by_part["001"])
        title_xhtml, body_xhtml = _set_chapter_headings(
            title_xhtml, body_xhtml, chapter.english_title, chapter.chinese_title
        )
        body_xhtml, triplets = _replace_paragraph_sequence(
            body_xhtml,
            english,
            original_chinese,
            a2_chinese,
            alignment_blocks,
            vocabulary,
            chapter.number,
        )
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{output_epub.stem}_", suffix=".tmp.epub", dir=output_epub.parent, delete=False
        )
        temp_epub = Path(handle.name)
        handle.close()
        try:
            with zipfile.ZipFile(temp_epub, "w") as target:
                target.comment = source.comment
                for info in source.infolist():
                    content = source.read(info)
                    if info.filename == names_by_part["000"]:
                        content = title_xhtml
                    elif info.filename == names_by_part["001"]:
                        content = body_xhtml
                    target.writestr(info, content)
            with zipfile.ZipFile(temp_epub) as check:
                if check.testzip() is not None:
                    raise ValueError("Generated one-chapter EPUB failed ZIP validation.")
                names = check.namelist()
                if names_by_part["000"] not in names or names_by_part["001"] not in names:
                    raise ValueError("Generated EPUB lost a chapter XHTML member.")
            temp_epub.replace(output_epub)
        finally:
            temp_epub.unlink(missing_ok=True)
    return triplets


def _manifest_checkpoint_map(previous_audio: Path, expected_synthesis: dict[str, Any]) -> dict[tuple[str, str], list[Path]]:
    """Index reusable old English/A2 WAV checkpoints by exact spoken text."""
    manifest_path = previous_audio / "manifest.json"
    if not manifest_path.is_file():
            raise FileNotFoundError(f"Previous chapter manifest missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("voices") != {"zh": "zf_003", "en": "af_heart"}:
        raise ValueError("Previous chapter audio uses different voices; refusing checkpoint reuse.")
    if manifest.get("speeds") != {"zh": 0.75, "en": 1.0}:
        raise ValueError("Previous chapter audio uses different speeds; refusing checkpoint reuse.")
    if manifest.get("synthesis") != expected_synthesis:
        raise ValueError("Previous chapter synthesis provenance differs; refusing checkpoint reuse.")

    checkpoints: dict[tuple[str, str], list[Path]] = {}
    for index, segment in enumerate(manifest.get("segments", []), 1):
        if segment.get("language") not in {"zh", "en"}:
            continue
        language = segment["language"]
        text = segment["text"]
        parts = _split_for_tts(text, manifest.get("max_chars", 450))
        paths = [
            previous_audio
            / "segments"
            / f"segment_{index:04d}_part_{part_index:02d}_{language}.wav"
            for part_index in range(1, len(parts) + 1)
        ]
        if all(path.is_file() for path in paths):
            checkpoints[(language, text)] = paths
    return checkpoints


def _append_triplet_audio(
    audio_parts: list[np.ndarray],
    original_audio: np.ndarray,
    a2_cue_audio: np.ndarray,
    a2_audio: list[np.ndarray],
    sample_rate: int,
) -> None:
    """Append original Chinese, a spoken A2 cue, and A2 Chinese in order.

    @param audio_parts: Output audio buffers being assembled.
    @param original_audio: Original Chinese paragraph audio.
    @param a2_cue_audio: Short Mandarin cue announcing the simplified version.
    @param a2_audio: Checkpoint chunks for the A2 Chinese paragraph.
    @param sample_rate: Expected sample rate for all audio buffers.
    @raises ValueError: If any buffer is empty, non-finite, or has invalid shape.
    """
    buffers = [original_audio, a2_cue_audio, *a2_audio]
    if not buffers or any(buffer.ndim != 1 or buffer.size == 0 or not np.isfinite(buffer).all() for buffer in buffers):
        raise ValueError("Triplet audio buffers must be finite, non-empty mono audio.")
    short_pause = np.zeros(int(sample_rate * 0.25), dtype=np.float32)
    audio_parts.extend((
        np.asarray(original_audio, dtype=np.float32),
        short_pause.copy(),
        np.asarray(a2_cue_audio, dtype=np.float32),
        short_pause.copy(),
        *[np.asarray(chunk, dtype=np.float32) for chunk in a2_audio],
        short_pause.copy(),
    ))


def create_english_first_chapter(
    paired_epub: Path,
    cache_dir: Path,
    previous_audio: Path,
    alignment_path: Path,
    output_dir: Path,
    *,
    previous_original_chinese_audio: Path | None = None,
    chinese_model: Path = Path("models/kokoro-v1.1-zh.onnx"),
    chinese_voices: Path = Path("models/voices-v1.1-zh.bin"),
    english_model: Path = Path("kokoro-v1.0.onnx"),
    english_voices: Path = Path("voices-v1.0.bin"),
) -> tuple[Path, Path, dict[str, int]]:
    """Create a fresh English-first triplet EPUB/audio sample for one chapter.

    @param paired_epub: One-chapter paired bilingual EPUB split by EpubSplit.
    @param cache_dir: Root containing the matching current-prompt A2 cache.
    @param previous_audio: Existing chapter audio directory containing reusable English/A2/glossary WAVs.
    @param alignment_path: JSON mapping grouping English paragraph numbers to each original Chinese paragraph.
    @param output_dir: New output folder; must not already exist.
    @param previous_original_chinese_audio: Optional directory with previously synthesized original-Chinese WAV checkpoints.
    @param chinese_model: Full-precision v1.1 Chinese model path.
    @param chinese_voices: Matching v1.1 Chinese voice bundle path.
    @param english_model: Existing English v1.0 model path.
    @param english_voices: Matching English v1.0 voice bundle path.
    @return: Study EPUB path, new audio path, and reuse/synthesis counts.
    @raises ValueError: If source/cache/alignment/settings are inconsistent.
    @raises FileNotFoundError: If required chapter, cache, model, or checkpoint files are missing.
    """
    paired_epub = paired_epub.resolve(); cache_dir = cache_dir.resolve(); previous_audio = previous_audio.resolve(); alignment_path = alignment_path.resolve(); output_dir = output_dir.resolve()
    if previous_original_chinese_audio is not None:
        previous_original_chinese_audio = previous_original_chinese_audio.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite the new sample output directory: {output_dir}")
    output_dir.mkdir(parents=True)
    alignment_record = json.loads(alignment_path.read_text(encoding="utf-8"))
    alignment = alignment_record.get("alignment", alignment_record)
    chapter_number = alignment_record.get("chapter", alignment.get("chapter"))
    chapters = _load_chapters(paired_epub)
    if not isinstance(chapter_number, int) or len(chapters) != 1 or chapter_number not in chapters:
        raise ValueError("Input paired EPUB must contain exactly the chapter named by the alignment file.")
    chapter = chapters[chapter_number]
    rewritten, metadata = load_preprocessed_chapter(chapter, cache_dir)
    cache_path = cache_dir / f"chapter_{chapter_number:04d}" / "chinese_simplification.json"
    record = json.loads(cache_path.read_text(encoding="utf-8"))
    alignment_source_hash = alignment_record.get("source_sha256", alignment.get("source_chapter_sha256"))
    if alignment_source_hash != record.get("source_sha256"):
        raise ValueError("Alignment file does not match the selected source/cache chapter.")
    alignment_blocks = alignment.get("blocks")
    if alignment_blocks is None:
        legacy_groups = alignment.get("english_paragraph_groups_by_chinese_paragraph")
        if not isinstance(legacy_groups, list):
            raise ValueError("Alignment file needs a blocks list.")
        alignment_blocks = [
            {"english_paragraphs": group, "chinese_paragraphs": [index]}
            for index, group in enumerate(legacy_groups, 1)
        ]
    if not isinstance(alignment_blocks, list):
        raise ValueError("Alignment blocks must be an array.")

    original = [s.text for s in chapter.segments if s.language == "zh" and s.kind == "paragraph"]
    english = [s.text for s in chapter.segments if s.language == "en" and s.kind == "paragraph"]
    a2 = [s.text for s in rewritten.segments if s.language == "zh" and s.kind == "paragraph"]
    if not original or not english or len(a2) != len(original) or not alignment_blocks:
        raise ValueError(
            f"Invalid chapter paragraph counts: English={len(english)}, original Chinese={len(original)}, A2={len(a2)}."
        )
    if any(
        not isinstance(block, dict)
        or not block.get("english_paragraphs")
        or not block.get("chinese_paragraphs")
        for block in alignment_blocks
    ):
        raise ValueError("Every alignment block must contain English and Chinese paragraph indices.")
    flat_english = [number for block in alignment_blocks for number in block["english_paragraphs"]]
    flat_chinese = [number for block in alignment_blocks for number in block["chinese_paragraphs"]]
    if any(not isinstance(number, int) or number < 1 or number > len(english) for number in flat_english):
        raise ValueError("Alignment contains an out-of-range English paragraph number.")
    if any(not isinstance(number, int) or number < 1 or number > len(original) for number in flat_chinese):
        raise ValueError("Alignment contains an out-of-range Chinese paragraph number.")
    if flat_english != list(range(1, len(english) + 1)):
        raise ValueError("Alignment blocks must cover every English paragraph once in source order.")
    if flat_chinese != list(range(1, len(original) + 1)):
        raise ValueError("Alignment blocks must cover every Chinese paragraph once in source order.")

    expected_synthesis = {
        "kokoro_onnx": "0.6.1",
        "misaki_fork": "0.9.6",
        "zh": {
            "model": str(chinese_model.resolve()),
            "voices_file": str(chinese_voices.resolve()),
            "voice": "zf_003",
            "g2p": "misaki-fork.ZHG2P",
            "g2p_version": "1.1",
            "input_is_phonemes": True,
        },
        "en": {
            "model": str(english_model.resolve()),
            "voices_file": str(english_voices.resolve()),
            "voice": "af_heart",
            "language": "en-us",
        },
    }
    reuse = _manifest_checkpoint_map(previous_audio, expected_synthesis)
    study_epub = output_dir / f"Chapter_{chapter_number:04d}_EnglishFirst_Study.epub"
    triplets = _make_study_epub(
        paired_epub,
        study_epub,
        chapter,
        english,
        original,
        a2,
        alignment_blocks,
        metadata["vocabulary"],
    )

    backend = KokoroBilingualBackend(chinese_model, chinese_voices, english_model, english_voices, "zf_003", "af_heart")
    previous_manifest = json.loads((previous_audio / "manifest.json").read_text(encoding="utf-8"))
    sample_rate = 24000
    audio_parts: list[np.ndarray] = []
    reused_count = 0
    synthesized_count = 0
    reused_original_count = 0
    original_audio_dir = output_dir / f"original_chinese_segments_{chapter_number:04d}"
    original_audio_dir.mkdir(parents=True, exist_ok=True)
    cue_path = output_dir / "a2_transition_cue_zh.wav"
    if not cue_path.exists():
        cue_samples, cue_rate = backend.synthesize_with_fallback("简化版", "zh", "zf_003", 0.75)
        if cue_rate != sample_rate or cue_samples.size == 0 or not np.isfinite(cue_samples).all():
            raise RuntimeError("Could not synthesize the short Mandarin A2 transition cue.")
        sf.write(str(cue_path), np.asarray(cue_samples, dtype=np.float32), cue_rate)
    a2_cue_audio, cue_rate = sf.read(str(cue_path), dtype="float32")
    if cue_rate != sample_rate or a2_cue_audio.size == 0 or not np.isfinite(a2_cue_audio).all():
        raise ValueError(f"Invalid A2 transition cue checkpoint: {cue_path}")
    a2_cue_audio = np.asarray(a2_cue_audio, dtype=np.float32)

    def load_reused(language: str, text: str) -> list[np.ndarray]:
        nonlocal reused_count
        paths = reuse.get((language, text))
        if not paths:
            raise FileNotFoundError(f"No matching previous {language} WAV checkpoint for text: {text[:80]!r}")
        arrays: list[np.ndarray] = []
        for path in paths:
            data, rate = sf.read(str(path), dtype="float32")
            if rate != sample_rate or data.size == 0 or not np.isfinite(data).all():
                raise ValueError(f"Invalid reusable WAV checkpoint: {path}")
            arrays.append(np.asarray(data, dtype=np.float32))
            reused_count += 1
        return arrays

    # Put the English heading first, then the original Chinese chapter heading.
    audio_parts.extend(load_reused("en", chapter.english_title))
    audio_parts.append(np.zeros(int(sample_rate * 0.25), dtype=np.float32))
    audio_parts.extend(load_reused("zh", chapter.chinese_title))
    audio_parts.append(np.zeros(int(sample_rate * 0.5), dtype=np.float32))

    for block_index, block in enumerate(alignment_blocks, 1):
        # English translation(s) always precede the corresponding source/A2 Chinese block.
        for english_index in block["english_paragraphs"]:
            audio_parts.extend(load_reused("en", english[english_index - 1]))
            audio_parts.append(np.zeros(int(sample_rate * 0.25), dtype=np.float32))
        translation_notice = block.get("translation_notice")
        if translation_notice:
            notice_samples, notice_rate = backend.synthesize_with_fallback(
                translation_notice, "en", "af_heart", 1.0
            )
            if notice_rate != sample_rate or notice_samples.size == 0 or not np.isfinite(notice_samples).all():
                raise RuntimeError(f"Could not synthesize translation notice for alignment block {block_index}.")
            audio_parts.extend((np.asarray(notice_samples, dtype=np.float32), np.zeros(int(sample_rate * 0.25), dtype=np.float32)))
        for chinese_index in block["chinese_paragraphs"]:
            original_path = original_audio_dir / f"original_{chinese_index:03d}_zh.wav"
            if not original_path.is_file():
                previous_original_path = (
                    previous_original_chinese_audio / original_path.name
                    if previous_original_chinese_audio is not None
                    else None
                )
                if previous_original_path is not None and previous_original_path.is_file():
                    shutil.copy2(previous_original_path, original_path)
                    reused_original_count += 1
                else:
                    samples, rate = backend.synthesize_with_fallback(original[chinese_index - 1], "zh", "zf_003", 0.75)
                    if rate != sample_rate or len(samples) == 0 or not np.isfinite(samples).all():
                        raise RuntimeError(f"Original Chinese synthesis failed for paragraph {chinese_index}.")
                    sf.write(str(original_path), samples.astype(np.float32, copy=False), rate)
                    synthesized_count += 1
            original_audio, rate = sf.read(str(original_path), dtype="float32")
            if rate != sample_rate or original_audio.size == 0 or not np.isfinite(original_audio).all():
                raise ValueError(f"Invalid synthesized original Chinese WAV: {original_path}")
            _append_triplet_audio(
                audio_parts,
                np.asarray(original_audio, dtype=np.float32),
                a2_cue_audio,
                load_reused("zh", a2[chinese_index - 1]),
                sample_rate,
            )

    old_vocab_dir = previous_audio / "vocabulary_segments"
    vocabulary = metadata["vocabulary"]
    if previous_manifest.get("vocabulary_audio", {}).get("items") != len(vocabulary):
        raise ValueError("Existing vocabulary audio checkpoint count differs from the selected chapter cache.")
    for index in range(1, len(vocabulary) + 1):
        for language in ("zh", "en"):
            path = old_vocab_dir / f"item_{index:03d}_{language}.wav"
            data, rate = sf.read(str(path), dtype="float32")
            if rate != sample_rate or data.size == 0 or not np.isfinite(data).all():
                raise ValueError(f"Invalid reusable vocabulary checkpoint: {path}")
            audio_parts.append(np.asarray(data, dtype=np.float32))
            reused_count += 1
            if language == "zh":
                audio_parts.append(np.zeros(int(sample_rate * 0.25), dtype=np.float32))
            elif index < len(vocabulary):
                audio_parts.append(np.zeros(int(sample_rate * 0.6), dtype=np.float32))

    audiobook = np.concatenate(audio_parts)
    audio_path = output_dir / f"chapter_{chapter_number:04d}_english_first.mp3"
    _export_mp3(audiobook, sample_rate, audio_path)
    alignment_copy = output_dir / "alignment.json"
    alignment_copy.write_text(json.dumps(alignment, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "chapter": chapter_number,
        "ordering": "english_translation_then_original_chinese_then_a2_chinese",
        "source_chapter_sha256": record["source_sha256"],
        "prompt_version": record["prompt_version"],
        "preprocessing_model": record["model"],
        "alignment_file": alignment_copy.name,
        "alignment_notes": alignment.get("alignment_notes", [alignment.get("alignment_note")] if alignment.get("alignment_note") else []),
        "translation_notice_blocks": [
            {"block": index, "notice": block["translation_notice"]}
            for index, block in enumerate(alignment_blocks, 1)
            if block.get("translation_notice")
        ],
        "alignment_blocks": len(triplets),
        "english_paragraphs": len(english),
        "original_chinese_paragraphs": len(original),
        "a2_chinese_paragraphs": len(a2),
        "vocabulary_entries": len(vocabulary),
        "audio_settings": {
            "zh_voice": "zf_003",
            "en_voice": "af_heart",
            "zh_speed": 0.75,
            "en_speed": 1.0,
            "sample_rate": sample_rate,
            "misaki_g2p_version": "1.1",
            "onnx_providers": backend.providers,
            "a2_transition_cue": "简化版",
            "a2_transition_cue_voice": "zf_003",
            "a2_transition_cue_speed": 0.75,
            "a2_transition_cue_wav": cue_path.name,
            "a2_transition_cue_repetitions": len(original),
            "english_and_a2_wavs_reused": True,
            "original_chinese_wavs_synthesized": synthesized_count,
            "original_chinese_wavs_reused": reused_original_count,
            "checkpoint_wavs_reused": reused_count,
        },
        "triplet_text": triplets,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return study_epub, audio_path, {
        "triplets": len(triplets),
        "english_paragraphs": len(english),
        "original_chinese_paragraphs": len(original),
        "a2_chinese_paragraphs": len(a2),
        "vocabulary_entries": len(vocabulary),
        "reused_wavs": reused_count,
        "synthesized_original_wavs": synthesized_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paired_epub", type=Path)
    parser.add_argument("cache_dir", type=Path)
    parser.add_argument("previous_audio_chapter_dir", type=Path)
    parser.add_argument("alignment", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--previous-original-chinese-audio", type=Path, default=None)
    parser.add_argument("--zh-model", type=Path, default=Path("models/kokoro-v1.1-zh.onnx"))
    parser.add_argument("--zh-voices", type=Path, default=Path("models/voices-v1.1-zh.bin"))
    parser.add_argument("--en-model", type=Path, default=Path("kokoro-v1.0.onnx"))
    parser.add_argument("--en-voices", type=Path, default=Path("voices-v1.0.bin"))
    args = parser.parse_args()
    try:
        epub_path, audio_path, stats = create_english_first_chapter(
            args.paired_epub,
            args.cache_dir,
            args.previous_audio_chapter_dir,
            args.alignment,
            args.output_dir,
            previous_original_chinese_audio=args.previous_original_chinese_audio,
            chinese_model=args.zh_model,
            chinese_voices=args.zh_voices,
            english_model=args.en_model,
            english_voices=args.en_voices,
        )
        print("Study EPUB:", epub_path)
        print("English-first audiobook:", audio_path)
        print("Stats:", json.dumps(stats))
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
