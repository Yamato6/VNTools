"""
bgi_arc - Archivos .arc del motor BGI / Ethornell (Buriko General Interpreter)

Formato (referencia: GARbro ArcFormats/Ethornell/ArcBGI.cs, MIT):
    v1: "PackFile    " · u32 n · n × { nombre[0x10] (cp932), u32 offset, u32 tamaño, 8 bytes }
    v2: "BURIKO ARC20" · u32 n · n × { nombre[0x60], u32 offset, u32 tamaño, 0x18 bytes }
    Los datos empiezan tras el índice; cada offset es relativo a ese punto.

Los archivos no llevan extensión: su tipo sale del contenido (ver bgi_image.detect).
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

from bgi_common import atomic_write, check_cancel, report

MAGIC_V1 = b'PackFile    '
MAGIC_V2 = b'BURIKO ARC20'
LAYOUT = {1: (0x20, 0x10), 2: (0x80, 0x60)}     # versión: (tamaño de entrada, tamaño del nombre)
COPY_CHUNK = 1 << 20

# Origen de los datos al escribir: bytes, un archivo suelto o (archivo, offset, tamaño)
Source = Union[bytes, Path, Tuple[str, int, int]]


@dataclass
class ArcEntry:
    index: int
    name: str
    offset: int         # absoluto en el archivo
    size: int
    extra: bytes = b''  # resto de la entrada del índice (se conserva al reempaquetar)


def read_header(path) -> Tuple[int, List[ArcEntry]]:
    """Devuelve (versión, entradas)."""
    path = Path(path)
    with open(path, 'rb') as f:
        head = f.read(16)
        if head[:12] == MAGIC_V1:
            version = 1
        elif head[:12] == MAGIC_V2:
            version = 2
        else:
            raise ValueError(f"{path.name} is not a BGI .arc archive")
        count, = struct.unpack_from('<I', head, 12)
        esz, nsz = LAYOUT[version]
        table = f.read(esz * count)
    if len(table) < esz * count:
        raise ValueError(f"{path.name}: the index is truncated")
    base = 16 + esz * count
    file_size = path.stat().st_size
    entries = []
    for i in range(count):
        raw = table[i * esz:(i + 1) * esz]
        name = raw[:nsz].split(b'\0')[0].decode('cp932', 'replace')
        off, size = struct.unpack_from('<II', raw, nsz)
        if base + off + size > file_size:
            raise ValueError(f"{path.name}: entry {name} is outside the file")
        entries.append(ArcEntry(i, name, base + off, size, raw[nsz + 8:]))
    return version, entries


def read_index(path) -> List[ArcEntry]:
    return read_header(path)[1]


class BgiArc:
    """Archivo .arc abierto para lectura. Úsese con 'with'."""

    def __init__(self, path):
        self.path = Path(path)
        self.version, self.entries = read_header(self.path)
        self.f = open(self.path, 'rb')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.f.close()

    def read_raw(self, e: ArcEntry) -> bytes:
        self.f.seek(e.offset)
        return self.f.read(e.size)

    def head(self, e: ArcEntry, n: int = 32) -> bytes:
        self.f.seek(e.offset)
        return self.f.read(min(n, e.size))

    def read(self, e: ArcEntry) -> bytes:
        """Datos de la entrada sin la capa DSC (si la lleva)."""
        from bgi_codecs import unpack
        return unpack(self.read_raw(e))


def encode_name(name: str, version: int = 1) -> bytes:
    raw = name.encode('cp932')
    limit = LAYOUT[version][1] - 1
    if len(raw) > limit:
        raise ValueError(f"The name '{name}' is too long for a .arc (at most {limit} bytes)")
    return raw


def _size(src: Source) -> int:
    if isinstance(src, bytes):
        return len(src)
    if isinstance(src, tuple):
        return src[2]
    return Path(src).stat().st_size


def write_arc(path, items: List[Tuple[str, Source]], version: int = 1, extras: Optional[List[bytes]] = None,
              progress=None, cancel=None) -> int:
    """Escribe un .arc con items [(nombre, origen)]. Devuelve la cantidad de entradas."""
    esz, nsz = LAYOUT[version]
    names = [encode_name(n, version) for n, _ in items]
    sizes = [_size(src) for _, src in items]
    index = bytearray((MAGIC_V1 if version == 1 else MAGIC_V2) + struct.pack('<I', len(items)))
    off = 0
    for i, (raw, size) in enumerate(zip(names, sizes)):
        extra = (extras[i] if extras and i < len(extras) else b'')[:esz - nsz - 8]
        index += raw.ljust(nsz, b'\0') + struct.pack('<II', off, size) + extra.ljust(esz - nsz - 8, b'\0')
        off += size
    if off > 0xFFFFFFFF:
        raise ValueError("The archive would be larger than 4 GB")
    total = len(items)
    with atomic_write(path) as out:
        out.write(index)
        for i, (name, src) in enumerate(items):
            check_cancel(cancel)
            report(progress, i, total, name)
            if isinstance(src, bytes):
                out.write(src)
                continue
            if isinstance(src, tuple):
                fpath, start, size = src
            else:
                fpath, start, size = src, 0, sizes[i]
            with open(fpath, 'rb') as f:
                f.seek(start)
                left = size
                while left:
                    chunk = f.read(min(COPY_CHUNK, left))
                    if not chunk:
                        raise ValueError(f"{name}: the source ended early")
                    out.write(chunk)
                    left -= len(chunk)
    report(progress, total, total)
    return total


def collect_files(folder) -> List[Tuple[str, Path]]:
    """(nombre, ruta) de los archivos de una carpeta (sin subcarpetas ni los de la herramienta)."""
    folder = Path(folder)
    return [(p.name, p) for p in sorted(folder.iterdir())
            if p.is_file() and not p.name.startswith('_') and not p.name.endswith('.tmp')]
