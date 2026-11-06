#!/usr/bin/env python3
"""
CatSystem2 Tools - Extractor y reempaquetador de archivos .int del motor CatSystem2
Probado con Grisaia no Kajitsu, Grisaia no Meikyuu y Grisaia no Rakuen (Frontwing).

Sin argumentos abre la interfaz gráfica (cs2_gui.py).
La lógica está en cs2_int.py, cs2_crypto.py, cs2_hg3.py, cs2_script.py y cs2_extract.py.
"""

import multiprocessing
import sys
import time
from collections import Counter
from pathlib import Path

import cs2_extract
import cs2_script
from cs2_common import CharMap
from cs2_crypto import find_passphrase, key_from_passphrase, native_available, passphrase_from_exe
from cs2_int import IntArchive, is_encrypted, pack


def print_progress(i, total, name):
    if name and i < total:
        print(f" [{i + 1}/{total}] {name}")


def archive_key(int_path: str, phrase):
    """Clave para un .int: la de --key o la de la carpeta del juego (None si no está cifrado)."""
    if not is_encrypted(int_path):
        return None
    key, why = cs2_extract.resolve_key(int_path, phrase)
    if key is None:
        raise ValueError(f"{Path(int_path).name} is encrypted and {why}: use --key <passphrase>")
    return key


def cmd_key(src: str):
    p = Path(src)
    if p.is_file() and p.suffix.lower() == '.exe':
        phrase, origin = passphrase_from_exe(p), p.name
    else:
        phrase, origin = find_passphrase(p)
    if not phrase:
        print("No passphrase found (the game .exe has no V_CODE2 resource and there is no config\\startup.xml)")
        sys.exit(1)
    print(f"Passphrase: {phrase}  (from {origin})")
    print(f"Key:        0x{key_from_passphrase(phrase):08X}")


def cmd_list(int_path: str, phrase):
    with IntArchive(int_path, archive_key(int_path, phrase)) as arc:
        types = Counter(e.ext or '(none)' for e in arc.entries)
        print(f"{int_path}: {len(arc.entries)} files" + (", encrypted" if arc.encrypted else ""))
        print("  " + ", ".join(f"{n} {ext}" for ext, n in types.most_common()))
        for e in arc.entries:
            print(f" {e.name:<44} {e.size:>12}")
        print(f"Total: {sum(e.size for e in arc.entries)} bytes")


def cmd_extract(src: str, out_dir: str, only, resume: bool, convert: bool, phrase, workers):
    t = time.time()
    if not native_available():
        print("Note: _cs2.dll was not found; using the Python version (slower)")
    r = cs2_extract.extract_all(src, out_dir, only, convert, resume, phrase, workers,
                                progress=print_progress)
    for w in r.warnings[:20]:
        print(f" Warning: {w}")
    if len(r.warnings) > 20:
        print(f" ... and {len(r.warnings) - 20} more warnings")
    for err in r.errors:
        print(f" ! {err}")
    print(f"Done in {time.time() - t:.0f} s: {r.files} files ({r.converted} images converted to PNG), "
          f"{len(r.errors)} errors" + (f" ({r.skipped} already done)" if r.skipped else ""))


def cmd_repack(int_path: str, folder: str, out_int: str, add_new: bool, phrase):
    r = cs2_extract.repack(int_path, folder, out_int, add_new, phrase, progress=print_progress)
    for err in r.errors:
        print(f" ! {err}")
    if r.errors:
        print("The file was not created: fix the errors and try again")
        sys.exit(1)
    if not r.entries:
        print("No file was edited since the extraction: no file was written")
        return
    for name in r.changed:
        print(f"  replaced: {name}")
    for name in r.added:
        print(f"  added: {name}")
    print(f"Archive created: {out_int} ({r.entries} files, {len(r.changed)} replaced, {len(r.added)} added)")


def cmd_pack(folder: str, out_int: str, like, phrase):
    key = archive_key(like, phrase) if like else None
    count = pack(folder, out_int, like, key, progress=print_progress)
    print(f"Archive created: {out_int} ({count} files" + (", encrypted like the original)" if key else ")"))


def cmd_text(int_path: str, out_dir: str, charmap, reverse: bool, json_out: bool, phrase):
    r = cs2_script.extract_text(int_path, out_dir, archive_key(int_path, phrase), charmap, reverse,
                                json_out, progress=print_progress)
    for w in r.warnings:
        print(f"  [WARNING] {w}")
    print(f"{r.lines} lines from {r.files} scripts in {out_dir}")


def cmd_text_rebuild(int_path: str, text_dir: str, out_int: str, charmap, phrase):
    rep = cs2_script.rebuild(int_path, text_dir, out_int, archive_key(int_path, phrase), charmap,
                             progress=print_progress)
    for w in rep.warnings[:10]:
        print(f"  [WARNING] {w}")
    if len(rep.warnings) > 10:
        print(f"  ... and {len(rep.warnings) - 10} more warnings")
    for e in rep.entries[:30]:
        print(f"  [{', '.join(e.flags)}] {e.entry} {e.key}: {e.detail}")
    if len(rep.entries) > 30:
        print(f"  ... and {len(rep.entries) - 30} more")
    print(f"  {rep.summary()}")
    if rep.output_entries:
        print(f"  Output: {out_int} ({rep.output_entries} files)")


def usage():
    print("CatSystem2 Tools - .int tools for the CatSystem2 engine (Grisaia series)")
    print()
    print("Usage:")
    print("  python cs2_tool.py                        (opens the graphical interface)")
    print("  python cs2_tool.py key     <game_folder|game.exe>")
    print("  python cs2_tool.py list    <file.int>")
    print("  python cs2_tool.py extract <file.int|game_folder> <output_folder>")
    print("                             [--only images,audio,video,scripts,data] [--resume] [--raw] [--workers N]")
    print("  python cs2_tool.py repack  <original.int> <folder> <output.int> [--add-new]")
    print("  python cs2_tool.py pack    <folder> <output.int> [--like <original.int>]")
    print()
    print("Text (scene.int):")
    print("  python cs2_tool.py text <scene.int> <text_folder> [--json] [--charmap <map.txt> --reverse]")
    print("  python cs2_tool.py text-rebuild <scene.int> <text_folder> <output.int> [--charmap <map.txt>]")
    print()
    print("All commands accept --key <passphrase>. By default the passphrase is read from the")
    print("game .exe (or config\\startup.xml) in the folder of the .int.")
    print()
    print("'extract' options:")
    print("  --only LIST    only these categories (default: all)")
    print("  --resume       continue where the last run stopped (_progress.txt)")
    print("  --raw          keep HG-3 images as .hg3 instead of converting them to PNG")
    print("  --workers N    parallel processes (default: half of the CPU cores)")
    print()
    print("Other options:")
    print("  --add-new      also add files of the folder that are not in the original .int")
    print("  --like         keep the order and encryption of an original .int (otherwise not encrypted)")
    print("  --json         also write the text as JSON (with the voice file of each line)")
    print("  --charmap      character table for a custom font ('á=ｱ' per line)")
    print("  --reverse      apply the table in reverse when extracting (already translated scripts)")
    print()
    print("Examples:")
    print("  python cs2_tool.py key \"D:\\Games\\Grisaia\"")
    print("  python cs2_tool.py extract \"D:\\Games\\Grisaia\" out --resume")
    print("  python cs2_tool.py repack \"D:\\Games\\Grisaia\\image03.int\" out image03.int")
    print("  python cs2_tool.py text \"D:\\Games\\Grisaia\\scene.int\" text")
    print("  python cs2_tool.py text-rebuild \"D:\\Games\\Grisaia\\scene.int\" text scene.int")


def get_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


VALUE_OPTIONS = ('--only', '--charmap', '--like', '--key', '--workers')


def positional(args):
    """Argumentos sin las opciones (ni sus valores)."""
    out = []
    skip = False
    for a in args:
        if skip:
            skip = False
        elif a in VALUE_OPTIONS:
            skip = True
        elif not a.startswith('--'):
            out.append(a)
    return out


def load_charmap(args):
    path = get_option(args, '--charmap')
    return CharMap.load(path) if path else None


def main():
    # La consola puede usar cp932 u otra codificación: no abortar por un nombre
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')

    if len(sys.argv) < 2:
        try:
            import cs2_gui
        except ImportError as e:
            print(f"Could not open the graphical interface: {e}")
            print("Install the dependencies with: pip install -r requirements.txt")
            print()
            usage()
            return
        cs2_gui.main()
        return

    cmd = sys.argv[1].lower()
    opts = sys.argv[2:]
    args = positional(opts)
    phrase = get_option(opts, '--key')

    try:
        if cmd == 'key' and len(args) == 1:
            cmd_key(args[0])
        elif cmd == 'list' and len(args) == 1:
            cmd_list(args[0], phrase)
        elif cmd == 'extract' and len(args) == 2:
            only = get_option(opts, '--only')
            cats = [c.strip() for c in only.split(',') if c.strip()] if only else cs2_extract.ALL_CATEGORIES
            workers = get_option(opts, '--workers')
            cmd_extract(args[0], args[1], cats, '--resume' in opts, '--raw' not in opts, phrase,
                        int(workers) if workers else None)
        elif cmd == 'repack' and len(args) == 3:
            cmd_repack(args[0], args[1], args[2], '--add-new' in opts, phrase)
        elif cmd == 'pack' and len(args) == 2:
            cmd_pack(args[0], args[1], get_option(opts, '--like'), phrase)
        elif cmd == 'text' and len(args) == 2:
            cmd_text(args[0], args[1], load_charmap(opts), '--reverse' in opts, '--json' in opts, phrase)
        elif cmd == 'text-rebuild' and len(args) == 3:
            cmd_text_rebuild(args[0], args[1], args[2], load_charmap(opts), phrase)
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
