"""
advhd_extract - Extracción de archivos e imágenes de los .arc (motor AdvHD)
Usado por la CLI (advhd_tool.py) y la GUI (advhd_gui.py).

Una imagen puede venir en varias entradas con el mismo nombre:
    PNG (RGB) o WIP (WIPF 24 bits, 1..N fotogramas) + MSK (WIPF 8 bits = alfa)
    MOS (WIPF 8 bits, junto a los CG EVCC*)
"""

import io
import os
import sys
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from PIL import Image

from advhd_arc import ArcEntry, read_entry, read_index
from advhd_common import atomic_write, check_cancel, report
from advhd_wipf import frame_to_image, mask_to_alpha, read_wipf

PROGRESS_FILE = '_progreso.txt'
AUDIO_ARCS = {'bgm', 'se', 'voice'}
SKIP_ARCS = {'rio'}                     # solo guiones (WSC)
IMAGE_TYPES = {'PNG', 'WIP', 'MSK', 'MOS'}
IGNORED_TYPES = {'WSC'}                 # guiones
DEFAULT_PNG_LEVEL = 1


@dataclass
class ImagesResult:
    images: int = 0
    others: int = 0
    skipped: int = 0                         # nombres ya hechos (--resume)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def find_arcs(src, include_audio: bool = False) -> List[Path]:
    """src puede ser un .arc o la carpeta del juego."""
    src = Path(src)
    if src.is_file():
        return [src]
    arcs = sorted(p for p in src.iterdir() if p.suffix.lower() == '.arc')
    skip = SKIP_ARCS if include_audio else SKIP_ARCS | AUDIO_ARCS
    return [p for p in arcs if p.stem.lower() not in skip]


def default_workers() -> int:
    """La mitad de los núcleos: deja el resto del equipo libre."""
    return max(1, (os.cpu_count() or 2) // 2)


# =============================================================================
# Archivos crudos
# =============================================================================

def extract_raw(arc_path, out_dir, names: Optional[Iterable[str]] = None,
                progress=None, cancel=None) -> int:
    """Guarda las entradas tal cual como NOMBRE.ext. names: nombres de archivo (entry.filename)."""
    out_dir = Path(out_dir)
    entries = read_index(arc_path)
    if names is not None:
        wanted = set(names)
        entries = [e for e in entries if e.filename in wanted]
    total = len(entries)
    with open(arc_path, 'rb') as f:
        for i, e in enumerate(entries):
            check_cancel(cancel)
            report(progress, i, total, e.filename)
            data = read_entry(f, e)
            with atomic_write(out_dir / e.filename) as out:
                out.write(data)
    report(progress, total, total)
    return total


# =============================================================================
# Imágenes
# =============================================================================

_open_arcs: Dict[str, object] = {}     # un archivo abierto por .arc en cada proceso


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


def _arc_file(path: str):
    f = _open_arcs.get(path)
    if f is None:
        f = _open_arcs[path] = open(path, 'rb')
    return f


def _frame_names(base: str, count: int, suffix: str = '') -> List[str]:
    """NOMBRE[suffix].png o, con varios fotogramas, NOMBRE[suffix]_000.png…"""
    if count == 1:
        return [f"{base}{suffix}.png"]
    return [f"{base}{suffix}_{i:03d}.png" for i in range(count)]


class _Writer:
    """Escribe las salidas de una imagen lógica y cuenta lo escrito."""

    def __init__(self, out: Path, png_level: int):
        self.out = out
        self.png_level = png_level
        self.images = 0
        self.others = 0
        self.last_png = ''
        self.warnings = []

    def png(self, name: str, img: Image.Image):
        with atomic_write(self.out / name) as f:
            img.save(f, format='PNG', compress_level=self.png_level)
        self.images += 1
        self.last_png = str(self.out / name)

    def png_bytes(self, name: str, data: bytes):
        with atomic_write(self.out / name) as f:
            f.write(data)
        self.images += 1
        self.last_png = str(self.out / name)

    def raw(self, name: str, data: bytes):
        with atomic_write(self.out / name) as f:
            f.write(data)
        self.others += 1


def _process_name(arc_path: str, name: str, entries: List[ArcEntry], out_dir: str,
                  compose: bool, png_level: int):
    """
    Convierte todas las entradas con el mismo nombre. Corre en un proceso aparte.
    Devuelve (nombre, imágenes, otros, errores, avisos, último PNG, terminado).
    """
    w = _Writer(Path(out_dir), png_level)
    label = f"{Path(arc_path).stem}/{name}"
    try:
        f = _arc_file(arc_path)
        parts = {e.ext.upper(): e for e in entries}

        masks = None
        if 'MSK' in parts:
            masks = [mask_to_alpha(fr) for fr in read_wipf(read_entry(f, parts['MSK']))]
            if compose and ('PNG' in parts or 'WIP' in parts) \
                    and all(m.getextrema() == (0, 0) for m in masks):
                # Aplicarla borraría la imagen entera (CHATWND)
                w.warnings.append(f"{label}: la máscara es totalmente transparente; "
                                  "la imagen se guarda opaca y la máscara aparte")
                compose = False

        def save_masks():
            for fname, m in zip(_frame_names(name, len(masks), '_msk'), masks):
                w.png(fname, m)

        if 'PNG' in parts:
            data = read_entry(f, parts['PNG'])
            if masks and compose:
                img = Image.open(io.BytesIO(data))
                if img.size == masks[0].size:
                    img = img.convert('RGB')
                    img.putalpha(masks[0])
                    w.png(f"{name}.png", img)
                else:
                    w.warnings.append(f"{label}: la máscara mide {masks[0].size} y la imagen "
                                      f"{img.size}; se guardan por separado")
                    w.png_bytes(f"{name}.png", data)
                    save_masks()
            else:
                w.png_bytes(f"{name}.png", data)
                if masks:
                    save_masks()

        elif 'WIP' in parts:
            frames = [frame_to_image(fr) for fr in read_wipf(read_entry(f, parts['WIP']))]
            names = _frame_names(name, len(frames))
            if masks and compose and [m.size for m in masks] == [im.size for im in frames]:
                for fname, img, m in zip(names, frames, masks):
                    img = img.convert('RGB')
                    img.putalpha(m)
                    w.png(fname, img)
            else:
                if masks and compose:
                    w.warnings.append(f"{label}: los fotogramas de la máscara no coinciden con "
                                      "los de la imagen; se guardan por separado")
                for fname, img in zip(names, frames):
                    w.png(fname, img)
                if masks:
                    save_masks()

        elif masks:
            save_masks()            # máscara sin imagen (transiciones, zonas de botones)

        if 'MOS' in parts:
            mos = [frame_to_image(fr) for fr in read_wipf(read_entry(f, parts['MOS']))]
            for fname, img in zip(_frame_names(name, len(mos), '_mos'), mos):
                w.png(fname, img)

        # ANM, TBL, TXT, MAT, OGG… en crudo (WSC y, sin audio, OGG ya vienen filtrados)
        for ext, e in parts.items():
            if ext not in IMAGE_TYPES:
                w.raw(e.filename, read_entry(f, e))
    except Exception as ex:
        return name, w.images, w.others, [f"{label}: {ex}"], w.warnings, w.last_png, False
    return name, w.images, w.others, [], w.warnings, w.last_png, True


def _read_done(progress_path: Path) -> set:
    try:
        return set(progress_path.read_text(encoding='utf-8').splitlines())
    except OSError:
        return set()


def extract_images(src, out_root, resume: bool = False, include_audio: bool = False,
                   compose: bool = True, workers: Optional[int] = None,
                   png_level: int = DEFAULT_PNG_LEVEL, progress=None, cancel=None) -> ImagesResult:
    """
    Guarda las imágenes de los .arc como PNG en <out_root>/<arc>/<nombre>.png (RGBA si tienen
    máscara y compose=True). Las entradas que no son imágenes se guardan en crudo; el audio
    solo con include_audio. Cada nombre terminado se apunta en <arc>/_progreso.txt;
    con resume=True se saltan los ya hechos.

    progress recibe como nombre la ruta del último PNG escrito (o '<arc>/<nombre>').
    """
    out_root = Path(out_root)
    workers = max(1, workers or default_workers())
    result = ImagesResult()

    jobs = []           # (arc_path, nombre, entradas, out_dir)
    progress_files = {}
    for arc_path in find_arcs(src, include_audio):
        groups = defaultdict(list)
        for e in read_index(arc_path):
            if e.ext.upper() not in IGNORED_TYPES and (include_audio or e.ext.upper() != 'OGG'):
                groups[e.name].append(e)
        if not groups:
            continue
        out_dir = out_root / arc_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        prog = out_dir / PROGRESS_FILE
        done = _read_done(prog) if resume else set()
        if not resume:
            prog.write_text('', encoding='utf-8')
        progress_files[str(arc_path)] = prog
        for name, entries in groups.items():
            if name in done:
                result.skipped += 1
            else:
                jobs.append((str(arc_path), name, entries, str(out_dir)))

    total = len(jobs)
    finished = 0

    def collect(arc_path, r):
        nonlocal finished
        name, n_img, n_other, errors, warnings, last_png, ok = r
        result.images += n_img
        result.others += n_other
        result.errors += errors
        result.warnings += warnings
        if ok:
            with open(progress_files[arc_path], 'a', encoding='utf-8') as f:
                f.write(f"{name}\n")
        report(progress, finished, total, last_png or f"{Path(arc_path).stem}/{name}")
        finished += 1

    if workers == 1:
        try:
            for arc_path, name, entries, out_dir in jobs:
                check_cancel(cancel)
                collect(arc_path, _process_name(arc_path, name, entries, out_dir, compose, png_level))
        finally:
            for f in _open_arcs.values():
                f.close()
            _open_arcs.clear()
    else:
        pool = ProcessPoolExecutor(max_workers=workers, initializer=_low_priority)
        try:
            pending = {pool.submit(_process_name, arc_path, name, entries, out_dir, compose, png_level): arc_path
                       for arc_path, name, entries, out_dir in jobs}
            while pending:
                check_cancel(cancel)
                done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                for fut in done:
                    collect(pending.pop(fut), fut.result())
        finally:
            # Al cancelar, las imágenes en curso terminan (se escriben de forma atómica)
            # y las pendientes se descartan
            pool.shutdown(wait=True, cancel_futures=True)

    report(progress, total, total)
    return result
