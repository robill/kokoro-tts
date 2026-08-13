@echo off
setlocal enabledelayedexpansion

REM Create timestamped log file
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c%%a%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set mytime=%%a%%b)
set "logfile=batch_process_%mydate%_%mytime%.log"

echo.
echo Batch TTS Processing with Auto-Validation
echo Log file: %logfile%
echo.

REM Track file status
setlocal enabledelayedexpansion
set "total_files=0"
set "successful_files=0"
set "failed_files=0"
set "retry_files="

REM Count total files
for %%f in (%1) do (
  set /a total_files+=1
)

if %total_files% equ 0 (
  echo ERROR: No files matching pattern: %1
  exit /b 1
)

echo Processing %total_files% file(s)...
echo.

REM Process each file
set "file_index=0"
for %%f in (%1) do (
  set /a file_index+=1
  set "inputFile=%%f"
  set "baseName=%%~nf"
  
  (
    echo.
    echo ============================================================
    echo [!file_index!/%total_files%] Processing: !inputFile!
    echo ============================================================
    
    REM Generate split audio chunks
    echo.
    echo [STEP 1/3] Generating audio chunks...
    python -m kokoro_tts "!inputFile!" --split-output "!baseName!_chunks" --lang en-us --voice "af_nicole:70,bm_lewis:30" --format mp3 --compute-type float16
    
    if !errorlevel! equ 0 (
      echo.
      echo [STEP 2/3] Validating chapters...
      python .\validate_chunks.py "!baseName!_chunks"
      
      if !errorlevel! equ 0 (
        echo.
        echo [STEP 3/3] Merging into complete sequential file...
        python .\merge_sequential_chunks.py "!baseName!_chunks" --format mp3 -o "!baseName!_complete_sequential.mp3"
        
        if !errorlevel! equ 0 (
          echo.
          echo ============================================================
          echo SUCCESS: !baseName!_complete_sequential.mp3 created
          echo ============================================================
          set /a successful_files+=1
        ) else (
          echo.
          echo ERROR: Failed to merge chunks
          echo ============================================================
          set /a failed_files+=1
          set "retry_files=!retry_files! !baseName!"
        )
      ) else (
        echo.
        echo WARNING: Some chapters have errors - needs retry
        echo ============================================================
        set /a failed_files+=1
        set "retry_files=!retry_files! !baseName!"
      )
    ) else (
      echo.
      echo ERROR: TTS processing failed
      echo ============================================================
      set /a failed_files+=1
      set "retry_files=!retry_files! !baseName!"
    )
  ) >> "%logfile%" 2>&1
)

REM Print summary to both console and log
(
  echo.
  echo.
  echo ============================================================
  echo BATCH PROCESSING COMPLETE
  echo ============================================================
  echo Total files: %total_files%
  echo Successful: %successful_files%
  echo Failed: %failed_files%
  echo.
  
  if %failed_files% gtr 0 (
    echo Files needing retry:%retry_files%
    echo.
    echo To retry failed files, run:
    echo   .\run_tts_batch_split_merge.bat "%retry_files:~1%*"
    echo.
  )
  
  echo Log file: %logfile%
  echo ============================================================
) | tee -a "%logfile%"

REM Display summary on console
echo.
echo ============================================================
echo BATCH PROCESSING COMPLETE
echo ============================================================
echo Total files: %total_files%
echo Successful: %successful_files%
echo Failed: %failed_files%
echo Log file: %logfile%

if %failed_files% gtr 0 (
  echo.
  echo Files needing retry:%retry_files%
  echo.
  set /p retry_prompt="Do you want to retry failed files now? (Y/N): "
  if /i "!retry_prompt!"=="Y" (
    echo Retrying...
    call :retry_files%retry_files%
  )
)

echo ============================================================
endlocal
exit /b 0

:retry_files
  for %%f in (%*) do (
    set "retryFile=%%f*"
    call :process_retry "!retryFile!"
  )
  goto :eof

:process_retry
  echo Retrying: %~1
  goto :eof
