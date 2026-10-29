"""
yeti_cpk - Lectura de archivos .cpk de CRIWARE (cabecera, TOC, ITOC)

Estructura:
    "CPK " + i32 0xFF + u64 tamaño + tabla @UTF con la cabecera
    TOC  (archivos con nombre) o ITOC (solo IDs: DataL / DataH con ID, FileSize, ExtractSize)
    datos, cada archivo alineado a Align desde ContentOffset

Los .cpk de CROSS†CHANNEL -Final Complete- usan CpkMode 0, solo ITOC.
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, List, Optional

from yeti_codecs import decompress_crilayla, is_crilayla
from yeti_utf import UTFTable

NONE_OFFSET = 0xFFFFFFFFFFFFFFFF


@dataclass
class Entry:
    path: str
    offset: int
    size: int           # tamaño almacenado
    extract_size: int   # tamaño real
    id: Optional[int] = None

    @property
    def compressed(self) -> bool:
        return self.extract_size != self.size


def _read_packet(f: BinaryIO, offset: int, magic: bytes) -> UTFTable:
    f.seek(offset)
    head = f.read(16)
    if head[:4] != magic:
        raise ValueError(f"Se esperaba {magic!r} en {offset:#x}, hay {head[:4]!r}")
    (size,) = struct.unpack_from('<Q', head, 8)
    return UTFTable(f.read(size))


class CPK:
    def __init__(self, path):
        self.path = Path(path)
        self.f = open(self.path, 'rb')
        try:
            self.header = _read_packet(self.f, 0, b'CPK ').rows[0]
        except Exception:
            self.f.close()
            raise
        h = self.header
        self.align = h.get('Align') or 0x800
        self.content_offset = h.get('ContentOffset') or 0
        self.entries: List[Entry] = []
        toc = h.get('TocOffset')
        itoc = h.get('ItocOffset')
        if toc not in (None, 0, NONE_OFFSET):
            self._read_toc(toc)
        elif itoc not in (None, 0, NONE_OFFSET):
            self._read_itoc(itoc)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def mode(self):
        return self.header.get('CpkMode')

    def _read_toc(self, toc_off: int):
        # Los FileOffset del TOC son relativos al menor entre TocOffset y ContentOffset
        base = min(toc_off, self.content_offset) if self.content_offset else toc_off
        for r in _read_packet(self.f, toc_off, b'TOC ').rows:
            d, n = r.get('DirName') or '', r.get('FileName') or ''
            path = f"{d}/{n}" if d else n
            size = r['FileSize']
            self.entries.append(Entry(path, base + r['FileOffset'], size,
                                      r.get('ExtractSize') or size, r.get('ID')))

    def _read_itoc(self, itoc_off: int):
        t = _read_packet(self.f, itoc_off, b'ITOC').rows[0]
        sizes = {}
        for key in ('DataL', 'DataH'):
            if t.get(key):
                for r in UTFTable(t[key]).rows:
                    sizes[r['ID']] = (r['FileSize'], r.get('ExtractSize') or r['FileSize'])
        off = self.content_offset
        for i in sorted(sizes):
            size, ext = sizes[i]
            self.entries.append(Entry(f"{i:05d}.bin", off, size, ext, i))
            off += size + (-size % self.align)

    def read(self, e: Entry) -> bytes:
        """Datos del archivo, ya sin CRILAYLA."""
        self.f.seek(e.offset)
        data = self.f.read(e.size)
        if len(data) != e.size:
            raise ValueError(f"Datos truncados: {e.path}")
        if is_crilayla(data):
            data = decompress_crilayla(data)
        return data

    def close(self):
        self.f.close()
