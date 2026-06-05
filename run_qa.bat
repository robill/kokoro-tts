@echo off
setlocal enabledelayedexpansion

REM ─────────────────────────────────────────────────────────────────────────
REM  run_qa.bat  —  Generate audio from an interview Q&A markdown file
REM
REM  Usage:
REM    run_qa.bat <qa_script.md> [output.mp3]
REM
REM  Script format expected:
REM    ### Q1: question text           → Interviewer voice
REM    **Model Answer:** answer text   → Candidate voice
REM    **Follow-up Probe:** text       → Interviewer voice
REM
REM  Voice assignments (edit below to change):
REM    Interviewer  →  bm_lewis    (British male)
REM    Candidate    →  af_sarah    (US female)
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
    echo Usage: run_qa.bat ^<qa_script.md^> [output.mp3]
    exit /b 1
)

if "%OUTPUT%"=="" (
    set "OUTPUT=%~n1.mp3"
)

REM ── GPU acceleration ─────────────────────────────────────────────────────
echo Setting ONNX_PROVIDER=CUDAExecutionProvider for GPU...
set ONNX_PROVIDER=CUDAExecutionProvider

REM ── Voice map ────────────────────────────────────────────────────────────
set "VOICE_MAP=Interviewer=bm_lewis,Candidate=af_sarah"
set "DEFAULT_VOICE=af_heart"

REM ── Run ──────────────────────────────────────────────────────────────────
python podcast_tts.py "%SCRIPT%" "%OUTPUT%" ^
    --voice-map "%VOICE_MAP%" ^
    --default-voice "%DEFAULT_VOICE%" ^
    --abbr-map abbreviations.json ^
    --script-type qa ^
    --lang en-us ^
    --format mp3 ^
    --compute-type float16 ^
    --silence 600

endlocal
