"""
nexas_pac - Archivos .pac del motor NeXAS (Giga)

Formato actual (little-endian):
    0x00  char magic[4] = "PAC\\0"
    0x04  u32  número de entradas
    0x08  u32  compresión: 0 ninguna, 1 LZSS, 2 Huffman, 3 zlib,
               4 zlib solo en las entradas cuyo tamaño guardado difiere del real
    0x0C  datos de las entradas
    fin-4-k  índice: k bytes invertidos (~b) y comprimidos con Huffman, que dan
             n × 0x4C bytes: char nombre[0x40] (Shift-JIS) · u32 offset · u32 tamaño
             real · u32 tamaño guardado
    fin-4 u32  k

Formato antiguo (juegos NeXAS anteriores): el índice va sin comprimir desde 0x0C,
con nombres de 0x40 o 0x20 bytes.

Referencia: GARbro (ArcFormats/Nexas/ArcPAC.cs), licencia MIT.
"""

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple, Union

from nexas_common import atomic_write, check_cancel, report, safe_name
from nexas_compress import huffman_decode, huffman_encode, lzss_decode, lzss_encode

MAGIC = b'PAC\0'
HEADER_SIZE = 12
NAME_SIZE = 0x40
ENTRY_SIZE = NAME_SIZE + 12

PACK_NONE, PACK_LZSS, PACK_HUFFMAN, PACK_ZLIB, PACK_ZLIB_IF_SMALLER = 0, 1, 2, 3, 4
PACK_NAMES = {0: 'none', 1: 'LZSS', 2: 'Huffman', 3: 'zlib', 4: 'zlib (when smaller)'}


@dataclass
class PacEntry:
    name: str           # tal como está en el índice (puede llevar subcarpetas con '\')
    offset: int
    size: int           # tamaño real (descomprimido)
    stored: int         # tamaño guardado en el archivo

    @property
    def ext(self) -> str:
        n = self.name.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
        return n.rsplit('.', 1)[-1].lower() if '.' in n else ''


class Pac:
    """Archivo .pac abierto para lectura. Úsese con 'with'."""

    def __init__(self, path):
        self.path = Path(path)
        self.f = open(self.path, 'rb')
        try:
            self._read_header()
        except Exception:
            self.f.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.f.close()

    def _read_header(self):
        head = self.f.read(HEADER_SIZE)
        if len(head) < HEADER_SIZE or head[:4] != MAGIC:
            raise ValueError("Not a NeXAS .pac file")
        self.count, self.pack = struct.unpack_from('<II', head, 4)
        self.f.seek(0, 2)
        self.file_size = self.f.tell()
        if not 0 < self.count < 1_000_000:
            raise ValueError("Not a NeXAS .pac file (invalid entry count)")
        if self.pack not in PACK_NAMES:
            raise ValueError(f"Unknown compression type {self.pack}")
        self.old_format = False
        self.entries = self._read_index()

    def _read_index(self) -> List[PacEntry]:
        self.f.seek(self.file_size - 4)
        (isize,) = struct.unpack('<I', self.f.read(4))
        if 0 < isize < self.file_size - HEADER_SIZE - 4:
            self.f.seek(self.file_size - 4 - isize)
            packed = bytes(b ^ 0xFF for b in self.f.read(isize))
            try:
                entries = self._parse_index(huffman_decode(packed, self.count * ENTRY_SIZE), NAME_SIZE)
            except ValueError:
                entries = None
            if entries is not None:
                return entries
        # Formato antiguo: índice sin comprimir desde 0x0C
        for name_size in (0x40, 0x20):
            self.f.seek(HEADER_SIZE)
            raw = self.f.read(self.count * (name_size + 12))
            entries = self._parse_index(raw, name_size)
            if entries is not None:
                self.old_format = True
                self.name_size = name_size
                return entries
        raise ValueError("Could not read the index of the .pac file")

    def _parse_index(self, raw: bytes, name_size: int) -> Optional[List[PacEntry]]:
        size = name_size + 12
        if len(raw) < self.count * size:
            return None
        entries = []
        for k in range(self.count):
            rec = raw[k * size:(k + 1) * size]
            name = rec[:name_size].split(b'\0')[0].decode('cp932', errors='replace')
            offset, usize, csize = struct.unpack_from('<III', rec, name_size)
            if not name or offset < HEADER_SIZE or offset + csize > self.file_size:
                return None
            entries.append(PacEntry(name, offset, usize, csize))
        return entries

    def is_packed(self, e: PacEntry) -> bool:
        if self.pack == PACK_NONE:
            return False
        if self.pack == PACK_ZLIB_IF_SMALLER:
            return e.stored != e.size
        return True

    def read_raw(self, e: PacEntry) -> bytes:
        """Los bytes tal como están guardados."""
        self.f.seek(e.offset)
        data = self.f.read(e.stored)
        if len(data) != e.stored:
            raise ValueError(f"Truncated data: {e.name}")
        return data

    def read(self, e: PacEntry) -> bytes:
        raw = self.read_raw(e)
        if not self.is_packed(e):
            return raw
        if self.pack == PACK_LZSS:
            return lzss_decode(raw, e.size)
        if self.pack == PACK_HUFFMAN:
            return huffman_decode(raw, e.size)
        try:
            return zlib.decompress(raw)
        except zlib.error as ex:
            raise ValueError(f"{e.name}: {ex}")


def read_index(path) -> List[PacEntry]:
    with Pac(path) as pac:
        return pac.entries


def compress(data: bytes, pack: int) -> bytes:
    if pack == PACK_NONE:
        return data
    if pack == PACK_LZSS:
        return lzss_encode(data)
    if pack == PACK_HUFFMAN:
        return huffman_encode(data)
    packed = zlib.compress(data, 9)
    if pack == PACK_ZLIB_IF_SMALLER and len(packed) >= len(data):
        return data
    return packed


# =============================================================================
# Escritura
# =============================================================================

# Contenido de una entrada: bytes, ruta de un archivo, o ('raw', bytes ya comprimidos, tamaño real)
Source = Union[bytes, bytearray, str, Path, Tuple[str, bytes, int]]


def _load(src: Source) -> bytes:
    if isinstance(src, (bytes, bytearray)):
        return bytes(src)
    return Path(src).read_bytes()


def encode_name(name: str) -> bytes:
    try:
        raw = name.encode('cp932')
    except UnicodeEncodeError:
        raise ValueError(f"Name not representable in Shift-JIS: {name}")
    if len(raw) > NAME_SIZE - 1:
        raise ValueError(f"Name too long (maximum {NAME_SIZE - 1} bytes): {name}")
    return raw


def write_pac(path, items: List[Tuple[str, Source]], pack: int = PACK_NONE,
              progress=None, cancel=None) -> int:
    """
    Escribe un .pac con las entradas (nombre, contenido) en ese orden, en el formato
    actual (índice comprimido al final). Devuelve el número de entradas.
    """
    if pack not in PACK_NAMES:
        raise ValueError(f"Invalid compression type: {pack}")
    if not items:
        raise ValueError("There are no files to pack")
    names = [encode_name(n) for n, _src in items]
    seen = set()
    for n, _ in items:
        if n.lower() in seen:
            raise ValueError(f"Duplicate name: {n}")
        seen.add(n.lower())

    index = bytearray()
    total = len(items)
    with atomic_write(path) as f:
        f.write(MAGIC + struct.pack('<II', total, pack))
        offset = HEADER_SIZE
        for i, ((name, src), raw_name) in enumerate(zip(items, names)):
            check_cancel(cancel)
            report(progress, i, total, name)
            if isinstance(src, tuple) and src and src[0] == 'raw':
                _tag, stored, size = src
            else:
                data = _load(src)
                size = len(data)
                stored = compress(data, pack)
            f.write(stored)
            index += raw_name.ljust(NAME_SIZE, b'\0') + struct.pack('<III', offset, size, len(stored))
            offset += len(stored)
        packed = bytes(b ^ 0xFF for b in huffman_encode(bytes(index)))
        f.write(packed)
        f.write(struct.pack('<I', len(packed)))
    report(progress, total, total)
    return total


def collect_files(folder) -> List[Tuple[str, Path]]:
    """Archivos de una carpeta (con subcarpetas) como (nombre en el .pac, ruta), ordenados."""
    folder = Path(folder)
    files = []
    for p in sorted(folder.rglob('*')):
        if p.is_file() and not p.name.startswith('_') and not p.name.endswith('.tmp'):
            files.append(('\\'.join(p.relative_to(folder).parts), p))
    return files


def pack(folder, pac_path, pack_type: int = PACK_NONE, order_from=None,
         progress=None, cancel=None) -> int:
    """
    Empaqueta los archivos de una carpeta en un .pac. Con order_from (un .pac original),
    las entradas que ya existían conservan su orden y compresión, y las nuevas van al final.
    Se ignoran los archivos que empiezan por '_' (registros de la herramienta).
    """
    folder, pac_path = Path(folder).resolve(), Path(pac_path).resolve()
    if folder in pac_path.parents:
        raise ValueError("The output .pac cannot be inside the input folder")
    files = collect_files(folder)
    if not files:
        raise ValueError("There are no files to pack")
    if order_from:
        with Pac(order_from) as orig:
            pack_type = orig.pack
            rank = {e.name.lower(): i for i, e in enumerate(orig.entries)}
        files.sort(key=lambda it: (rank.get(it[0].lower(), len(rank)), it[0].lower()))
    return write_pac(pac_path, files, pack_type, progress, cancel)


def replace_entries(original, out_path, replacements: dict, add_new: bool = True,
                    progress=None, cancel=None) -> Tuple[int, int, int]:
    """
    Copia original en out_path cambiando las entradas de replacements ({nombre: contenido});
    las entradas sin cambios se copian sin volver a comprimir. Los nombres que no existen
    se añaden al final si add_new. Devuelve (entradas, reemplazadas, añadidas).
    """
    repl = {k.lower(): (k, v) for k, v in replacements.items()}
    with Pac(original) as pac:
        items = []
        replaced = 0
        for e in pac.entries:
            hit = repl.pop(e.name.lower(), None)
            if hit is not None:
                items.append((e.name, hit[1]))
                replaced += 1
            else:
                items.append((e.name, ('raw', pac.read_raw(e), e.size)))
        added = 0
        if add_new:
            for name, src in repl.values():
                items.append((name, src))
                added += 1
        elif repl:
            raise ValueError("Not in the original .pac: " + ", ".join(n for n, _ in list(repl.values())[:5]))
        write_pac(out_path, items, pac.pack, progress, cancel)
    return len(items), replaced, added


def extract(pac_path, out_dir, names: Optional[Iterable[str]] = None,
            progress=None, cancel=None) -> int:
    """Guarda las entradas tal cual (descomprimidas). names: solo esas entradas."""
    out_dir = Path(out_dir)
    with Pac(pac_path) as pac:
        entries = pac.entries
        if names is not None:
            wanted = set(names)
            entries = [e for e in entries if e.name in wanted]
        total = len(entries)
        for i, e in enumerate(entries):
            check_cancel(cancel)
            report(progress, i, total, e.name)
            data = pac.read(e)
            with atomic_write(out_dir / safe_name(e.name)) as f:
                f.write(data)
    report(progress, total, total)
    return total
