@echo off
rem Starts Analyst-in-a-Box and opens the browser. Close this window to stop the server.
set VIRTUAL_ENV=
cd /d "%~dp0"
title Analyst-in-a-Box
uv run analyst-in-a-box %*
if errorlevel 1 pause
