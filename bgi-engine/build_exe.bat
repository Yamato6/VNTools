@echo off
rem Genera dist\BGI_Tools_GUI.exe (requiere: pip install -r requirements.txt y gcc)
cd /d "%~dp0"
rem CompressedBG nativo: sin gcc el .exe usa la versión en Python (unas 100 veces más lenta)
where gcc >nul 2>nul
if errorlevel 1 (
    echo Aviso: no se encontro gcc; se usara _bgi.dll si ya existe.
) else (
    gcc -O2 -shared -static-libgcc -o _bgi.dll _bgi.c
    if errorlevel 1 (
        echo Error al compilar _bgi.dll.
        pause
        exit /b 1
    )
)
set ADD_DLL=
if exist _bgi.dll set ADD_DLL=--add-binary "_bgi.dll;."
pyinstaller --noconfirm --onefile --windowed --name BGI_Tools_GUI --collect-data customtkinter --exclude-module fontTools %ADD_DLL% bgi_gui.py
if errorlevel 1 (
    echo Error al generar el ejecutable.
    pause
    exit /b 1
)
echo.
echo Listo: dist\BGI_Tools_GUI.exe
pause
