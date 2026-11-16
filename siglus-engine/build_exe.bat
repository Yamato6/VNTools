@echo off
rem Genera dist\Siglus_Tools_GUI.exe (requiere: pip install -r requirements.txt y gcc)
cd /d "%~dp0"
rem LZ y NWA nativos: sin gcc el .exe usa la versión en Python (mucho más lenta)
where gcc >nul 2>nul
if errorlevel 1 (
    echo Aviso: no se encontro gcc; se usara _siglus.dll si ya existe.
) else (
    gcc -O2 -shared -static-libgcc -o _siglus.dll _siglus.c
    if errorlevel 1 (
        echo Error al compilar _siglus.dll.
        pause
        exit /b 1
    )
)
set ADD_DLL=
if exist _siglus.dll set ADD_DLL=--add-binary "_siglus.dll;."
pyinstaller --noconfirm --onefile --windowed --name Siglus_Tools_GUI --collect-data customtkinter --exclude-module fontTools %ADD_DLL% siglus_gui.py
if errorlevel 1 (
    echo Error al generar el ejecutable.
    pause
    exit /b 1
)
echo.
echo Listo: dist\Siglus_Tools_GUI.exe
pause
