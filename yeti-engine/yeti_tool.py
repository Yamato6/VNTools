#!/usr/bin/env python3
"""
Yeti Tools - Extractor de archivos .cpk e imágenes XTX (motor Yeti/Regista)
Juego: CROSS†CHANNEL -Final Complete- (PC)

Sin argumentos abre la interfaz gráfica (yeti_gui.py).
La lógica está en yeti_cpk.py, yeti_codecs.py, yeti_xtx.py y yeti_extract.py.
"""

import multiprocessing
import sys
import time

import yeti_codecs
import yeti_extract
from yeti_cpk import CPK


def print_progress(i, total, name):
    if name:
        print(f" [{i + 1}/{total}] {name}")


def cmd_list(cpk_path: str):
    with CPK(cpk_path) as cpk:
        print(f"{cpk_path}: CpkMode={cpk.mode}  {len(cpk.entries)} archivos  Align={cpk.align}")
        for e in cpk.entries:
            print(f" {e.path:<12} {e.size:>12}  {'CRILAYLA' if e.compressed else ''}")
        print(f"Total: {sum(e.size for e in cpk.entries)} bytes")


def cmd_extract(cpk_path: str, out_dir: str):
    print(f"Extrayendo desde {cpk_path}...")
    count = yeti_extract.extract_raw(cpk_path, out_dir, progress=print_progress)
    print(f"Extracción completada: {count} archivos en {out_dir}")


def cmd_images(src: str, out_dir: str, resume: bool, include_audio: bool, workers, png_level: int):
    if not yeti_codecs.native_available():
        print(f"Aviso: no se encontró {yeti_codecs.LIB_NAME}; CRILAYLA usará la versión en Python "
              "(mucho más lenta)")
    t = time.time()
    r = yeti_extract.extract_images(src, out_dir, resume, include_audio, workers, png_level,
                                    progress=print_progress)
    for err in r.errors:
        print(f" ! {err}")
    print(f"Hecho en {time.time() - t:.0f} s: {r.images} imágenes, {r.others} otros archivos, "
          f"{len(r.errors)} errores" + (f" ({r.skipped} ya hechos)" if r.skipped else ""))


def cmd_xtx(xtx_path: str, png_path: str, split: bool):
    for path in yeti_extract.xtx_file_to_png(xtx_path, png_path, split):
        print(f"{xtx_path} -> {path}")


def usage():
    print("Yeti Tools - Extractor .cpk / XTX (CROSS†CHANNEL -Final Complete-)")
    print()
    print("Uso:")
    print("  python yeti_tool.py                          (abre la interfaz gráfica)")
    print("  python yeti_tool.py list    <archivo.cpk>")
    print("  python yeti_tool.py extract <archivo.cpk> <directorio_salida>")
    print("  python yeti_tool.py images  <archivo.cpk|carpeta_data> <directorio_salida>")
    print("                              [--resume] [--all] [--workers N] [--png-level N]")
    print("  python yeti_tool.py xtx     <archivo.xtx> <salida.png> [--split]")
    print()
    print("Opciones de 'images':")
    print("  --resume       continúa donde se quedó la última ejecución (_progreso.txt)")
    print("  --all          incluye también los .cpk de audio (bgm, bgs, se, vo)")
    print(f"  --workers N    procesos en paralelo (por defecto {yeti_extract.default_workers()}, "
          "la mitad de los núcleos)")
    print(f"  --png-level N  compresión PNG 0-9 (por defecto {yeti_extract.DEFAULT_PNG_LEVEL}: "
          "rápido, archivos algo más grandes)")
    print()
    print("Opciones de 'xtx':")
    print("  --split        guarda cada canal por separado (_a, _r, _g, _b); font48.xtx guarda")
    print("                 una hoja de glyphs en cada canal")
    print()
    print("Ejemplos:")
    print("  python yeti_tool.py list data\\bg.cpk")
    print("  python yeti_tool.py images data png --resume")
    print("  python yeti_tool.py xtx data\\font48.xtx font48.png")


def get_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def positional(args):
    """Argumentos sin las opciones (ni sus valores)."""
    out = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a in ('--workers', '--png-level'):
            skip = True
        elif not a.startswith('--'):
            out.append(a)
    return out


def main():
    # La consola puede usar cp932 u otra codificación: no abortar por un nombre
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')

    if len(sys.argv) < 2:
        try:
            import yeti_gui
        except ImportError as e:
            print(f"No se pudo abrir la interfaz gráfica: {e}")
            print("Instala las dependencias con: pip install -r requirements.txt")
            print()
            usage()
            return
        yeti_gui.main()
        return

    cmd = sys.argv[1].lower()
    opts = sys.argv[2:]
    args = positional(opts)

    try:
        if cmd == 'list' and len(args) == 1:
            cmd_list(args[0])
        elif cmd == 'extract' and len(args) == 2:
            cmd_extract(args[0], args[1])
        elif cmd == 'images' and len(args) == 2:
            workers = get_option(opts, '--workers')
            cmd_images(args[0], args[1], '--resume' in opts, '--all' in opts,
                       int(workers) if workers else None,
                       int(get_option(opts, '--png-level', yeti_extract.DEFAULT_PNG_LEVEL)))
        elif cmd == 'xtx' and len(args) == 2:
            cmd_xtx(args[0], args[1], '--split' in opts)
        else:
            usage()
    except KeyboardInterrupt:
        print("Cancelado")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
