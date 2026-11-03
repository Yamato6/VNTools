#!/usr/bin/env python3
"""
NeXAS Tools - Extractor y reempaquetador de archivos .pac del motor NeXAS (Giga)
Juego: Maji de Watashi ni Koi Shinasai! S (Minato Soft, 2012)

Sin argumentos abre la interfaz gráfica (nexas_gui.py).
La lógica está en nexas_pac.py, nexas_script.py, nexas_formats.py y nexas_extract.py.
"""

import sys
import time
from collections import Counter

import nexas_extract
import nexas_script
from nexas_pac import PACK_NAMES, Pac, pack


def print_progress(i, total, name):
    if name:
        print(f" [{i + 1}/{total}] {name}")


def cmd_list(pac_path: str):
    with Pac(pac_path) as pac:
        types = Counter(e.ext or '(none)' for e in pac.entries)
        print(f"{pac_path}: {len(pac.entries)} files, compression: {PACK_NAMES[pac.pack]}"
              + (" (old format)" if pac.old_format else ""))
        print("  " + ", ".join(f"{n} {ext}" for ext, n in types.most_common()))
        for e in pac.entries:
            print(f" {e.name:<44} {e.size:>12}")
        print(f"Total: {sum(e.size for e in pac.entries)} bytes")


def cmd_extract(src: str, out_dir: str, only, resume: bool, convert: bool, extras: bool, sprites: bool):
    t = time.time()
    r = nexas_extract.extract_all(src, out_dir, only, convert, extras, sprites, resume,
                                  progress=print_progress)
    for w in r.warnings[:20]:
        print(f" Warning: {w}")
    if len(r.warnings) > 20:
        print(f" ... and {len(r.warnings) - 20} more warnings")
    for err in r.errors:
        print(f" ! {err}")
    print(f"Done in {time.time() - t:.0f} s: {r.files} files ({r.converted} converted), "
          f"{r.extras} extra files, {len(r.errors)} errors"
          + (f" ({r.skipped} already done)" if r.skipped else ""))


def cmd_repack(pac_path: str, folder: str, out_pac: str, patch: bool, add_new: bool, allow_resize: bool):
    r = nexas_extract.repack(pac_path, folder, out_pac, patch, add_new, allow_resize,
                             progress=print_progress)
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
    print(f"Archive created: {out_pac} ({r.entries} files, {len(r.changed)} replaced, {len(r.added)} added)")


def cmd_pack(folder: str, out_pac: str, compression: int, like):
    count = pack(folder, out_pac, compression, like, progress=print_progress)
    print(f"Archive created: {out_pac} ({count} files)")


def cmd_text(pac_path: str, out_dir: str, reference, charmap, reverse: bool, listing: bool, json_out: bool):
    r = nexas_script.extract_text(pac_path, out_dir, reference, charmap, reverse, listing, json_out,
                                  progress=print_progress)
    for w in r.warnings:
        print(f"  [WARNING] {w}")
    print(f"{r.lines} lines from {r.files} files in {out_dir}")


def cmd_text_rebuild(pac_path: str, text_dir: str, out_pac: str, charmap, patch: bool):
    rep = nexas_script.rebuild(pac_path, text_dir, out_pac, charmap, patch, progress=print_progress)
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
        print(f"  Output: {out_pac} ({rep.output_entries} files)")


def usage():
    print("NeXAS Tools - .pac tools for the NeXAS engine (Maji de Watashi ni Koi Shinasai! S)")
    print()
    print("Usage:")
    print("  python nexas_tool.py                        (opens the graphical interface)")
    print("  python nexas_tool.py list    <file.pac>")
    print("  python nexas_tool.py extract <file.pac|game_folder> <output_folder>")
    print("                               [--only images,audio,video,scripts,data] [--resume]")
    print("                               [--raw] [--no-extras] [--sprites]")
    print("  python nexas_tool.py repack  <original.pac> <folder> <output.pac> [--patch] [--add-new] [--allow-resize]")
    print("  python nexas_tool.py pack    <folder> <output.pac> [--like <original.pac>] [--compression N]")
    print()
    print("Text (Script.pac, Update3.pac, Config.pac, Update2.pac):")
    print("  python nexas_tool.py text <file.pac> <text_folder> [--reference <other.pac>] [--json] [--listing]")
    print("                            [--charmap <map.txt> --reverse]")
    print("  python nexas_tool.py text-rebuild <file.pac> <text_folder> <output.pac> [--patch] [--charmap <map.txt>]")
    print()
    print("'extract' options:")
    print("  --only LIST    only these categories (default: all)")
    print("  --resume       continue where the last run stopped (_progress.txt)")
    print("  --raw          keep FIL masks as .fil instead of converting them to PNG")
    print("  --no-extras    don't write NMV frames, SPM as JSON and .dat tables as CSV")
    print("  --sprites      also compose every character sprite (SPM) into full images")
    print()
    print("Other options:")
    print("  --patch        the output only holds the changed files (save it as Update4.pac to Update8.pac)")
    print("  --add-new      also add files of the folder that are not in the original .pac")
    print("  --allow-resize allow images with a different size than the original")
    print("  --like         keep the order and compression of an original .pac")
    print("  --compression  0 none, 3 zlib (default 0)")
    print("  --reference    another .pac with the same scripts (e.g. Update3.pac): its text is added as '#>' comments")
    print("  --json         also write the text as JSON (with the voice file of each line)")
    print("  --listing      also write a disassembly of each script (listing folder)")
    print("  --charmap      character table for a custom font ('á=ｱ' per line)")
    print("  --reverse      apply the table in reverse when extracting (already translated scripts)")
    print()
    print("Examples:")
    print("  python nexas_tool.py list Script.pac")
    print("  python nexas_tool.py extract \"D:\\Games\\Majikoi S\" out --resume")
    print("  python nexas_tool.py repack Update2.pac out Update4.pac --patch")
    print("  python nexas_tool.py text Script.pac text --reference Update3.pac")
    print("  python nexas_tool.py text-rebuild Script.pac text Update4.pac --patch")


def get_option(args, name, default=None):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return default


VALUE_OPTIONS = ('--only', '--charmap', '--reference', '--like', '--compression')


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
    return nexas_script.CharMap.load(path) if path else None


def main():
    # La consola puede usar cp932 u otra codificación: no abortar por un nombre
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='replace')

    if len(sys.argv) < 2:
        try:
            import nexas_gui
        except ImportError as e:
            print(f"Could not open the graphical interface: {e}")
            print("Install the dependencies with: pip install -r requirements.txt")
            print()
            usage()
            return
        nexas_gui.main()
        return

    cmd = sys.argv[1].lower()
    opts = sys.argv[2:]
    args = positional(opts)

    try:
        if cmd == 'list' and len(args) == 1:
            cmd_list(args[0])
        elif cmd == 'extract' and len(args) == 2:
            only = get_option(opts, '--only')
            cats = [c.strip() for c in only.split(',') if c.strip()] if only else nexas_extract.ALL_CATEGORIES
            cmd_extract(args[0], args[1], cats, '--resume' in opts, '--raw' not in opts,
                        '--no-extras' not in opts, '--sprites' in opts)
        elif cmd == 'repack' and len(args) == 3:
            cmd_repack(args[0], args[1], args[2], '--patch' in opts, '--add-new' in opts,
                       '--allow-resize' in opts)
        elif cmd == 'pack' and len(args) == 2:
            cmd_pack(args[0], args[1], int(get_option(opts, '--compression', 0)), get_option(opts, '--like'))
        elif cmd == 'text' and len(args) == 2:
            cmd_text(args[0], args[1], get_option(opts, '--reference'), load_charmap(opts),
                     '--reverse' in opts, '--listing' in opts, '--json' in opts)
        elif cmd == 'text-rebuild' and len(args) == 3:
            cmd_text_rebuild(args[0], args[1], args[2], load_charmap(opts), '--patch' in opts)
        else:
            usage()
    except KeyboardInterrupt:
        print("Cancelled")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
