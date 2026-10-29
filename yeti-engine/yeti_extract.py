"""
yeti_extract - Extracción de archivos e imágenes de los .cpk (motor Yeti/Regista)
Usado por la CLI (yeti_tool.py) y la GUI (yeti_gui.py).

Cada archivo del .cpk:
    comprimido con CRILAYLA (bg.cpk) o con el LZSS de Yeti (sys.cpk)
    -> pak de Yeti (con paks anidados) -> texturas XTX y datos sin identificar
"""

import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image

from yeti_codecs import compress_yeti_lzss, decompress_yeti_lzss
from yeti_common import atomic_write, check_cancel, report
from yeti_cpk import CPK, Entry, write_cpk
from yeti_xtx import (image_to_xtx, is_pak, is_xtx, pak_entries, replace_leaves, walk_pak,
                      xtx_to_image)

PROGRESS_FILE = '_progreso.txt'
IMAGES_FILE = '_images.json'            # hash de cada PNG y si el archivo llevaba LZSS
AUDIO_CPKS = {'bgm', 'se', 'vo'}          # HCA de CRIWARE (bgs.cpk son texturas)
DEFAULT_PNG_LEVEL = 1


@dataclass
class ImagesResult:
    images: int = 0
    others: int = 0
    skipped: int = 0                         # IDs ya hechos (--resume)
    errors: List[str] = field(default_factory=list)


def is_hca(data: bytes) -> bool:
    """Audio HCA de CRIWARE (la firma puede llevar el bit alto puesto)."""
    return bytes(c & 0x7F for c in data[:4]) == b'HCA\0'


def unpack(cpk: CPK, e: Entry) -> bytes:
    """Datos de un archivo ya descomprimidos (CRILAYLA lo quita CPK.read)."""
    return unpack_info(cpk, e)[0]


def unpack_info(cpk: CPK, e: Entry) -> Tuple[bytes, bool]:
    """(datos descomprimidos, si llevaba el LZSS de Yeti)."""
    data = cpk.read(e)
    if e.compressed or is_xtx(data) or is_hca(data) or len(data) < 5:
        return data, False
    # Sin CRILAYLA: puede llevar el LZSS de Yeti. Solo se acepta si sale un pak o un XTX.
    (size,) = struct.unpack_from('<I', data, 0)
    if 0 < size < 1 << 30:
        try:
            out = decompress_yeti_lzss(data)
        except (IndexError, struct.error):
            return data, False
        if is_pak(out) or is_xtx(out):
            return out, True
    return data, False


def leaves(e: Entry, data: bytes) -> Iterable[Tuple[str, bytes]]:
    """(nombre, datos) de cada hoja: <id>_<entrada>[_<subentrada>]."""
    if is_pak(data):
        return walk_pak(data, f"{e.id:05d}_")
    return [(f"{e.id:05d}", data)]


def find_cpks(src, audio: bool = False) -> List[Path]:
    """
    src puede ser un .cpk o la carpeta data\\ del juego. De una carpeta devuelve los .cpk
    de imágenes o, con audio=True, los de audio.
    """
    src = Path(src)
    if src.is_file():
        return [src]
    cpks = sorted(p for p in src.iterdir() if p.suffix.lower() == '.cpk')
    return [p for p in cpks if (p.stem.lower() in AUDIO_CPKS) == audio]


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
    Devuelve (id, imágenes, otros, errores, último PNG, registro, terminado); el registro va a
    _images.json: {'lzss': bool, 'files': {png: sha1}} (None en los PNG que ya existían).
    """
    out = Path(out_dir)
    n_img = n_other = 0
    errors = []
    last_png = ''
    files = {}
    try:
        data, lzss = unpack_info(_cpk_for(cpk_path), e)
        for name, blob in leaves(e, data):
            if is_xtx(blob):
                dest = out / f"{name}.png"
                if skip_existing and dest.exists():
                    files[dest.name] = None
                else:
                    try:
                        img = xtx_to_image(blob)
                    except Exception as ex:
                        errors.append(f"{out.name}/{name}: {ex}")
                        continue
                    buf = io.BytesIO()
                    img.save(buf, format='PNG', compress_level=png_level)
                    with atomic_write(dest) as f:
                        f.write(buf.getvalue())
                    files[dest.name] = hashlib.sha1(buf.getvalue()).hexdigest()
                    last_png = str(dest)
                n_img += 1
            else:
                with atomic_write(out / f"{name}.bin") as f:
                    f.write(blob)
                n_other += 1
    except Exception as ex:
        errors.append(f"{out.name}/{e.id:05d}: {ex}")
        return e.id, n_img, n_other, errors, last_png, None, False
    record = {'lzss': lzss, 'files': files} if files else None
    return e.id, n_img, n_other, errors, last_png, record, True


def _read_done(progress_path: Path) -> set:
    try:
        return {int(x) for x in progress_path.read_text(encoding='utf-8').split()}
    except (OSError, ValueError):
        return set()


def read_images_info(folder) -> Dict[str, dict]:
    try:
        return json.loads((Path(folder) / IMAGES_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _write_images_info(folder: Path, info: Dict[str, dict]):
    data = json.dumps(dict(sorted(info.items())), indent=1)
    with atomic_write(folder / IMAGES_FILE) as f:
        f.write(data.encode('utf-8'))


def extract_images(src, out_root, resume: bool = False,
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
    images_info = {}    # cpk_path -> {id: registro}
    for cpk_path in find_cpks(src):
        out_dir = out_root / cpk_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        prog = out_dir / PROGRESS_FILE
        done = _read_done(prog) if resume else set()
        if not resume:
            prog.write_text('', encoding='utf-8')
        progress_files[str(cpk_path)] = prog
        images_info[str(cpk_path)] = read_images_info(out_dir) if resume else {}
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
        eid, n_img, n_other, errors, last_png, record, ok = r
        if record:
            # Los PNG que ya existían conservan el hash de la extracción anterior
            old = images_info[cpk_path].get(str(eid), {}).get('files', {})
            record['files'] = {f: sha or old.get(f) for f, sha in record['files'].items()}
            images_info[cpk_path][str(eid)] = record
        result.images += n_img
        result.others += n_other
        result.errors += errors
        if ok:
            with open(progress_files[cpk_path], 'a', encoding='utf-8') as f:
                f.write(f"{eid}\n")
        report(progress, finished, total, last_png or f"{Path(cpk_path).stem}/{eid:05d}")
        finished += 1

    try:
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
    finally:
        # También al cancelar: lo extraído hasta ahora se puede reempaquetar
        for cpk_path, info in images_info.items():
            _write_images_info(Path(progress_files[cpk_path]).parent, info)

    report(progress, total, total)
    return result


# =============================================================================
# Reempaquetar
# =============================================================================

@dataclass
class PackResult:
    entries: int = 0                         # archivos del .cpk
    changed: List[str] = field(default_factory=list)    # archivos (o PNG) reemplazados
    errors: List[str] = field(default_factory=list)


def _sha1_file(path: Path) -> Optional[str]:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _leaf(data: bytes, path: Tuple[int, ...]) -> bytes:
    for i in path:
        data = pak_entries(data)[i]
    return data


def find_images_folder(folder, cpk_path) -> Path:
    """Acepta la carpeta de un .cpk (<salida>/bg) o la de toda la extracción (<salida>)."""
    folder = Path(folder)
    if not (folder / IMAGES_FILE).exists() and (folder / Path(cpk_path).stem / IMAGES_FILE).exists():
        folder = folder / Path(cpk_path).stem
    if not (folder / IMAGES_FILE).exists():
        raise ValueError(f"No se encontró {IMAGES_FILE} en {folder}: extrae primero las imágenes "
                         "con esta versión de la herramienta")
    return folder


def pack_images(original_cpk, images_folder, out_cpk, progress=None, cancel=None) -> PackResult:
    """
    Crea out_cpk a partir de original_cpk, sustituyendo las texturas cuyos PNG se editaron
    desde la extracción (según los SHA-1 de _images.json). Cada textura conserva su formato
    y tamaño; los archivos tocados se guardan sin CRILAYLA (con LZSS si el original lo llevaba).
    """
    folder = find_images_folder(images_folder, original_cpk)
    info = read_images_info(folder)
    result = PackResult()
    replace = {}
    with CPK(original_cpk) as cpk:
        result.entries = len(cpk.entries)
        by_id = {e.id: e for e in cpk.entries}
        todo = [(int(k), rec) for k, rec in info.items() if int(k) in by_id]
        for i, (eid, rec) in enumerate(todo):
            check_cancel(cancel)
            changed = [f for f, sha in rec['files'].items()
                       if sha and _sha1_file(folder / f) not in (sha, None)]
            if not changed:
                continue
            report(progress, i, len(todo), changed[0])
            data, _lzss = unpack_info(cpk, by_id[eid])
            leaves_new = {}
            for fname in changed:
                path = tuple(int(x) for x in Path(fname).stem.split('_')[1:])
                try:
                    leaf = _leaf(data, path)
                    with Image.open(folder / fname) as img:
                        img = img.convert('RGBA')
                    if img.tobytes() == xtx_to_image(leaf).convert('RGBA').tobytes():
                        continue                # se guardó de nuevo sin cambios
                    leaves_new[path] = image_to_xtx(img, leaf)
                    result.changed.append(fname)
                except Exception as ex:
                    result.errors.append(f"{fname}: {ex}")
            if leaves_new:
                new = replace_leaves(data, leaves_new)
                replace[eid] = compress_yeti_lzss(new) if rec.get('lzss') else new
        if result.errors:
            return result
        write_cpk(cpk, out_cpk, replace, cancel=cancel)
    report(progress, 1, 1)
    return result


def pack_files(original_cpk, folder, out_cpk, progress=None, cancel=None) -> PackResult:
    """
    Crea out_cpk a partir de original_cpk con los archivos <id>.bin o <id>.hca de folder
    que sean distintos del original (los extraídos con 'extract' o 'audio'). Los .bin están
    descomprimidos: si el original llevaba LZSS de Yeti, se vuelve a comprimir.
    """
    folder = Path(folder)
    result = PackResult()
    replace = {}
    with CPK(original_cpk) as cpk:
        result.entries = len(cpk.entries)
        for i, e in enumerate(cpk.entries):
            check_cancel(cancel)
            for ext in ('.bin', '.hca'):
                path = folder / f"{e.id:05d}{ext}"
                if path.exists():
                    break
            else:
                continue
            report(progress, i, len(cpk.entries), path.name)
            new = path.read_bytes()
            data, lzss = unpack_info(cpk, e)
            if new != data:
                replace[e.id] = compress_yeti_lzss(new) if lzss else new
                result.changed.append(path.name)
        write_cpk(cpk, out_cpk, replace, cancel=cancel)
    report(progress, 1, 1)
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


# =============================================================================
# Audio
# =============================================================================

@dataclass
class AudioResult:
    files: int = 0
    wav: int = 0
    errors: List[str] = field(default_factory=list)


def find_vgmstream() -> Optional[str]:
    """vgmstream-cli (convierte HCA a WAV) si está en el PATH o junto a la herramienta."""
    found = shutil.which('vgmstream-cli')
    if found:
        return found
    base = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent
    local = base / 'vgmstream-cli.exe'
    return str(local) if local.exists() else None


def _to_wav(vgmstream: str, hca: Path) -> Optional[str]:
    wav = hca.with_suffix('.wav')
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
    r = subprocess.run([vgmstream, '-o', str(wav), str(hca)], capture_output=True, creationflags=flags)
    if r.returncode != 0 or not wav.exists():
        return f"{hca.parent.name}/{hca.name}: vgmstream-cli falló ({r.returncode})"
    return None


def extract_audio(src, out_root, wav: bool = True, workers: Optional[int] = None,
                  progress=None, cancel=None) -> AudioResult:
    """
    Guarda el audio de bgm, se y vo.cpk como <out_root>/<cpk>/<id>.hca y, con wav=True y
    vgmstream-cli disponible, también como .wav.
    """
    cpks = find_cpks(src, audio=True)
    if not cpks:
        raise ValueError("No hay archivos de audio (bgm.cpk, se.cpk, vo.cpk)")
    result = AudioResult()
    written = []
    total = 0
    for cpk_path in cpks:
        with CPK(cpk_path) as cpk:
            total += len(cpk.entries)
    i = 0
    for cpk_path in cpks:
        out_dir = Path(out_root) / cpk_path.stem
        with CPK(cpk_path) as cpk:
            for e in cpk.entries:
                check_cancel(cancel)
                report(progress, i, total, f"{cpk_path.stem}/{e.id:05d}.hca")
                path = out_dir / f"{e.id:05d}.hca"
                with atomic_write(path) as f:
                    f.write(cpk.read(e))
                written.append(path)
                i += 1
    result.files = len(written)

    vgmstream = find_vgmstream() if wav else None
    if vgmstream:
        with ThreadPoolExecutor(max_workers=workers or default_workers()) as pool:
            futures = [pool.submit(_to_wav, vgmstream, p) for p in written]
            for k, fut in enumerate(futures):
                if cancel is not None and cancel.is_set():
                    for f in futures:
                        f.cancel()
                    check_cancel(cancel)
                report(progress, k, len(futures), f"WAV: {written[k].parent.name}/{written[k].name}")
                err = fut.result()
                if err:
                    result.errors.append(err)
                else:
                    result.wav += 1
    report(progress, total, total)
    return result
