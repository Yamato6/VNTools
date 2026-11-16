@echo off
rem Genera dist\KiriKiri_Tools_GUI.exe (requiere: pip install -r requirements.txt y gcc)
cd /d "%~dp0"
rem TLG nativo: sin gcc el .exe usa la versión en Python (mucho más lenta)
where gcc >nul 2>nul
if errorlevel 1 (
    echo Aviso: no se encontro gcc; se usara _krkr.dll si ya existe.
) else (
    gcc -O2 -shared -static-libgcc -o _krkr.dll _krkr.c
    if errorlevel 1 (
        echo Error al compilar _krkr.dll.
        pause
        exit /b 1
    )
)
set ADD_DLL=
if exist _krkr.dll set ADD_DLL=--add-binary "_krkr.dll;."
pyinstaller --noconfirm --onefile --windowed --name KiriKiri_Tools_GUI --collect-data customtkinter --exclude-module fontTools %ADD_DLL% krkr_gui.py
if errorlevel 1 (
    echo Error al generar el ejecutable.
    pause
    exit /b 1
)
echo.
echo Listo: dist\KiriKiri_Tools_GUI.exe
pause
