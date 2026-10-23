@echo off
rem Genera dist\CrossChannel_MultiTool_GUI.exe (requiere: pip install -r requirements.txt)
cd /d "%~dp0"
pyinstaller --noconfirm --onefile --windowed --name CrossChannel_MultiTool_GUI --collect-data customtkinter crosschannel_gui.py
if errorlevel 1 (
    echo Error al generar el ejecutable.
    pause
    exit /b 1
)
echo.
echo Listo: dist\CrossChannel_MultiTool_GUI.exe
pause
