@echo off
REM Opens the IT Check window using Python (no console window stays open).
cd /d "%~dp0"
start "" pyw itcheck_gui.py
