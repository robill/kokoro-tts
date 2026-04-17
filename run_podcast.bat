@echo off
setlocal enabledelayedexpansion

REM ─────────────────────────────────────────────────────────────────────────
REM  run_podcast.bat  —  Generate an audio podcast from a markdown script
REM
REM  Usage:
REM    run_podcast.bat <script.md> [output.mp3]
REM
REM  The script must be in the format produced by the podcast script generator,
REM  with speakers marked as  **Host A:**  and  **Host B:**  etc.
REM
REM  Voice assignments (edit below to change):
REM    Host A  →  af_nicole:70,bm_lewis:30   (female-led blend)
REM    Host B  →  bm_lewis                   (British male)
REM    Both    →  af_heart                   (default fallback)
REM
REM  Available en-us female : af_aoede, af_bella, af_heart, af_jessica,
REM                           af_nicole, af_sarah
REM  Available en-us male   : am_adam, am_echo, am_michael
REM  Available en-gb female : bf_emma, bf_isabella, bf_lily
REM  Available en-gb male   : bm_daniel, bm_george, bm_lewis
REM ─────────────────────────────────────────────────────────────────────────

set "SCRIPT=%~1"
set "OUTPUT=%~2"

if "%SCRIPT%"=="" (
    echo Usage: run_podcast.bat ^<script.md^> [output.mp3]
    exit /b 1
)

REM Derive default output name from script if not provided
if "%OUTPUT%"=="" (
    set "OUTPUT=%~n1.mp3"
)

REM ── GPU acceleration ─────────────────────────────────────────────────────
echo Setting ONNX_PROVIDER=CUDAExecutionProvider for GPU...
set ONNX_PROVIDER=CUDAExecutionProvider

REM ── Voice map: edit these to change who speaks what ──────────────────────
set "VOICE_MAP=Host A=af_sarah,Host B=bm_lewis"
set "DEFAULT_VOICE=af_heart"

REM ── Run ──────────────────────────────────────────────────────────────────
python podcast_tts.py "%SCRIPT%" "%OUTPUT%" ^
    --voice-map "%VOICE_MAP%" ^
    --default-voice "%DEFAULT_VOICE%" ^
    --abbr-map abbreviations.json ^
    --lang en-us ^
    --format mp3 ^
    --silence 500

endlocal
