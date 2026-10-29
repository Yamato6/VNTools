"""
advhd_arc - Lectura de archivos .arc del motor AdvHD (WillPlus)

Estructura (little-endian, sin compresión ni cifrado):
    u32 n_tipos
    n_tipos x (char ext[4], u32 n_archivos, u32 offset_tabla)
    por tipo, en offset_tabla: n_archivos x entrada de 21 bytes:
        char nombre[13] (cp932, terminado en nulo), u32 tamaño, u32 offset absoluto

El mismo nombre puede aparecer en varios tipos (TBYM0000 como PNG y como MSK).
Los originales tienen los tipos en orden alfabético, los nombres ordenados dentro de
cada tipo y las tablas y los datos seguidos, sin relleno: pack() escribe igual.
"""

import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from advhd_common import atomic_write, check_cancel, report

ENTRY_SIZE = 21
NAME_SIZE = 13
COPY_CHUNK = 1 << 20


@dataclass
class ArcEntry:
    name: str
    ext: str
    size: int
    offset: int

    @property
    def filename(self) -> str:
        return f"{self.name}.{self.ext.lower()}"


def read_index(arc_path) -> List[ArcEntry]:
    arc_path = Path(arc_path)
    file_size = arc_path.stat().st_size
    with open(arc_path, 'rb') as f:
        head = f.read(4)
        if len(head) < 4:
            raise ValueError("Archivo demasiado pequeño")
        (n_types,) = struct.unpack('<I', head)
        if not 0 < n_types < 256:
            raise ValueError("No es un archivo .arc de AdvHD")
        raw = f.read(12 * n_types)
        types = []
        for i in range(n_types):
            r = raw[i * 12:i * 12 + 12]
            ext = r[:4].rstrip(b'\0')
            if len(r) < 12 or not ext.isalnum():
                raise ValueError("No es un archivo .arc de AdvHD")
            types.append((ext.decode('ascii'),) + struct.unpack_from('<II', r, 4))

        entries = []
        for ext, count, off in types:
            f.seek(off)
            tab = f.read(count * ENTRY_SIZE)
            if len(tab) != count * ENTRY_SIZE:
                raise ValueError(f"Tabla de {ext} truncada")
            for j in range(count):
                p = j * ENTRY_SIZE
                name = tab[p:p + NAME_SIZE].split(b'\0')[0].decode('cp932', errors='replace')
                size, offset = struct.unpack_from('<II', tab, p + NAME_SIZE)
                if offset + size > file_size:
                    raise ValueError(f"Entrada fuera del archivo: {name}.{ext}")
                entries.append(ArcEntry(name, ext, size, offset))
    return entries


def read_entry(f, entry: ArcEntry) -> bytes:
    """f: archivo .arc abierto en modo binario."""
    f.seek(entry.offset)
    data = f.read(entry.size)
    if len(data) != entry.size:
        raise ValueError(f"Datos truncados: {entry.filename}")
    return data


# =============================================================================
# Empaquetar
# =============================================================================

def encode_name(name: str) -> bytes:
    raw = name.encode('cp932')
    if len(raw) > NAME_SIZE - 1:
        raise ValueError(f"Nombre demasiado largo (máximo {NAME_SIZE - 1} bytes): {name}")
    return raw


def collect_files(folder) -> List[Tuple[str, str, Path]]:
    """
    (nombre, TIPO, ruta) de los archivos NOMBRE.ext de una carpeta, en el orden del .arc:
    tipos en orden alfabético y nombres ordenados dentro de cada tipo.
    Se ignoran los archivos que empiezan por '_' (_progreso.txt, _images.json…).
    """
    files = []
    for p in Path(folder).iterdir():
        if not p.is_file() or p.name.startswith('_') or not p.suffix:
            continue
        ext = p.suffix[1:].upper()
        if len(ext) > 4 or not ext.isascii() or not ext.isalnum():
            continue
        files.append((p.stem, ext, p))
    files.sort(key=lambda t: (t[1], encode_name(t[0])))
    return files


def write_arc(arc_path, items, progress=None, cancel=None) -> int:
    """
    items: lista ordenada de (nombre, TIPO, origen), donde origen es bytes o una ruta.
    Devuelve la cantidad de archivos.
    """
    types = {}
    for name, ext, _src in items:
        types.setdefault(ext, []).append(name)
    if len(types) > 255:
        raise ValueError("Demasiados tipos de archivo")
    sizes = [len(src) if isinstance(src, (bytes, bytearray)) else Path(src).stat().st_size
             for _n, _e, src in items]

    header = bytearray(struct.pack('<I', len(types)))
    table_off = 4 + 12 * len(types)
    for ext, names in types.items():
        header += ext.encode('ascii').ljust(4, b'\0') + struct.pack('<II', len(names), table_off)
        table_off += ENTRY_SIZE * len(names)

    offset = table_off
    for (name, _ext, _src), size in zip(items, sizes):
        header += encode_name(name).ljust(NAME_SIZE, b'\0') + struct.pack('<II', size, offset)
        offset += size

    total = len(items)
    with atomic_write(arc_path) as out:
        out.write(header)
        for i, (name, ext, src) in enumerate(items):
            check_cancel(cancel)
            report(progress, i, total, f"{name}.{ext.lower()}")
            if isinstance(src, (bytes, bytearray)):
                out.write(src)
            else:
                with open(src, 'rb') as f:
                    shutil.copyfileobj(f, out, COPY_CHUNK)
    report(progress, total, total)
    return total


def pack(folder, arc_path, progress=None, cancel=None) -> int:
    """Empaqueta los archivos NOMBRE.ext de una carpeta en un .arc. Devuelve la cantidad."""
    files = collect_files(folder)
    if not files:
        raise ValueError("No hay archivos que empaquetar")
    folder, arc_path = Path(folder).resolve(), Path(arc_path).resolve()
    if folder in arc_path.parents:
        raise ValueError("El .arc de salida no puede estar dentro de la carpeta de entrada")
    return write_arc(arc_path, files, progress, cancel)
