@echo off
setlocal
cd /d "%~dp0"
title Vox installer
REM Builds Vox.exe on this PC and installs it to %LOCALAPPDATA%\Programs\Vox
set "APPDIR=%LOCALAPPDATA%\Programs\Vox"
set "WORK=%LOCALAPPDATA%\Vox\build"
set "VENV=%LOCALAPPDATA%\Vox\venv"

set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe""
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY ( echo Python not found. Install it from python.org. & pause & exit /b 1 )

echo [1/5] Preparing Python packages...
if not exist "%VENV%\Scripts\python.exe" ( %PY% -m venv "%VENV%" || goto :fail )
"%VENV%\Scripts\python.exe" -m pip install -q --upgrade pip || goto :fail
"%VENV%\Scripts\python.exe" -m pip install -q -r requirements.txt pyinstaller || goto :fail

echo [2/5] Closing any running Vox...
taskkill /f /im Vox.exe >nul 2>&1
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='pythonw.exe' or name='python.exe'\" | Where-Object { $_.CommandLine -match 'vox(_app)?\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

echo [3/5] Building Vox.exe (1-3 minutes)...
set "GADD="
if exist "%~dp0google_client.json" set GADD=--add-data "%~dp0google_client.json;."
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --clean --log-level WARN --onedir --windowed --name Vox ^
  --icon "%~dp0vox.ico" --add-data "%~dp0ui;ui" --add-data "%~dp0vox.ico;." ^
  --hidden-import pystray._win32 --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 ^
  --paths "%~dp0..\relay" --hidden-import relay ^
  --collect-data soundcard --collect-data tzdata --collect-submodules recurring_ical_events --hidden-import soundfile --collect-binaries _soundfile_data --collect-data _soundfile_data --exclude-module PIL._avif --exclude-module PIL.AvifImagePlugin --exclude-module PIL._webp --exclude-module PIL.WebPImagePlugin %GADD% ^
  --distpath "%WORK%\dist" --workpath "%WORK%\work" --specpath "%WORK%" "%~dp0vox_app.py" || goto :fail

echo [4/5] Installing to %APPDIR%...
if not exist "%APPDATA%\Vox" mkdir "%APPDATA%\Vox"
if not exist "%APPDATA%\Vox\config.json" if exist "%~dp0config.json" copy /y "%~dp0config.json" "%APPDATA%\Vox\config.json" >nul
robocopy "%WORK%\dist\Vox" "%APPDIR%" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Programs')+'\Vox.lnk'); $s.TargetPath='%APPDIR%\Vox.exe'; $s.IconLocation='%APPDIR%\Vox.exe,0'; $s.Description='Vox voice dictation'; $s.Save()"
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v Vox /t REG_SZ /d "\"%APPDIR%\Vox.exe\"" /f >nul

echo [5/5] Starting Vox...
start "" "%APPDIR%\Vox.exe"
echo.
echo Done. Vox is in the Start menu and starts with Windows (turn that off in Vox ^> Settings).
timeout /t 6 >nul
exit /b 0

:fail
echo.
echo Build failed. Take a screenshot of this window and send it to Claude.
pause
exit /b 1
