"""
krkr_extract - Extracción de los .xp3 de KiriKiri y empaquetado de parches
Usado por la CLI (krkr_tool.py) y la GUI (krkr_gui.py).

La extracción escribe <salida>/<xp3>/<ruta del archivo>: las imágenes TLG como PNG, los PSB
también como .json legible y el resto tal cual. El archivo del aviso "protected archive" se
omite. Un registro <xp3>/_files.json guarda cómo se extrajo cada archivo y el SHA-1 de lo
escrito; el empaquetado lo usa para saber qué se editó. Los sprites por capas de fgimage se
componen además en <xp3>/_composed/ (cada ropa y variante con la primera expresión, en el
tamaño más pequeño).

El empaquetado crea un parche (patchN.xp3) con los archivos editados: el motor carga
patch.xp3, patch2.xp3, patch3.xp3... por orden y sus archivos sustituyen a los del juego.
En un parche no hay carpetas: cada archivo va con su nombre solo.
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

from krkr_common import atomic_write, check_cancel, report, safe_name
from krkr_image import (compose, decode, detect, image_info, info_file, layer_file, parse_info,
                        parse_layers, sprite_names, sprite_variants)
from krkr_psb import to_json
from krkr_tlg import write_tlg5
from krkr_xp3 import Xp3, find_xp3, merge_into_xp3

FILES_FILE = '_files.json'
PROGRESS_FILE = '_progress.txt'
COMPOSED_DIR = '_composed'
DEFAULT_PNG_LEVEL = 6


def default_workers() -> int:
    """La mitad de los núcleos: deja el resto del equipo libre."""
    return max(1, (os.cpu_count() or 2) // 2)


@dataclass
class ExtractResult:
    files: int = 0              # archivos escritos
    converted: int = 0          # TLG convertidas a PNG y PSB a JSON
    composed: int = 0           # sprites compuestos
    skipped: int = 0            # ya hechos (resume)
    protected: bool = False     # algún .xp3 trae el aviso del autor
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


_open: Dict[str, Tuple[Xp3, dict, dict]] = {}       # un .xp3 abierto por proceso


def _archive(path: str) -> Tuple[Xp3, dict, dict]:
    hit = _open.get(path)
    if hit is None:
        x = Xp3(path)
        hit = _open[path] = (x, {e.index: e for e in x.entries}, {e.name: e for e in x.entries})
    return hit


def _close_archives():
    for x, _, _ in _open.values():
        x.close()
    _open.clear()


def _write(path: Path, data: bytes) -> str:
    with atomic_write(path) as f:
        f.write(data)
    return hashlib.sha1(data).hexdigest()


def png_name(name: str) -> str:
    base = safe_name(name)
    return (base[:-4] if base.lower().endswith('.tlg') else base) + '.png'


def convert_entry(name: str, data: bytes, out: Path, png_level: int):
    """
    Guarda un archivo en out. Devuelve (registro, convertido, último PNG); el registro va a
    _files.json: [ruta, SHA-1, conversión ('image' o None), bits].
    """
    kind = detect(data[:16])
    if kind == 'tlg':
        img = decode(data)
        buf = io.BytesIO()
        img.save(buf, format='PNG', compress_level=png_level)
        rel = png_name(name)
        return [rel, _write(out / rel, buf.getvalue()), 'image', image_info(data)[2]], True, str(out / rel)
    rel = safe_name(name)
    sha = _write(out / rel, data)
    if kind == 'psb':
        try:
            _write(out / (rel + '.json'), to_json(data).encode('utf-8'))
            return [rel, sha, None, 0], True, ''
        except Exception as ex:          # un PSB que no se entiende se guarda solo en binario
            return [rel, sha, None, 0, f"psb: {ex}"], False, ''
    return [rel, sha, None, 0], False, str(out / rel) if kind in ('png', 'jpg', 'bmp') else ''


def _process_entry(xp3_path: str, index: int, out_dir: str, png_level: int):
    """Extrae una entrada. Corre en un proceso aparte. (nombre, registro, convertido, errores, PNG, ok)"""
    x, by_index, _ = _archive(xp3_path)
    e = by_index[index]
    try:
        record, conv, last = convert_entry(e.name, x.read(e, check=True), Path(out_dir), png_level)
        return e.name, record, conv, [], last, True
    except Exception as ex:
        return e.name, None, False, [f"{Path(xp3_path).stem}/{e.name}: {ex}"], '', False


def compose_sheet(x: Xp3, by_name: dict, sheet_name: str, out: Path, all_faces: bool = False,
                  png_level: int = DEFAULT_PNG_LEVEL) -> List[str]:
    """Compone los sprites de una tabla de capas en out/_composed/<pose>/. Devuelve los PNG."""
    sheet = parse_layers(x.read(by_name[sheet_name]))
    info_e = by_name.get(info_file(sheet_name))
    if info_e is None:
        return []
    info = parse_info(x.read(info_e))
    cache: Dict[int, Optional[Image.Image]] = {}

    def load(layer):
        if layer.layer_id not in cache:
            e = by_name.get(layer_file(sheet_name, layer))
            cache[layer.layer_id] = decode(x.read(e)) if e else None
        return cache[layer.layer_id]

    folder = out / COMPOSED_DIR / safe_name(sheet_name[:-4])
    written = []
    for label, layers in sprite_variants(info, all_faces):
        img = compose(sheet, layers, load)
        path = folder / (safe_name(label.replace(' ', '_') or 'sprite') + '.png')
        buf = io.BytesIO()
        img.save(buf, format='PNG', compress_level=png_level)
        _write(path, buf.getvalue())
        written.append(str(path))
    return written


def _process_compose(xp3_path: str, sheet_name: str, out_dir: str, png_level: int):
    x, _, by_name = _archive(xp3_path)
    try:
        written = compose_sheet(x, by_name, sheet_name, Path(out_dir), png_level=png_level)
        return sheet_name, len(written), [], written[-1] if written else ''
    except Exception as ex:
        return sheet_name, 0, [f"{Path(xp3_path).stem}/{sheet_name} (composed): {ex}"], ''


def smallest_sheets(names: Iterable[str]) -> List[str]:
    """De cada pose, la tabla de capas del tamaño más pequeño (el número más bajo)."""
    best: Dict[str, Tuple[int, str]] = {}
    for n in names:
        sn = sprite_names(n)
        if sn and sn[1].isdigit():
            if sn[0] not in best or int(sn[1]) < best[sn[0]][0]:
                best[sn[0]] = (int(sn[1]), n)
    return sorted(v[1] for v in best.values())


def extract_all(src, out_root, resume: bool = False, workers: Optional[int] = None,
                png_level: int = DEFAULT_PNG_LEVEL, names: Optional[Iterable[str]] = None,
                composed: bool = True, progress=None, cancel=None) -> ExtractResult:
    """
    Extrae los archivos de los .xp3 (src: un .xp3 o la carpeta del juego) en <out_root>/<xp3>/.
    resume: se salta lo ya hecho. names: solo esos archivos; se suman a una extracción anterior
    sin borrar su registro. composed: también compone los sprites por capas (no con names).

    progress recibe como nombre la ruta del último PNG escrito (o '<xp3>/<archivo>').
    """
    out_root = Path(out_root)
    workers = max(1, workers or default_workers())
    res = ExtractResult()
    selected = set(names) if names is not None else None
    merge = resume or selected is not None
    archives = find_xp3(src)
    if not archives:
        raise ValueError(f"There are no .xp3 files in {src}")

    jobs = []                   # ('entry', xp3, índice, out_dir, stem) o ('compose', xp3, tabla, out_dir, stem)
    progress_files, infos = {}, {}
    for path in archives:
        stem = path.stem
        try:
            with Xp3(path) as x:
                res.protected |= x.has_warning
                wanted = [e for e in x.files() if selected is None or e.name in selected]
                sheets = smallest_sheets(e.name for e in x.files() if e.name.lower().endswith('.txt')) \
                    if composed and selected is None else []
                sheets = [s for s in sheets if info_file(s) in {e.name for e in x.entries}]
        except (OSError, ValueError) as ex:
            res.errors.append(f"{path.name}: {ex}")
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
        info['xp3'] = path.name
        info.setdefault('files', {})
        infos[stem] = info
        for e in wanted:
            if e.name in done:
                res.skipped += 1
            else:
                jobs.append(('entry', str(path), e.index, str(out_dir), stem))
        for s in sheets:
            if f"{COMPOSED_DIR}:{s}" in done:
                res.skipped += 1
            else:
                jobs.append(('compose', str(path), s, str(out_dir), stem))

    total = len(jobs)
    finished = 0

    def mark_done(stem, key):
        with open(progress_files[stem], 'a', encoding='utf-8') as f:
            f.write(key + '\n')

    def collect(job, r):
        nonlocal finished
        kind, stem = job[0], job[4]
        if kind == 'entry':
            name, record, conv, errors, last_png, ok = r
            if record:
                if len(record) > 4:
                    res.warnings.append(f"{stem}/{name}: {record.pop()}")
                infos[stem]['files'][name] = record
                res.files += 1
                res.converted += conv
            if ok:
                mark_done(stem, name)
        else:
            name, count, errors, last_png = r
            res.composed += count
            if not errors:
                mark_done(stem, f"{COMPOSED_DIR}:{name}")
        res.errors += errors
        if last_png:
            res.last_png = last_png
        report(progress, finished, total, last_png or f"{stem}/{name}")
        finished += 1

    def run(job):
        if job[0] == 'entry':
            return _process_entry(job[1], job[2], job[3], png_level)
        return _process_compose(job[1], job[2], job[3], png_level)

    try:
        if workers == 1 or total < 8:
            try:
                for job in jobs:
                    check_cancel(cancel)
                    collect(job, run(job))
            finally:
                _close_archives()
        else:
            pool = ProcessPoolExecutor(max_workers=workers, initializer=_low_priority)
            try:
                pending = {}
                for job in jobs:
                    fn = _process_entry if job[0] == 'entry' else _process_compose
                    pending[pool.submit(fn, job[1], job[2], job[3], png_level)] = job
                while pending:
                    check_cancel(cancel)
                    done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                    for fut in done:
                        collect(pending.pop(fut), fut.result())
            finally:
                # Al cancelar, lo que está en curso termina (se escribe de forma atómica)
                pool.shutdown(wait=True, cancel_futures=True)
    finally:
        for stem, info in infos.items():
            _write_files_info(Path(progress_files[stem]).parent, info)

    report(progress, total, total, res.last_png)
    return res


# =============================================================================
# Parches
# =============================================================================

@dataclass
class PackResult:
    entries: int = 0                                    # archivos del parche escrito
    changed: List[str] = field(default_factory=list)    # archivos editados que entran
    added: List[str] = field(default_factory=list)      # archivos de una carpeta normal
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _sha1_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha1()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def extraction_folders(folder) -> List[Path]:
    """Carpetas con _files.json: la propia carpeta o sus subcarpetas (toda una extracción)."""
    folder = Path(folder)
    if (folder / FILES_FILE).exists():
        return [folder]
    return sorted(p for p in folder.iterdir() if p.is_dir() and (p / FILES_FILE).exists()) \
        if folder.is_dir() else []


def encode_file(path: Path, original_name: str, conv: Optional[str]) -> bytes:
    """Contenido para el parche: un PNG que era TLG vuelve a TLG5 (sin comprimir)."""
    if conv == 'image' and original_name.lower().endswith('.tlg'):
        with Image.open(path) as img:
            img.load()
            return write_tlg5(img)
    return path.read_bytes()


def collect_patch_files(folder, progress=None, cancel=None) -> Tuple[Dict[str, bytes], PackResult]:
    """
    {nombre en el parche: datos} de los archivos editados de una extracción (según _files.json)
    o, si la carpeta no viene de una extracción, de todos sus archivos.
    """
    folder = Path(folder)
    res = PackResult()
    items: Dict[str, bytes] = {}
    origin: Dict[str, str] = {}

    def add(name: str, data: bytes, label: str):
        flat = name.replace('\\', '/').rsplit('/', 1)[-1]
        if flat in items:
            res.errors.append(f"{label}: another file is also named {flat} "
                              f"({origin[flat]}); a patch cannot have folders")
            return
        items[flat] = data
        origin[flat] = label

    sources = extraction_folders(folder)
    if sources:
        work = []
        for src in sources:
            for name, rec in read_files_info(src).get('files', {}).items():
                work.append((src, name, rec))
        for i, (src, name, rec) in enumerate(work):
            check_cancel(cancel)
            rel, sha, conv = rec[0], rec[1], rec[2]
            path = src / rel
            if not path.exists() or _sha1_file(path) == sha:
                continue
            report(progress, i, len(work), f"{src.name}/{rel}")
            try:
                add(name, encode_file(path, name, conv), f"{src.name}/{rel}")
                res.changed.append(f"{src.name}/{name}")
            except Exception as ex:
                res.errors.append(f"{src.name}/{rel}: {ex}")
        report(progress, len(work), len(work))
    elif folder.is_dir():
        files = sorted(p for p in folder.rglob('*') if p.is_file() and not p.name.startswith('_')
                       and not p.name.endswith('.tmp'))
        for i, p in enumerate(files):
            check_cancel(cancel)
            report(progress, i, len(files), p.name)
            add(p.name, p.read_bytes(), str(p.relative_to(folder)))
            res.added.append(p.name)
    else:
        raise ValueError(f"{folder} is not a folder")
    return items, res


def pack_patch(folder, out_xp3, progress=None, cancel=None) -> PackResult:
    """
    Crea (o amplía) el parche out_xp3 con los archivos editados de folder. Si out_xp3 ya
    existe, sus demás archivos se conservan. Si no hay nada que añadir, no se escribe.
    """
    out_xp3 = Path(out_xp3)
    if Path(folder).resolve() in out_xp3.resolve().parents:
        raise ValueError("The output .xp3 cannot be inside the input folder")
    items, res = collect_patch_files(folder, progress, cancel)
    if res.errors or not items:
        return res
    res.entries = merge_into_xp3(out_xp3, items, cancel=cancel)
    return res
