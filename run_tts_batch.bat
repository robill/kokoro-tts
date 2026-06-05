@echo off
setlocal enabledelayedexpansion

rem Loop through all files matching the pattern
for %%f in (%1) do (
  set "inputFile=%%f"
  set "baseName=%%~nf"
  python -m kokoro_tts "!inputFile!" "!baseName!.mp3" --lang en-us --voice "af_nicole:70,bm_lewis:30" --format mp3 --compute-type float16
)

endlocal