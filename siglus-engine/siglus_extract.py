"""
siglus_extract - Extracción de los archivos de un juego SiglusEngine y empaquetado de los editados
Usado por la CLI (siglus_tool.py) y la GUI (siglus_gui.py).

SiglusEngine no usa contenedores: los recursos van sueltos en carpetas (g00, bgm, koe, wav,
mov, dat, gan). La extracción los convierte con la misma estructura en <salida>:
    g00\\NOMBRE.png            (G00 de tipo 2 con regiones que se solapan: NOMBRE_RR.png)
    g00\\_composed\\NOMBRE.png (CG y sprites sin cara, con la primera expresión)
    bgm\\, wav\\NOMBRE.wav      (NWA; los .ogg sueltos se copian)
    koe\\zNNNN\\zNNNN_IIIII.ogg (cada entrada de los .ovk de voces)
    mov\\NOMBRE.ogv            (OMV; los .wmv se copian)
    dat\\NOMBRE.csv            (tablas .dbs, además del original)
    Gameexe.txt              (configuración)
_files.json guarda cómo se extrajo cada archivo y el SHA-1 de lo escrito; Pack lo usa para
saber qué se editó. Los archivos se procesan en paralelo, en procesos de baja prioridad.

Pack escribe en otra carpeta (<juego>_new) los G00 de las imágenes editadas y Gameexe.dat si se
editó Gameexe.txt, con la misma estructura: se copian encima de los del juego.
"""

import hashlib
import io
import json
import os
import re
import sys
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from PIL import Image

import siglus_g00
from siglus_audio import is_nwa, nwa_to_wav, omv_to_ogv, ovk_entry_name, read_ovk_index
from siglus_common import atomic_write, check_cancel, report
from siglus_gameexe import dbs_to_csv, read_gameexe, write_gameexe

FILES_FILE = '_files.json'
PROGRESS_FILE = '_progress.txt'
COMPOSED_DIR = '_composed'
DEFAULT_PNG_LEVEL = 6
FOLDERS = ('g00', 'bgm', 'koe', 'wav', 'mov', 'dat', 'gan')
ROOT_FILES = ('Gameexe.dat',)


def default_workers() -> int:
    """La mitad de los núcleos: deja el resto del equipo libre."""
    return max(1, (os.cpu_count() or 2) // 2)


def kind_of(rel: str) -> str:
    """Tipo de un archivo del juego por su carpeta y extensión."""
    ext = rel.rsplit('.', 1)[-1].lower() if '.' in rel else ''
    return {'g00': 'image', 'g01': 'image', 'nwa': 'audio', 'ogg': 'audio', 'ovk': 'voice',
            'omv': 'video', 'wmv': 'video', 'mpg': 'video', 'dbs': 'table', 'dat': 'config',
            'ttf': 'font', 'otf': 'font', 'gan': 'animation'}.get(ext, 'data')


def find_files(game) -> List[str]:
    """Rutas relativas (con '/') de los recursos del juego, ordenadas."""
    game = Path(game)
    out = [name for name in ROOT_FILES if (game / name).is_file()]
    for folder in FOLDERS:
        base = game / folder
        if base.is_dir():
            out += sorted(p.relative_to(game).as_posix() for p in base.rglob('*') if p.is_file())
    return out


def face_partner(rel: str, by_lower: Dict[str, str]) -> Optional[str]:
    """
    El G00 de expresiones de una imagen sin cara: CG_MR11_BASE___000 → CG_MR11_FACE,
    BS_AZ11_01B → BS_AZ11_face (el prefijo más largo que tenga un '<prefijo>_face.g00').
    by_lower: {ruta en minúsculas: ruta} de los archivos del juego.
    """
    if '/' not in rel or not rel.lower().endswith('.g00'):
        return None
    folder, name = rel.rsplit('/', 1)
    stem = name[:-4]
    parts = stem.split('_')
    # Solo las bases sin cara: CG_XX_BASE___000 y los sprites XX_YYNN_01B (las CG con número
    # solo, como CG_MS10_0314, ya llevan la cara puesta)
    is_base = any(p.lower() == 'base' for p in parts) or bool(re.fullmatch(r'\d+[A-Za-z]', parts[-1]))
    if not is_base or re.search(r'face', stem, re.I):
        return None
    for k in range(len(parts) - 1, 0, -1):
        prefix = '_'.join(parts[:k]).rstrip('_')
        if not prefix:
            continue
        hit = by_lower.get(f"{folder}/{prefix}_face.g00".lower())
        if hit:
            return hit
    return None


def composed_path(rel: str) -> str:
    folder, name = rel.rsplit('/', 1)
    return f"{folder}/{COMPOSED_DIR}/{name[:-4]}.png"


@dataclass
class ExtractResult:
    files: int = 0              # archivos del juego procesados
    written: int = 0            # archivos escritos
    converted: int = 0          # convertidos (PNG, WAV, OGG, OGV, CSV, TXT)
    skipped: int = 0            # ya hechos (resume)
    errors: List[str] = field(default_factory=list)
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


def _write(path: Path, data: bytes) -> str:
    with atomic_write(path) as f:
        f.write(data)
    return hashlib.sha1(data).hexdigest()


def _png(img: Image.Image, level: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format='PNG', compress_level=level)
    return buf.getvalue()


def g00_outputs(rel: str, data: bytes) -> List[tuple]:
    """[(ruta del PNG, región o None para el lienzo completo)] de un G00."""
    base = rel.rsplit('.', 1)[0]
    if data[0] != 2 or siglus_g00.regions_disjoint(data):
        return [(base + '.png', None)]
    return [(f"{base}_{r:02d}.png", r) for r in siglus_g00.region_indexes(data)]


def convert_file(game: Path, rel: str, out: Path, png_level: int, face: Optional[str] = None) -> dict:
    """
    Convierte un archivo del juego. Devuelve el registro de _files.json:
    {'conv': tipo, 'out': [[ruta, sha1], ...], ...} y 'last' con el último PNG escrito.
    face: el G00 de expresiones de una imagen sin cara; se guarda además la imagen completa
    con la primera expresión en <carpeta>/_composed/ (solo para verla, no se empaqueta).
    """
    src = game / rel
    low = rel.lower()
    ext = low.rsplit('.', 1)[-1]
    rec = {'conv': None, 'out': []}

    def put(path, data):
        rec['out'].append([path, _write(out / path, data)])

    if ext in ('g00', 'g01'):
        data = src.read_bytes()
        outputs = g00_outputs(rel, data)
        rec['conv'] = 'g00'
        if outputs[0][1] is None:
            img = siglus_g00.compose(data)
            put(outputs[0][0], _png(img, png_level))
        else:
            rec['regions'] = [r for _, r in outputs]
            images = siglus_g00.decode(data)
            for (path, _r), (img, _d) in zip(outputs, images):
                put(path, _png(img, png_level))
        rec['last'] = str(out / rec['out'][-1][0])
        if face:
            face_data = (game / face).read_bytes()
            if siglus_g00.g00_info(face_data)[1:3] == siglus_g00.g00_info(data)[1:3]:
                path = composed_path(rel)
                img = siglus_g00.with_face(data, siglus_g00.layers(face_data)[0])
                _write(out / path, _png(img, png_level))
                rec['composed'] = path
                rec['last'] = str(out / path)
    elif ext == 'nwa':
        data = src.read_bytes()
        if not is_nwa(data):
            raise ValueError("not a NWA file")
        rec['conv'] = 'nwa'
        put(rel[:-4] + '.wav', nwa_to_wav(data))
    elif ext == 'ovk':
        data = src.read_bytes()
        rec['conv'] = 'ovk'
        stem = Path(rel).stem
        folder = rel.rsplit('/', 1)[0] + '/' + stem
        for vid, _samples, off, size in read_ovk_index(data):
            put(f"{folder}/{ovk_entry_name(stem, vid)}", data[off:off + size])
    elif ext == 'omv':
        rec['conv'] = 'omv'
        path = rel[:-4] + '.ogv'
        with atomic_write(out / path) as f:
            omv_to_ogv(src, f)
        rec['out'].append([path, None])           # vídeo: no se vuelve a empaquetar
    elif ext == 'dbs':
        data = src.read_bytes()
        put(rel, data)
        try:
            put(rel[:-4] + '.csv', dbs_to_csv(data).encode('utf-8-sig'))
            rec['conv'] = 'dbs'
        except Exception:           # una tabla que no se entiende se guarda solo como original
            pass
    elif low == 'gameexe.dat':
        data = src.read_bytes()
        rec['conv'] = 'gameexe'
        text = read_gameexe(data, _game_key(game) if data[4:8] != b'\0\0\0\0' else None)
        put('Gameexe.txt', text.encode('utf-8-sig'))
    else:
        put(rel, src.read_bytes())
    return rec


def _game_key(game: Path) -> Optional[bytes]:
    try:
        from siglus_pck import ScenePck
        return ScenePck(game / 'Scene.pck').key
    except (OSError, ValueError):
        return None


def _process(game: str, rel: str, out_dir: str, png_level: int, face: Optional[str] = None):
    """Corre en un proceso aparte. (ruta, registro o None, errores)."""
    try:
        return rel, convert_file(Path(game), rel, Path(out_dir), png_level, face), []
    except Exception as ex:
        return rel, None, [f"{rel}: {ex}"]


def extract_all(game, out_dir, resume: bool = False, workers: Optional[int] = None,
                png_level: int = DEFAULT_PNG_LEVEL, names: Optional[Iterable[str]] = None,
                composed: bool = True, progress=None, cancel=None) -> ExtractResult:
    """
    Extrae los recursos de la carpeta del juego en out_dir. resume: se salta lo ya hecho.
    names: solo esas rutas; se suman a una extracción anterior sin borrar su registro.
    composed: las CG y sprites sin cara también se guardan completos, con la primera expresión,
    en g00/_composed/.
    progress recibe como nombre la ruta del último PNG escrito (o la del archivo).
    """
    game, out = Path(game), Path(out_dir)
    if not game.is_dir():
        raise ValueError(f"{game} is not a folder")
    workers = max(1, workers or default_workers())
    res = ExtractResult()
    files = find_files(game)
    if not files:
        raise ValueError(f"There are no SiglusEngine resources in {game}")
    selected = set(names) if names is not None else None
    merge = resume or selected is not None
    wanted = [f for f in files if selected is None or f in selected]
    by_lower = {f.lower(): f for f in files}
    faces = {f: face_partner(f, by_lower) for f in wanted} if composed else {}
    out.mkdir(parents=True, exist_ok=True)
    prog = out / PROGRESS_FILE
    done = _read_done(prog) if resume and selected is None else set()
    if not merge:
        prog.write_text('', encoding='utf-8')
    info = read_files_info(out) if merge else {}
    info['game'] = str(game)
    info.setdefault('files', {})
    jobs = []
    for rel in wanted:
        if rel in done:
            res.skipped += 1
        else:
            jobs.append(rel)
    total = len(jobs)
    finished = 0

    def collect(r):
        nonlocal finished
        rel, rec, errors = r
        if rec:
            last = rec.pop('last', '')
            info['files'][rel] = rec
            res.files += 1
            res.written += len(rec['out']) + ('composed' in rec)
            if rec['conv']:
                res.converted += 1
            if last:
                res.last_png = last
            with open(prog, 'a', encoding='utf-8') as f:
                f.write(rel + '\n')
        res.errors += errors
        report(progress, finished, total, res.last_png if rel.lower().endswith(('.g00', '.g01')) else rel)
        finished += 1

    try:
        if workers == 1 or total < 8:
            for rel in jobs:
                check_cancel(cancel)
                collect(_process(str(game), rel, str(out), png_level, faces.get(rel)))
        else:
            pool = ProcessPoolExecutor(max_workers=workers, initializer=_low_priority)
            try:
                # Los archivos grandes primero, para que no queden al final
                jobs.sort(key=lambda rel: -(game / rel).stat().st_size)
                pending = {pool.submit(_process, str(game), rel, str(out), png_level, faces.get(rel)): rel
                           for rel in jobs}
                while pending:
                    check_cancel(cancel)
                    ready, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                    for fut in ready:
                        pending.pop(fut)
                        collect(fut.result())
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
    finally:
        _write_files_info(out, info)
    report(progress, total, total, res.last_png)
    return res


# =============================================================================
# Pack
# =============================================================================

@dataclass
class PackResult:
    written: List[str] = field(default_factory=list)    # archivos escritos en la salida
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


def _load_png(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img.load()
        return img.copy()


def pack(folder, game, out_dir, progress=None, cancel=None) -> PackResult:
    """
    Escribe en out_dir los G00 de las imágenes editadas desde la extracción (folder) y
    Gameexe.dat si se editó Gameexe.txt, con la estructura del juego.
    """
    folder, game, out = Path(folder), Path(game), Path(out_dir)
    if out.resolve() == game.resolve():
        raise ValueError("The output folder must be different from the game folder")
    info = read_files_info(folder)
    files = info.get('files', {})
    if not files:
        raise ValueError(f"{FILES_FILE} not found in {folder}: extract the files first with this tool")
    res = PackResult()
    items = sorted(files.items())
    for i, (rel, rec) in enumerate(items):
        check_cancel(cancel)
        conv = rec.get('conv')
        if conv not in ('g00', 'gameexe'):
            continue
        changed = [(path, sha) for path, sha in rec['out'] if sha and _sha1_file(folder / path) not in (sha, None)]
        if not changed:
            continue
        report(progress, i, len(items), rel)
        try:
            original = (game / rel).read_bytes()
            if conv == 'gameexe':
                text = (folder / rec['out'][0][0]).read_text(encoding='utf-8-sig')
                key = _game_key(game) if original[4:8] != b'\0\0\0\0' else None
                data = write_gameexe(text, original, key)
            else:
                regions = rec.get('regions')
                if regions is None:
                    canvas = _load_png(folder / rec['out'][0][0])
                    images = siglus_g00.split_canvas(original, canvas)
                else:
                    images = {}
                    for (path, sha), region in zip(rec['out'], regions):
                        if _sha1_file(folder / path) != sha:
                            images[region] = _load_png(folder / path)
                lost = siglus_g00.lost_pixels(original, images)
                if lost:
                    res.warnings.append(f"{rel}: {lost} pixels are outside the parts of the original "
                                        "image and were not saved")
                data = siglus_g00.encode(original, images)
            with atomic_write(out / rel) as f:
                f.write(data)
            res.written.append(rel)
        except Exception as ex:
            res.errors.append(f"{rel}: {ex}")
    report(progress, len(items), len(items))
    return res
