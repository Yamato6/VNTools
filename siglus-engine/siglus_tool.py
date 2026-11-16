#!/usr/bin/env python3
"""
Siglus Tools - Extractor y reempaquetador de los recursos y guiones de SiglusEngine
Probado con Hoshi Ori Yume Mirai Perfect Edition (Tone Work's, 2017) con el parche en inglés.

Sin argumentos abre la interfaz gráfica (siglus_gui.py).
La lógica está en siglus_crypto.py, siglus_pck.py, siglus_ss.py, siglus_script.py,
siglus_g00.py, siglus_audio.py, siglus_gameexe.py y siglus_extract.py.
"""

import multiprocessing
import sys
import time
from collections import Counter

import siglus_extract
import siglus_script
from siglus_crypto import native_available
from siglus_pck import ScenePck


def print_progress(i, total, name):
    if name and i < total:
        print(f" [{i + 1}/{total}] {name}")


def cmd_list(game: str):
    files = siglus_extract.find_files(game)
    print(f"{game}: {len(files)} files")
    print("  " + ", ".join(f"{n} {k}" for k, n in Counter(map(siglus_extract.kind_of, files)).most_common()))
    try:
        pck = ScenePck(siglus_script.find_pck(game))
        print(f"  Scene.pck: {len(pck)} scenes" + (f", key {pck.key.hex(' ')}" if pck.key else ""))
    except ValueError as e:
        print(f"  Scene.pck: {e}")


def cmd_findkey(src: str):
    pck = ScenePck(siglus_script.find_pck(src))
    if not pck.key:
        print("This Scene.pck does not use a key")
    else:
        print(pck.key.hex(' ').upper())


def cmd_extract(game: str, out_dir: str, resume: bool, composed: bool, workers):
    t = time.time()
    if not native_available():
        print("Note: _siglus.dll was not found; using the Python version (much slower)")
    r = siglus_extract.extract_all(game, out_dir, resume, workers, composed=composed, progress=print_progress)
    for err in r.errors:
        print(f" ! {err}")
    print(f"Done in {time.time() - t:.0f} s: {r.files} files ({r.converted} converted, {r.written} written), "
          f"{len(r.errors)} errors" + (f" ({r.skipped} already done)" if r.skipped else ""))


def cmd_pack(folder: str, game: str, out_dir: str):
    r = siglus_extract.pack(folder, game, out_dir, progress=print_progress)
    for w in r.warnings:
        print(f" Warning: {w}")
    for err in r.errors:
        print(f" ! {err}")
    if not r.written:
        print("No file was edited since the extraction: no file was written")
        return
    for name in r.written:
        print(f"  written: {name}")
    print(f"{len(r.written)} files in {out_dir}: copy them over the game folder")


def cmd_text(src: str, out_dir: str, reference, json_out: bool):
    r = siglus_script.extract_text(src, out_dir, reference, json_out, progress=print_progress)
    for w in r.warnings:
        print(f"  [WARNING] {w}")
    print(f"{r.lines} lines ({r.voiced} with voice) from {r.files} scenes in {out_dir}")


def cmd_text_rebuild(src: str, text_dir: str, out_pck: str):
    rep = siglus_script.rebuild(src, text_dir, out_pck, progress=print_progress)
    for w in rep.warnings[:10]:
        print(f"  [WARNING] {w}")
    for e in rep.entries[:30]:
        print(f"  [{', '.join(e.flags)}] {e.entry} {e.key}: {e.detail}")
    if len(rep.entries) > 30:
        print(f"  ... and {len(rep.entries) - 30} more")
    print(f"  {rep.summary()}")
    if rep.written:
        print(f"  Output: {out_pck}")


def usage():
    print("Siglus Tools - resource and script tools for SiglusEngine (Hoshi Ori Yume Mirai)")
    print()
    print("Usage:")
    print("  python siglus_tool.py                        (opens the graphical interface)")
    print("  python siglus_tool.py list    <game_folder>")
    print("  python siglus_tool.py findkey <game_folder|Scene.pck>")
    print("  python siglus_tool.py extract <game_folder> <output_folder> [--resume] [--workers N] [--no-composed]")
    print("  python siglus_tool.py pack    <extracted_folder> <game_folder> <output_folder>")
    print()
    print("Text (Scene.pck):")
    print("  python siglus_tool.py text <game_folder|Scene.pck> <text_folder> [--reference <Scene.pck>] [--json]")
    print("  python siglus_tool.py text-rebuild <game_folder|Scene.pck> <text_folder> <output Scene.pck>")
    print()
    print("Options:")
    print("  --resume       continue where the last run stopped (_progress.txt)")
    print("  --workers N    parallel processes (default: half of the CPU cores)")
    print("  --no-composed  do not save the CGs and sprites without a face also complete (g00/_composed)")
    print("  --reference    the same Scene.pck in another language; its text is added to each")
    print("                 line as a '#>' comment")
    print("  --json         also write the text as JSON (with the voice file of each line)")
    print()
    print("Examples:")
    print("  python siglus_tool.py extract \"D:\\Games\\Hoshi Ori\" out --resume")
    print("  python siglus_tool.py pack out \"D:\\Games\\Hoshi Ori\" \"D:\\Games\\Hoshi Ori_new\"")
    print("  python siglus_tool.py text \"D:\\Games\\Hoshi Ori\" text")
    print("  python siglus_tool.py text-rebuild \"D:\\Games\\Hoshi Ori\" text \"D:\\Games\\Hoshi Ori_new\\Scene.pck\"")


def get_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


VALUE_OPTIONS = ('--workers', '--reference')


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


def main():
    # La consola puede usar cp932 u otra codificación: no abortar por un nombre
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')

    if len(sys.argv) < 2:
        try:
            import siglus_gui
        except ImportError as e:
            print(f"Could not open the graphical interface: {e}")
            print("Install the dependencies with: pip install -r requirements.txt")
            print()
            usage()
            return
        siglus_gui.main()
        return

    cmd = sys.argv[1].lower()
    opts = sys.argv[2:]
    args = positional(opts)

    try:
        if cmd == 'list' and len(args) == 1:
            cmd_list(args[0])
        elif cmd == 'findkey' and len(args) == 1:
            cmd_findkey(args[0])
        elif cmd == 'extract' and len(args) == 2:
            workers = get_option(opts, '--workers')
            cmd_extract(args[0], args[1], '--resume' in opts, '--no-composed' not in opts,
                        int(workers) if workers else None)
        elif cmd == 'pack' and len(args) == 3:
            cmd_pack(args[0], args[1], args[2])
        elif cmd == 'text' and len(args) == 2:
            cmd_text(args[0], args[1], get_option(opts, '--reference'), '--json' in opts)
        elif cmd == 'text-rebuild' and len(args) == 3:
            cmd_text_rebuild(args[0], args[1], args[2])
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
