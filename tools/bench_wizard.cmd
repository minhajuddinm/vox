@echo off
rem Double-click to run the whole Vox benchmark (record, transcribe, compare the cleanup). See tools\bench_wizard.py.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo The project venv is missing. In this folder run:
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r windows\requirements.txt -r tests\requirements.txt
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\bench_wizard.py %*
pause
