@echo off
setlocal enabledelayedexpansion
set "inputFile=%1"
set "baseName=%~n1"

:: Replace colons with underscores in the voice parameter for a valid filename
set "voiceSuffix=%voiceParam::=_%"

REM en-us female: af_aoede, af_bella, [af_heart], af_jessica, af_sarah, 
REM en-us male: am_echo
REM en-gb bf_isabella, bf_lily, bm_lewis
REM zf_xiaobei, zf_xiaoni, zf_xiaoxiao, zf_xiaoyi, zm_yunjian, zm_yunxi, zm_yunxia, zm_yunyang

if "%inputFile%"=="" (
	echo No input file specified. Running merge only.
	set /p "baseName=Enter base name for split directory: "
	python kokoro-tts --merge-chunks --split-output "%baseName%_chunks" --format mp3
) else (
	REM Generate split audio chunks with specified voice blend
	python kokoro-tts %inputFile% --split-output "!baseName!_chunks" --lang en-us --voice "af_nicole:70,bm_lewis:30" --format mp3

	REM Merge the split audio chunks into chapter files
	python kokoro-tts --merge-chunks --split-output "!baseName!_chunks" --format mp3
)
