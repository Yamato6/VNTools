#!/usr/bin/env python3
"""
FVP Tools - Tools for the Favorite View Point System visual novel engine
Extracts/packs BIN archives, converts NVSG images, and handles HCB scripts

Sin argumentos abre la interfaz gráfica (fvp_gui.py).
La lógica está en fvp_bin.py, fvp_nvsg.py y fvp_hcb.py.
"""

import sys

try:
    from PIL import Image  # noqa: F401
    import numpy as np  # noqa: F401
except ImportError:
    print("Error: Requires Pillow and NumPy")
    print("Install with: pip install pillow numpy")
    sys.exit(1)

import fvp_bin
import fvp_hcb
import fvp_nvsg
from fvp_bin import detect_extension  # noqa: F401  (compatibilidad)


def print_progress(i, total, name):
    if name:
        print(f"-> {name}")


# =============================================================================
# Funciones con los nombres de la versión anterior (compatibilidad)
# =============================================================================

def bin_extract(bin_path: str, output_folder: str, auto_ext: bool = True):
    count = fvp_bin.extract(bin_path, output_folder, auto_ext, progress=print_progress)
    print(f"\n[OK] Extraction complete: {count} files")


def bin_pack(input_folder: str, bin_path: str):
    count = fvp_bin.pack(input_folder, bin_path, progress=print_progress)
    print(f"\n[OK] Packing complete: {bin_path} ({count} files)")


def nvsg_decode(nvsg_path: str, png_path: str) -> dict:
    h = fvp_nvsg.decode(nvsg_path, png_path)
    print(f"Decoded {nvsg_path} -> {png_path} "
          f"(x={h.x}, y={h.y}, count={h.image_count}, {h.width}x{h.height}, fmt={h.fmt})")
    return {'x': h.x, 'y': h.y, 'image_count': h.frames,
            'width': h.width, 'height': h.height, 'format': h.fmt}


def nvsg_encode(png_path: str, nvsg_path: str, x: int, y: int, image_count: int = 1, fmt=None):
    h = fvp_nvsg.encode(png_path, nvsg_path, x, y, image_count, fmt)
    print(f"Encoded {png_path} -> {nvsg_path} "
          f"(x={x}, y={y}, count={image_count}, {h.width}x{h.height}, fmt={h.fmt})")


def batch_decode(input_folder: str, output_folder: str):
    count, errors = fvp_nvsg.batch_decode(input_folder, output_folder, progress=print_progress)
    for e in errors:
        print(f"[ERROR] {e}")
    print(f"\n[OK] {count} images decoded. Log saved: {output_folder}/{fvp_nvsg.LOG_NAME}")


def batch_encode(input_folder: str, output_folder: str, log_path: str):
    count, missing = fvp_nvsg.batch_encode(input_folder, output_folder, log_path,
                                           progress=print_progress)
    for name in missing:
        print(f"[WARN] No log entry for: {name}")
    print(f"\n[OK] {count} images encoded")


def hcb_decode(hcb_path: str, output_path: str, strings_path=None, charmap=None, reverse=False):
    funcs, strings = fvp_hcb.decompile(hcb_path, output_path, strings_path, charmap, reverse)
    print(f"HCB file: {hcb_path}")
    print(f"  Functions: {funcs}")
    print(f"  Strings found: {strings}")
    print(f"  Output: {output_path}")
    if strings_path:
        print(f"  Strings file: {strings_path}")
    return funcs, strings


def hcb_extract_strings(hcb_path: str, output_path: str, charmap=None, reverse=False):
    count = fvp_hcb.extract_strings(hcb_path, output_path, charmap, reverse)
    print(f"Extracted {count} strings from {hcb_path}")
    print(f"  Output: {output_path}")
    return count


def hcb_rebuild(original_hcb: str, strings_path: str, output_hcb: str, charmap=None, mode='reloc'):
    rep = fvp_hcb.rebuild(original_hcb, strings_path, output_hcb, charmap, mode)
    print(f"HCB rebuild: {original_hcb} ({'reubicando' if mode == 'reloc' else 'tamaño fijo'})")
    for w in rep.warnings[:10]:
        print(f"  [WARN] {w}")
    if len(rep.warnings) > 10:
        print(f"  ... y {len(rep.warnings) - 10} avisos más")

    notable = [e for e in rep.entries
               if fvp_hcb.ST_CUT in e.flags or fvp_hcb.ST_UNSUPPORTED in e.flags]
    for e in notable[:20]:
        print(f"  [{', '.join(e.flags)}] {e.sid:04d} 0x{e.addr:08X}: {e.detail}")
    if len(notable) > 20:
        print(f"  ... y {len(notable) - 20} textos más con problemas")

    print(f"  {rep.summary()}")
    print(f"  Original size: {rep.original_size} bytes")
    print(f"  New size: {rep.new_size} bytes")
    print(f"  Output: {output_hcb}")
    return rep


def hcb_split_strings(strings_path: str):
    for msg in fvp_hcb.split_strings(strings_path):
        print(f"  {msg}")


def hcb_merge_strings(build_script_path: str, output_path: str):
    count, messages = fvp_hcb.merge_strings(build_script_path, output_path)
    for msg in messages:
        print(f"  {msg}")
    print(f"Merged -> {output_path} ({count} strings)")
    return count


def bin_list(bin_path: str):
    entries = fvp_bin.read_index(bin_path)
    print(f"{bin_path}: {len(entries)} files")
    for e in entries:
        kind = fvp_bin.detect_type(fvp_bin.read_entry(bin_path, e, 16))
        print(f" {e.index:04d} {e.size:>10}  {kind:<5} {e.name}")


# =============================================================================
# CLI
# =============================================================================

def print_usage():
    print("""
FVP Tools - Tools for Favorite View Point System

Usage:
  (sin argumentos abre la interfaz gráfica)

  BIN Archive:
    python fvp_tools.py bin-list <file.bin>
    python fvp_tools.py bin-extract <file.bin> <output_folder> [--no-ext]
    python fvp_tools.py bin-pack <input_folder> <file.bin>

  NVSG Images:
    python fvp_tools.py nvsg-decode <nvsg_file> <png_file>
    python fvp_tools.py nvsg-encode <png_file> <nvsg_file> --x <N> --y <N> [--count <N>] [--format <0-3>]

  Batch Operations:
    python fvp_tools.py batch-decode <nvsg_folder> <png_folder>
    python fvp_tools.py batch-encode <png_folder> <nvsg_folder> <decode_log.txt>

  HCB Scripts:
    python fvp_tools.py hcb-decode <file.hcb> <output.txt> [--strings <strings.txt>] [--charmap <map.txt> --reverse]
    python fvp_tools.py hcb-strings <file.hcb> <strings.txt> [--charmap <map.txt> --reverse]
    python fvp_tools.py hcb-rebuild <original.hcb> <strings.txt> <output.hcb> [--charmap <map.txt>] [--fixed]
    python fvp_tools.py hcb-split <strings.txt>
    python fvp_tools.py hcb-merge <build_script.txt> <output_strings.txt>

Options:
  --no-ext    Do not add automatic extension (for NVSG files)
  --strings   Also export strings to separate file for translation
  --charmap   Character table for a custom font (e.g. 'á=ｱ' per line)
  --reverse   Apply the character table in reverse when extracting (already translated HCB)
  --fixed     Keep every string at its original size (old behavior)

Note: NVSG files have no extension (engine requirement).
      Audio files (OGG/WAV) are detected automatically.
      HCB strings use Shift-JIS (CP932); each string can hold up to 254 bytes.
""")


def get_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


def load_charmap(args):
    path = get_option(args, '--charmap')
    return fvp_hcb.CharMap.load(path) if path else None


def main():
    # La consola puede usar cp932 u otra codificación: no abortar por un nombre
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')

    args = sys.argv[1:]

    if len(args) < 1:
        try:
            import fvp_gui
        except ImportError as e:
            print(f"No se pudo abrir la interfaz gráfica: {e}")
            print("Instala las dependencias con: pip install -r requirements.txt")
            print_usage()
            return
        fvp_gui.main()
        return

    cmd = args[0].lower()

    try:
        if cmd == 'bin-list' and len(args) >= 2:
            bin_list(args[1])

        elif cmd == 'bin-extract' and len(args) >= 3:
            bin_extract(args[1], args[2], auto_ext='--no-ext' not in args)

        elif cmd == 'bin-pack' and len(args) >= 3:
            bin_pack(args[1], args[2])

        elif cmd == 'nvsg-decode' and len(args) >= 3:
            nvsg_decode(args[1], args[2])

        elif cmd == 'nvsg-encode' and len(args) >= 3:
            fmt = get_option(args, '--format')
            nvsg_encode(args[1], args[2], int(get_option(args, '--x', 0)), int(get_option(args, '--y', 0)),
                        int(get_option(args, '--count', 1)), int(fmt) if fmt is not None else None)

        elif cmd == 'batch-decode' and len(args) >= 3:
            batch_decode(args[1], args[2])

        elif cmd == 'batch-encode' and len(args) >= 4:
            batch_encode(args[1], args[2], args[3])

        elif cmd == 'hcb-decode' and len(args) >= 3:
            hcb_decode(args[1], args[2], get_option(args, '--strings'), load_charmap(args),
                       '--reverse' in args)

        elif cmd == 'hcb-strings' and len(args) >= 3:
            hcb_extract_strings(args[1], args[2], load_charmap(args), '--reverse' in args)

        elif cmd == 'hcb-rebuild' and len(args) >= 4:
            hcb_rebuild(args[1], args[2], args[3], load_charmap(args),
                        'fixed' if '--fixed' in args else 'reloc')

        elif cmd == 'hcb-split' and len(args) >= 2:
            hcb_split_strings(args[1])

        elif cmd == 'hcb-merge' and len(args) >= 3:
            hcb_merge_strings(args[1], args[2])

        else:
            print_usage()

    except KeyboardInterrupt:
        print("Cancelado")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
