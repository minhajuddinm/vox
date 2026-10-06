@echo off
rem Double-click to start Vox Tuner (speak, compare the old and new cleanup, approve one). See tools\vox_tuner.py.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo The project venv is missing. In this folder run:
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r windows\requirements.txt -r tests\requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\vox_tuner.py %*
pause
