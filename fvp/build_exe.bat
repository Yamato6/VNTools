@echo off
rem Genera dist\FVP_Tools_GUI.exe (requiere: pip install -r requirements.txt)
cd /d "%~dp0"
pyinstaller --noconfirm --onefile --windowed --name FVP_Tools_GUI --collect-data customtkinter --exclude-module numpy fvp_gui.py
if errorlevel 1 (
    echo Error al generar el ejecutable.
    pause
    exit /b 1
)
echo.
echo Listo: dist\FVP_Tools_GUI.exe
pause
