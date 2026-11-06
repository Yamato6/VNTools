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
    if name and i < total:
        print(f" [{i + 1}/{total}] {name}")


def cmd_list(cpk_path: str):
    with CPK(cpk_path) as cpk:
        print(f"{cpk_path}: CpkMode={cpk.mode}  {len(cpk.entries)} files  Align={cpk.align}")
        for e in cpk.entries:
            print(f" {e.path:<12} {e.size:>12}  {'CRILAYLA' if e.compressed else ''}")
        print(f"Total: {sum(e.size for e in cpk.entries)} bytes")


def cmd_extract(cpk_path: str, out_dir: str, png: bool):
    print(f"Extracting from {cpk_path}...")
    count = yeti_extract.extract_raw(cpk_path, out_dir, png=png, progress=print_progress)
    print(f"Extraction complete: {count} files in {out_dir}")


def cmd_bin_png(src: str, out_dir: str):
    r = yeti_extract.bins_to_png(src, out_dir, progress=print_progress)
    for err in r.errors:
        print(f" ! {err}")
    print(f"{r.images} images from {r.files} .bin files in {out_dir} "
          f"({r.without} without textures, {len(r.errors)} errors)")


def cmd_images(src: str, out_dir: str, resume: bool, workers, png_level: int):
    if not yeti_codecs.native_available():
        print(f"Warning: {yeti_codecs.LIB_NAME} not found; CRILAYLA will use the Python version "
              "(much slower)")
    t = time.time()
    r = yeti_extract.extract_images(src, out_dir, resume, workers, png_level,
                                    progress=print_progress)
    for err in r.errors:
        print(f" ! {err}")
    print(f"Done in {time.time() - t:.0f} s: {r.images} images, {r.others} other files, "
          f"{len(r.errors)} errors" + (f" ({r.skipped} already done)" if r.skipped else ""))


def cmd_audio(src: str, out_dir: str, wav: bool):
    vgm = yeti_extract.find_vgmstream()
    if wav and not vgm:
        print("Warning: vgmstream-cli not found; audio is saved as .hca only.")
        print("  To convert to WAV, download vgmstream (https://vgmstream.org) and put")
        print("  vgmstream-cli.exe in the PATH or next to this tool.")
    r = yeti_extract.extract_audio(src, out_dir, wav, progress=print_progress)
    for err in r.errors[:20]:
        print(f" ! {err}")
    print(f"Audio extracted: {r.files} .hca files" + (f", {r.wav} .wav" if r.wav else "") + f" in {out_dir}")


def cmd_xtx(xtx_path: str, png_path: str, split: bool):
    for path in yeti_extract.xtx_file_to_png(xtx_path, png_path, split):
        print(f"{xtx_path} -> {path}")


def _print_pack(r, out_cpk: str):
    for err in r.errors:
        print(f" ! {err}")
    if r.errors:
        print("The file was not created: fix the errors and try again")
        sys.exit(1)
    for name in r.changed:
        print(f"  replaced: {name}")
    print(f"Archive created: {out_cpk} ({len(r.changed)} replaced, {r.entries} files in total)")


def cmd_images_pack(cpk_path: str, folder: str, out_cpk: str):
    _print_pack(yeti_extract.pack_images(cpk_path, folder, out_cpk, progress=print_progress), out_cpk)


def cmd_pack(cpk_path: str, folder: str, out_cpk: str):
    _print_pack(yeti_extract.pack_files(cpk_path, folder, out_cpk, progress=print_progress), out_cpk)


def cmd_script_strings(sn_path: str, out_dir: str, charmap, reverse: bool, listing: bool):
    files, strings = yeti_script.extract_strings(sn_path, out_dir, charmap, reverse, listing)
    print(f"{strings} lines from {files} scripts in {out_dir}")


def cmd_script_rebuild(sn_path: str, strings_dir: str, out_path: str, charmap, mode: str):
    rep = yeti_script.rebuild(sn_path, strings_dir, out_path, charmap, mode)
    print(f"Scripts: {sn_path} ({'relocating' if mode == 'reloc' else 'fixed size'})")
    for w in rep.warnings[:10]:
        print(f"  [WARNING] {w}")
    if len(rep.warnings) > 10:
        print(f"  ... and {len(rep.warnings) - 10} more warnings")
    notable = [e for e in rep.entries
               if yeti_script.ST_CUT in e.flags or yeti_script.ST_UNSUPPORTED in e.flags]
    for e in notable[:20]:
        print(f"  [{', '.join(e.flags)}] {e.script} {e.sid:04d} 0x{e.addr:08X}: {e.detail}")
    if len(notable) > 20:
        print(f"  ... and {len(notable) - 20} more lines with problems")
    print(f"  {rep.summary()}")
    print(f"  Output: {out_path}")


def usage():
    print("Yeti Tools - .cpk, XTX, scripts and audio (CROSS†CHANNEL -Final Complete-)")
    print()
    print("Usage:")
    print("  python yeti_tool.py                          (opens the graphical interface)")
    print("  python yeti_tool.py list    <file.cpk>")
    print("  python yeti_tool.py extract <file.cpk> <output_folder> [--png]")
    print("  python yeti_tool.py images  <file.cpk|data_folder> <output_folder>")
    print("                              [--resume] [--workers N] [--png-level N]")
    print("  python yeti_tool.py audio   <data_folder|file.cpk> <output_folder> [--no-wav]")
    print("  python yeti_tool.py xtx     <file.xtx> <output.png> [--split]")
    print("  python yeti_tool.py bin-png <file.bin|folder> <output_folder>   (.bin files saved by 'extract')")
    print()
    print("Repacking:")
    print("  python yeti_tool.py images-pack <original.cpk> <images_folder> <output.cpk>")
    print("  python yeti_tool.py pack        <original.cpk> <folder_with_bin_or_hca> <output.cpk>")
    print()
    print("Scripts (sn.bin):")
    print("  python yeti_tool.py script-strings <sn.bin> <text_folder> [--listing] [--charmap <map.txt> --reverse]")
    print("  python yeti_tool.py script-rebuild <sn.bin> <text_folder> <new_sn.bin> [--charmap <map.txt>] [--fixed]")
    print()
    print("'extract' options:")
    print("  --png          save the XTX textures as PNG (<id>_<n>.png, as 'images' does)")
    print()
    print("'images' options:")
    print("  --resume       continue where the last run stopped (_progreso.txt)")
    print(f"  --workers N    parallel processes (default {yeti_extract.default_workers()}, "
          "half the cores)")
    print(f"  --png-level N  PNG compression 0-9 (default {yeti_extract.DEFAULT_PNG_LEVEL}: "
          "fast, somewhat larger files)")
    print()
    print("Other options:")
    print("  --no-wav       (audio) don't convert to WAV even if vgmstream-cli is available")
    print("  --listing      also save the full listing of each script (listing folder)")
    print("  --charmap      character table for a custom font ('á=ｱ' per line)")
    print("  --reverse      apply the table in reverse when extracting (already translated scripts)")
    print("  --fixed        each text keeps its original size (padded or cut)")
    print()
    print("'xtx' options:")
    print("  --split        save each channel separately (_a, _r, _g, _b); font48.xtx stores")
    print("                 a glyph sheet in each channel")
    print()
    print("Examples:")
    print("  python yeti_tool.py list data\\bg.cpk")
    print("  python yeti_tool.py images data png --resume")
    print("  python yeti_tool.py xtx data\\font48.xtx font48.png")
    print("  python yeti_tool.py images-pack data\\bg.cpk png bg_new.cpk")
    print("  python yeti_tool.py script-strings data\\sn.bin text")
    print("  python yeti_tool.py script-rebuild data\\sn.bin text sn_new.bin")


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
            print(f"Could not open the graphical interface: {e}")
            print("Install the dependencies with: pip install -r requirements.txt")
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
        elif cmd == 'bin-png' and len(args) == 2:
            cmd_bin_png(args[0], args[1])
        elif cmd == 'extract' and len(args) == 2:
            cmd_extract(args[0], args[1], '--png' in opts)
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
        print("Cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
