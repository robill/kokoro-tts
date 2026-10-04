# Kokoro TTS

A CLI text-to-speech tool using the Kokoro model, supporting multiple languages, voices (with blending), and various input formats including EPUB books and PDF documents.

![ngpt-s-c](https://raw.githubusercontent.com/nazdridoy/kokoro-tts/main/previews/kokoro-tts-h.png)

## Features

- Multiple language and voice support
- Voice blending with customizable weights
- EPUB, PDF and TXT file input support
- Standard input (stdin) and `|` piping from other programs
- Streaming audio playback
- Split output into chapters
- Adjustable speech speed
- WAV and MP3 output formats
- Chapter merging capability
- Detailed debug output option
- GPU Support

## Demo

Kokoro TTS is an open-source CLI tool that delivers high-quality text-to-speech right from your terminal. Think of it as your personal voice studio, capable of transforming any text into natural-sounding speech with minimal effort.

https://github.com/user-attachments/assets/8413e640-59e9-490e-861d-49187e967526

[Demo Audio (MP3)](https://github.com/nazdridoy/kokoro-tts/raw/main/previews/demo.mp3) | [Demo Audio (WAV)](https://github.com/nazdridoy/kokoro-tts/raw/main/previews/demo.wav)

## TODO

- [x] Add GPU support
- [x] Add PDF support
- [ ] Add GUI

## Prerequisites

- Python 3.9-3.12 (Python 3.13+ is not currently supported)

## Installation

### Method 1: Install from PyPI (Recommended)

The easiest way to install Kokoro TTS is from PyPI:

```bash
# Using uv (recommended)
uv tool install kokoro-tts

# Using pip
pip install kokoro-tts
```

```bash
kokoro-tts --help
```

### Method 2: Install from Git

```bash
# Using uv (recommended)
uv tool install git+https://github.com/nazdridoy/kokoro-tts

# Using pip
pip install git+https://github.com/nazdridoy/kokoro-tts
```

### Method 3: Clone and Install Locally

1. Clone the repository:
```bash
git clone https://github.com/nazdridoy/kokoro-tts.git
cd kokoro-tts
```

2. Install the package:

**With `uv` (recommended):**
```bash
uv venv
uv pip install -e .
```

**With `pip`:**
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

3. Run the tool:
```bash
# If using uv
uv run kokoro-tts --help

# If using pip with activated venv
kokoro-tts --help
```

### Method 4: Run Without Installation

If you prefer to run without installing:

1. Clone the repository:
```bash
git clone https://github.com/nazdridoy/kokoro-tts.git
cd kokoro-tts
```

2. Install dependencies only:

**With `uv`:**
```bash
uv venv
uv sync
```

**With `pip`:**
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

3. Run directly:
```bash
# With uv
uv run -m kokoro_tts --help

# With pip (venv activated)
python -m kokoro_tts --help
```

### Download Model Files

After installation, download the required model files to your working directory:

```bash
# Download voice data (bin format is preferred)
wget https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/voices-v1.0.bin

# Download the model
wget https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/kokoro-v1.0.onnx
```

> The script requires `voices-v1.0.bin` and `kokoro-v1.0.onnx` to be present in the same directory where you run the `kokoro-tts` command.

## Supported voices:

| **Category** | **Voices** | **Language Code** |
| --- | --- | --- |
| 🇺🇸 👩 | af\_alloy, af\_aoede, af\_bella, af\_heart, af\_jessica, af\_kore, af\_nicole, af\_nova, af\_river, af\_sarah, af\_sky | **en-us** |
| 🇺🇸 👨 | am\_adam, am\_echo, am\_eric, am\_fenrir, am\_liam, am\_michael, am\_onyx, am\_puck | **en-us** |
| 🇬🇧 | bf\_alice, bf\_emma, bf\_isabella, bf\_lily, bm\_daniel, bm\_fable, bm\_george, bm\_lewis | **en-gb** |
| 🇫🇷 | ff\_siwis | **fr-fr** |
| 🇮🇹 | if\_sara, im\_nicola | **it** |
| 🇯🇵 | jf\_alpha, jf\_gongitsune, jf\_nezumi, jf\_tebukuro, jm\_kumo | **ja** |
| 🇨🇳 | zf\_xiaobei, zf\_xiaoni, zf\_xiaoxiao, zf\_xiaoyi, zm\_yunjian, zm\_yunxi, zm\_yunxia, zm\_yunyang | **cmn** |

## Usage

### Basic Usage

```bash
kokoro-tts <input_text_file> [<output_audio_file>] [options]
```

> [!NOTE]
> - If you installed via Method 1 (PyPI) or Method 2 (git install), use `kokoro-tts` directly
> - If you installed via Method 3 (local install), use `uv run kokoro-tts` or activate your virtual environment first
> - If you're using Method 4 (no install), use `uv run -m kokoro_tts` or `python -m kokoro_tts` with activated venv

### Commands

- `-h, --help`: Show help message
- `--help-languages`: List supported languages
- `--help-voices`: List available voices
- `--merge-chunks`: Merge existing chunks into chapter files

### Options

- `--stream`: Stream audio instead of saving to file
- `--speed <float>`: Set speech speed (default: 1.0)
- `--lang <str>`: Set language (default: en-us)
- `--voice <str>`: Set voice or blend voices (default: interactive selection)
  - Single voice: Use voice name (e.g., "af_sarah")
  - Blended voices: Use "voice1:weight,voice2:weight" format
- `--split-output <dir>`: Save each chunk as separate file in directory
- `--format <str>`: Audio format: wav or mp3 (default: wav)
- `--debug`: Show detailed debug information during processing

### Input Formats

- `.txt`: Text file input
- `.epub`: EPUB book input (will process chapters)
- `.pdf`: PDF document input (extracts chapters from TOC or content)
- `-` or `/dev/stdin` (Linux/macOS) or `CONIN$` (Windows): Standard input (stdin)

### Examples

```bash
# Basic usage with output file
kokoro-tts input.txt output.wav --speed 1.2 --lang en-us --voice af_sarah

# Read from standard input (stdin)
echo "Hello World" | kokoro-tts - --stream
cat input.txt | kokoro-tts - output.wav

# Cross-platform stdin support:
# Linux/macOS: echo "text" | kokoro-tts - --stream
# Windows: echo "text" | kokoro-tts - --stream
# All platforms also support: kokoro-tts /dev/stdin --stream (Linux/macOS) or kokoro-tts CONIN$ --stream (Windows)

# Use voice blending (60-40 mix)
kokoro-tts input.txt output.wav --voice "af_sarah:60,am_adam:40"

# Use equal voice blend (50-50)
kokoro-tts input.txt --stream --voice "am_adam,af_sarah"

# Process EPUB and split into chunks
kokoro-tts input.epub --split-output ./chunks/ --format mp3

# Stream audio directly
kokoro-tts input.txt --stream --speed 0.8

# Merge existing chunks
kokoro-tts --merge-chunks --split-output ./chunks/ --format wav

# Process EPUB with detailed debug output
kokoro-tts input.epub --split-output ./chunks/ --debug

# Process PDF and split into chapters
kokoro-tts input.pdf --split-output ./chunks/ --format mp3

# List available voices
kokoro-tts --help-voices

# List supported languages
kokoro-tts --help-languages
```

> [!TIP]
> If you're using Method 3, replace `kokoro-tts` with `uv run kokoro-tts` in the examples above.
> If you're using Method 4, replace `kokoro-tts` with `uv run -m kokoro_tts` or `python -m kokoro_tts` in the examples above.

### Bilingual Chinese Study Audio (VIO)

#### Mandarin G2P audio runtime

The custom bilingual and vocabulary audio generators use a dedicated
`.venv_kokoro_061` environment with `kokoro-onnx==0.6.1` and
`misaki-fork[zh]==0.9.6`. Keep the original project environment pinned to
`kokoro-onnx==0.3.9` for the upstream CLI. The Mandarin path converts Hanzi
with Misaki `ZHG2P(version="1.1")`, then supplies phonemes to the v1.1 Chinese
model with `is_phonemes=True`. English remains on the existing v1.0 model and
voice bundle. Use the full-precision Chinese model; the tested FP16 asset
produced non-finite audio.

The v1.1 voice IDs are different from the old v1.0 IDs such as
`zf_xiaoxiao`. The selected v1.1 voice is `zf_003`. The audio generators
record model/G2P settings in their manifests and refuse to reuse checkpoints
made with different synthesis settings.

Create the isolated runtime without changing the upstream CLI's pinned
environment:

```powershell
# Install CPython 3.11.9 x64 from https://www.python.org/downloads/release/python-3119/
# and FFmpeg 8.1.1 (ffmpeg + ffprobe on PATH) first; Windows builds:
# https://www.gyan.dev/ffmpeg/builds/
# Check out the repository commit/branch, then run from the project root:
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_bilingual_audio_env.ps1 -Provider cuda
```

The bootstrap installs the exact packages in
`requirements-bilingual-audio.lock.txt`, then installs exactly one ONNX Runtime
build. The tested setup uses CPython 3.11.9 x64, pip 26.2.1, Kokoro ONNX 0.6.1,
Misaki Fork 0.9.6, and ONNX Runtime GPU 1.23.2. Exact application package
versions are in `requirements-bilingual-audio.lock.txt`; ONNX Runtime is
installed separately because CPU and GPU wheels conflict. The root project
environment remains separate and pinned to Kokoro ONNX 0.3.9. The bootstrap
loads both models and synthesizes short Chinese and English smoke samples.
The repository's `.python-version` applies to its separate upstream CLI
environment; the bilingual audio bootstrap intentionally creates its own
CPython 3.11.9 environment.
The known-good machine also uses CUDA 12.9, cuDNN 9.14, and FFmpeg 8.1.1;
these are native system tools, not Python packages.

For GPU execution, install a CUDA 12.x and cuDNN 9 runtime compatible with the
ONNX Runtime GPU build. If their DLL folders are not discoverable via
`CUDA_PATH` and `CUDNN_PATH`, pass them explicitly, for example:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_bilingual_audio_env.ps1 -Provider cuda `
  -CudaBin "C:\Path\To\CUDA\bin" `
  -CudnnBin "C:\Path\To\cuDNN\bin"
```

Use `powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_bilingual_audio_env.ps1 -Provider cpu` on a machine without a
compatible NVIDIA CUDA/cuDNN runtime. CPU synthesis is slower but avoids the
GPU DLL dependency. Do not install `onnxruntime` and `onnxruntime-gpu`
simultaneously; they use the same Python import namespace. CUDA/cuDNN
installation paths are machine-specific. The setup script uses `CUDA_PATH` and
`CUDNN_PATH` when available, or accepts explicit binary-directory parameters;
use that machine's compatible runtime rather than copying DLLs from this PC.

#### Model and voice assets

The setup script downloads missing upstream model/voice assets and verifies
these SHA-256 digests. Keep the Chinese model full precision; the tested FP16
Chinese model generated non-finite samples.

| Asset | Location | SHA-256 |
| --- | --- | --- |
| Kokoro v1.1 Chinese FP32 | `models/kokoro-v1.1-zh.onnx` | `859f9ded9f53be16c24857cdab3254a45da53c3afd5ba6ef134c7de3f822e326` |
| v1.1 Chinese voices | `models/voices-v1.1-zh.bin` | `14cb6186c99e4f6016871405f62046c5df863ae27465cbdc4ee08be7dd703acd` |
| Kokoro v1.0 English | `kokoro-v1.0.onnx` | `7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5` |
| v1.0 voices | `voices-v1.0.bin` | `d19762d46cf0e6648cb28a7711df1637aad15818185d13f4ff840d57f2f6dfed` |

Official asset sources: [Kokoro ONNX v1.1 model/voice release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1),
[Kokoro TTS v1.0 release](https://github.com/nazdridoy/kokoro-tts/releases/tag/v1.0.0),
and the [official Chinese G2P example](https://github.com/thewh1teagle/kokoro-onnx/blob/main/examples/chinese.py).
The tested flow does not require the example's `config.json`; the v1.1 model
embeds the vocabulary used by Misaki.

For original-EPUB splitting, also check out the separate
[EpubSplit repository](https://github.com/JimmXinu/EpubSplit) and install its
standalone CLI dependencies (`beautifulsoup4` and `six`). To run the supplied
`split_epub_chapters.ps1` with the locked environment's Python, put
`.venv_kokoro_061\Scripts` at the front of `PATH` in that Command Prompt
session before invoking the script. Split-point mapping is book-specific; use
the preflight instructions in the [bilingual audiobook skill](.github/skills/bilingual-epub-audiobook/SKILL.md)
and verify the produced chapter XHTML pairs before starting VIO or TTS.

VIO credentials/configuration are deliberately machine-specific: copy
`.env.example` to `.env`, fill in the destination computer's approved values,
and never commit `.env`.

For bilingual EPUBs with paired Chinese/English paragraphs, the optional VIO
preprocessor can simplify only the Chinese text and retain the English as
meaning context. It produces Chinese-only, audio-ready narration paragraphs;
Pinyin and English meanings are kept in a separate study glossary and are not
spoken in the chapter audio. It writes a JSON cache and a side-by-side Markdown
review for each chapter. Review the narration before using it for TTS.

Configure `.env` locally with `VIO_API_KEY`, `VIO_BASE_URL` (HTTPS), and
`AI_MODEL`. Never commit `.env`. Confirm the endpoint is approved for the
source text before sending it. Install the VIO client dependencies from
`requirements.txt`.

```powershell
# First preprocess only one chapter; inspect its review Markdown.
python .\chinese_llm_preprocess.py ".\RMJI Bilingual Chapters 0721-0770.epub" `
  --start 721 --end 721 --output-dir ".\RMJI_chinese_preprocessed"

# After reviewing, generate audio from the validated cached Chinese text.
.\.venv_kokoro_061\Scripts\python.exe .\bilingual_epub_tts.py ".\RMJI Bilingual Chapters 0721-0770.epub" `
  --start 721 --end 721 `
  --preprocessed-dir ".\RMJI_chinese_preprocessed" `
  --output-dir ".\RMJI_Chapter_0721_study_audio" `
  --zh-voice zf_003 --en-voice af_heart `
  --zh-speed 0.75 --en-speed 1.0
```

The preprocessing cache is bound to the exact source text and prompt version.
The audio tool refuses stale or misaligned cache files. Use a new output folder
when comparing different adaptations or voice/speed settings.

To append the cached vocabulary after the chapter narration, add
`--include-vocabulary-audio`. Each item is spoken as Hanzi by the Mandarin
voice, followed by its English meaning; Pinyin is not synthesized. WAV
checkpoints are stored separately in the chapter output so interrupted runs
can resume. The default pause after each English meaning is 600 ms.

```powershell
.\.venv_kokoro_061\Scripts\python.exe .\bilingual_epub_tts.py `
  ".\RMJI Bilingual Chapters 0721-0770.epub" `
  --start 721 --end 721 `
  --preprocessed-dir ".\RMJI_chinese_preprocessed" `
  --output-dir ".\RMJI_Chapter_0721_study_audio" `
  --zh-voice zf_003 --en-voice af_heart `
  --zh-speed 0.75 --en-speed 1.0 `
  --include-vocabulary-audio
```

#### Optional simplified-EPUB export

After reviewing the preprocessing cache, export the selected rewritten
chapters as a separate EPUB. This step uses the existing cache only; it does
not call the LLM or alter the source EPUB. English paragraphs, Chinese chapter
headings, EPUB package metadata, and chapters outside the selected range are
preserved. The exporter checks each cache against its source digest and
paragraph alignment before writing, then verifies the finished archive. Add
`--include-glossary` to append that chapter's written Hanzi, tone-marked Pinyin,
and English meanings at the end of the chapter. Pinyin remains written and is
not added to the spoken audio.

```powershell
python .\simplified_epub_export.py `
  ".\RMJI Bilingual Chapters 0721-0770.epub" `
  ".\RMJI_chinese_preprocessed" `
  ".\RMJI_Bilingual_Chapter_0721_simplified_study.epub" `
  --start 721 --end 721 --include-glossary
```

Use `--start` and `--end` to export only chapters with reviewed caches. If
omitted, the exporter selects every paired chapter in the source and requires
a valid cache for each. An existing output is not overwritten unless `--force`
is supplied. When regenerating an existing EPUB, the export preserves its
Calibre reading bookmark metadata.

To create a separate review track for the chapter vocabulary, synthesize each
Hanzi term in Mandarin followed by its English meaning. Pinyin stays in the
written review sheet and is not spoken:

```powershell
.\.venv_kokoro_061\Scripts\python.exe .\vocabulary_audio.py `
  ".\RMJI_chinese_preprocessed\chapter_0721\chinese_simplification.json" `
  ".\RMJI_chinese_preprocessed\chapter_0721\vocabulary_0721.mp3" `
  --zh-voice zf_003 --en-voice af_heart --zh-speed 0.75 --en-speed 1.0
```

#### Merge chapters into one audiobook

After all chapter MP3s have been generated, concatenate the numbered files in
chapter order into one MP3. The merger requires every chapter in the requested
range and verifies matching audio properties and full-file decodability. It
uses MP3 stream copy, so the audio is not re-encoded and per-chapter files stay
in place.

```powershell
.\.venv_kokoro_061\Scripts\python.exe .\merge_chapter_audio.py `
  ".\RMJI_Bilingual_Chapters_0721-0770_zf003_Misaki_Audio" `
  ".\RMJI_Bilingual_Chapters_0721-0770_zf003_Misaki_Audiobook.mp3" `
  --start 721 --end 770
```

Add `--force` to replace an existing combined output after validation.

#### Run the complete bilingual-EPUB workflow from Command Prompt

The project-scoped Copilot skill at
[.github/skills/bilingual-epub-audiobook/SKILL.md](kokoro-tts/.github/skills/bilingual-epub-audiobook/SKILL.md)
guides source inspection/splitting, paired-XHTML preflight, VIO cache creation
and review, study-EPUB export, chapter TTS with spoken vocabulary, and final
merge. It explicitly checks that each chapter has the expected
`split_000`/`split_001` pair and that EPUB TOC chapter numbers are not confused
with spine/split-line indices.

For a prepared paired bilingual EPUB, the generic Windows runner executes the
full pipeline, prompting for review after preprocessing. It reuses valid
caches and WAV checkpoints when resuming an interrupted run:

```bat
run_bilingual_epub_pipeline.bat "D:\Books\MyBook_Paired.epub" 101 150
```

Use the fourth argument `--plan` to show the selected paths/stages without
running them, or `--no-pause` only after the review has been approved. The
existing `run_rmji_pipeline.bat` is a convenience wrapper for the RMJI sample
range 721–770. Outputs are placed in the project folder using the source stem
and selected range; per-chapter MP3s and resumable WAVs remain alongside the
single merged audiobook and study EPUB.

## Features in Detail

### EPUB Processing
- Automatically extracts chapters from EPUB files
- Preserves chapter titles and structure
- Creates organized output for each chapter
- Detailed debug output available for troubleshooting

### Audio Processing
- Chunks long text into manageable segments
- Supports streaming for immediate playback
- Voice blending with customizable mix ratios
- Progress indicators for long processes
- Handles interruptions gracefully

### Output Options
- Single file output
- Split output with chapter organization
- Chunk merging capability
- Multiple audio format support

### Debug Mode
- Shows detailed information about file processing
- Displays NCX parsing details for EPUB files
- Lists all found chapters and their metadata
- Helps troubleshoot processing issues

### Input Options
- Text file input (.txt)
- EPUB book input (.epub)
- Standard input (stdin)
- Supports piping from other programs

## Contributing

This is a personal project. But if you want to contribute, please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Kokoro-ONNX](https://github.com/thewh1teagle/kokoro-onnx)
