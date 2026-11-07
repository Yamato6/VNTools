#!/usr/bin/env python3
"""
BGI Tools - Extractor y reempaquetador de archivos .arc del motor BGI / Ethornell
Probado con Daitoshokan no Hitsujikai (August, 2013).

Sin argumentos abre la interfaz gráfica (bgi_gui.py).
La lógica está en bgi_arc.py, bgi_codecs.py, bgi_image.py, bgi_audio.py, bgi_script.py
y bgi_extract.py.
"""

import multiprocessing
import sys
import time
from collections import Counter
from pathlib import Path

import bgi_extract
import bgi_script
from bgi_arc import BgiArc
from bgi_codecs import native_available, unpack_head
from bgi_common import CharMap
from bgi_image import detect


def print_progress(i, total, name):
    if name and i < total:
        print(f" [{i + 1}/{total}] {name}")


def cmd_list(arc_path: str):
    with BgiArc(arc_path) as arc:
        kinds = []
        for e in arc.entries:
            kinds.append(detect(unpack_head(arc.head(e, 0x400))))
        print(f"{arc_path}: {len(arc.entries)} files (version {arc.version})")
        print("  " + ", ".join(f"{n} {k}" for k, n in Counter(kinds).most_common()))
        for e, k in zip(arc.entries, kinds):
            print(f" {e.name:<20} {k:<7} {e.size:>12}")
        print(f"Total: {sum(e.size for e in arc.entries)} bytes")


def cmd_extract(src: str, out_dir: str, resume: bool, loose: bool, workers):
    t = time.time()
    if not native_available():
        print("Note: _bgi.dll was not found; using the Python version (much slower)")
    r = bgi_extract.extract_all(src, out_dir, resume, workers, loose=loose, progress=print_progress)
    for w in r.warnings[:20]:
        print(f" Warning: {w}")
    for err in r.errors:
        print(f" ! {err}")
    print(f"Done in {time.time() - t:.0f} s: {r.files} files ({r.converted} converted), "
          f"{len(r.errors)} errors" + (f" ({r.skipped} already done)" if r.skipped else ""))


def cmd_repack(arc_path: str, folder: str, out_arc: str, add_new: bool):
    r = bgi_extract.repack(arc_path, folder, out_arc, add_new, progress=print_progress)
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
    print(f"Archive created: {out_arc} ({r.entries} files, {len(r.changed)} replaced, {len(r.added)} added)")


def cmd_pack(folder: str, out_arc: str, like):
    count = bgi_extract.pack(folder, out_arc, like, progress=print_progress)
    print(f"Archive created: {out_arc} ({count} files)")


def cmd_text(arc_path: str, out_dir: str, reference, charmap, reverse: bool, json_out: bool):
    r = bgi_script.extract_text(arc_path, out_dir, reference, charmap, reverse, json_out,
                                progress=print_progress)
    for w in r.warnings:
        print(f"  [WARNING] {w}")
    print(f"{r.lines} lines from {r.files} scripts in {out_dir}")


def cmd_text_rebuild(arc_path: str, text_dir: str, out_arc: str, charmap):
    rep = bgi_script.rebuild(arc_path, text_dir, out_arc, charmap, progress=print_progress)
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
        print(f"  Output: {out_arc} ({rep.output_entries} files)")


def cmd_join_loops(folder: str, out_dir: str):
    """Une cada NOMBRE_head.ogg con su NOMBRE_loop.ogg (Ogg encadenado) en NOMBRE.ogg."""
    folder, out = Path(folder), Path(out_dir)
    count = 0
    for head in sorted(folder.glob('*_head.ogg')):
        loop = head.with_name(head.name[:-len('_head.ogg')] + '_loop.ogg')
        if loop.exists():
            out.mkdir(parents=True, exist_ok=True)
            (out / (head.name[:-len('_head.ogg')] + '.ogg')).write_bytes(head.read_bytes() + loop.read_bytes())
            count += 1
    print(f"{count} tracks joined in {out}")


def usage():
    print("BGI Tools - .arc tools for the BGI / Ethornell engine (Daitoshokan no Hitsujikai)")
    print()
    print("Usage:")
    print("  python bgi_tool.py                        (opens the graphical interface)")
    print("  python bgi_tool.py list    <file.arc>")
    print("  python bgi_tool.py extract <file.arc|game_folder> <output_folder> [--resume] [--workers N] [--no-loose]")
    print("  python bgi_tool.py repack  <original.arc> <folder> <output.arc> [--add-new]")
    print("  python bgi_tool.py pack    <folder> <output.arc> [--like <original.arc>]")
    print("  python bgi_tool.py join-loops <music_folder> <output_folder>")
    print()
    print("Text (data01000.arc):")
    print("  python bgi_tool.py text <scripts.arc> <text_folder> [--reference <folder|file.arc>] [--json]")
    print("                          [--charmap <map.txt> --reverse]")
    print("  python bgi_tool.py text-rebuild <scripts.arc> <text_folder> <output.arc> [--charmap <map.txt>]")
    print()
    print("'extract' options:")
    print("  --resume       continue where the last run stopped (_progress.txt)")
    print("  --workers N    parallel processes (default: half of the CPU cores)")
    print("  --no-loose     with a game folder, skip the loose files (patch) of the folder")
    print()
    print("Other options:")
    print("  --add-new      also add files of the folder that are not in the original .arc")
    print("  --like         keep the order of an original .arc")
    print("  --reference    the same scripts in another language (e.g. the game folder with the")
    print("                 English patch); its text is added to each line as a '#>' comment")
    print("  --json         also write the text as JSON (with the voice file of each line)")
    print("  --charmap      character table for a custom font ('á=ｱ' per line)")
    print("  --reverse      apply the table in reverse when extracting (already translated scripts)")
    print()
    print("Examples:")
    print("  python bgi_tool.py extract \"D:\\Games\\Daitoshokan\" out --resume")
    print("  python bgi_tool.py repack \"D:\\Games\\Daitoshokan\\data02400.arc\" out data02400.arc")
    print("  python bgi_tool.py text \"D:\\Games\\Daitoshokan\\data01000.arc\" text --reference \"D:\\Games\\Daitoshokan\"")
    print("  python bgi_tool.py text-rebuild \"D:\\Games\\Daitoshokan\\data01000.arc\" text data01000.arc")


def get_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


VALUE_OPTIONS = ('--charmap', '--like', '--workers', '--reference')


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
            import bgi_gui
        except ImportError as e:
            print(f"Could not open the graphical interface: {e}")
            print("Install the dependencies with: pip install -r requirements.txt")
            print()
            usage()
            return
        bgi_gui.main()
        return

    cmd = sys.argv[1].lower()
    opts = sys.argv[2:]
    args = positional(opts)

    try:
        if cmd == 'list' and len(args) == 1:
            cmd_list(args[0])
        elif cmd == 'extract' and len(args) == 2:
            workers = get_option(opts, '--workers')
            cmd_extract(args[0], args[1], '--resume' in opts, '--no-loose' not in opts,
                        int(workers) if workers else None)
        elif cmd == 'repack' and len(args) == 3:
            cmd_repack(args[0], args[1], args[2], '--add-new' in opts)
        elif cmd == 'pack' and len(args) == 2:
            cmd_pack(args[0], args[1], get_option(opts, '--like'))
        elif cmd == 'join-loops' and len(args) == 2:
            cmd_join_loops(args[0], args[1])
        elif cmd == 'text' and len(args) == 2:
            cmd_text(args[0], args[1], get_option(opts, '--reference'), load_charmap(opts),
                     '--reverse' in opts, '--json' in opts)
        elif cmd == 'text-rebuild' and len(args) == 3:
            cmd_text_rebuild(args[0], args[1], args[2], load_charmap(opts))
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
