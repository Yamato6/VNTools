"""
advhd_arc - Lectura de archivos .arc del motor AdvHD (WillPlus)

Estructura (little-endian, sin compresión ni cifrado):
    u32 n_tipos
    n_tipos x (char ext[4], u32 n_archivos, u32 offset_tabla)
    por tipo, en offset_tabla: n_archivos x entrada de 21 bytes:
        char nombre[13] (cp932, terminado en nulo), u32 tamaño, u32 offset absoluto

El mismo nombre puede aparecer en varios tipos (TBYM0000 como PNG y como MSK).
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List

ENTRY_SIZE = 21
NAME_SIZE = 13


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
