"""
bgi_extract - Extracción de todo el contenido de los .arc de BGI y reempaquetado
Usado por la CLI (bgi_tool.py) y la GUI (bgi_gui.py).

La extracción escribe <salida>/<arc>/<entrada>: las imágenes (CompressedBG o BGI) como PNG,
el audio bw como .ogg y el resto sin la capa DSC. Un registro <arc>/_files.json guarda cómo
se extrajo cada entrada y el SHA-1 del archivo escrito; el reempaquetado usa ese registro para
saber qué archivos se editaron y solo vuelve a codificar esos. Las entradas se procesan en
paralelo, en procesos de baja prioridad.

Los archivos sueltos de la carpeta del juego (los de un parche, que el motor usa en lugar de
los del .arc) se extraen aparte, en <salida>/loose_files/.
"""

import hashlib
import io
import json
import os
import sys
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from PIL import Image

from bgi_arc import BgiArc, collect_files, read_header, write_arc
from bgi_audio import bw_to_ogg, is_bw, ogg_to_bw
from bgi_codecs import dsc_pack, is_dsc, unpack, unpack_head
from bgi_common import atomic_write, check_cancel, report, safe_name
from bgi_image import EXTENSIONS, decode, detect, image_info, image_to_bgi

FILES_FILE = '_files.json'
PROGRESS_FILE = '_progress.txt'
LOOSE_DIR = 'loose_files'
DEFAULT_PNG_LEVEL = 6
LOOSE_KINDS = {'cbg', 'bgi', 'script', 'bw', 'bp', 'ttf', 'otf'}     # sueltos que usa el motor


def find_arcs(src) -> List[Path]:
    """src puede ser un .arc o la carpeta del juego."""
    src = Path(src)
    if src.is_file():
        return [src]
    return sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() == '.arc')


def find_loose(folder) -> List[Path]:
    """Archivos sueltos del motor en la carpeta del juego (imágenes, guiones, audio)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.suffix.lower() in ('.arc', '.exe', '.dll', '.ico', '.hvl', '.txt', '.ini'):
            continue
        try:
            with open(p, 'rb') as f:
                head = f.read(0x400)
        except OSError:
            continue
        if is_dsc(head) or detect(unpack_head(head)) in LOOSE_KINDS:
            out.append(p)
    return out


def default_workers() -> int:
    """La mitad de los núcleos: deja el resto del equipo libre."""
    return max(1, (os.cpu_count() or 2) // 2)


@dataclass
class ExtractResult:
    files: int = 0              # archivos escritos
    converted: int = 0          # imágenes convertidas a PNG y audio a .ogg
    skipped: int = 0            # ya hechas (--resume)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    last_png: str = ''


def read_files_info(folder) -> dict:
    try:
        return json.loads((Path(folder) / FILES_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _write_files_info(folder: Path, info: dict):
    info['files'] = dict(sorted(info.get('files', {}).items()))
    with atomic_write(folder / FILES_FILE) as f:
        f.write(json.dumps(info, ensure_ascii=False, indent=1).encode('utf-8'))


def _read_done(path: Path) -> Set[str]:
    try:
        return set(path.read_text(encoding='utf-8').splitlines())
    except OSError:
        return set()


def _low_priority():
    """Baja la prioridad del proceso de trabajo para que el PC siga respondiendo."""
    try:
        if sys.platform == 'win32':
            import ctypes
            BELOW_NORMAL_PRIORITY_CLASS = 0x4000
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(10)
    except (OSError, AttributeError):
        pass


_open_arcs: Dict[str, Tuple[BgiArc, dict]] = {}     # un archivo abierto por .arc en cada proceso


def _archive(path: str) -> Tuple[BgiArc, dict]:
    hit = _open_arcs.get(path)
    if hit is None:
        arc = BgiArc(path)
        hit = _open_arcs[path] = (arc, {e.index: e for e in arc.entries})
    return hit


def _close_archives():
    for arc, _ in _open_arcs.values():
        arc.close()
    _open_arcs.clear()


def _write(path: Path, data: bytes) -> str:
    with atomic_write(path) as f:
        f.write(data)
    return hashlib.sha1(data).hexdigest()


def convert_entry(name: str, raw: bytes, out: Path, png_level: int):
    """
    Guarda una entrada (o archivo suelto) en out. Devuelve (registro, convertido, último PNG);
    el registro va a _files.json: [ruta, SHA-1, conversión ('image', 'bw' o None), bpp, DSC].
    """
    data = unpack(raw)
    kind = detect(data[:32])
    base = safe_name(name)
    dsc = is_dsc(raw)
    if kind in ('cbg', 'bgi'):
        img = decode(data)
        buf = io.BytesIO()
        img.save(buf, format='PNG', compress_level=png_level)
        rel = base + '.png'
        return [rel, _write(out / rel, buf.getvalue()), 'image', image_info(data)[2], dsc], True, str(out / rel)
    if kind == 'bw' and is_bw(data):
        rel = base + '.ogg'
        return [rel, _write(out / rel, bw_to_ogg(data)), 'bw', 0, dsc], True, ''
    ext = EXTENSIONS.get(kind, '')
    rel = base if base.lower().endswith(ext) else base + ext
    last = str(out / rel) if kind in ('png', 'bmp') else ''
    return [rel, _write(out / rel, data), None, 0, dsc], False, last


def _process_entry(arc_path: str, index: int, out_dir: str, png_level: int):
    """
    Extrae una entrada (o un archivo suelto si index < 0). Corre en un proceso aparte.
    Devuelve (nombre, registro, convertido, errores, último PNG, terminado).
    """
    out = Path(out_dir)
    if index < 0:
        name, label = Path(arc_path).name, f"{LOOSE_DIR}/{Path(arc_path).name}"
    else:
        arc, by_index = _archive(arc_path)
        e = by_index[index]
        name, label = e.name, f"{Path(arc_path).stem}/{e.name}"
    try:
        raw = Path(arc_path).read_bytes() if index < 0 else arc.read_raw(e)
        record, conv, last = convert_entry(name, raw, out, png_level)
        return name, record, conv, [], last, True
    except Exception as ex:
        return name, None, False, [f"{label}: {ex}"], '', False


def extract_all(src, out_root, resume: bool = False, workers: Optional[int] = None,
                png_level: int = DEFAULT_PNG_LEVEL, names: Optional[Iterable[str]] = None,
                loose: bool = True, progress=None, cancel=None) -> ExtractResult:
    """
    Extrae las entradas de los .arc (src: un .arc o la carpeta del juego) en <out_root>/<arc>/.
    Con una carpeta y loose, también los archivos sueltos del motor en <out_root>/loose_files/.
    Con resume, se saltan las entradas ya hechas. names: solo esas entradas; se suman a una
    extracción anterior sin borrar su registro.

    progress recibe como nombre la ruta del último PNG escrito (o '<arc>/<entrada>').
    """
    out_root = Path(out_root)
    workers = max(1, workers or default_workers())
    res = ExtractResult()
    selected = set(names) if names is not None else None
    merge = resume or selected is not None

    arcs = find_arcs(src)
    groups = [(str(p), p.stem) for p in arcs]
    loose_files = find_loose(src) if loose and Path(src).is_dir() and selected is None else []
    if loose_files:
        groups.append((None, LOOSE_DIR))
    if not groups:
        raise ValueError(f"There are no .arc files in {src}")

    jobs = []               # (archivo, índice, out_dir)
    progress_files, infos = {}, {}
    for arc_path, stem in groups:
        if arc_path is None:
            wanted = [(str(p), -1, p.name) for p in loose_files]
        else:
            try:
                with BgiArc(arc_path) as arc:
                    wanted = [(arc_path, e.index, e.name) for e in arc.entries
                              if selected is None or e.name in selected]
            except (OSError, ValueError) as ex:
                res.errors.append(f"{Path(arc_path).name}: {ex}")
                continue
        if not wanted:
            continue
        out_dir = out_root / stem
        out_dir.mkdir(parents=True, exist_ok=True)
        prog = out_dir / PROGRESS_FILE
        done = _read_done(prog) if resume and selected is None else set()
        if not merge:
            prog.write_text('', encoding='utf-8')
        progress_files[stem] = prog
        info = read_files_info(out_dir) if merge else {}
        info['arc'] = Path(arc_path).name if arc_path else ''
        info.setdefault('files', {})
        infos[stem] = info
        for path, index, name in wanted:
            if name in done:
                res.skipped += 1
            else:
                jobs.append((path, index, str(out_dir), stem))

    total = len(jobs)
    finished = 0

    def collect(stem, r):
        nonlocal finished
        name, record, conv, errors, last_png, ok = r
        if record:
            infos[stem]['files'][name] = record
            res.files += 1
            res.converted += conv
        res.errors += errors
        if last_png:
            res.last_png = last_png
        if ok:
            with open(progress_files[stem], 'a', encoding='utf-8') as f:
                f.write(name + '\n')
        report(progress, finished, total, last_png or f"{stem}/{name}")
        finished += 1

    try:
        if workers == 1 or total < 8:
            try:
                for path, index, out_dir, stem in jobs:
                    check_cancel(cancel)
                    collect(stem, _process_entry(path, index, out_dir, png_level))
            finally:
                _close_archives()
        else:
            pool = ProcessPoolExecutor(max_workers=workers, initializer=_low_priority)
            try:
                pending = {pool.submit(_process_entry, path, index, out_dir, png_level): stem
                           for path, index, out_dir, stem in jobs}
                while pending:
                    check_cancel(cancel)
                    done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                    for fut in done:
                        collect(pending.pop(fut), fut.result())
            finally:
                # Al cancelar, las entradas en curso terminan (se escriben de forma atómica)
                # y las pendientes se descartan
                pool.shutdown(wait=True, cancel_futures=True)
    finally:
        # También al cancelar: lo extraído hasta ahora se puede reempaquetar
        for stem, info in infos.items():
            _write_files_info(Path(progress_files[stem]).parent, info)

    report(progress, total, total, res.last_png)
    return res


# =============================================================================
# Reempaquetado
# =============================================================================

@dataclass
class RepackResult:
    entries: int = 0                                    # entradas del .arc de salida
    changed: List[str] = field(default_factory=list)    # entradas reemplazadas
    added: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def find_files_folder(folder, arc_path) -> Path:
    """Acepta la carpeta de un .arc (<salida>/data02400) o la de toda la extracción (<salida>)."""
    folder = Path(folder)
    sub = folder / Path(arc_path).stem
    if not (folder / FILES_FILE).exists() and (sub / FILES_FILE).exists():
        folder = sub
    if not (folder / FILES_FILE).exists():
        raise ValueError(f"{FILES_FILE} not found in {folder}: extract the files first "
                         "with this version of the tool")
    return folder


def _sha1_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha1()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def encode_file(path: Path, conv: Optional[str], bpp: int, dsc: bool, original: Optional[bytes],
                size: Optional[Tuple[int, int]] = None) -> bytes:
    """Vuelve a codificar un archivo extraído para guardarlo en el .arc."""
    if conv == 'image':
        with Image.open(path) as img:
            img.load()
            if size and img.size != size:
                raise ValueError(f"it is {img.size[0]}×{img.size[1]} and the original is "
                                 f"{size[0]}×{size[1]}: images must keep their size")
            data = image_to_bgi(img, bpp or (32 if 'A' in img.mode else 24))
        return dsc_pack(data)       # imagen BGI en un DSC, como en sysgrp.arc
    data = path.read_bytes()
    if conv == 'bw':
        return ogg_to_bw(data, original)
    return dsc_pack(data) if dsc else data


def repack(original_arc, folder, out_arc, add_new: bool = False,
           progress=None, cancel=None) -> RepackResult:
    """
    Crea out_arc a partir de original_arc con los archivos editados desde la extracción
    (según los SHA-1 de _files.json). Las imágenes editadas se guardan como imagen BGI sin
    comprimir y deben conservar su tamaño; el audio vuelve a bw. Las demás entradas se
    copian tal cual. add_new: también añade los archivos de la carpeta que no están en el .arc
    (los PNG como imagen y los .ogg como audio, con el nombre sin extensión).
    """
    original_arc = Path(original_arc)
    if original_arc.resolve() == Path(out_arc).resolve():
        raise ValueError("The output .arc must be different from the original")
    folder = find_files_folder(folder, original_arc)
    files = read_files_info(folder).get('files', {})
    res = RepackResult()
    with BgiArc(original_arc) as arc:
        replaced: Dict[str, bytes] = {}
        known_paths = set()
        total = len(arc.entries)
        for i, e in enumerate(arc.entries):
            check_cancel(cancel)
            rec = files.get(e.name)
            if not rec:
                continue
            rel, sha, conv, bpp, dsc = rec
            path = folder / rel
            known_paths.add(path.resolve())
            if not path.exists() or _sha1_file(path) == sha:
                continue        # sin cambios (o borrado: se deja el original)
            report(progress, i, total, e.name)
            try:
                original = arc.read(e)
                size = image_info(original)[:2] if conv == 'image' else None
                replaced[e.name] = encode_file(path, conv, bpp, dsc, original, size)
                res.changed.append(e.name)
            except Exception as ex:
                res.errors.append(f"{e.name}: {ex}")

        new_items = []
        if add_new:
            existing = {e.name.lower() for e in arc.entries}
            for p in sorted(folder.iterdir()):
                if not p.is_file() or p.resolve() in known_paths or p.name.startswith('_') \
                        or p.name.endswith('.tmp'):
                    continue
                ext = p.suffix.lower()
                name = p.stem if ext in ('.png', '.ogg') else p.name
                if name.lower() in existing:
                    continue
                try:
                    conv = {'.png': 'image', '.ogg': 'bw'}.get(ext)
                    new_items.append((name, encode_file(p, conv, 0, False, None)))
                    res.added.append(name)
                except Exception as ex:
                    res.errors.append(f"{p.name}: {ex}")

        if res.errors:
            return res
        if not res.changed and not res.added:
            return res                  # nada editado: no se escribe ningún archivo
        items = [(e.name, replaced[e.name] if e.name in replaced else (str(arc.path), e.offset, e.size))
                 for e in arc.entries] + new_items
        extras = [e.extra for e in arc.entries]
        res.entries = len(items)
        write_arc(out_arc, items, arc.version, extras, cancel=cancel)
    report(progress, total, total)
    return res


def pack(folder, arc_path, like=None, progress=None, cancel=None) -> int:
    """
    Empaqueta los archivos de una carpeta en un .arc: los .png como imagen BGI y los .ogg como
    audio bw, con el nombre sin extensión; el resto tal cual. Con like (un .arc), se sigue su
    orden y versión, y los archivos nuevos van al final.
    """
    files = collect_files(folder)
    if not files:
        raise ValueError("There are no files to pack")
    folder, arc_path = Path(folder).resolve(), Path(arc_path).resolve()
    if folder in arc_path.parents:
        raise ValueError("The output .arc cannot be inside the input folder")
    conversions = {'.png': 'image', '.ogg': 'bw'}
    by_name = {}
    for name, path in files:
        conv = conversions.get(path.suffix.lower())
        by_name.setdefault(path.stem if conv else name, (path, conv))
    version, order, extras = 1, [], []
    if like:
        version, entries = read_header(like)
        for e in entries:
            if e.name in by_name:
                order.append(e.name)
                extras.append(e.extra)
    order += sorted(n for n in by_name if n not in set(order))
    items = []
    for i, name in enumerate(order):
        check_cancel(cancel)
        path, conv = by_name[name]
        report(progress, i, len(order), path.name)
        items.append((name, encode_file(path, conv, 0, False, None) if conv else path))
    return write_arc(arc_path, items, version, extras, cancel=cancel)
