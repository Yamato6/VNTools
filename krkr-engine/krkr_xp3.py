"""
krkr_xp3 - Archivos .xp3 del motor KiriKiri 2 (KAG)

Formato (referencia: GARbro ArcFormats/KiriKiri/ArcXP3.cs, MIT):
    "XP3\\r\\n \\n\\x1a\\x8bg\\x01" · i64 offset del índice
    (versión 2.30: en ese offset hay u32 0x80 · u8 · i64 offset real del índice)
    índice: u8 tipo · tipo 1: i64 tamaño zlib · i64 tamaño · zlib(...) / tipo 0: i64 tamaño · datos
    bloques del índice: char sig[4] · i64 tamaño · cuerpo. "File" contiene subbloques:
        info: u32 flags · i64 tamaño · i64 tamaño guardado · u16 n · UTF-16[n] nombre
        segm: n × { u32 flags (1 = zlib) · i64 offset · i64 tamaño · i64 tamaño guardado }
        adlr: u32 adler32 del archivo
    Los demás bloques (de otros juegos: "yuz:", "hnfn", "eliF"...) se ignoran.

Los parches del juego (patch.xp3, patch2.xp3...) se cargan después de los archivos base y
sus archivos sustituyen a los del mismo nombre.
"""

import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple, Union

from krkr_common import atomic_write, check_cancel, report

MAGIC = b'XP3\r\n \n\x1a\x8bg\x01'
COPY_CHUNK = 1 << 20

# Archivo que Yuzusoft y otros incluyen en cada .xp3 para pedir que no se extraiga
WARNING_TEXT = ("These archives include a notice from the author asking not to extract them. "
                "Use the extracted files only with your own copy of the game and do not share them.")

Source = Union[bytes, Path]


@dataclass
class Xp3Entry:
    index: int
    name: str
    size: int                   # tamaño real
    packed: int                 # tamaño guardado
    flags: int = 0
    segments: List[Tuple[bool, int, int, int]] = field(default_factory=list)  # (zlib, off, tam, guardado)
    adler: int = None

    @property
    def compressed(self) -> bool:
        return any(s[0] for s in self.segments)


def is_warning(name: str) -> bool:
    """El archivo del aviso de 'protected archive' que llevan los .xp3 de algunos juegos."""
    return 'protected archive' in name or ('$$$' in name and name.endswith('.txt'))


def _parse_index(idx: bytes) -> List[Xp3Entry]:
    entries = []
    p = 0
    while p + 12 <= len(idx):
        sig = idx[p:p + 4]
        size, = struct.unpack_from('<q', idx, p + 4)
        body = idx[p + 12:p + 12 + size]
        p += 12 + size
        if sig != b'File':
            continue
        e = Xp3Entry(len(entries), '', 0, 0)
        q = 0
        while q + 12 <= len(body):
            ssig = body[q:q + 4]
            ssize, = struct.unpack_from('<q', body, q + 4)
            s = body[q + 12:q + 12 + ssize]
            q += 12 + ssize
            if ssig == b'info':
                e.flags, e.size, e.packed = struct.unpack_from('<Iqq', s, 0)
                n, = struct.unpack_from('<H', s, 20)
                e.name = s[22:22 + 2 * n].decode('utf-16le', 'replace')
            elif ssig == b'segm':
                for k in range(len(s) // 0x1C):
                    comp, off, sz, psz = struct.unpack_from('<Iqqq', s, k * 0x1C)
                    e.segments.append((bool(comp & 7), off, sz, psz))
            elif ssig == b'adlr':
                e.adler, = struct.unpack_from('<I', s, 0)
        if e.name:
            entries.append(e)
    return entries


def read_index(path) -> List[Xp3Entry]:
    path = Path(path)
    with open(path, 'rb') as f:
        head = f.read(19)
        if head[:11] != MAGIC:
            raise ValueError(f"{path.name} is not a KiriKiri .xp3 archive")
        off, = struct.unpack_from('<q', head, 11)
        f.seek(off)
        if struct.unpack('<I', f.read(4))[0] == 0x80:
            f.seek(off + 9)
            off, = struct.unpack('<q', f.read(8))
        f.seek(off)
        kind = f.read(1)[0]
        if kind & 7 == 1:
            packed, size = struct.unpack('<qq', f.read(16))
            idx = zlib.decompress(f.read(packed))
        else:
            size, = struct.unpack('<q', f.read(8))
            idx = f.read(size)
    entries = _parse_index(idx)
    file_size = path.stat().st_size
    for e in entries:
        for _, off, _, psz in e.segments:
            if off + psz > file_size:
                raise ValueError(f"{path.name}: entry {e.name} is outside the file")
    return entries


class Xp3:
    """Archivo .xp3 abierto para lectura. Úsese con 'with'."""

    def __init__(self, path):
        self.path = Path(path)
        self.entries = read_index(self.path)
        self.f = open(self.path, 'rb')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.f.close()

    @property
    def has_warning(self) -> bool:
        return any(is_warning(e.name) for e in self.entries)

    def files(self) -> List[Xp3Entry]:
        """Entradas sin el archivo del aviso."""
        return [e for e in self.entries if not is_warning(e.name)]

    def read(self, e: Xp3Entry, check: bool = False) -> bytes:
        out = bytearray()
        for comp, off, _size, psz in e.segments:
            self.f.seek(off)
            d = self.f.read(psz)
            out += zlib.decompress(d) if comp else d
        data = bytes(out)
        if check and e.adler is not None and zlib.adler32(data) != e.adler:
            raise ValueError("the checksum (adler32) does not match")
        return data

    def head(self, e: Xp3Entry, n: int = 64) -> bytes:
        """Los primeros n bytes (sin descomprimir todo el archivo)."""
        if not e.segments:
            return b''
        comp, off, _size, psz = e.segments[0]
        self.f.seek(off)
        if not comp:
            return self.f.read(min(n, psz))
        d = zlib.decompressobj()
        out = b''
        left = psz
        while left and len(out) < n:
            chunk = self.f.read(min(0x10000, left))
            if not chunk:
                break
            left -= len(chunk)
            out += d.decompress(chunk, n - len(out))
        return out[:n]


# =============================================================================
# Orden de carga y parches
# =============================================================================

_PATCH_RE = re.compile(r'^patch(\d*)$', re.I)


def patch_number(path) -> int:
    """0 para un archivo base, 1 para patch.xp3, N para patchN.xp3."""
    m = _PATCH_RE.match(Path(path).stem)
    if not m:
        return 0
    return int(m.group(1)) if m.group(1) else 1


def find_xp3(src) -> List[Path]:
    """src: un .xp3 o la carpeta del juego. En orden de carga: base y luego patch, patch2..."""
    src = Path(src)
    if src.is_file():
        return [src]
    found = [p for p in src.iterdir() if p.is_file() and p.suffix.lower() == '.xp3']
    return sorted(found, key=lambda p: (patch_number(p), p.name.lower()))


def next_patch_name(folder) -> str:
    """Nombre del siguiente parche libre de la carpeta del juego (p. ej. patch4.xp3)."""
    numbers = [patch_number(p) for p in find_xp3(folder)] if Path(folder).is_dir() else []
    n = max(numbers, default=0) + 1
    return 'patch.xp3' if n == 1 else f'patch{n}.xp3'


# =============================================================================
# Escritura
# =============================================================================

def _chunk(sig: bytes, body: bytes) -> bytes:
    return sig + struct.pack('<q', len(body)) + body


def write_xp3(path, items: List[Tuple[str, Source]], progress=None, cancel=None) -> int:
    """
    Escribe un .xp3 con items [(nombre, bytes o ruta)]. Cada archivo va comprimido con zlib si
    así ocupa menos. Devuelve la cantidad de archivos.
    """
    total = len(items)
    index = bytearray()
    with atomic_write(path) as out:
        out.write(MAGIC + struct.pack('<q', 0))
        for i, (name, src) in enumerate(items):
            check_cancel(cancel)
            report(progress, i, total, name)
            data = src if isinstance(src, bytes) else Path(src).read_bytes()
            packed = zlib.compress(data, 6)
            comp = len(packed) < len(data)
            stored = packed if comp else data
            off = out.tell()
            out.write(stored)
            raw_name = name.replace('\\', '/').encode('utf-16le')
            info = struct.pack('<Iqq', 0, len(data), len(stored)) + \
                struct.pack('<H', len(raw_name) // 2) + raw_name
            segm = struct.pack('<Iqqq', 1 if comp else 0, off, len(data), len(stored))
            adlr = struct.pack('<I', zlib.adler32(data))
            index += _chunk(b'File', _chunk(b'info', info) + _chunk(b'segm', segm) + _chunk(b'adlr', adlr))
        index_off = out.tell()
        packed = zlib.compress(bytes(index), 9)
        out.write(b'\x01' + struct.pack('<qq', len(packed), len(index)) + packed)
        out.seek(11)
        out.write(struct.pack('<q', index_off))
    report(progress, total, total)
    return total


def merge_into_xp3(out_xp3, items: Dict[str, bytes], cancel=None) -> int:
    """Escribe items en out_xp3 conservando los demás archivos que ya tuviera (un parche)."""
    out_xp3 = Path(out_xp3)
    merged: List[Tuple[str, Source]] = []
    if out_xp3.is_file():
        with Xp3(out_xp3) as x:
            merged = [(e.name, x.read(e)) for e in x.entries if e.name not in items and not is_warning(e.name)]
    merged += sorted(items.items())
    return write_xp3(out_xp3, merged, cancel=cancel)
