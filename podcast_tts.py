#!/usr/bin/env python3
"""
podcast_tts.py — Generate audio from a multi-speaker podcast script.

Parses markdown scripts where speakers are marked as **Speaker Name:** and
assigns different kokoro-tts voices to each speaker.

Usage:
    python podcast_tts.py <script.md> [output.mp3] [options]

Voice mapping (--voice-map):
    A comma-separated list of  SpeakerName=voice_spec  pairs.
    voice_spec may be a single voice name or a blend like "af_nicole:70,bm_lewis:30".

    Example:
        --voice-map "Host A=af_nicole,Host B=bm_lewis"
        --voice-map "Host A=af_nicole:70,bm_lewis:30,Host B=bm_lewis"

    Speakers not mentioned in the map fall back to --default-voice.

Full example:
    python podcast_tts.py podcast_script.md podcast.mp3 \\
        --voice-map "Host A=af_nicole:70,bm_lewis:30,Host B=bm_lewis" \\
        --lang en-us --format mp3 --silence 500
"""

import os
import sys
import re
import json
import argparse
import threading
import itertools
import time
import signal

import numpy as np
import soundfile as sf

# ── reuse helpers from the installed kokoro_tts package ──────────────────────
from kokoro_tts import (
    check_required_files,
    validate_language,
    validate_voice,
    process_chunk_sequential,
    chunk_text,
    spinning_wheel,
)
from kokoro_onnx import Kokoro

# ── globals used by the spinner helper ───────────────────────────────────────
import kokoro_tts as _ktts_module

# ─────────────────────────────────────────────────────────────────────────────
# Markdown stripping
# ─────────────────────────────────────────────────────────────────────────────

_MD_STRIP_PATTERNS = [
    (re.compile(r'^#{1,6}\s+'), ''),           # headings
    (re.compile(r'!?\[([^\]]*)\]\([^)]*\)'), r'\1'),  # links / images → label
    (re.compile(r'`{1,3}[^`]*`{1,3}'), ''),    # inline code / fenced code
    (re.compile(r'\*{3}([^*]+)\*{3}'), r'\1'),  # bold+italic
    (re.compile(r'\*{2}([^*]+)\*{2}'), r'\1'),  # bold
    (re.compile(r'\*([^*]+)\*'), r'\1'),        # italic *
    (re.compile(r'_([^_]+)_'), r'\1'),          # italic _
    (re.compile(r'~~([^~]+)~~'), r'\1'),        # strikethrough
    (re.compile(r'^[-*+]\s+', re.MULTILINE), ''),  # unordered list bullets
    (re.compile(r'^\d+\.\s+', re.MULTILINE), ''),  # ordered list numbers
    (re.compile(r'^>+\s*', re.MULTILINE), ''),  # blockquotes
    (re.compile(r'^-{3,}$', re.MULTILINE), ''), # horizontal rules
    (re.compile(r'\s+'), ' '),                  # collapse whitespace
]


def _strip_markdown(text: str) -> str:
    """Remove common markdown markup from text before passing to TTS."""
    for pattern, replacement in _MD_STRIP_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.strip()


# Explicit abbreviation → spoken-form map.
# Entries are applied longest-first so "ASIL-B" is matched before "ASIL".
# Add or adjust entries here to tune pronunciation for your domain.
_DEFAULT_ABBR_FILE = os.path.join(os.path.dirname(__file__), "abbreviations.json")

# Fallback: space out any remaining run of 3-6 uppercase letters so they are
# spelled out letter-by-letter rather than mispronounced as a word.
_CAPS_RE = re.compile(r'\b([A-Z]{3,6})\b')


def _load_abbreviations(path: str) -> list[tuple[re.Pattern, str]]:
    """Load abbreviations from a JSON file and return compiled (pattern, replacement) pairs.

    The JSON may be flat  {"ABBR": "spoken form"}  or grouped into named
    sections  {"Section name": {"ABBR": "spoken form"}}.  Keys starting with
    '_' are treated as comments and skipped.  Entries are sorted longest-first
    so longer keys (e.g. 'ASIL-B') are matched before shorter prefixes ('ASIL').
    """
    if not os.path.exists(path):
        print(f"Warning: abbreviation map '{path}' not found — skipping.")
        return []

    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    flat: dict[str, str] = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue  # comment entry
        if isinstance(value, dict):
            # grouped section
            for abbr, expansion in value.items():
                if not abbr.startswith("_"):
                    flat[abbr] = expansion
        else:
            flat[key] = value

    patterns = [
        (re.compile(r'(?<![\w-])' + re.escape(abbr) + r'(?![\w-])'), expansion)
        for abbr, expansion in sorted(flat.items(), key=lambda x: len(x[0]), reverse=True)
    ]
    print(f"Loaded {len(patterns)} abbreviation(s) from '{path}'")
    return patterns


# Module-level cache; replaced when generate_podcast() is called.
_ABBR_PATTERNS: list[tuple[re.Pattern, str]] = []


def _expand_abbreviations(text: str) -> str:
    """Replace known abbreviations with their spoken forms and space out the rest."""
    for pattern, expansion in _ABBR_PATTERNS:
        text = pattern.sub(expansion, text)
    # Auto-space any leftover ALL-CAPS sequences
    text = _CAPS_RE.sub(lambda m: ' '.join(m.group(1)), text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Script parsing
# ─────────────────────────────────────────────────────────────────────────────

# Matches lines like:  **Host A:** some text here
# The markdown uses **Speaker:** where the colon sits *inside* the bold markers.
_SPEAKER_RE = re.compile(r"^\*\*([^*]+):\*\*\s*(.*)")


def parse_podcast_script(filepath: str) -> list[tuple[str, str]]:
    """Return a list of (speaker, text) tuples from a markdown podcast script.

    Rules
    -----
    * Lines starting with ``#`` or ``---`` are treated as section markers and
      cause any buffered segment to be flushed (the markers themselves are
      not emitted).
    * A line matching ``**Speaker:**`` starts a new segment.  Any remaining
      text on that same line is the first part of the segment.
    * Subsequent non-blank lines that do *not* start a new speaker are
      continuations of the current segment.
    * A blank line ends the current segment.
    * The **Both:** speaker (or any variant) is kept as-is so callers can
      handle it however they like.
    """
    with open(filepath, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    segments: list[tuple[str, str]] = []
    current_speaker: str | None = None
    current_parts: list[str] = []

    def _flush():
        nonlocal current_speaker, current_parts
        if current_speaker:
            text = " ".join(current_parts).strip()
            if text:
                segments.append((current_speaker, text))
        current_speaker = None
        current_parts = []

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")

        # Section headers / horizontal rules → flush and continue
        if line.lstrip().startswith("#") or line.strip() == "---":
            _flush()
            continue

        # New speaker line
        m = _SPEAKER_RE.match(line)
        if m:
            _flush()
            current_speaker = m.group(1).strip()
            rest = m.group(2).strip()
            if rest:
                current_parts = [rest]
            else:
                current_parts = []
            continue

        # Blank line → end of segment
        if not line.strip():
            _flush()
            continue

        # Continuation of current speaker
        if current_speaker is not None:
            current_parts.append(line.strip())

    _flush()
    return segments


# ─────────────────────────────────────────────────────────────────────────────
# Interview Q&A script parsing
# ─────────────────────────────────────────────────────────────────────────────

# ### Q1: question text
_QA_QUESTION_RE = re.compile(r'^#{1,4}\s+Q(\d+):\s*(.*)')

# Map raw **X:** labels to canonical speaker names
_QA_SPEAKER_REMAP: dict[str, str] = {
    "Model Answer":  "Candidate",
    "Follow-up Probe": "Interviewer",
}


def parse_qa_script(filepath: str) -> list[tuple[str, str]]:
    """Return (speaker, text) tuples from an interview Q&A markdown file.

    Speaker mapping
    ---------------
    * ``### Q<n>: <text>``  →  ("Interviewer", "Question N. <text>")
    * ``**Model Answer:**``  →  ("Candidate", <text>)
    * ``**Follow-up Probe:**``  →  ("Interviewer", "Follow-up question. <text>")
    * ``## Quick Reference`` section (repeated question list) → skipped.
    """
    with open(filepath, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    segments: list[tuple[str, str]] = []
    current_speaker: str | None = None
    current_parts: list[str] = []
    skip_to_end = False

    def _flush():
        nonlocal current_speaker, current_parts
        if current_speaker:
            text = " ".join(current_parts).strip()
            if text:
                segments.append((current_speaker, text))
        current_speaker = None
        current_parts = []

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")

        # Quick Reference section is a repetition of questions — skip it
        if re.match(r'^#{1,3}\s+Quick Reference', line, re.IGNORECASE):
            _flush()
            skip_to_end = True
            continue
        if skip_to_end:
            continue

        # Horizontal rules
        if line.strip() == "---":
            _flush()
            continue

        # Heading lines
        if line.lstrip().startswith("#"):
            # Question heading:  ### Q1: text
            qm = _QA_QUESTION_RE.match(line)
            if qm:
                _flush()
                q_num = qm.group(1)
                q_text = qm.group(2).strip()
                current_speaker = "Interviewer"
                current_parts = [f"Question {q_num}. {q_text}"]
            else:
                # Section header (## A. Role...) — flush and skip
                _flush()
            continue

        # **Model Answer:** / **Follow-up Probe:** speaker lines
        m = _SPEAKER_RE.match(line)
        if m:
            _flush()
            raw_speaker = m.group(1).strip()
            current_speaker = _QA_SPEAKER_REMAP.get(raw_speaker, raw_speaker)
            rest = m.group(2).strip()
            if raw_speaker == "Follow-up Probe":
                current_parts = [f"Follow-up question. {rest}"] if rest else ["Follow-up question."]
            else:
                current_parts = [rest] if rest else []
            continue

        # Blank line → end segment
        if not line.strip():
            _flush()
            continue

        # Continuation
        if current_speaker is not None:
            current_parts.append(line.strip())

    _flush()
    return segments


def _detect_script_type(filepath: str) -> str:
    """Return 'qa' if the file looks like an interview Q&A, else 'podcast'."""
    with open(filepath, "r", encoding="utf-8") as fh:
        head = fh.read(8192)
    if re.search(r'\*\*Model Answer:\*\*|\*\*Follow-up Probe:\*\*', head):
        return "qa"
    return "podcast"


# ─────────────────────────────────────────────────────────────────────────────
# Voice-map parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_voice_map(raw: str) -> dict[str, str]:
    """Parse a voice-map string into a {speaker: voice_spec} dict.

    The format is a comma-separated list of  Name=voice_spec  pairs.
    Because voice blends already use commas (``af_nicole:70,bm_lewis:30``),
    we split on commas that are followed by a ``=`` somewhere before the
    next ``=``.  Concretely we split on the pattern  ``,(?=[^=,]+=)``.

    Example input:
        "Host A=af_nicole:70,bm_lewis:30,Host B=bm_lewis"

    Produces:
        {"Host A": "af_nicole:70,bm_lewis:30", "Host B": "bm_lewis"}
    """
    result: dict[str, str] = {}
    # Split on a comma that immediately precedes a  key=  token
    pairs = re.split(r",(?=[^=,]+=)", raw)
    for pair in pairs:
        if "=" not in pair:
            continue
        idx = pair.index("=")
        speaker = pair[:idx].strip()
        voice_spec = pair[idx + 1:].strip()
        if speaker and voice_spec:
            result[speaker] = voice_spec
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Audio helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mix_audio(a: list[float], b: list[float]) -> list[float]:
    """Overlay two audio arrays by summing and normalising to prevent clipping."""
    len_a, len_b = len(a), len(b)
    length = max(len_a, len_b)
    arr_a = np.zeros(length, dtype=np.float32)
    arr_b = np.zeros(length, dtype=np.float32)
    arr_a[:len_a] = a
    arr_b[:len_b] = b
    mixed = arr_a + arr_b
    peak = np.max(np.abs(mixed))
    if peak > 1.0:
        mixed /= peak
    return mixed.tolist()


def _silence_samples(duration_ms: int, sample_rate: int) -> np.ndarray:
    """Return a numpy array of silent samples for the given duration."""
    n = int(sample_rate * duration_ms / 1000)
    return np.zeros(n, dtype=np.float32)


def _save_audio(samples: list[float], sample_rate: int, output_file: str, fmt: str):
    """Write audio to disk, converting to MP3 via pydub when requested."""
    if fmt == "mp3":
        # Write temporary WAV then convert
        tmp_wav = output_file + ".tmp.wav"
        sf.write(tmp_wav, np.array(samples, dtype=np.float32), sample_rate)
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_wav(tmp_wav)
            audio.export(output_file, format="mp3")
        except ImportError:
            print(
                "Warning: pydub not installed — saving as WAV instead. "
                "Install pydub + ffmpeg for MP3 support."
            )
            wav_out = os.path.splitext(output_file)[0] + ".wav"
            os.rename(tmp_wav, wav_out)
            return
        finally:
            if os.path.exists(tmp_wav):
                os.remove(tmp_wav)
    else:
        sf.write(output_file, np.array(samples, dtype=np.float32), sample_rate)


# ─────────────────────────────────────────────────────────────────────────────
# Main generation logic
# ─────────────────────────────────────────────────────────────────────────────

def generate_podcast(
    script_file: str,
    output_file: str,
    voice_map: dict[str, str],
    default_voice: str = "af_heart",
    lang: str = "en-us",
    speed: float = 1.0,
    silence_ms: int = 400,
    fmt: str = "mp3",
    debug: bool = False,
    model_path: str = "kokoro-v1.0.onnx",
    voices_path: str = "voices-v1.0.bin",
    list_speakers: bool = False,
    dry_run: bool = False,
    abbr_map_path: str = _DEFAULT_ABBR_FILE,
    script_type: str = "auto",
):
    # ── load abbreviations ────────────────────────────────────────────────────
    global _ABBR_PATTERNS
    _ABBR_PATTERNS = _load_abbreviations(abbr_map_path)

    # ── parse script ─────────────────────────────────────────────────────────
    resolved_type = script_type if script_type != "auto" else _detect_script_type(script_file)
    print(f"Parsing script: {script_file}  [type: {resolved_type}]")
    if resolved_type == "qa":
        segments = parse_qa_script(script_file)
    else:
        segments = parse_podcast_script(script_file)

    if not segments:
        print("No speaker segments found in the script.")
        sys.exit(1)

    # Collect unique speakers
    unique_speakers = list(dict.fromkeys(s for s, _ in segments))

    if list_speakers:
        print(f"\nDetected {len(unique_speakers)} unique speaker(s):")
        for sp in unique_speakers:
            mapped = voice_map.get(sp, f"<default: {default_voice}>")
            print(f"  {sp!r:30s} → {mapped}")
        print(f"\nTotal segments: {len(segments)}")
        total_words = sum(len(t.split()) for _, t in segments)
        print(f"Total words:    {total_words:,}")
        print(f"Est. duration:  {total_words / 150:.1f} minutes")
        return

    if dry_run:
        print(f"\n[dry-run] {len(segments)} segments, speakers: {unique_speakers}")
        for i, (sp, text) in enumerate(segments, 1):
            voice_spec = voice_map.get(sp, default_voice)
            clean = _expand_abbreviations(_strip_markdown(text))
            print(f"  [{i:03d}] {sp!r} ({voice_spec}): {clean[:80]!r}{'...' if len(clean) > 80 else ''}")
        return

    # ── load model ───────────────────────────────────────────────────────────
    check_required_files(model_path, voices_path)
    print("Loading Kokoro model…")
    try:
        kokoro = Kokoro(model_path, voices_path)
    except Exception as exc:
        print(f"Error loading model: {exc}")
        sys.exit(1)

    lang = validate_language(lang, kokoro)

    # ── identify the two main hosts for 'Both' mixing ─────────────────────────
    # 'Both' hosts = the two most frequent non-Both speakers (by appearance order)
    non_both_speakers = [s for s in unique_speakers if s.lower() != 'both']
    both_hosts = non_both_speakers[:2]  # at most two voices to mix

    # ── resolve voices for every unique speaker ───────────────────────────────
    resolved_voices: dict[str, object] = {}
    for speaker in unique_speakers:
        spec = voice_map.get(speaker, default_voice)
        resolved_voices[speaker] = validate_voice(spec, kokoro)
        label = spec if isinstance(resolved_voices[speaker], str) else f"{spec} (blended)"
        if speaker.lower() == 'both':
            label += f"  [mixed with: {', '.join(both_hosts)}]"
        print(f"  Speaker {speaker!r:30s} → {label}")

    # ── generate audio ────────────────────────────────────────────────────────
    all_samples: list[float] = []
    sample_rate: int | None = None

    total_segments = len(segments)
    print(f"\nGenerating audio for {total_segments} segments…\n")

    stop_flag = threading.Event()

    def _handle_sigint(signum, frame):
        print("\nInterrupted — stopping…")
        stop_flag.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_sigint)

    def _generate_chunks(speaker: str, text: str, seg_idx: int) -> tuple[list[float], int | None]:
        """Generate audio samples for one speaker segment, stripping markdown first."""
        clean_text = _expand_abbreviations(_strip_markdown(text))
        voice = resolved_voices[speaker]
        chunks = chunk_text(clean_text, initial_chunk_size=1000)
        total_chunks = len(chunks)
        progress_label = f"[{seg_idx}/{total_segments}] {speaker}"
        seg_samples: list[float] = []
        seg_rate: int | None = None

        for chunk_idx, chunk in enumerate(chunks, 1):
            if stop_flag.is_set():
                break
            progress = f"chunk {chunk_idx}/{total_chunks}"
            _ktts_module.stop_spinner = False
            spinner = threading.Thread(
                target=spinning_wheel,
                args=(progress_label, progress),
                daemon=True,
            )
            spinner.start()
            samples, sr = process_chunk_sequential(
                chunk, kokoro, voice, speed, lang,
                retry_count=0, debug=debug,
            )
            _ktts_module.stop_spinner = True
            spinner.join()
            if samples is not None:
                if seg_rate is None:
                    seg_rate = sr
                seg_samples.extend(samples)

        return seg_samples, seg_rate

    for seg_idx, (speaker, text) in enumerate(segments, 1):
        if stop_flag.is_set():
            break

        if speaker.lower() == 'both' and len(both_hosts) >= 2:
            # Generate audio for each host independently then mix (overlay)
            print(f"  [{seg_idx}/{total_segments}] Both — generating Host A track...")
            samples_a, sr_a = _generate_chunks(both_hosts[0], text, seg_idx)
            print(f"  [Both] generating Host B track...")
            samples_b, sr_b = _generate_chunks(both_hosts[1], text, seg_idx)
            seg_samples = _mix_audio(samples_a, samples_b)
            sr = sr_a or sr_b
        else:
            seg_samples, sr = _generate_chunks(speaker, text, seg_idx)

        if sr is not None:
            if sample_rate is None:
                sample_rate = sr
            all_samples.extend(seg_samples)

        # Insert silence between segments (but not after the last one)
        if seg_idx < total_segments and sample_rate is not None:
            all_samples.extend(_silence_samples(silence_ms, sample_rate).tolist())

    if not all_samples or sample_rate is None:
        print("No audio was generated.")
        sys.exit(1)

    # ── save output ───────────────────────────────────────────────────────────
    print(f"\nSaving {output_file}…")
    _save_audio(all_samples, sample_rate, output_file, fmt)
    duration_s = len(all_samples) / sample_rate
    print(f"Done. Duration: {duration_s / 60:.1f} min  ({duration_s:.0f} s)")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate audio podcast from a multi-speaker markdown script.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("script", help="Path to the markdown podcast script (.md or .txt)")
    parser.add_argument(
        "output", nargs="?",
        help="Output audio file (default: <script_basename>.<format>)",
    )

    parser.add_argument(
        "--voice-map", default="",
        metavar="MAP",
        help=(
            'Comma-separated SpeakerName=voice_spec pairs. '
            'Example: "Host A=af_nicole:70,bm_lewis:30,Host B=bm_lewis"'
        ),
    )
    parser.add_argument(
        "--default-voice", default="af_heart",
        help="Voice used for speakers not listed in --voice-map (default: af_heart)",
    )
    parser.add_argument("--lang", default="en-us", help="Language code (default: en-us)")
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed (default: 1.0)")
    parser.add_argument(
        "--silence", type=int, default=400,
        metavar="MS",
        help="Silence between speaker segments in milliseconds (default: 400)",
    )
    parser.add_argument(
        "--format", default="mp3", choices=["wav", "mp3"],
        help="Output audio format (default: mp3)",
    )
    parser.add_argument("--debug", action="store_true", help="Verbose debug output")
    parser.add_argument(
        "--model", default="kokoro-v1.0.onnx",
        help="Path to kokoro-v1.0.onnx (default: ./kokoro-v1.0.onnx)",
    )
    parser.add_argument(
        "--voices-bin", default="voices-v1.0.bin",
        help="Path to voices-v1.0.bin (default: ./voices-v1.0.bin)",
    )
    parser.add_argument(
        "--abbr-map",
        default=_DEFAULT_ABBR_FILE,
        metavar="FILE",
        help=(
            f"Path to abbreviation map JSON file "
            f"(default: abbreviations.json next to this script)"
        ),
    )
    parser.add_argument(
        "--script-type", default="auto", choices=["auto", "podcast", "qa"],
        help=(
            "Script format: 'podcast' (**Host A:** style), "
            "'qa' (### Q1:/Model Answer: style), or 'auto' to detect (default: auto)"
        ),
    )
    parser.add_argument(
        "--list-speakers", action="store_true",
        help="Print detected speakers with their mapped voices and exit",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse the script and print segments without generating audio",
    )

    args = parser.parse_args()

    # Resolve output path
    if args.output:
        output_file = args.output
    else:
        base = os.path.splitext(args.script)[0]
        output_file = f"{base}.{args.format}"

    # Parse voice map
    voice_map = _parse_voice_map(args.voice_map) if args.voice_map else {}

    generate_podcast(
        script_file=args.script,
        output_file=output_file,
        voice_map=voice_map,
        default_voice=args.default_voice,
        lang=args.lang,
        speed=args.speed,
        silence_ms=args.silence,
        fmt=args.format,
        debug=args.debug,
        model_path=args.model,
        voices_path=args.voices_bin,
        list_speakers=args.list_speakers,
        dry_run=args.dry_run,
        abbr_map_path=args.abbr_map,
        script_type=args.script_type,
    )


if __name__ == "__main__":
    main()
