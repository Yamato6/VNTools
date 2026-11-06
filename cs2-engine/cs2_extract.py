"""
cs2_extract - Extracción de todo el contenido de los .int y reempaquetado
Usado por la CLI (cs2_tool.py) y la GUI (cs2_gui.py).

La extracción escribe <salida>/<int>/<entrada> (las imágenes HG-3 como PNG, una por
fotograma) y un registro <int>/_files.json con los archivos y el SHA-1 de cada uno; el
reempaquetado usa ese registro para saber qué archivos se editaron y solo vuelve a
codificar esos. Las entradas se procesan en paralelo, en procesos de baja prioridad.
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

from cs2_common import atomic_write, check_cancel, report, safe_name
from cs2_crypto import find_passphrase, key_from_passphrase
from cs2_hg3 import decode, frame_names, is_hg3, read_frames, replace_frames
from cs2_int import IntArchive, KeyRequired, is_encrypted, write_int

FILES_FILE = '_files.json'
PROGRESS_FILE = '_progress.txt'
DEFAULT_PNG_LEVEL = 6

# Categorías por extensión; lo que no aparece aquí va a 'data'
CATEGORIES = {
    'images': {'hg3', 'anm', 'png', 'bmp', 'jpg', 'jpeg'},
    'audio': {'ogg', 'wav'},
    'video': {'wmv', 'mpg', 'mpeg', 'ogv', 'avi', 'mp4'},
    'scripts': {'cst', 'kcs', 'fes'},
    'data': set(),
}
ALL_CATEGORIES = tuple(CATEGORIES)


def category_of(ext: str) -> str:
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return 'data'


def find_ints(src) -> List[Path]:
    """src puede ser un .int o la carpeta del juego."""
    src = Path(src)
    if src.is_file():
        return [src]
    return sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() == '.int')


def default_workers() -> int:
    """La mitad de los núcleos: deja el resto del equipo libre."""
    return max(1, (os.cpu_count() or 2) // 2)


def resolve_key(src, phrase: Optional[str] = None) -> Tuple[Optional[int], str]:
    """
    Clave principal para los .int de src (un .int o la carpeta del juego): la de la frase
    indicada o, si no, la que se encuentra en la carpeta del juego.
    Devuelve (clave, descripción) o (None, motivo).
    """
    if phrase:
        return key_from_passphrase(phrase), f"passphrase {phrase}"
    found, origin = find_passphrase(src)
    if found:
        return key_from_passphrase(found), f"passphrase {found} (from {origin})"
    return None, "no passphrase found in the game folder"


# =============================================================================
# Extracción
# =============================================================================

@dataclass
class ExtractResult:
    files: int = 0              # archivos escritos
    converted: int = 0          # imágenes HG-3 convertidas (PNG escritos)
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


_open_ints: Dict[str, Tuple[IntArchive, dict]] = {}    # un archivo abierto por .int en cada proceso


def _archive(path: str, key: Optional[int]) -> Tuple[IntArchive, dict]:
    """El .int abierto y sus entradas por índice."""
    hit = _open_ints.get(path)
    if hit is None:
        arc = IntArchive(path, key)
        hit = _open_ints[path] = (arc, {e.index: e for e in arc.entries})
    return hit


def _close_archives():
    for arc, _ in _open_ints.values():
        arc.close()
    _open_ints.clear()


def _save_png(path: Path, img: Image.Image, level: int) -> str:
    buf = io.BytesIO()
    img.save(buf, format='PNG', compress_level=level)
    data = buf.getvalue()
    with atomic_write(path) as f:
        f.write(data)
    return hashlib.sha1(data).hexdigest()


def _process_entry(int_path: str, key: Optional[int], index: int, out_dir: str,
                   convert: bool, png_level: int):
    """
    Extrae una entrada. Corre en un proceso aparte.
    Devuelve (nombre, registro, archivos, convertidos, errores, avisos, último PNG, terminado);
    el registro va a _files.json: [rutas, SHA-1, tipo de conversión o None].
    """
    arc, by_index = _archive(int_path, key)
    e = by_index[index]
    out = Path(out_dir)
    label = f"{Path(int_path).stem}/{e.name}"
    try:
        name = safe_name(e.name)
        if e.ext == 'hg3' and convert:
            data = arc.read(e)
            if is_hg3(data):
                frames = decode(data)
                stem = name.rsplit('.', 1)[0]
                rels = frame_names(stem, len(frames))
                shas = [_save_png(out / rel, img, png_level) for rel, (img, _fr) in zip(rels, frames)]
                return (e.name, [rels, shas, 'hg3'], len(rels), len(rels), [], [],
                        str(out / rels[-1]), True)
            warn = [f"{label}: not an HG-3 image, saved unchanged"]
        else:
            warn = []
        sha = hashlib.sha1()
        with atomic_write(out / name) as f:
            for chunk in arc.iter_data(e):
                sha.update(chunk)
                f.write(chunk)
        last = str(out / name) if e.ext in ('png', 'bmp', 'jpg', 'jpeg') else ''
        return e.name, [[name], [sha.hexdigest()], None], 1, 0, [], warn, last, True
    except Exception as ex:
        return e.name, None, 0, 0, [f"{label}: {ex}"], [], '', False


def extract_all(src, out_root, categories: Iterable[str] = ALL_CATEGORIES, convert: bool = True,
                resume: bool = False, phrase: Optional[str] = None, workers: Optional[int] = None,
                png_level: int = DEFAULT_PNG_LEVEL, progress=None, cancel=None) -> ExtractResult:
    """
    Extrae las entradas de los .int (src: un .int o la carpeta del juego) en
    <out_root>/<int>/. Con convert, las imágenes HG-3 se guardan como PNG (NOMBRE.png, o
    NOMBRE_NN.png si tienen varios fotogramas); el resto, tal cual. phrase: frase del juego
    (si no, se busca en la carpeta del juego). Con resume, se saltan las entradas ya hechas.

    progress recibe como nombre la ruta del último PNG escrito (o '<int>/<entrada>').
    """
    categories = set(categories)
    unknown = categories - set(CATEGORIES)
    if unknown:
        raise ValueError(f"Unknown category: {', '.join(sorted(unknown))}")
    out_root = Path(out_root)
    workers = max(1, workers or default_workers())
    res = ExtractResult()

    ints = find_ints(src)
    if not ints:
        raise ValueError(f"There are no .int files in {src}")
    key = None
    if any(is_encrypted(p) for p in ints):
        key, why = resolve_key(src, phrase)
        if key is None:
            raise KeyRequired(f"The archives are encrypted and {why}: enter the passphrase")

    jobs = []               # (int_path, índice, out_dir)
    progress_files = {}
    infos = {}
    for int_path in ints:
        try:
            with IntArchive(int_path, key) as arc:
                wanted = [e for e in arc.entries if category_of(e.ext) in categories]
        except ValueError as ex:
            res.errors.append(f"{int_path.name}: {ex}")
            continue
        if not wanted:
            continue
        out_dir = out_root / int_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        prog = out_dir / PROGRESS_FILE
        done = _read_done(prog) if resume else set()
        if not resume:
            prog.write_text('', encoding='utf-8')
        progress_files[str(int_path)] = prog
        info = read_files_info(out_dir) if resume else {}
        info['int'] = int_path.name
        info.setdefault('files', {})
        infos[str(int_path)] = info
        for e in wanted:
            if e.name in done:
                res.skipped += 1
            else:
                jobs.append((str(int_path), e.index, str(out_dir)))

    total = len(jobs)
    finished = 0

    def collect(int_path, r):
        nonlocal finished
        name, record, n_files, n_conv, errors, warnings, last_png, ok = r
        if record:
            infos[int_path]['files'][name] = record
        res.files += n_files
        res.converted += n_conv
        res.errors += errors
        res.warnings += warnings
        if last_png:
            res.last_png = last_png
        if ok:
            with open(progress_files[int_path], 'a', encoding='utf-8') as f:
                f.write(name + '\n')
        report(progress, finished, total, last_png or f"{Path(int_path).stem}/{name}")
        finished += 1

    try:
        if workers == 1 or total < 8:
            try:
                for int_path, index, out_dir in jobs:
                    check_cancel(cancel)
                    collect(int_path, _process_entry(int_path, key, index, out_dir, convert, png_level))
            finally:
                _close_archives()
        else:
            pool = ProcessPoolExecutor(max_workers=workers, initializer=_low_priority)
            try:
                pending = {pool.submit(_process_entry, int_path, key, index, out_dir, convert,
                                       png_level): int_path
                           for int_path, index, out_dir in jobs}
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
        for int_path, info in infos.items():
            _write_files_info(Path(progress_files[int_path]).parent, info)

    report(progress, total, total, res.last_png)
    return res


def extract_entries(int_path, out_dir, names: Optional[Iterable[str]] = None, key: Optional[int] = None,
                    convert: bool = False, png_level: int = DEFAULT_PNG_LEVEL,
                    progress=None, cancel=None) -> int:
    """
    Guarda las entradas de un .int en out_dir (names: solo esas). Con convert, las imágenes
    HG-3 se guardan como PNG (NOMBRE.png o NOMBRE_NN.png); el resto, tal cual.
    Devuelve el número de archivos escritos.
    """
    out_dir = Path(out_dir)
    written = 0
    with IntArchive(int_path, key) as arc:
        entries = arc.entries
        if names is not None:
            wanted = set(names)
            entries = [e for e in entries if e.name in wanted]
        total = len(entries)
        for i, e in enumerate(entries):
            check_cancel(cancel)
            name = safe_name(e.name)
            if convert and e.ext == 'hg3':
                data = arc.read(e)
                if is_hg3(data):
                    frames = decode(data)
                    rels = frame_names(name.rsplit('.', 1)[0], len(frames))
                    for rel, (img, _fr) in zip(rels, frames):
                        _save_png(out_dir / rel, img, png_level)
                    written += len(rels)
                    report(progress, i, total, str(out_dir / rels[-1]))
                    continue
            report(progress, i, total, e.name)
            arc.save(e, out_dir / name)
            written += 1
    report(progress, total, total)
    return written


# =============================================================================
# Reempaquetado
# =============================================================================

@dataclass
class RepackResult:
    entries: int = 0                                    # entradas del .int de salida
    changed: List[str] = field(default_factory=list)    # entradas reemplazadas
    added: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def find_files_folder(folder, int_path) -> Path:
    """Acepta la carpeta de un .int (<salida>/image00) o la de toda la extracción (<salida>)."""
    folder = Path(folder)
    sub = folder / Path(int_path).stem
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


def repack(original_int, folder, out_int, add_new: bool = False, phrase: Optional[str] = None,
           progress=None, cancel=None) -> RepackResult:
    """
    Crea out_int a partir de original_int con los archivos editados desde la extracción
    (según los SHA-1 de _files.json), con el mismo cifrado que el original. Los PNG de las
    imágenes HG-3 vuelven a codificarse (solo los fotogramas cambiados) y deben conservar
    su tamaño. Las demás entradas se copian sin descifrar.
    add_new: también añade los archivos de la carpeta que no están en el .int.
    """
    original_int = Path(original_int)
    if original_int.resolve() == Path(out_int).resolve():
        raise ValueError("The output .int must be different from the original")
    folder = find_files_folder(folder, original_int)
    files = read_files_info(folder).get('files', {})
    key = None
    if is_encrypted(original_int):
        key, why = resolve_key(original_int, phrase)
        if key is None:
            raise KeyRequired(f"{original_int.name} is encrypted and {why}: enter the passphrase")
    res = RepackResult()
    with IntArchive(original_int, key) as arc:
        replaced: Dict[str, object] = {}
        known_paths = set()
        total = len(arc.entries)
        for i, e in enumerate(arc.entries):
            check_cancel(cancel)
            rec = files.get(e.name)
            if not rec:
                continue
            rels, shas, conv = rec
            paths = [folder / r for r in rels]
            known_paths.update(p.resolve() for p in paths)
            changed = [k for k, (p, sha) in enumerate(zip(paths, shas))
                       if p.exists() and _sha1_file(p) != sha]
            if not changed:
                continue        # sin cambios (o borrado: se deja el original)
            report(progress, i, total, e.name)
            try:
                if conv == 'hg3':
                    data = arc.read(e)
                    if len(read_frames(data)) != len(rels):
                        raise ValueError("the number of frames does not match the extraction")
                    images = {}
                    for k in changed:
                        with Image.open(paths[k]) as img:
                            img.load()
                            images[k] = img.copy()
                    replaced[e.name] = replace_frames(data, images)
                else:
                    replaced[e.name] = paths[0]
                res.changed.append(e.name)
            except Exception as ex:
                res.errors.append(f"{e.name}: {ex}")

        new_items = []
        if add_new:
            existing = {e.name.lower() for e in arc.entries}
            for p in sorted(folder.rglob('*')):
                if not p.is_file() or p.resolve() in known_paths:
                    continue
                rel = p.relative_to(folder)
                if any(part.startswith('_') for part in rel.parts) or p.name.endswith('.tmp'):
                    continue
                name = '\\'.join(rel.parts)
                if name.lower() not in existing:
                    new_items.append((name, p))
                    res.added.append(name)

        if res.errors:
            return res
        if not res.changed and not res.added:
            return res                  # nada editado: no se escribe ningún archivo
        items = [(e.name, replaced[e.name] if e.name in replaced else ('entry', arc, e))
                 for e in arc.entries] + new_items
        res.entries = len(items)
        write_int(out_int, items, arc.crypto, cancel=cancel)
    report(progress, total, total)
    return res
