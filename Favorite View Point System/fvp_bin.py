"""
fvp_bin - Extractor/Empaquetador de archivos .bin (Favorite View Point System)

Estructura:
    u32 file_count
    u32 names_size
    file_count x (u32 name_offset, u32 file_offset, u32 file_size)
    nombres (Shift_JIS, terminados en nulo)
    datos
"""

import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from fvp_common import atomic_write, check_cancel, report

INFO_FILE = '_bininfo.json'

# Formato por magic bytes: (magic, extensión, tipo mostrado)
MAGIC_TYPES = [
    (b'OggS', '.ogg', 'OGG'),           # Ogg Vorbis audio
    (b'RIFF', '.wav', 'WAV'),           # WAV audio
    (b'hzc1', '', 'NVSG'),              # NVSG (sin extensión, requisito del motor)
    (b'\x89PNG', '.png', 'PNG'),
    (b'\xff\xd8\xff', '.jpg', 'JPG'),
]

PREFIX_RE = re.compile(r'^(\d{4,})_(.*)$', re.DOTALL)


@dataclass
class BinEntry:
    index: int
    name: str
    offset: int
    size: int


def detect_extension(data: bytes) -> str:
    """Detecta la extensión adecuada según los magic bytes."""
    for magic, ext, _ in MAGIC_TYPES:
        if data.startswith(magic):
            return ext
    return ''


def detect_type(data: bytes) -> str:
    for magic, _, kind in MAGIC_TYPES:
        if data.startswith(magic):
            return kind
    return ''


def read_index(bin_path) -> List[BinEntry]:
    """Lee la tabla de archivos sin extraer los datos."""
    with open(bin_path, 'rb') as f:
        header = f.read(8)
        if len(header) != 8:
            raise ValueError("Archivo demasiado pequeño para ser un .bin")
        file_count, names_size = struct.unpack('<II', header)
        table = f.read(12 * file_count)
        if len(table) != 12 * file_count:
            raise ValueError("Tabla de archivos truncada o archivo no válido")
        names = f.read(names_size)

    entries = []
    for i in range(file_count):
        name_offset, offset, size = struct.unpack_from('<III', table, i * 12)
        end = names.find(b'\x00', name_offset)
        raw = names[name_offset:end if end >= 0 else len(names)]
        entries.append(BinEntry(i, raw.decode('shift_jis', errors='replace'), offset, size))
    return entries


def read_entry(bin_path, entry: BinEntry, limit: Optional[int] = None) -> bytes:
    """Lee los datos de una entrada (o solo los primeros limit bytes)."""
    with open(bin_path, 'rb') as f:
        f.seek(entry.offset)
        return f.read(entry.size if limit is None else min(limit, entry.size))


def output_name(entry: BinEntry, data: bytes, auto_ext: bool) -> str:
    name = f"{entry.index:04d}_{entry.name}"
    if auto_ext:
        name += detect_extension(data)
    return name


def extract(bin_path, output_folder, auto_ext: bool = True,
            indices: Optional[Iterable[int]] = None, progress=None, cancel=None) -> int:
    """
    Extrae archivos de un .bin. Si indices es None extrae todos.
    Guarda _bininfo.json con los nombres originales para reempaquetar.
    """
    entries = read_index(bin_path)
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    selected = entries if indices is None else [entries[i] for i in sorted(set(indices))]
    info_path = output_folder / INFO_FILE
    names_map = {}
    if info_path.exists():
        try:
            names_map = json.loads(info_path.read_text(encoding='utf-8')).get('names', {})
        except (ValueError, OSError):
            names_map = {}

    total = len(selected)
    with open(bin_path, 'rb') as f:
        for i, entry in enumerate(selected):
            check_cancel(cancel)
            report(progress, i, total, entry.name)
            f.seek(entry.offset)
            content = f.read(entry.size)
            out_name = output_name(entry, content, auto_ext)
            # Nombre sin caracteres no válidos en Windows
            safe = re.sub(r'[\\/:*?"<>|]', '_', out_name)
            (output_folder / safe).write_bytes(content)
            names_map[safe] = entry.name

    info_path.write_text(json.dumps({'names': names_map}, ensure_ascii=False, indent=1),
                         encoding='utf-8')
    report(progress, total, total)
    return total


def _sort_key(path: Path):
    m = PREFIX_RE.match(path.name)
    return (0, int(m.group(1)), path.name) if m else (1, 0, path.name)


def collect_files(input_folder):
    """Lista (ruta, nombre_interno) en orden de empaquetado."""
    input_folder = Path(input_folder)
    names_map = {}
    info_path = input_folder / INFO_FILE
    if info_path.exists():
        try:
            names_map = json.loads(info_path.read_text(encoding='utf-8')).get('names', {})
        except (ValueError, OSError):
            names_map = {}

    files = sorted((p for p in input_folder.iterdir() if p.is_file() and p.name != INFO_FILE),
                   key=_sort_key)
    result = []
    for p in files:
        if p.name in names_map:
            name = names_map[p.name]
        else:
            # Quitar el prefijo "0000_"
            m = PREFIX_RE.match(p.name)
            name = m.group(2) if m else p.name
        result.append((p, name))
    return result


def pack(input_folder, bin_path, progress=None, cancel=None) -> int:
    """Empaqueta los archivos de una carpeta en un .bin."""
    files = collect_files(input_folder)
    if not files:
        raise ValueError("Carpeta vacía")

    names = []
    for _, name in files:
        try:
            names.append(name.encode('shift_jis') + b'\x00')
        except UnicodeEncodeError:
            raise ValueError(f"Nombre con caracteres no soportados (Shift_JIS): {name}")

    file_count = len(files)
    table_size = file_count * 12
    names_size = sum(len(n) for n in names)

    with atomic_write(bin_path) as out:
        out.write(struct.pack('<II', file_count, names_size))
        out.write(b'\x00' * table_size)

        name_offsets = []
        pos = 0
        for name in names:
            name_offsets.append(pos)
            out.write(name)
            pos += len(name)

        entries = []
        for i, (path, name) in enumerate(files):
            check_cancel(cancel)
            report(progress, i, file_count, path.name)
            offset = out.tell()
            content = path.read_bytes()
            out.write(content)
            entries.append((name_offsets[i], offset, len(content)))

        out.seek(8)
        for entry in entries:
            out.write(struct.pack('<III', *entry))

    report(progress, file_count, file_count)
    return file_count
