# EPUB-to-audiobook workflow

Plan audio duration from the EPUB text, optionally split the EPUB, generate recoverable audio chunks, and merge verified chunks in reading order. Examples use Windows PowerShell, the project `venv`, and the English voice blend used for *Esoteric Astrology*.

## 1. Inspect and plan

1. Keep the original EPUB unchanged. Decide whether to narrate front matter, navigation pages, tables of contents, and other non-story content.
2. Inspect EPUB split points using the EpubSplit checkout, if available:

   ```powershell
   & 'D:\Project\EpubSplit\venv\Scripts\python.exe' 'D:\Project\EpubSplit\epubsplit.py' 'D:\Project\EpubSplit\book.epub'
   ```

   These are split points, not necessarily logical chapters. Compare their order and content with the source EPUB, especially the first and last sections.
3. Measure the text that Kokoro will extract. From the Kokoro project directory:

   ```powershell
   $env:PYTHONIOENCODING = 'utf-8'
   @'
   import contextlib, io
   from kokoro_tts import extract_chapters_from_epub

   path = r'D:\Project\EpubSplit\book.epub'
   with contextlib.redirect_stdout(io.StringIO()):
       sections = extract_chapters_from_epub(path)
   for number, section in enumerate(sections, 1):
       print(f"{number:03d}  {len(section['content'].split()):6d} words  {section['title'].replace(chr(10), ' ')}")
   print('Sections:', len(sections))
   print('Words:', sum(len(section['content'].split()) for section in sections))
   '@ | & '.\venv\Scripts\python.exe' -
   ```

   Kokoro normally follows the EPUB table of contents; headings and document titles are a fallback. Check the extracted list against the source: repeated titles may refer to different text sections, and navigation or table-of-contents text may be included.
4. Estimate duration using a comparable completed audiobook with the same voice, speed and language:

   `estimated hours = new extracted words / reference extracted words * reference audio hours`

   Compare the estimate with the desired part length. One part is fine if it is already close; otherwise, divide at section boundaries using accumulated word counts rather than assuming equal section counts imply equal durations. This is only an estimate: pauses, text structure and voice settings affect the actual time.

### Reference example

The original *Esoteric Astrology* EPUB yielded approximately 200,018 extracted words; the five previously generated audio parts totalled approximately 26.5 hours. Its four main parts averaged about 6.5 hours, with a much shorter fifth remainder. Coverage of those historical outputs was not independently verified, so this is a rough reference, not a correctness benchmark.

*Education in the New Age* yielded 51,495 words in 46 extracted sections, including a preface and table of contents. The same rough ratio estimates about 6.8 hours as one part. Alternatively, extracted sections 1-23 and 24-46 contain approximately 24,695 and 26,800 words, or about 3.3 and 3.6 hours respectively. These are proposed reading-order groups, not verified EpubSplit command arguments.

## 2. Split only if needed

Use a splitting method that preserves the desired source reading order. The separate EpubSplit repository contains `split_epub_chapters.ps1`, but it groups parsed numeric IDs and assumes the EpubSplit line number is the chapter number plus one. That assumption is book-specific: it may omit or misassign sections. Its default start filter can also retain chapter zero. Do not rely on filenames or chapter counts alone.

Before generating audio, inspect each produced EPUB's ordered split points and compare its section contents with the source. Confirm that every intended section occurs exactly once across all parts, in order, including the first and last sections. Preserve or deliberately exclude front matter and navigation content. Confirm Kokoro's extracted section list for each part separately; its extraction order and boundaries need not match EPUB split-point numbering.

## 3. Generate recoverable chunks

From the Kokoro project directory, with the project environment and model/voice files installed, use a unique output directory for each EPUB part:

```powershell
& '.\venv\Scripts\python.exe' -m kokoro_tts '.\book-part.epub' --split-output '.\book-part_chunks' --lang en-us --voice 'af_nicole:70,bm_lewis:30' --format mp3 --compute-type float16 *> '.\book-part_tts.log'
$LASTEXITCODE
```

The command records console output to the log and leaves individual chunks on disk. A successful process exit does not prove that every chunk was generated: inspect the log for errors and incomplete `Completed ...` counts. Do not reuse a checkpoint directory after changing the EPUB, extraction/chunking code, voice, model, speed or other relevant settings; existing files are skipped by name, not checked against those settings.

The project's `run_tts_batch_split_merge.bat` attempts batch generation, validation, merging, logging and a retry prompt. **Do not rely on it for unattended recovery in its current form:** the retry routine only prints a message rather than regenerating audio, it uses an external `tee` command that may not be installed, and its filename handling and timestamps have Windows/locale limitations. Use the individual steps here and review each log instead.

## 4. Check and repair

Run the existing MP3 checks, then compare against the expected source:

```powershell
& '.\venv\Scripts\python.exe' '.\validate_chunks.py' '.\book-part_chunks' --show-all
& '.\venv\Scripts\python.exe' '.\merge_sequential_chunks.py' '.\book-part_chunks' --format mp3 --dry-run
```

These are useful diagnostics, **not proof of completeness**. The validator checks readable MP3s and interior numbering gaps, but can report an empty folder as complete and cannot detect missing first or last chunks, missing chapter folders, or compare reliably with the EPUB's expected chunk counts. The merge dry run checks audio readability and interior gaps, but likewise cannot identify missing beginning/end sections without an expected manifest. Neither check detects incorrect narration or a valid but wrong audio file.

For stronger verification, compare the expected chunk count for every Kokoro-extracted section to the actual numbered files. Use the same input EPUB and code version used to generate them:

```powershell
@'
import contextlib, io
from pathlib import Path
from kokoro_tts import extract_chapters_from_epub, chunk_text

book = Path('book-part.epub')
output = Path('book-part_chunks')
with contextlib.redirect_stdout(io.StringIO()):
    sections = extract_chapters_from_epub(str(book))
for number, section in enumerate(sections, 1):
    folder = output / f'chapter_{number:03d}'
    expected = {f'chunk_{i:03d}.mp3' for i in range(1, len(chunk_text(section['content'], initial_chunk_size=1000)) + 1)}
    actual = {p.name for p in folder.glob('chunk_*.mp3')} if folder.is_dir() else set()
    if expected != actual:
        print(f'{folder}: missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}')
print(f'Compared {len(sections)} expected sections; also inspect for unexpected chapter folders.')
'@ | & '.\venv\Scripts\python.exe' -
```

Also inspect for unexpected chapter folders and listen to representative chunks and section boundaries. To retry a missing chunk, rerun the same generation command with the same EPUB and settings. For a corrupt or incorrect existing chunk, first back it up or move it outside the chunk directory; Kokoro skips existing files without checking their audio. Rerun generation, then repeat validation and comparison. Deleting or replacing a whole chapter directory is a way to regenerate that section, but keep a backup until the replacement is verified.

## 5. Merge and verify

Once the expected sections and chunks are verified, merge directly from the numbered folders; the built-in `--merge-chunks` instead produces individual chapter files, sometimes named by title.

```powershell
& '.\venv\Scripts\python.exe' '.\merge_sequential_chunks.py' '.\book-part_chunks' --format mp3 -o '.\book-part_complete_sequential.mp3'
```

The merge sorts chapter and chunk numbers numerically and checks sample rate and channel consistency. It loads all decoded audio into memory and re-encodes the MP3, so long books need sufficient RAM and disk space. Avoid overwriting an existing final file unintentionally. Compare the output duration with the sum of input chunk durations, listen to the beginning, section boundaries and ending, and retain the source EPUB, logs and chunks until the finished audio has been reviewed. When using multiple EPUB parts, repeat the checks for each part; a single full-book merge requires an additional ordered merge of the verified parts.