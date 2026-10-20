@echo off
REM Builds ITCheck.exe, a single double-click app with no console window.
REM Run this on Windows with Python installed. The finished file is dist\ITCheck.exe.

cd /d "%~dp0"
py -m pip install --upgrade pyinstaller
py -m PyInstaller --onefile --windowed --clean --noconfirm --name ITCheck itcheck_gui.py

echo.
echo Done. Your app is dist\ITCheck.exe
pause
