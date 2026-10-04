@echo off
setlocal EnableExtensions
call "%~dp0run_bilingual_epub_pipeline.bat" "%~dp0RMJI Bilingual Chapters 0721-0770.epub" 721 770 %*
exit /b %ERRORLEVEL%
