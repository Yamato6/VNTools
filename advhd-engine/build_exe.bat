@echo off
rem Genera dist\AdvHD_Tools_GUI.exe (requiere: pip install -r requirements.txt)
cd /d "%~dp0"
pyinstaller --noconfirm --onefile --windowed --name AdvHD_Tools_GUI --collect-data customtkinter --exclude-module fontTools advhd_gui.py
if errorlevel 1 (
    echo Error al generar el ejecutable.
    pause
    exit /b 1
)
echo.
echo Listo: dist\AdvHD_Tools_GUI.exe
pause
