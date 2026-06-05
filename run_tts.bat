@echo off
setlocal enabledelayedexpansion
set "inputFile=%1"
set "voiceParam=%2"
set "baseName=%~n1"

:: Replace colons with underscores in the voice parameter for a valid filename
set "voiceSuffix=%voiceParam::=_%"

REM en-us female: af_aoede, af_bella, [af_heart], af_jessica, af_sarah, 
REM en-us male: am_echo
REM en-gb bf_isabella, bf_lily, bm_lewis
REM zf_xiaobei, zf_xiaoni, zf_xiaoxiao, zf_xiaoyi, zm_yunjian, zm_yunxi, zm_yunxia, zm_yunyang

REM python kokoro-tts %inputFile% !baseName!_!voiceSuffix!.mp3 --lang en-us --voice %2 --format mp3
REM python kokoro-tts %1 --stream --lang en-us --voice %2
REM python kokoro-tts %1 --stream --lang en-us --voice "af_nicole:60,am_adam:40"
REM python kokoro-tts %1 --lang en-us --voice af_heart --split-output ./chunks/ --format mp3
REM python kokoro-tts %1 output.mp3 --lang en-us --voice bm_lewis --format mp3
REM python kokoro-tts %1 output.mp3 --lang en-us --voice am_echo --format mp3
REM python kokoro-tts %1 output.mp3 --lang en-us --voice am_echo --format mp3
REM python kokoro-tts %1 output.mp3 --lang en-us --voice "af_nicole:60,am_adam:40" --format mp3
REM python kokoro-tts --merge-chunks --split-output ./chunks/ --format mp3
REM python kokoro-tts %inputFile% "!baseName!.mp3" --lang en-us --voice "af_nicole:70,bm_lewis:30" --format mp3
REM python kokoro-tts %inputFile% "!baseName!.mp3" --lang en-us --voice "af_nicole:70,bm_lewis:30" --format mp3 --background "beach-waves(chosic.com).mp3"
REM python -m kokoro_tts %inputFile% "!baseName!.wav" --lang en-us --voice "af_nicole:70,bm_lewis:30"
python -m kokoro_tts %inputFile% "!baseName!.mp3" --lang en-us --voice "af_nicole:70,bm_lewis:30" --format mp3 --compute-type float16
