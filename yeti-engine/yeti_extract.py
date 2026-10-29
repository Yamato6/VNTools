"""
yeti_extract - Extracción de archivos e imágenes de los .cpk (motor Yeti/Regista)
Usado por la CLI (yeti_tool.py) y la GUI (yeti_gui.py).

Cada archivo del .cpk:
    comprimido con CRILAYLA (bg.cpk) o con el LZSS de Yeti (sys.cpk)
    -> pak de Yeti (con paks anidados) -> texturas XTX y datos sin identificar
"""

import os
import struct
import sys
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from yeti_codecs import decompress_yeti_lzss
from yeti_common import atomic_write, check_cancel, report
from yeti_cpk import CPK, Entry
from yeti_xtx import is_pak, is_xtx, walk_pak, xtx_to_image

PROGRESS_FILE = '_progreso.txt'
AUDIO_CPKS = {'bgm', 'bgs', 'se', 'vo'}
DEFAULT_PNG_LEVEL = 1


@dataclass
class ImagesResult:
    images: int = 0
    others: int = 0
    skipped: int = 0                         # IDs ya hechos (--resume)
    errors: List[str] = field(default_factory=list)


def unpack(cpk: CPK, e: Entry) -> bytes:
    """Datos de un archivo ya descomprimidos (CRILAYLA lo quita CPK.read)."""
    data = cpk.read(e)
    if e.compressed or is_xtx(data) or len(data) < 5:
        return data
    # Sin CRILAYLA: puede llevar el LZSS de Yeti. Solo se acepta si sale un pak o un XTX.
    (size,) = struct.unpack_from('<I', data, 0)
    if 0 < size < 1 << 30:
        try:
            out = decompress_yeti_lzss(data)
        except (IndexError, struct.error):
            return data
        if is_pak(out) or is_xtx(out):
            return out
    return data


def leaves(e: Entry, data: bytes) -> Iterable[Tuple[str, bytes]]:
    """(nombre, datos) de cada hoja: <id>_<entrada>[_<subentrada>]."""
    if is_pak(data):
        return walk_pak(data, f"{e.id:05d}_")
    return [(f"{e.id:05d}", data)]


def find_cpks(src, include_audio: bool = False) -> List[Path]:
    """src puede ser un .cpk o la carpeta data\\ del juego."""
    src = Path(src)
    if src.is_file():
        return [src]
    cpks = sorted(p for p in src.iterdir() if p.suffix.lower() == '.cpk')
    if not include_audio:
        cpks = [p for p in cpks if p.stem.lower() not in AUDIO_CPKS]
    return cpks


# =============================================================================
# Archivos crudos
# =============================================================================

def extract_raw(cpk_path, out_dir, ids: Optional[Iterable[int]] = None,
                progress=None, cancel=None) -> int:
    """Guarda cada archivo del .cpk ya descomprimido como <id>.bin. Devuelve la cantidad."""
    out_dir = Path(out_dir)
    with CPK(cpk_path) as cpk:
        entries = cpk.entries
        if ids is not None:
            wanted = set(ids)
            entries = [e for e in entries if e.id in wanted]
        total = len(entries)
        for i, e in enumerate(entries):
            check_cancel(cancel)
            report(progress, i, total, e.path)
            data = unpack(cpk, e)
            with atomic_write(out_dir / e.path) as f:
                f.write(data)
    report(progress, total, total)
    return total


# =============================================================================
# Imágenes
# =============================================================================

_open_cpks: Dict[str, CPK] = {}     # uno por proceso de trabajo


def default_workers() -> int:
    """La mitad de los núcleos: deja el resto del equipo libre."""
    return max(1, (os.cpu_count() or 2) // 2)


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


def _cpk_for(path: str) -> CPK:
    cpk = _open_cpks.get(path)
    if cpk is None:
        cpk = _open_cpks[path] = CPK(path)
    return cpk


def _process_entry(cpk_path: str, e: Entry, out_dir: str, skip_existing: bool, png_level: int):
    """
    Convierte un archivo del .cpk. Corre en un proceso aparte.
    Devuelve (id, imágenes, otros, errores, último PNG, terminado).
    """
    out = Path(out_dir)
    n_img = n_other = 0
    errors = []
    last_png = ''
    try:
        data = unpack(_cpk_for(cpk_path), e)
        for name, blob in leaves(e, data):
            if is_xtx(blob):
                dest = out / f"{name}.png"
                if not (skip_existing and dest.exists()):
                    try:
                        img = xtx_to_image(blob)
                    except Exception as ex:
                        errors.append(f"{out.name}/{name}: {ex}")
                        continue
                    with atomic_write(dest) as f:
                        img.save(f, format='PNG', compress_level=png_level)
                    last_png = str(dest)
                n_img += 1
            else:
                with atomic_write(out / f"{name}.bin") as f:
                    f.write(blob)
                n_other += 1
    except Exception as ex:
        errors.append(f"{out.name}/{e.id:05d}: {ex}")
        return e.id, n_img, n_other, errors, last_png, False
    return e.id, n_img, n_other, errors, last_png, True


def _read_done(progress_path: Path) -> set:
    try:
        return {int(x) for x in progress_path.read_text(encoding='utf-8').split()}
    except (OSError, ValueError):
        return set()


def extract_images(src, out_root, resume: bool = False, include_audio: bool = False,
                   workers: Optional[int] = None, png_level: int = DEFAULT_PNG_LEVEL,
                   progress=None, cancel=None) -> ImagesResult:
    """
    Guarda todas las texturas de los .cpk como PNG en <out_root>/<cpk>/<id>_<entrada>.png
    y el resto de hojas como .bin. Cada ID terminado se apunta en <cpk>/_progreso.txt;
    con resume=True se saltan los ya hechos.

    progress recibe como nombre la ruta del último PNG escrito (o '<cpk>/<id>').
    """
    out_root = Path(out_root)
    workers = max(1, workers or default_workers())
    result = ImagesResult()

    # Trabajo pendiente de todos los .cpk
    jobs = []           # (cpk_path, entry, out_dir)
    progress_files = {}
    for cpk_path in find_cpks(src, include_audio):
        out_dir = out_root / cpk_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        prog = out_dir / PROGRESS_FILE
        done = _read_done(prog) if resume else set()
        if not resume:
            prog.write_text('', encoding='utf-8')
        progress_files[str(cpk_path)] = prog
        with CPK(cpk_path) as cpk:
            for e in cpk.entries:
                if e.id in done:
                    result.skipped += 1
                else:
                    jobs.append((str(cpk_path), e, str(out_dir)))

    total = len(jobs)
    finished = 0

    def collect(cpk_path, r):
        nonlocal finished
        eid, n_img, n_other, errors, last_png, ok = r
        result.images += n_img
        result.others += n_other
        result.errors += errors
        if ok:
            with open(progress_files[cpk_path], 'a', encoding='utf-8') as f:
                f.write(f"{eid}\n")
        report(progress, finished, total, last_png or f"{Path(cpk_path).stem}/{eid:05d}")
        finished += 1

    if workers == 1:
        try:
            for cpk_path, e, out_dir in jobs:
                check_cancel(cancel)
                collect(cpk_path, _process_entry(cpk_path, e, out_dir, resume, png_level))
        finally:
            for cpk in _open_cpks.values():
                cpk.close()
            _open_cpks.clear()
    else:
        pool = ProcessPoolExecutor(max_workers=workers, initializer=_low_priority)
        try:
            pending = {pool.submit(_process_entry, cpk_path, e, out_dir, resume, png_level): cpk_path
                       for cpk_path, e, out_dir in jobs}
            while pending:
                check_cancel(cancel)
                done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for fut in done:
                    collect(pending.pop(fut), fut.result())
        finally:
            # Al cancelar, los archivos en curso terminan (se escriben de forma atómica)
            # y los pendientes se descartan
            pool.shutdown(wait=True, cancel_futures=True)

    report(progress, total, total)
    return result


def xtx_file_to_png(xtx_path, png_path, split: bool = False, png_level: int = 6) -> List[Path]:
    """
    Convierte una textura XTX suelta (p. ej. font48.xtx). Devuelve los PNG escritos.

    split=True guarda cada canal como imagen en escala de grises (<nombre>_a/_r/_g/_b.png):
    font48.xtx guarda una hoja de glyphs distinta en cada canal, en orden A, R, G, B.
    """
    img = xtx_to_image(Path(xtx_path).read_bytes())
    png_path = Path(png_path)
    if split:
        r, g, b, a = img.split()
        outputs = [(png_path.with_name(f"{png_path.stem}_{n}.png"), ch)
                   for n, ch in (('a', a), ('r', r), ('g', g), ('b', b))]
    else:
        outputs = [(png_path, img)]
    for path, im in outputs:
        with atomic_write(path) as f:
            im.save(f, format='PNG', compress_level=png_level)
    return [path for path, _ in outputs]

