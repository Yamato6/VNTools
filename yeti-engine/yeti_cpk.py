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
from typing import BinaryIO, Dict, List, Optional

from yeti_codecs import decompress_crilayla, is_crilayla
from yeti_common import atomic_write, check_cancel, report
from yeti_utf import UTFTable, write_table

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


# =============================================================================
# Escritura
# =============================================================================

COPY_CHUNK = 1 << 20
SMALL_LIMIT = 0x10000       # DataL: archivos cuyos tamaños caben en 16 bits


def _packet(magic: bytes, table: bytes) -> bytes:
    return magic + struct.pack('<IQ', 0xFF, len(table)) + table


def write_cpk(cpk: CPK, out_path, replace: Dict[int, bytes], progress=None, cancel=None) -> int:
    """
    Escribe un .cpk igual que cpk (mismo modo, solo ITOC) con los archivos de replace
    (ID -> datos tal cual se guardan, sin CRILAYLA) en lugar de los originales.
    Sin cambios, el resultado es idéntico al original. Devuelve los archivos reemplazados.
    """
    if cpk.header.get('TocOffset') not in (None, 0, NONE_OFFSET) or not cpk.header.get('ItocOffset'):
        raise ValueError("Solo se pueden reempaquetar .cpk con ITOC (sin TOC)")
    align = cpk.align
    itoc_off = cpk.header['ItocOffset']
    itoc = _read_packet(cpk.f, itoc_off, b'ITOC')
    tables = {k: UTFTable(itoc.rows[0][k]) for k in ('DataL', 'DataH')}

    sizes = []          # (entrada, tamaño guardado, tamaño real)
    for e in cpk.entries:
        if e.id in replace:
            sizes.append((e, len(replace[e.id]), len(replace[e.id])))
        else:
            sizes.append((e, e.size, e.extract_size))
    rows = {'DataL': [], 'DataH': []}
    for e, size, ext in sizes:
        key = 'DataL' if max(size, ext) < SMALL_LIMIT else 'DataH'
        rows[key].append({'ID': e.id, 'FileSize': size, 'ExtractSize': ext})
    itoc_row = dict(itoc.rows[0])
    itoc_row.update({'FilesL': len(rows['DataL']), 'FilesH': len(rows['DataH'])})
    for k in ('DataL', 'DataH'):
        itoc_row[k] = write_table(tables[k].name, tables[k].columns, rows[k])
    itoc_packet = _packet(b'ITOC', write_table(itoc.name, itoc.columns, [itoc_row]))

    content_off = itoc_off + len(itoc_packet)
    content_off += -content_off % align
    header = dict(cpk.header)
    header.update({
        'ItocSize': len(itoc_packet),
        'ContentOffset': content_off,
        'ContentSize': sum(size + (-size % align) for _e, size, _x in sizes),
        'EnabledDataSize': sum(size for _e, size, _x in sizes),
        'EnabledPackedSize': sum(ext for _e, _s, ext in sizes),
        'Files': len(sizes),
    })
    header_table = _read_packet(cpk.f, 0, b'CPK ')
    header_packet = _packet(b'CPK ', write_table(header_table.name, header_table.columns, [header]))
    cpk.f.seek(0)
    head_area = bytearray(cpk.f.read(itoc_off))
    if len(header_packet) > len(head_area):
        raise ValueError("La cabecera del .cpk no cabe antes del ITOC")
    head_area[:len(header_packet)] = header_packet

    total = len(sizes)
    with atomic_write(out_path) as out:
        out.write(head_area)
        out.write(itoc_packet)
        out.write(bytes(content_off - itoc_off - len(itoc_packet)))
        pos = content_off
        for i, (e, size, _ext) in enumerate(sizes):
            check_cancel(cancel)
            report(progress, i, total, e.path)
            if pos % align:
                out.write(bytes(-pos % align))
                pos += -pos % align
            if e.id in replace:
                out.write(replace[e.id])
            else:
                cpk.f.seek(e.offset)
                left = e.size
                while left:
                    chunk = cpk.f.read(min(COPY_CHUNK, left))
                    if not chunk:
                        raise ValueError(f"Datos truncados: {e.path}")
                    out.write(chunk)
                    left -= len(chunk)
            pos += size
    report(progress, total, total)
    return sum(1 for e in cpk.entries if e.id in replace)
