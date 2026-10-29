#!/usr/bin/env python3
"""
AdvHD Tools - Extractor de archivos .arc e imágenes WIPF (motor AdvHD de WillPlus)
Juego: CROSS†CHANNEL (edición WillPlus, 2012)

Sin argumentos abre la interfaz gráfica (advhd_gui.py).
La lógica está en advhd_arc.py, advhd_wipf.py y advhd_extract.py.
"""

import multiprocessing
import sys
import time
from collections import Counter

import advhd_extract
from advhd_arc import read_index


def print_progress(i, total, name):
    if name:
        print(f" [{i + 1}/{total}] {name}")


def cmd_list(arc_path: str):
    entries = read_index(arc_path)
    types = Counter(e.ext for e in entries)
    print(f"{arc_path}: {len(entries)} archivos  ("
          + ", ".join(f"{n} {ext}" for ext, n in types.items()) + ")")
    for e in entries:
        print(f" {e.filename:<18} {e.size:>12}")
    print(f"Total: {sum(e.size for e in entries)} bytes")


def cmd_extract(arc_path: str, out_dir: str):
    print(f"Extrayendo desde {arc_path}...")
    count = advhd_extract.extract_raw(arc_path, out_dir, progress=print_progress)
    print(f"Extracción completada: {count} archivos en {out_dir}")


def cmd_images(src: str, out_dir: str, resume: bool, audio: bool, compose: bool, workers,
               png_level: int):
    t = time.time()
    r = advhd_extract.extract_images(src, out_dir, resume, audio, compose, workers, png_level,
                                     progress=print_progress)
    for w in r.warnings:
        print(f" Aviso: {w}")
    for err in r.errors:
        print(f" ! {err}")
    print(f"Hecho en {time.time() - t:.0f} s: {r.images} imágenes, {r.others} otros archivos, "
          f"{len(r.errors)} errores" + (f" ({r.skipped} ya hechos)" if r.skipped else ""))


def usage():
    print("AdvHD Tools - Extractor .arc / WIPF (CROSS†CHANNEL, edición WillPlus)")
    print()
    print("Uso:")
    print("  python advhd_tool.py                          (abre la interfaz gráfica)")
    print("  python advhd_tool.py list    <archivo.arc>")
    print("  python advhd_tool.py extract <archivo.arc> <directorio_salida>")
    print("  python advhd_tool.py images  <archivo.arc|carpeta_juego> <directorio_salida>")
    print("                               [--resume] [--audio] [--no-alpha] [--workers N] [--png-level N]")
    print()
    print("Opciones de 'images':")
    print("  --resume       continúa donde se quedó la última ejecución (_progreso.txt)")
    print("  --audio        incluye el audio (Bgm, Se y Voice.arc) como .ogg")
    print("  --no-alpha     no aplica las máscaras: guarda la imagen y NOMBRE_msk.png por separado")
    print(f"  --workers N    procesos en paralelo (por defecto {advhd_extract.default_workers()}, "
          "la mitad de los núcleos)")
    print(f"  --png-level N  compresión PNG 0-9 (por defecto {advhd_extract.DEFAULT_PNG_LEVEL}: "
          "rápido, archivos algo más grandes)")
    print()
    print("Ejemplos:")
    print("  python advhd_tool.py list Chip_T.arc")
    print("  python advhd_tool.py images \"CROSS†CHANNEL\" png --resume")


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
            import advhd_gui
        except ImportError as e:
            print(f"No se pudo abrir la interfaz gráfica: {e}")
            print("Instala las dependencias con: pip install -r requirements.txt")
            print()
            usage()
            return
        advhd_gui.main()
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
            cmd_images(args[0], args[1], '--resume' in opts, '--audio' in opts,
                       '--no-alpha' not in opts, int(workers) if workers else None,
                       int(get_option(opts, '--png-level', advhd_extract.DEFAULT_PNG_LEVEL)))
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
