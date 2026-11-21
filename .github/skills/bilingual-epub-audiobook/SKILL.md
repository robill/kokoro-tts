---
name: bilingual-epub-audiobook
description: Prepare or split a bilingual EPUB, validate chapter pairing, preprocess and review Chinese narration, export a study EPUB, generate Chinese/English audio with an optional spoken glossary, and merge chapters into one audiobook.
---

# Bilingual EPUB Audiobook Workflow

Use this skill when the user wants to process a new Chinese/English EPUB from source preparation through a study EPUB and combined audiobook. Execute the workflow, not just explain it. Stop at review/approval gates and report concrete validation results.

## Safety and user decisions

- Treat the original EPUB as read-only. Never overwrite, rename, or delete the source.
- Do not read, print, log, or ask the user to paste `.env` secrets. Check only whether required variable names are configured. The preprocessor validates that `VIO_BASE_URL` is HTTPS.
- Before sending book text to VIO, ensure the user has authorized this endpoint for that source. If not clear, ask before the first request.
- Do not start full audio until the user approves the simplification review, unless they explicitly request unattended processing.
- Never delete old outputs to resolve checkpoint conflicts. Choose a fresh output root or ask first.
- Do not force-overwrite an existing merged audiobook without user approval. A study EPUB overwrite is protected by its exporter but should still be intentional.

## Required inputs and preconditions

Obtain or infer:

1. The source EPUB path.
2. Inclusive chapter range, or confirmation to process all chapters.
3. Whether the source is already the paired bilingual split EPUB or must first be split/prepared.
4. Permission to use the configured VIO endpoint for this text.

The existing parser expects one paired XHTML file per chapter:

- A member matching `NNNN_<chapter>_<title>_split_000.xhtml`, containing the Chinese chapter heading.
- A matching `NNNN_<chapter>_<title>_split_001.xhtml`, containing the English heading and body paragraphs classified as Chinese or English.
- A complete contiguous chapter-number range. Every selected chapter must have both files.

The paired source commonly interleaves original Chinese and English paragraphs; the parser classifies each by Hanzi/Latin content and skips punctuation-only paragraphs. For the requested study/audio presentation, the target order is **English translation(s), original Chinese, A2 Chinese rewrite** for each aligned passage. This is not the same as source order. The exporter safely refuses Chinese paragraphs with nested inline tags rather than silently dropping that markup.

Paragraph counts may differ between the source languages, and a translated paragraph can cover multiple source paragraphs or vice versa. Never align solely by equal indices or paragraph counts. Create an explicit per-chapter alignment map that assigns every English paragraph exactly once to one source-Chinese paragraph group; each source paragraph maps to exactly one A2 paragraph. Review the paired text and flag uncertain boundaries for the user rather than guessing.

## Stage 1: Locate and inspect the source

1. Confirm the input is a readable, non-DRM EPUB and is the intended language edition.
2. Inspect the ZIP member list, OPF/TOC, and sample chapter XHTML read-only. Determine whether it already satisfies the paired-file structure above.
3. If it is already a prepared paired EPUB, use it directly and do not split it again.
4. If splitting is required, find the user's EpubSplit checkout and inspect its `split_epub_chapters.ps1` parameters and `epubsplit.py` behavior first. The script accepts source, start chapter, end chapter, chapters per output, and title pattern. Run `python epubsplit.py <source.epub>` to inspect possible split points before choosing boundaries.
5. Do not assume a novel's displayed chapter number equals an EPUB spine/split-line index. Map the requested TOC headings to actual split points by checking titles/content, then run the supplied splitter into a new staging directory. Never guess offsets. The splitter divides at EPUB boundaries; it does not create missing translations or turn a Chinese-only book into bilingual paragraphs.
6. Inspect the resulting EPUB(s): ensure every requested chapter has exactly one `split_000`/`split_001` pair and that the body has the expected Chinese and English paragraphs. Record the actual chapter numbers for later stages.
7. If the output layout is incompatible or the range mapping is ambiguous, stop and show the detected structure; ask the user how to map it rather than generating a misleading sample.

## Stage 2: Preflight the TTS environment

Run from the Kokoro project root. Verify, without displaying credentials:

- On a fresh Windows checkout, run `setup_bilingual_audio_env.ps1 -Provider cuda` (or `-Provider cpu`) to build `.venv_kokoro_061` from `requirements-bilingual-audio.lock.txt`, fetch/checksum model assets, and smoke-test both language models. For nonstandard CUDA/cuDNN DLL folders, pass `-CudaBin` and `-CudnnBin`.
- The tested runtime is CPython 3.11.9 x64, `kokoro-onnx==0.6.1`, and `misaki-fork[zh]==0.9.6`. Keep the upstream CLI venv (Kokoro 0.3.9) separate from the bilingual-audio venv (0.6.1).
- For splitting, use the checked-out EpubSplit utility with its pinned `beautifulsoup4`/`six` dependencies; the lock file includes both and the skill recommends placing the isolated venv's `Scripts` directory first on `PATH` when invoking the PowerShell wrapper.
- `.venv_kokoro_061\Scripts\python.exe` imports the pinned Kokoro/Misaki packages, `beautifulsoup4`, `openai`, and `python-dotenv`.
- Full-precision `models/kokoro-v1.1-zh.onnx` and `models/voices-v1.1-zh.bin` exist. Do not use the FP16 Chinese model; it produced NaNs in testing.
- English `kokoro-v1.0.onnx` and `voices-v1.0.bin` exist.
- `ffmpeg` and `ffprobe` are on PATH.
- `.env` contains the required VIO settings. Never echo their values.

The setup script downloads the Chinese v1.1 files from the official Kokoro ONNX
model-files release and the English v1.0 files from the upstream Kokoro TTS
release. It verifies SHA-256 checksums listed in `setup_bilingual_audio_env.ps1`.
The v1.1 Chinese `config.json` is optional for this tested pipeline because the
model embeds the vocabulary used by Misaki.

Use Chinese voice `zf_003`, English voice `af_heart`, Chinese speed `0.75`, English speed `1.0` by default. The Chinese route must be Misaki `ZHG2P(version="1.1")` followed by Kokoro `is_phonemes=True`; do not use the old v1.0 Chinese tokenizer.

## Stage 3: Dry-run the selected range

Run `bilingual_epub_tts.py` with the candidate EPUB, selected `--start`/`--end`, and `--dry-run`. Confirm:

- All requested chapter numbers are present, without gaps.
- Titles and language tags look right; each chapter has Chinese and English content as expected.
- The tool did not silently choose a different chapter range.

If any chapter is missing, mispaired, or language-tagged incorrectly, stop and repair/re-split the source before preprocessing.

## Stage 4: Preprocess and review Chinese

Use a unique cache directory derived from the source stem and selected range. Run `chinese_llm_preprocess.py` for that range. The script reuses only caches with the same source hash, prompt version, and model; stale caches are regenerated. The cache contains A2 Chinese narration and glossary; original Chinese and English translations remain in the paired source EPUB. Report how many chapters were reused versus sent to VIO, and the paragraph/glossary counts.

Before audio generation:

- Check each chapter has a JSON cache and a side-by-side review Markdown.
- Review every chapter's narration for paragraph alignment, names, plot meaning, and Chinese-only speech text; vocabulary must remain separate.
- Summarize any anomalies or suspected LLM errors with chapter numbers.
- Present the review directory to the user and wait for approval. Do not treat the batch file's keypress pause as semantic user approval if running the steps through an agent.

## Stage 5: Export the study EPUB

After review approval and alignment-map validation, run the English-first exporter/generator for the requested chapter. For Chapter 745, `english_first_chapter.py` consumes a chapter-only paired EPUB plus `alignment.json`, reuses the A2 cache and matching old English/A2/glossary WAVs, synthesizes the original Chinese, and writes a new one-chapter study EPUB/audio output. It does not modify the source EPUB or previous outputs.

The existing `simplified_epub_export.py` is the legacy A2-only transformation; do not use it when the user requests English-first triplets containing original Chinese. For a selected range, prepare one source-hash-bound alignment map and human-readable review per chapter. `run_english_first_range.py` creates chapter-only paired EPUBs plus a range EPUB, exports the new English-first study EPUB, renders chapter audio, and merges only the selected chapters. Give it a fresh output root; it refuses to overwrite an existing one. The chapter-745 map records that English paragraphs 23 and 24 together translate original Chinese paragraph 23; other paragraphs map one-to-one in order. The exporter validates source hashes/counts, includes the written Hanzi/Pinyin/English glossary, and checks ZIP integrity. Keep outputs distinct from source and prior outputs.

## Stage 6: Generate bilingual audio and spoken vocabulary

For the legacy interleaved bilingual mode, `bilingual_epub_tts.py` uses:

- `--preprocessed-dir <cache-root>`
- `--output-dir <fresh-or-matching-audio-root>`
- `--zh-voice zf_003 --en-voice af_heart`
- `--zh-speed 0.75 --en-speed 1.0`
- `--include-vocabulary-audio`

Vocabulary audio speaks each Hanzi term with the Mandarin G2P path, then its English meaning with the English voice. Pinyin remains unspoken. Per-segment and per-vocabulary WAV checkpoints allow interrupted chapters to resume. Existing chapter manifests must match the source/cache, voices, speeds, and synthesis backend; if not, select a fresh audio output root rather than deleting checkpoints.

For English-first triplets, use `english_first_chapter.py` with an explicit alignment file, or `run_english_first_range.py` for a selected contiguous range. It reuses exact matching English/A2 and glossary WAV checkpoints from prior chapter manifests, synthesizes original Chinese paragraphs when no compatible checkpoint exists, and assembles each passage as English, original Chinese, A2 Chinese. Do not combine pre-existing chapters 721–744 with a new-order sample unless the user asks for consistent ordering across the whole audiobook.

Insert a short Mandarin cue (`简化版`) between original Chinese and A2 Chinese so listeners can distinguish the versions. Synthesize the cue once and reuse it between triplets; record the cue text, voice, speed, and repetition count in the manifest.

The generic Windows orchestrator `run_bilingual_epub_pipeline.bat <paired-epub> <start> <end>` still runs the legacy interleaved narration. Do not use it for the new English-first triplet order until it supports alignment maps. For English-first, process one chapter at a time with the reviewed alignment file; merge only chapters generated with the same ordering policy. Use `--no-pause` only after review approval.

## Stage 7: Merge and validate

Run `merge_chapter_audio.py <audio-root> <new-output.mp3> --start <n> --end <m>` only after every chapter MP3 is complete. The merger requires the full contiguous range, checks codec/sample-rate/channel agreement, concatenates by numeric chapter order via stream copy (no re-encoding), and fully decodes the final result.

Finally verify:

- The selected range has exactly one chapter MP3 and manifest per chapter.
- Every manifest records `zf_003`, `af_heart`, requested rates, Misaki G2P 1.1, ordering policy/alignment, and whether vocabulary audio was appended.
- Narration and glossary WAV checkpoint counts match each validated cache; all WAVs contain finite, non-empty 24-kHz audio.
- The single audiobook is a decodable mono 24-kHz MP3 whose duration is close to the sum of chapter durations.
- The study EPUB opens with an EPUB parser and has the same selected Chinese paragraph/glossary counts.
- The original EPUB hash is unchanged.

Report final file paths, chapter range, cache/review path, audio settings, whether VIO was called/reused, validation results, and any remaining anomalies. Keep generated EPUB/audio outputs local unless the user specifically requests otherwise.
