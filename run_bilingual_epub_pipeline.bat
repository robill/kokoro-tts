@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Usage: run_bilingual_epub_pipeline.bat "paired.epub" <start> <end> [--plan|--no-pause]
if "%~1"=="" goto :usage
if "%~2"=="" goto :usage
if "%~3"=="" goto :usage

set "ROOT=%~dp0"
set "SOURCE=%~f1"
set "START=%~2"
set "END=%~3"
set "STEM=%~n1"
set "CACHE=%ROOT%%STEM%_chinese_preprocessed_%START%-%END%"
set "AUDIO=%ROOT%%STEM%_zf003_Misaki_Audio_%START%-%END%"
set "STUDY_EPUB=%ROOT%%STEM%_%START%-%END%_Simplified_Study.epub"
set "AUDIOBOOK=%ROOT%%STEM%_%START%-%END%_zf003_Misaki_Audiobook.mp3"
set "PYTHON=%ROOT%.venv_kokoro_061\Scripts\python.exe"
set "ZH_VOICE=zf_003"
set "EN_VOICE=af_heart"
set "ZH_SPEED=0.75"
set "EN_SPEED=1.0"
set "NO_PAUSE=0"

if /I "%~4"=="--plan" goto :plan
if /I "%~4"=="--no-pause" set "NO_PAUSE=1"

if not exist "%PYTHON%" (
    echo ERROR: Isolated Python environment not found: "%PYTHON%"
    goto :failed
)
if not exist "%SOURCE%" (
    echo ERROR: Source EPUB not found: "%SOURCE%"
    goto :failed
)
if not exist "%ROOT%models\kokoro-v1.1-zh.onnx" (
    echo ERROR: Full-precision v1.1 Chinese model is missing.
    goto :failed
)
if not exist "%ROOT%models\voices-v1.1-zh.bin" (
    echo ERROR: v1.1 Chinese voice bundle is missing.
    goto :failed
)
if not exist "%ROOT%kokoro-v1.0.onnx" (
    echo ERROR: English Kokoro v1.0 model is missing.
    goto :failed
)
if not exist "%ROOT%voices-v1.0.bin" (
    echo ERROR: English Kokoro v1.0 voices are missing.
    goto :failed
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
    echo ERROR: ffmpeg was not found on PATH.
    goto :failed
)
where ffprobe >nul 2>nul
if errorlevel 1 (
    echo ERROR: ffprobe was not found on PATH.
    goto :failed
)

rem Select CUDA only when the machine's matching CUDA/cuDNN runtime paths exist.
set "ONNX_PROVIDER="
if exist "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9\bin" if exist "C:\Program Files\NVIDIA\CUDNN\v9.14\bin\12.9" (
    set "PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9\bin;C:\Program Files\NVIDIA\CUDNN\v9.14\bin\12.9;%PATH%"
    set "ONNX_PROVIDER=CUDAExecutionProvider"
    echo GPU provider: CUDAExecutionProvider
) else (
    echo GPU runtime paths not found; ONNX Runtime will use its default provider.
)

echo.
echo Bilingual EPUB pipeline: chapters %START%-%END%
echo Source: "%SOURCE%"
echo Cache/reviews: "%CACHE%"
echo Chapter audio: "%AUDIO%"
echo Study EPUB: "%STUDY_EPUB%"
echo Single audiobook: "%AUDIOBOOK%"
echo Voices/speeds: Chinese %ZH_VOICE% at %ZH_SPEED%; English %EN_VOICE% at %EN_SPEED%
echo.

rem Stage 1: Reuse valid chapter caches and preprocess only missing/stale chapters.
echo [1/4] Preprocess Chinese and create review sheets...
"%PYTHON%" "%ROOT%chinese_llm_preprocess.py" "%SOURCE%" --start %START% --end %END% --output-dir "%CACHE%"
if errorlevel 1 goto :failed

if "%NO_PAUSE%"=="0" (
    echo.
    echo Review the chapter_NNNN\chinese_simplification_review.md files in:
    echo "%CACHE%"
    echo Press any key when ready to continue to study EPUB and audio generation.
    pause >nul
)

rem Stage 2: Export a separate study EPUB with written Hanzi/Pinyin/English glossaries.
echo [2/4] Export simplified study EPUB...
"%PYTHON%" "%ROOT%simplified_epub_export.py" "%SOURCE%" "%CACHE%" "%STUDY_EPUB%" --start %START% --end %END% --include-glossary --force
if errorlevel 1 goto :failed

rem Stage 3: Generate bilingual per-chapter MP3s plus spoken Hanzi/English vocabulary.
echo [3/4] Generate/resume per-chapter audio and vocabulary...
"%PYTHON%" "%ROOT%bilingual_epub_tts.py" "%SOURCE%" --start %START% --end %END% --preprocessed-dir "%CACHE%" --output-dir "%AUDIO%" --zh-voice %ZH_VOICE% --en-voice %EN_VOICE% --zh-speed %ZH_SPEED% --en-speed %EN_SPEED% --include-vocabulary-audio
if errorlevel 1 goto :failed

rem Stage 4: Concatenate chapters into one validated audiobook without re-encoding.
echo [4/4] Merge chapter MP3s into one audiobook...
"%PYTHON%" "%ROOT%merge_chapter_audio.py" "%AUDIO%" "%AUDIOBOOK%" --start %START% --end %END% --force
if errorlevel 1 goto :failed

echo.
echo SUCCESS: Bilingual EPUB pipeline completed.
echo Study EPUB: "%STUDY_EPUB%"
echo One-file audiobook: "%AUDIOBOOK%"
echo Per-chapter MP3s and resumable WAV checkpoints: "%AUDIO%"
if "%NO_PAUSE%"=="0" pause
exit /b 0

:plan
echo Planned pipeline: "%SOURCE%", chapters %START%-%END%
echo 1. Reuse/preprocess Chinese cache and create chapter review sheets.
echo 2. Pause for review, then export a study EPUB with written glossaries.
echo 3. Generate/resume bilingual chapter audio and spoken vocabulary.
echo 4. Merge numbered MP3s into one audiobook without re-encoding.
echo Cache: "%CACHE%"
echo Study EPUB: "%STUDY_EPUB%"
echo Audio: "%AUDIO%"
echo Audiobook: "%AUDIOBOOK%"
echo No files are created by plan mode.
exit /b 0

:usage
echo Usage: %~nx0 "paired-bilingual.epub" ^<start-chapter^> ^<end-chapter^> [--plan^|--no-pause]
echo Example: %~nx0 "D:\Books\MyBook.epub" 101 150
exit /b 2

:failed
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" set "RC=1"
echo.
echo ERROR: Pipeline stopped with exit code %RC%.
echo Completed caches and audio checkpoints remain available for resume.
if "%NO_PAUSE%"=="0" pause
exit /b %RC%
