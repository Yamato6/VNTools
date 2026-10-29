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
import advhd_wsc
from advhd_arc import pack, read_index


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


def cmd_pack(folder: str, arc_path: str):
    print(f"Empaquetando {folder} en {arc_path}...")
    count = pack(folder, arc_path, progress=print_progress)
    print(f"Paquete creado: {arc_path} ({count} archivos)")


def cmd_audio(src: str, out_dir: str):
    count = advhd_extract.extract_audio(src, out_dir, progress=print_progress)
    print(f"Audio extraído: {count} archivos en {out_dir}")


def cmd_images_pack(arc_path: str, folder: str, out_arc: str, allow_resize: bool):
    r = advhd_extract.pack_images(arc_path, folder, out_arc, allow_resize, progress=print_progress)
    for err in r.errors:
        print(f" ! {err}")
    if r.errors:
        print("No se creó el archivo: corrige los errores y vuelve a intentarlo")
        sys.exit(1)
    for name in r.changed:
        print(f"  reemplazado: {name}")
    print(f"Paquete creado: {out_arc} ({len(r.changed)} de {r.entries} archivos reemplazados)")


def cmd_wsc_strings(arc_path: str, out_dir: str, charmap, reverse: bool, listing: bool):
    files, strings = advhd_wsc.extract_strings(arc_path, out_dir, charmap, reverse, listing)
    print(f"{strings} textos de {files} guiones en {out_dir}")


def cmd_wsc_rebuild(arc_path: str, strings_dir: str, out_arc: str, charmap, mode: str):
    rep = advhd_wsc.rebuild(arc_path, strings_dir, out_arc, charmap, mode)
    print(f"Guiones: {arc_path} ({'reubicando' if mode == 'reloc' else 'tamaño fijo'})")
    for w in rep.warnings[:10]:
        print(f"  [AVISO] {w}")
    if len(rep.warnings) > 10:
        print(f"  ... y {len(rep.warnings) - 10} avisos más")
    notable = [e for e in rep.entries
               if advhd_wsc.ST_CUT in e.flags or advhd_wsc.ST_UNSUPPORTED in e.flags]
    for e in notable[:20]:
        print(f"  [{', '.join(e.flags)}] {e.script} {e.sid:04d} 0x{e.addr:08X}: {e.detail}")
    if len(notable) > 20:
        print(f"  ... y {len(notable) - 20} textos más con problemas")
    print(f"  {rep.summary()}")
    print(f"  Salida: {out_arc}")


def usage():
    print("AdvHD Tools - Extractor .arc / WIPF (CROSS†CHANNEL, edición WillPlus)")
    print()
    print("Uso:")
    print("  python advhd_tool.py                          (abre la interfaz gráfica)")
    print("  python advhd_tool.py list    <archivo.arc>")
    print("  python advhd_tool.py extract <archivo.arc> <directorio_salida>")
    print("  python advhd_tool.py images  <archivo.arc|carpeta_juego> <directorio_salida>")
    print("                               [--resume] [--audio] [--no-alpha] [--workers N] [--png-level N]")
    print("  python advhd_tool.py images-pack <original.arc> <carpeta_imagenes> <salida.arc> [--allow-resize]")
    print("  python advhd_tool.py pack    <carpeta> <archivo.arc>")
    print("  python advhd_tool.py audio   <carpeta_juego> <directorio_salida>")
    print()
    print("Guiones (Rio.arc):")
    print("  python advhd_tool.py wsc-strings <Rio.arc> <carpeta_textos> [--listing] [--charmap <map.txt> --reverse]")
    print("  python advhd_tool.py wsc-rebuild <Rio.arc> <carpeta_textos> <Rio_nuevo.arc> [--charmap <map.txt>] [--fixed]")
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
    print("Otras opciones:")
    print("  --allow-resize  permite imágenes con otro tamaño que el original")
    print("  --listing       guarda también el listado completo de cada guion (carpeta listado)")
    print("  --charmap       tabla de caracteres para una fuente propia ('á=ｱ' por línea)")
    print("  --reverse       aplica la tabla al revés al extraer (guiones ya traducidos)")
    print("  --fixed         cada texto conserva su tamaño original (se rellena o se corta)")
    print()
    print("Ejemplos:")
    print("  python advhd_tool.py list Chip_T.arc")
    print("  python advhd_tool.py images \"CROSS†CHANNEL\" png --resume")
    print("  python advhd_tool.py images-pack Chip_T.arc png Chip_T_nuevo.arc")
    print("  python advhd_tool.py wsc-strings Rio.arc textos")
    print("  python advhd_tool.py wsc-rebuild Rio.arc textos Rio_nuevo.arc")


def get_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def load_charmap(args):
    path = get_option(args, '--charmap')
    return advhd_wsc.CharMap.load(path) if path else None


def positional(args):
    """Argumentos sin las opciones (ni sus valores)."""
    out = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a in ('--workers', '--png-level', '--charmap'):
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
        elif cmd == 'pack' and len(args) == 2:
            cmd_pack(args[0], args[1])
        elif cmd == 'audio' and len(args) == 2:
            cmd_audio(args[0], args[1])
        elif cmd == 'images-pack' and len(args) == 3:
            cmd_images_pack(args[0], args[1], args[2], '--allow-resize' in opts)
        elif cmd == 'wsc-strings' and len(args) == 2:
            cmd_wsc_strings(args[0], args[1], load_charmap(opts), '--reverse' in opts, '--listing' in opts)
        elif cmd == 'wsc-rebuild' and len(args) == 3:
            cmd_wsc_rebuild(args[0], args[1], args[2], load_charmap(opts),
                            'fixed' if '--fixed' in opts else 'reloc')
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
