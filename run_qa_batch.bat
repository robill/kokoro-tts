@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Generate interview Q&A audio for all matching markdown files.
REM Usage: run_qa_batch.bat

if /I "%~1"=="--help" goto :usage
if /I "%~1"=="/?" goto :usage

set "ROOT=%~dp0"
pushd "%ROOT%" || exit /b 1

REM Ensure run_qa.bat uses this project's virtual environment.
set "PATH=%ROOT%venv\Scripts;%PATH%"

set /a PROCESSED=0
set /a FAILED=0

for %%F in ("*_interview_qa.md") do (
    if exist "%%~F" (
        set /a PROCESSED+=1
        echo.
        echo ================================================================
        echo Generating audio for %%~nxF
        echo ================================================================
        call "%ROOT%run_qa.bat" "%%~F"
        if errorlevel 1 (
            echo FAILED: %%~nxF
            set /a FAILED+=1
        ) else (
            echo Complete: %%~nF.mp3
        )
    )
)

echo.
echo Processed: %PROCESSED% file(s), failures: %FAILED%
popd

if not "%FAILED%"=="0" exit /b 1
exit /b 0

:usage
echo Usage: run_qa_batch.bat
echo Generates MP3 files for *_interview_qa.md files in this folder.
exit /b 0