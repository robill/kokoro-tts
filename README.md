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

After installation, you can run:
```bash
kokoro-tts --help
```

### Method 2: Install from Git

Install directly from the repository:

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
python -m venv .venv_kokoro_061
.\.venv_kokoro_061\Scripts\python.exe -m pip install "kokoro-onnx==0.6.1" "misaki-fork[zh]==0.9.6" beautifulsoup4 soundfile pydub
```

Install the ONNX Runtime GPU build matching the installed CUDA/cuDNN versions
if GPU execution is desired. Then, from the project root, select CUDA and run
the custom audio scripts with the isolated interpreter:

```powershell
$env:ONNX_PROVIDER = "CUDAExecutionProvider"
.\.venv_kokoro_061\Scripts\python.exe .\bilingual_epub_tts.py `
  ".\RMJI Bilingual Chapters 0721-0770.epub" `
  --start 721 --end 721 `
  --preprocessed-dir ".\RMJI_Bilingual_Chapter_0721_audio_narration_preprocessed" `
  --output-dir ".\RMJI_Chapter_0721_misaki_g2p_audio" `
  --zh-voice zf_003 --en-voice af_heart `
  --zh-speed 0.75 --en-speed 1.0
```

If recreating this environment, install `beautifulsoup4` there as well; install
the ONNX Runtime GPU package matching the machine's CUDA/cuDNN versions if GPU
execution is desired.

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

To create a separate review track for the chapter vocabulary, synthesize each
Hanzi term in Mandarin followed by its English meaning. Pinyin stays in the
written review sheet and is not spoken:

```powershell
.\.venv_kokoro_061\Scripts\python.exe .\vocabulary_audio.py `
  ".\RMJI_Bilingual_Chapter_0721_audio_narration_preprocessed\chapter_0721\chinese_simplification.json" `
  ".\RMJI_Bilingual_Chapter_0721_audio_narration_preprocessed\chapter_0721\vocabulary_0721.mp3" `
  --zh-voice zf_003 --en-voice af_heart --zh-speed 0.75 --en-speed 1.0
```

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
