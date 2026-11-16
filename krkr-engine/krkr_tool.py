#!/usr/bin/env python3
"""
KiriKiri Tools - Extractor y creador de parches .xp3 del motor KiriKiri 2 (KAG / KAGEX)
Probado con Noble Works (Yuzusoft, 2010).

Sin argumentos abre la interfaz gráfica (krkr_gui.py).
La lógica está en krkr_xp3.py, krkr_tlg.py, krkr_image.py, krkr_psb.py, krkr_script.py
y krkr_extract.py.
"""

import multiprocessing
import sys
import time
from collections import Counter
from pathlib import Path

import krkr_extract
import krkr_script
from krkr_image import kind_of
from krkr_psb import to_json
from krkr_tlg import native_available
from krkr_xp3 import WARNING_TEXT, Xp3


def print_progress(i, total, name):
    if name and i < total:
        print(f" [{i + 1}/{total}] {name}")


def cmd_list(path: str):
    with Xp3(path) as x:
        files = x.files()
        kinds = [kind_of(e.name, x.head(e, 16)) for e in files]
        print(f"{path}: {len(files)} files")
        print("  " + ", ".join(f"{n} {k}" for k, n in Counter(kinds).most_common()))
        for e, k in zip(files, kinds):
            print(f" {e.name:<40} {k:<6} {e.size:>12}")
        print(f"Total: {sum(e.size for e in files)} bytes")
        if x.has_warning:
            print(f"Note: {WARNING_TEXT}")


def cmd_extract(src: str, out_dir: str, resume: bool, composed: bool, workers):
    t = time.time()
    if not native_available():
        print("Note: _krkr.dll was not found; using the Python version (much slower)")
    r = krkr_extract.extract_all(src, out_dir, resume, workers, composed=composed, progress=print_progress)
    for w in r.warnings[:20]:
        print(f" Warning: {w}")
    for err in r.errors:
        print(f" ! {err}")
    print(f"Done in {time.time() - t:.0f} s: {r.files} files ({r.converted} converted, "
          f"{r.composed} composed sprites), {len(r.errors)} errors"
          + (f" ({r.skipped} already done)" if r.skipped else ""))
    if r.protected:
        print(f"Note: {WARNING_TEXT}")


def cmd_pack(folder: str, out_xp3: str):
    r = krkr_extract.pack_patch(folder, out_xp3, progress=print_progress)
    for err in r.errors:
        print(f" ! {err}")
    if r.errors:
        print("The patch was not created: fix the errors and try again")
        sys.exit(1)
    if not r.entries:
        print("No file was edited since the extraction: no file was written")
        return
    for name in r.changed:
        print(f"  edited: {name}")
    print(f"Patch created: {out_xp3} ({r.entries} files, {len(r.changed) + len(r.added)} new)")


def cmd_text(game: str, out_dir: str, reference: bool, json_out: bool):
    r = krkr_script.extract_text(game, out_dir, reference, json_out, progress=print_progress)
    for w in r.warnings:
        print(f"  [WARNING] {w}")
    if r.reference:
        print(f"  Reference: the text without {r.reference}")
    print(f"{r.lines} lines from {r.files} scripts in {out_dir}")


def cmd_text_rebuild(game: str, text_dir: str, out_xp3: str):
    rep = krkr_script.rebuild(game, text_dir, out_xp3, progress=print_progress)
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
        print(f"  Output: {out_xp3} ({rep.output_entries} files)")


def cmd_psb(path: str, out: str):
    Path(out).write_text(to_json(Path(path).read_bytes()), encoding='utf-8')
    print(f"Written: {out}")


def usage():
    print("KiriKiri Tools - .xp3 tools for the KiriKiri 2 engine (Noble Works)")
    print()
    print("Usage:")
    print("  python krkr_tool.py                        (opens the graphical interface)")
    print("  python krkr_tool.py list    <file.xp3>")
    print("  python krkr_tool.py extract <file.xp3|game_folder> <output_folder> [--resume] [--workers N]")
    print("                              [--no-composed]")
    print("  python krkr_tool.py pack    <folder> <patch.xp3>")
    print("  python krkr_tool.py psb     <file.psb> <output.json>")
    print()
    print("Text (scene databases scene.sdb / scenedata.sdb / scenepatch.sdb):")
    print("  python krkr_tool.py text <game_folder> <text_folder> [--reference] [--json]")
    print("  python krkr_tool.py text-rebuild <game_folder> <text_folder> <patch.xp3>")
    print()
    print("'extract' options:")
    print("  --resume       continue where the last run stopped (_progress.txt)")
    print("  --workers N    parallel processes (default: half of the CPU cores)")
    print("  --no-composed  do not compose the layered sprites (fgimage)")
    print()
    print("Other options:")
    print("  --reference    add the text of the game without its text patch (e.g. the Japanese")
    print("                 under an English patch) as '#>' comments")
    print("  --json         also write the text as JSON (with the voice file of each line)")
    print()
    print("'pack' and 'text-rebuild' write a patch: copy it to the game folder with the next free")
    print("number (patch4.xp3 if the game has patch3.xp3). If the patch exists, its other files are kept.")
    print()
    print("Examples:")
    print("  python krkr_tool.py extract \"D:\\Games\\Noble Works\" out --resume")
    print("  python krkr_tool.py pack out \"D:\\Games\\Noble Works_new\\patch4.xp3\"")
    print("  python krkr_tool.py text \"D:\\Games\\Noble Works\" text --reference")
    print("  python krkr_tool.py text-rebuild \"D:\\Games\\Noble Works\" text \"D:\\Games\\Noble Works_new\\patch4.xp3\"")


def get_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


VALUE_OPTIONS = ('--workers',)


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
            import krkr_gui
        except ImportError as e:
            print(f"Could not open the graphical interface: {e}")
            print("Install the dependencies with: pip install -r requirements.txt")
            print()
            usage()
            return
        krkr_gui.main()
        return

    cmd = sys.argv[1].lower()
    opts = sys.argv[2:]
    args = positional(opts)

    try:
        if cmd == 'list' and len(args) == 1:
            cmd_list(args[0])
        elif cmd == 'extract' and len(args) == 2:
            workers = get_option(opts, '--workers')
            cmd_extract(args[0], args[1], '--resume' in opts, '--no-composed' not in opts,
                        int(workers) if workers else None)
        elif cmd == 'pack' and len(args) == 2:
            cmd_pack(args[0], args[1])
        elif cmd == 'psb' and len(args) == 2:
            cmd_psb(args[0], args[1])
        elif cmd == 'text' and len(args) == 2:
            cmd_text(args[0], args[1], '--reference' in opts, '--json' in opts)
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
