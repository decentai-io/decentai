@echo off
rem DecentAI on this computer: what a person double-clicks.
rem The work is in DecentAI.ps1, beside this file.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0DecentAI.ps1" %*
set CODE=%ERRORLEVEL%
rem Opened by a double-click, the window closes with the program:
rem what went wrong is kept on the screen until a key is pressed.
if not "%CODE%"=="0" if "%~1"=="" pause
exit /b %CODE%
