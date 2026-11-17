@echo off
rem Double-click to launch the offline vocabulary app (no console window).
setlocal
set "PYW=C:\Users\17660\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=C:\Users\17660\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0vocab_app\main.py"
endlocal
