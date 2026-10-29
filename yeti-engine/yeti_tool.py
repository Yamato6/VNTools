#!/usr/bin/env python3
"""
Yeti Tools - Archivos .cpk, imágenes XTX, guiones y audio (motor Yeti/Regista)
Juego: CROSS†CHANNEL -Final Complete- (PC)

Sin argumentos abre la interfaz gráfica (yeti_gui.py).
La lógica está en yeti_cpk.py, yeti_codecs.py, yeti_xtx.py, yeti_extract.py y yeti_script.py.
"""

import multiprocessing
import sys
import time

import yeti_codecs
import yeti_extract
import yeti_script
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


def cmd_images(src: str, out_dir: str, resume: bool, workers, png_level: int):
    if not yeti_codecs.native_available():
        print(f"Aviso: no se encontró {yeti_codecs.LIB_NAME}; CRILAYLA usará la versión en Python "
              "(mucho más lenta)")
    t = time.time()
    r = yeti_extract.extract_images(src, out_dir, resume, workers, png_level,
                                    progress=print_progress)
    for err in r.errors:
        print(f" ! {err}")
    print(f"Hecho en {time.time() - t:.0f} s: {r.images} imágenes, {r.others} otros archivos, "
          f"{len(r.errors)} errores" + (f" ({r.skipped} ya hechos)" if r.skipped else ""))


def cmd_audio(src: str, out_dir: str, wav: bool):
    vgm = yeti_extract.find_vgmstream()
    if wav and not vgm:
        print("Aviso: no se encontró vgmstream-cli; el audio se guarda solo como .hca.")
        print("  Para convertir a WAV descarga vgmstream (https://vgmstream.org) y deja")
        print("  vgmstream-cli.exe en el PATH o junto a esta herramienta.")
    r = yeti_extract.extract_audio(src, out_dir, wav, progress=print_progress)
    for err in r.errors[:20]:
        print(f" ! {err}")
    print(f"Audio extraído: {r.files} archivos .hca" + (f", {r.wav} .wav" if r.wav else "") + f" en {out_dir}")


def cmd_xtx(xtx_path: str, png_path: str, split: bool):
    for path in yeti_extract.xtx_file_to_png(xtx_path, png_path, split):
        print(f"{xtx_path} -> {path}")


def _print_pack(r, out_cpk: str):
    for err in r.errors:
        print(f" ! {err}")
    if r.errors:
        print("No se creó el archivo: corrige los errores y vuelve a intentarlo")
        sys.exit(1)
    for name in r.changed:
        print(f"  reemplazado: {name}")
    print(f"Paquete creado: {out_cpk} ({len(r.changed)} reemplazados, {r.entries} archivos en total)")


def cmd_images_pack(cpk_path: str, folder: str, out_cpk: str):
    _print_pack(yeti_extract.pack_images(cpk_path, folder, out_cpk, progress=print_progress), out_cpk)


def cmd_pack(cpk_path: str, folder: str, out_cpk: str):
    _print_pack(yeti_extract.pack_files(cpk_path, folder, out_cpk, progress=print_progress), out_cpk)


def cmd_script_strings(sn_path: str, out_dir: str, charmap, reverse: bool, listing: bool):
    files, strings = yeti_script.extract_strings(sn_path, out_dir, charmap, reverse, listing)
    print(f"{strings} textos de {files} guiones en {out_dir}")


def cmd_script_rebuild(sn_path: str, strings_dir: str, out_path: str, charmap, mode: str):
    rep = yeti_script.rebuild(sn_path, strings_dir, out_path, charmap, mode)
    print(f"Guiones: {sn_path} ({'reubicando' if mode == 'reloc' else 'tamaño fijo'})")
    for w in rep.warnings[:10]:
        print(f"  [AVISO] {w}")
    if len(rep.warnings) > 10:
        print(f"  ... y {len(rep.warnings) - 10} avisos más")
    notable = [e for e in rep.entries
               if yeti_script.ST_CUT in e.flags or yeti_script.ST_UNSUPPORTED in e.flags]
    for e in notable[:20]:
        print(f"  [{', '.join(e.flags)}] {e.script} {e.sid:04d} 0x{e.addr:08X}: {e.detail}")
    if len(notable) > 20:
        print(f"  ... y {len(notable) - 20} textos más con problemas")
    print(f"  {rep.summary()}")
    print(f"  Salida: {out_path}")


def usage():
    print("Yeti Tools - .cpk, XTX, guiones y audio (CROSS†CHANNEL -Final Complete-)")
    print()
    print("Uso:")
    print("  python yeti_tool.py                          (abre la interfaz gráfica)")
    print("  python yeti_tool.py list    <archivo.cpk>")
    print("  python yeti_tool.py extract <archivo.cpk> <directorio_salida>")
    print("  python yeti_tool.py images  <archivo.cpk|carpeta_data> <directorio_salida>")
    print("                              [--resume] [--workers N] [--png-level N]")
    print("  python yeti_tool.py audio   <carpeta_data|archivo.cpk> <directorio_salida> [--no-wav]")
    print("  python yeti_tool.py xtx     <archivo.xtx> <salida.png> [--split]")
    print()
    print("Reempaquetar:")
    print("  python yeti_tool.py images-pack <original.cpk> <carpeta_imagenes> <salida.cpk>")
    print("  python yeti_tool.py pack        <original.cpk> <carpeta_con_bin_o_hca> <salida.cpk>")
    print()
    print("Guiones (sn.bin):")
    print("  python yeti_tool.py script-strings <sn.bin> <carpeta_textos> [--listing] [--charmap <map.txt> --reverse]")
    print("  python yeti_tool.py script-rebuild <sn.bin> <carpeta_textos> <sn_nuevo.bin> [--charmap <map.txt>] [--fixed]")
    print()
    print("Opciones de 'images':")
    print("  --resume       continúa donde se quedó la última ejecución (_progreso.txt)")
    print(f"  --workers N    procesos en paralelo (por defecto {yeti_extract.default_workers()}, "
          "la mitad de los núcleos)")
    print(f"  --png-level N  compresión PNG 0-9 (por defecto {yeti_extract.DEFAULT_PNG_LEVEL}: "
          "rápido, archivos algo más grandes)")
    print()
    print("Otras opciones:")
    print("  --no-wav       (audio) no convierte a WAV aunque vgmstream-cli esté disponible")
    print("  --listing      guarda también el listado completo de cada guion (carpeta listado)")
    print("  --charmap      tabla de caracteres para una fuente propia ('á=ｱ' por línea)")
    print("  --reverse      aplica la tabla al revés al extraer (guiones ya traducidos)")
    print("  --fixed        cada texto conserva su tamaño original (se rellena o se corta)")
    print()
    print("Opciones de 'xtx':")
    print("  --split        guarda cada canal por separado (_a, _r, _g, _b); font48.xtx guarda")
    print("                 una hoja de glyphs en cada canal")
    print()
    print("Ejemplos:")
    print("  python yeti_tool.py list data\\bg.cpk")
    print("  python yeti_tool.py images data png --resume")
    print("  python yeti_tool.py xtx data\\font48.xtx font48.png")
    print("  python yeti_tool.py images-pack data\\bg.cpk png bg_nuevo.cpk")
    print("  python yeti_tool.py script-strings data\\sn.bin textos")
    print("  python yeti_tool.py script-rebuild data\\sn.bin textos sn_nuevo.bin")


def get_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def load_charmap(args):
    path = get_option(args, '--charmap')
    return yeti_script.CharMap.load(path) if path else None


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
            cmd_images(args[0], args[1], '--resume' in opts,
                       int(workers) if workers else None,
                       int(get_option(opts, '--png-level', yeti_extract.DEFAULT_PNG_LEVEL)))
        elif cmd == 'audio' and len(args) == 2:
            cmd_audio(args[0], args[1], '--no-wav' not in opts)
        elif cmd == 'images-pack' and len(args) == 3:
            cmd_images_pack(args[0], args[1], args[2])
        elif cmd == 'pack' and len(args) == 3:
            cmd_pack(args[0], args[1], args[2])
        elif cmd == 'script-strings' and len(args) == 2:
            cmd_script_strings(args[0], args[1], load_charmap(opts), '--reverse' in opts, '--listing' in opts)
        elif cmd == 'script-rebuild' and len(args) == 3:
            cmd_script_rebuild(args[0], args[1], args[2], load_charmap(opts),
                               'fixed' if '--fixed' in opts else 'reloc')
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
