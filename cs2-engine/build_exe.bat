@echo off
rem Genera dist\CS2_Tools_GUI.exe (requiere: pip install -r requirements.txt y gcc)
cd /d "%~dp0"
rem Blowfish y HG-3 nativos: sin gcc el .exe usa la versión en Python (mucho más lenta)
where gcc >nul 2>nul
if errorlevel 1 (
    echo Aviso: no se encontro gcc; se usara _cs2.dll si ya existe.
) else (
    gcc -O2 -shared -static-libgcc -o _cs2.dll _cs2.c
    if errorlevel 1 (
        echo Error al compilar _cs2.dll.
        pause
        exit /b 1
    )
)
set ADD_DLL=
if exist _cs2.dll set ADD_DLL=--add-binary "_cs2.dll;."
pyinstaller --noconfirm --onefile --windowed --name CS2_Tools_GUI --collect-data customtkinter --exclude-module numpy --exclude-module fontTools %ADD_DLL% cs2_gui.py
if errorlevel 1 (
    echo Error al generar el ejecutable.
    pause
    exit /b 1
)
echo.
echo Listo: dist\CS2_Tools_GUI.exe
pause
