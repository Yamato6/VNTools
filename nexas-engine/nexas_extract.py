"""
nexas_extract - Extracción de todo el contenido de los .pac y reempaquetado
Usado por la CLI (nexas_tool.py) y la GUI (nexas_gui.py).

La extracción escribe <salida>/<pac>/<entrada> y un registro <pac>/_files.json con el
archivo y el SHA-1 de cada entrada; el reempaquetado usa ese registro para saber qué
archivos se editaron y solo vuelve a codificar esos.
"""

import hashlib
import io
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from PIL import Image

from nexas_common import atomic_write, check_cancel, report, safe_name
from nexas_formats import (compose_spm, dat_to_csv, fil_to_image, image_to_fil, nmv_frame_image,
                           read_dat, read_nmv, read_spm, spm_to_dict)
from nexas_pac import Pac, write_pac

FILES_FILE = '_files.json'
PROGRESS_FILE = '_progress.txt'

# Categorías por extensión
CATEGORIES = {
    'images': {'png', 'bmp', 'jpg', 'jpeg', 'fil', 'spm'},
    'audio': {'ogg', 'wav'},
    'video': {'nmv', 'mpg', 'mpeg'},
    'scripts': {'bin'},
    'data': {'dat'},
}
ALL_CATEGORIES = tuple(CATEGORIES)


def category_of(ext: str) -> str:
    for cat, exts in CATEGORIES.items():
        if ext in exts:
            return cat
    return 'data'


def find_pacs(src) -> List[Path]:
    """src puede ser un .pac o la carpeta del juego."""
    src = Path(src)
    if src.is_file():
        return [src]
    return sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() == '.pac')


def find_movies(src) -> List[Path]:
    src = Path(src)
    movie = src / 'Movie'
    if src.is_file() or not movie.is_dir():
        return []
    return sorted(p for p in movie.iterdir() if p.is_file() and p.suffix.lower() in ('.mpg', '.mpeg'))


# =============================================================================
# Extracción
# =============================================================================

@dataclass
class ExtractResult:
    files: int = 0              # entradas escritas
    converted: int = 0          # FIL convertidos a PNG
    extras: int = 0             # fotogramas NMV, sprites compuestos, CSV
    skipped: int = 0            # ya hechas (--resume)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    last_png: str = ''


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def read_files_info(folder) -> dict:
    try:
        return json.loads((Path(folder) / FILES_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _write_files_info(folder: Path, info: dict):
    with atomic_write(folder / FILES_FILE) as f:
        f.write(json.dumps(info, ensure_ascii=False, indent=1).encode('utf-8'))


def _read_done(path: Path) -> Set[str]:
    try:
        return set(path.read_text(encoding='utf-8').splitlines())
    except OSError:
        return set()


def _save_png(path: Path, img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format='PNG', compress_level=6)
    data = buf.getvalue()
    with atomic_write(path) as f:
        f.write(data)
    return data


def extract_all(src, out_root, categories: Iterable[str] = ALL_CATEGORIES, convert: bool = True,
                extras: bool = True, sprites: bool = False, resume: bool = False,
                names: Optional[Iterable[str]] = None, progress=None, cancel=None) -> ExtractResult:
    """
    Extrae las entradas de los .pac (src: un .pac o la carpeta del juego) en
    <out_root>/<pac>/. Con convert, los FIL se guardan como PNG. Con extras, además se
    escriben los fotogramas de los NMV (versión 4), los SPM como JSON y las tablas .dat
    como CSV (solo para consulta). Con sprites, cada SPM se compone en
    <pac>/_sprites/<nombre>_<patrón>.png. Los vídeos de la carpeta Movie se copian con
    la categoría 'video'. names: solo esas entradas; se suman a una extracción anterior sin
    borrar su registro.
    """
    categories = set(categories)
    selected = set(names) if names is not None else None
    merge = resume or selected is not None
    unknown = categories - set(CATEGORIES)
    if unknown:
        raise ValueError(f"Unknown category: {', '.join(sorted(unknown))}")
    out_root = Path(out_root)
    res = ExtractResult()

    jobs = []           # (pac_path, entradas)
    for pac_path in find_pacs(src):
        try:
            with Pac(pac_path) as pac:
                wanted = [e for e in pac.entries if category_of(e.ext) in categories
                          and (selected is None or e.name in selected)]
        except ValueError as ex:
            res.errors.append(f"{pac_path.name}: {ex}")
            continue
        if wanted:
            jobs.append((pac_path, wanted))
    movies = find_movies(src) if 'video' in categories and selected is None else []

    total = sum(len(w) for _, w in jobs) + len(movies)
    done_count = 0

    for pac_path, wanted in jobs:
        out_dir = out_root / pac_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        prog = out_dir / PROGRESS_FILE
        done = _read_done(prog) if resume and selected is None else set()
        if not merge:
            prog.write_text('', encoding='utf-8')
        info = read_files_info(out_dir) if merge else {}
        info.setdefault('pac', pac_path.name)
        files = info.setdefault('files', {})
        try:
            with Pac(pac_path) as pac, open(prog, 'a', encoding='utf-8') as prog_f:
                names_lower = {e.name.lower(): e for e in pac.entries}
                for e in wanted:
                    check_cancel(cancel)
                    if e.name in done:
                        res.skipped += 1
                        done_count += 1
                        continue
                    report(progress, done_count, total, res.last_png or f"{pac_path.stem}/{e.name}")
                    try:
                        _extract_entry(pac, e, out_dir, files, convert, extras, sprites, names_lower, res)
                        prog_f.write(e.name + '\n')
                        prog_f.flush()
                    except Exception as ex:
                        res.errors.append(f"{pac_path.stem}/{e.name}: {ex}")
                    done_count += 1
        finally:
            # También al cancelar: lo extraído hasta ahora se puede reempaquetar
            _write_files_info(out_dir, info)

    for mv in movies:
        check_cancel(cancel)
        report(progress, done_count, total, f"Movie/{mv.name}")
        dst = out_root / 'Movie' / mv.name
        if not (resume and dst.exists() and dst.stat().st_size == mv.stat().st_size):
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + '.tmp')
            shutil.copyfile(mv, tmp)
            tmp.replace(dst)
            res.files += 1
        else:
            res.skipped += 1
        done_count += 1

    report(progress, total, total, res.last_png)
    return res


def _extract_entry(pac: Pac, e, out_dir: Path, files: dict, convert: bool, extras: bool,
                   sprites: bool, names_lower: dict, res: ExtractResult):
    data = pac.read(e)
    name = safe_name(e.name)
    ext = e.ext

    if ext == 'fil' and convert:
        rel = name.rsplit('.', 1)[0] + '.fil.png'
        png = _save_png(out_dir / rel, fil_to_image(data))
        files[e.name] = [rel, _sha1(png), 'fil']
        res.converted += 1
        res.files += 1
        res.last_png = str(out_dir / rel)
        return

    with atomic_write(out_dir / name) as f:
        f.write(data)
    files[e.name] = [name, _sha1(data), None]
    res.files += 1
    if ext == 'png':
        res.last_png = str(out_dir / name)

    if not extras:
        return
    if ext == 'nmv':
        try:
            nmv = read_nmv(data)
        except ValueError as ex:
            res.warnings.append(f"{e.name}: {ex}")
            return
        if nmv.version != 4:
            return              # códec sin descifrar: solo el .nmv
        frames_dir = out_dir / '_frames' / name.rsplit('.', 1)[0]
        timing = []
        for k, fr in enumerate(nmv.frames):
            _save_png(frames_dir / f"{k:03d}.png", nmv_frame_image(nmv, fr))
            timing.append(fr.time)
            res.extras += 1
        with atomic_write(frames_dir / '_timing.json') as f:
            f.write(json.dumps({'width': nmv.width, 'height': nmv.height, 'ms': timing}).encode('utf-8'))
        res.last_png = str(frames_dir / f"{len(nmv.frames) - 1:03d}.png")
    elif ext == 'spm':
        spm = read_spm(data)
        with atomic_write(out_dir / '_spm' / (name + '.json')) as f:
            f.write(json.dumps(spm_to_dict(spm), ensure_ascii=False, indent=1).encode('utf-8'))
        res.extras += 1
        if sprites:
            cache = {}

            def load(img_name):
                key = img_name.lower()
                if key not in cache:
                    entry = names_lower.get(key)
                    cache[key] = Image.open(io.BytesIO(pac.read(entry))) if entry else None
                return cache[key]
            base = name.rsplit('.', 1)[0]
            missing = 0
            for k, pat in enumerate(spm.patterns):
                if pat.size[0] <= 0 or pat.size[1] <= 0 or not pat.parts:
                    continue                # patrón vacío (sin usar)
                if any(load(spm.images[p.image]) is None for p in pat.parts):
                    missing += 1            # el juego no incluye alguna imagen del patrón
                    continue
                path = out_dir / '_sprites' / f"{base}_{k:02d}.png"
                _save_png(path, compose_spm(spm, k, load))
                res.extras += 1
                res.last_png = str(path)
            if missing:
                res.warnings.append(f"{e.name}: {missing} patterns skipped "
                                    "(their images are not in the .pac)")
    elif ext == 'dat':
        try:
            csv_text = dat_to_csv(read_dat(data))
        except ValueError as ex:
            res.warnings.append(f"{e.name}: {ex}")
            return
        with atomic_write(out_dir / '_csv' / (name + '.csv')) as f:
            f.write(csv_text.encode('utf-8-sig'))
        res.extras += 1


# =============================================================================
# Reempaquetado
# =============================================================================

@dataclass
class RepackResult:
    entries: int = 0                                    # entradas del .pac de salida
    changed: List[str] = field(default_factory=list)    # entradas reemplazadas
    added: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def find_files_folder(folder, pac_path) -> Path:
    """Acepta la carpeta de un .pac (<salida>/Visual) o la de toda la extracción (<salida>)."""
    folder = Path(folder)
    sub = folder / Path(pac_path).stem
    if not (folder / FILES_FILE).exists() and (sub / FILES_FILE).exists():
        folder = sub
    if not (folder / FILES_FILE).exists():
        raise ValueError(f"{FILES_FILE} not found in {folder}: extract the files first "
                         "with this version of the tool")
    return folder


def _encode_back(entry_name: str, path: Path, conv: Optional[str], original: bytes,
                 allow_resize: bool) -> bytes:
    if conv == 'fil':
        img = Image.open(path)
        old = fil_to_image(original)
        if img.size != old.size and not allow_resize:
            raise ValueError(f"it is {img.size} and the original is {old.size} "
                             "(use --allow-resize if this is intended)")
        return image_to_fil(img)
    data = path.read_bytes()
    if Path(entry_name).suffix.lower() == '.png' and not allow_resize:
        new_size = Image.open(io.BytesIO(data)).size
        old_size = Image.open(io.BytesIO(original)).size
        if new_size != old_size:
            raise ValueError(f"it is {new_size} and the original is {old_size} "
                             "(use --allow-resize if this is intended)")
    return data


def repack(original_pac, folder, out_pac, patch_only: bool = False, add_new: bool = False,
           allow_resize: bool = False, progress=None, cancel=None) -> RepackResult:
    """
    Crea out_pac a partir de original_pac con los archivos editados desde la extracción
    (según los SHA-1 de _files.json). Las demás entradas se copian sin volver a comprimir.
    patch_only: out_pac solo lleva las entradas cambiadas (para un UpdateN.pac).
    add_new: también añade los archivos de la carpeta que no están en el .pac.
    """
    folder = find_files_folder(folder, original_pac)
    files = read_files_info(folder).get('files', {})
    res = RepackResult()
    with Pac(original_pac) as pac:
        replaced: Dict[str, bytes] = {}
        known_paths = set()
        total = len(pac.entries)
        for i, e in enumerate(pac.entries):
            check_cancel(cancel)
            rec = files.get(e.name)
            if not rec:
                continue
            rel, sha, conv = rec
            path = folder / rel
            known_paths.add(path.resolve())
            try:
                current = hashlib.sha1(path.read_bytes()).hexdigest()
            except OSError:
                continue        # borrado: se deja el original
            if current == sha:
                continue
            report(progress, i, total, e.name)
            try:
                replaced[e.name] = _encode_back(e.name, path, conv, pac.read(e), allow_resize)
                res.changed.append(e.name)
            except Exception as ex:
                res.errors.append(f"{e.name}: {ex}")

        new_items = []
        if add_new:
            existing = {e.name.lower() for e in pac.entries}
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
        if patch_only:
            items = [(n, d) for n, d in replaced.items()] + new_items
        else:
            items = [(e.name, replaced[e.name]) if e.name in replaced
                     else (e.name, ('raw', pac.read_raw(e), e.size)) for e in pac.entries] + new_items
        if not res.changed and not res.added:
            return res                  # nada editado: no se escribe ningún archivo
        res.entries = len(items)
        write_pac(out_pac, items, pac.pack, cancel=cancel)
    report(progress, total, total)
    return res
