"""
cs2_int - Archivos .int (KIF) de CatSystem2, con o sin cifrado

Formato (little-endian):
    0x00  char magic[4] = "KIF\\0"
    0x04  u32  número de entradas (n)
    0x08  n × 0x48 bytes: char nombre[0x40] · u32 offset · u32 tamaño
    ...   datos. En los archivos cifrados, antes de los datos de cada entrada va una
          copia del registro anterior de la tabla (0x48 bytes)

Cifrado (la entrada 0 se llama "__key__.dat"):
    - Su campo de tamaño es la semilla; la clave de Blowfish es el primer número del
      Mersenne Twister sembrado con ella (cs2_crypto.mt_first), como u32 LE.
    - Entrada i (1..n-1): (offset, tamaño) = Blowfish.decipher(offset_guardado + i, tamaño).
    - Nombre: César sobre las letras con k = suma de los 4 bytes de mt_first(clave + i),
      que aumenta en 1 por cada carácter. La clave principal sale de la frase del juego.
    - Datos: Blowfish ECB sobre bloques de dos u32 LE; la cola de menos de 8 bytes va
      sin cifrar.

Referencia: GARbro ArcFormats/CatSystem/ArcINT.cs (MIT).
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple, Union

from cs2_common import atomic_write, check_cancel, report, safe_name
from cs2_crypto import M32, Blowfish, mt_first

MAGIC = b'KIF\0'
ENTRY_SIZE = 0x48
NAME_SIZE = 0x40
KEY_NAME = b'__key__.dat'
CHUNK = 16 << 20            # múltiplo de 8: los bloques cifrados no se parten

ALPHA = b'zyxwvutsrqponmlkjihgfedcbaZYXWVUTSRQPONMLKJIHGFEDCBA'
_ALPHA_IDX = {c: i for i, c in enumerate(ALPHA)}


class KeyRequired(ValueError):
    """El archivo está cifrado y no se indicó la clave del juego."""


# =============================================================================
# Nombres
# =============================================================================

def _name_shift(key: int, index: int) -> int:
    r = mt_first((key + index) & M32)
    return ((r >> 24) + (r >> 16) + (r >> 8) + r) & 0xFF


def decipher_name(raw: bytes, key: int, index: int) -> bytes:
    k = _name_shift(key, index)
    out = bytearray()
    for c in raw:
        if c == 0:
            break
        j = _ALPHA_IDX.get(c)
        if j is not None:
            j = (j - k % 52) % 52
            c = ALPHA[51 - j]
        out.append(c)
        k += 1
    return bytes(out)


def encipher_name(name: bytes, key: int, index: int) -> bytes:
    k = _name_shift(key, index)
    out = bytearray()
    for c in name:
        j = _ALPHA_IDX.get(c)
        if j is not None:
            c = ALPHA[(51 - j + k % 52) % 52]
        out.append(c)
        k += 1
    return bytes(out)


def encode_name(name: str) -> bytes:
    raw = name.replace('/', '\\').encode('cp932')
    if not raw or len(raw) >= NAME_SIZE:
        raise ValueError(f"Invalid name (empty or over {NAME_SIZE - 1} bytes): {name}")
    return raw


# =============================================================================
# Lectura
# =============================================================================

@dataclass
class IntEntry:
    name: str
    offset: int
    size: int
    index: int          # posición en la tabla (la entrada 0 es la clave si está cifrado)

    @property
    def ext(self) -> str:
        n = self.name.rsplit('\\', 1)[-1].rsplit('/', 1)[-1]
        return n.rsplit('.', 1)[-1].lower() if '.' in n else ''


@dataclass
class IntCrypto:
    """Cifrado de un archivo: semilla, clave principal y registro de __key__.dat."""
    seed: int
    key: int
    key_record: bytes

    def __post_init__(self):
        self.bf = Blowfish(struct.pack('<I', mt_first(self.seed)))

    def same_as(self, other: Optional['IntCrypto']) -> bool:
        return other is not None and other.seed == self.seed and other.key == self.key


def is_encrypted(path) -> bool:
    with open(path, 'rb') as f:
        head = f.read(8 + len(KEY_NAME) + 1)
    return head[:4] == MAGIC and head[8:8 + len(KEY_NAME) + 1] == KEY_NAME + b'\0'


class IntArchive:
    """Archivo .int abierto para lectura. Úsese con 'with'. key: clave principal del juego."""

    def __init__(self, path, key: Optional[int] = None):
        self.path = Path(path)
        self.f = open(self.path, 'rb')
        try:
            self._read_table(key)
        except Exception:
            self.f.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.f.close()

    def _read_table(self, key):
        head = self.f.read(8)
        if len(head) < 8 or head[:4] != MAGIC:
            raise ValueError(f"{self.path.name} is not a CatSystem2 .int archive (KIF)")
        count = struct.unpack_from('<I', head, 4)[0]
        file_size = self.path.stat().st_size
        if count * ENTRY_SIZE + 8 > file_size:
            raise ValueError(f"{self.path.name}: damaged file table")
        table = self.f.read(ENTRY_SIZE * count)
        self.crypto: Optional[IntCrypto] = None
        self.entries: List[IntEntry] = []
        if count and table[:len(KEY_NAME) + 1] == KEY_NAME + b'\0':
            if key is None:
                raise KeyRequired(f"{self.path.name} is encrypted: the game key is needed")
            seed = struct.unpack_from('<I', table, 0x44)[0]
            self.crypto = IntCrypto(seed, key, table[:ENTRY_SIZE])
            bf = self.crypto.bf
            for i in range(1, count):
                rec = table[ENTRY_SIZE * i:ENTRY_SIZE * (i + 1)]
                off, size = struct.unpack_from('<II', rec, NAME_SIZE)
                off, size = bf.decipher_pair((off + i) & M32, size)
                name = decipher_name(rec[:NAME_SIZE], key, i).decode('cp932', 'replace')
                self.entries.append(IntEntry(name, off, size, i))
        else:
            for i in range(count):
                rec = table[ENTRY_SIZE * i:ENTRY_SIZE * (i + 1)]
                name = rec[:NAME_SIZE].split(b'\0')[0].decode('cp932', 'replace')
                off, size = struct.unpack_from('<II', rec, NAME_SIZE)
                self.entries.append(IntEntry(name, off, size, i))
        for e in self.entries:
            if e.offset + e.size > file_size:
                raise ValueError(f"{self.path.name}: entry {e.name} is outside the file "
                                 "(wrong key or damaged file)")

    @property
    def encrypted(self) -> bool:
        return self.crypto is not None

    def read_raw(self, e: IntEntry) -> bytes:
        """Datos tal como están guardados (cifrados si el archivo lo está)."""
        self.f.seek(e.offset)
        return self.f.read(e.size)

    def iter_raw(self, e: IntEntry, chunk: int = CHUNK) -> Iterator[bytes]:
        self.f.seek(e.offset)
        left = e.size
        while left > 0:
            data = self.f.read(min(chunk, left))
            if not data:
                raise ValueError(f"{self.path.name}: {e.name} is truncated")
            left -= len(data)
            yield data

    def iter_data(self, e: IntEntry, chunk: int = CHUNK) -> Iterator[bytes]:
        """Datos descifrados por partes (para archivos grandes, como los vídeos)."""
        for data in self.iter_raw(e, chunk):
            yield self.crypto.bf.decipher(data) if self.crypto else data

    def read(self, e: IntEntry) -> bytes:
        data = self.read_raw(e)
        return self.crypto.bf.decipher(data) if self.crypto else data

    def save(self, e: IntEntry, path):
        with atomic_write(path) as f:
            for data in self.iter_data(e):
                f.write(data)


# =============================================================================
# Escritura
# =============================================================================

# Contenido de una entrada: bytes, una ruta, o ('entry', IntArchive, IntEntry) para
# copiarla de otro archivo (sin descifrar si el cifrado es el mismo)
Source = Union[bytes, bytearray, str, Path, Tuple[str, IntArchive, IntEntry]]


def _source_size(src: Source) -> int:
    if isinstance(src, tuple):
        return src[2].size
    if isinstance(src, (bytes, bytearray)):
        return len(src)
    return Path(src).stat().st_size


def _source_chunks(src: Source, crypto: Optional[IntCrypto]) -> Iterator[bytes]:
    """Datos de la entrada ya cifrados como en el archivo de salida."""
    if isinstance(src, tuple):
        _tag, arc, e = src
        if (crypto is None and arc.crypto is None) or (crypto and crypto.same_as(arc.crypto)):
            yield from arc.iter_raw(e)
            return
        chunks = arc.iter_data(e)
    elif isinstance(src, (bytes, bytearray)):
        chunks = (bytes(src[i:i + CHUNK]) for i in range(0, len(src), CHUNK))
    else:
        def read_file(p):
            with open(p, 'rb') as f:
                while True:
                    data = f.read(CHUNK)
                    if not data:
                        return
                    yield data
        chunks = read_file(src)
    for data in chunks:
        yield crypto.bf.encipher(data) if crypto else data


def write_int(path, items: List[Tuple[str, Source]], crypto: Optional[IntCrypto] = None,
              progress=None, cancel=None) -> int:
    """
    Escribe un .int con las entradas (nombre, contenido) en ese orden. Con crypto, cifrado
    con esa semilla y clave (las de un archivo original); si no, sin cifrar.
    Devuelve el número de entradas.
    """
    if not items:
        raise ValueError("There are no files to pack")
    seen = set()
    for n, _ in items:
        if n.lower() in seen:
            raise ValueError(f"Duplicate name: {n}")
        seen.add(n.lower())
    names = [encode_name(n) for n, _ in items]
    sizes = [_source_size(src) for _, src in items]

    first = 1 if crypto else 0
    gap = ENTRY_SIZE if crypto else 0       # los originales cifrados repiten el registro anterior
    count = len(items) + first
    records = [crypto.key_record] if crypto else []
    offset = 8 + ENTRY_SIZE * count
    for i, (raw, size) in enumerate(zip(names, sizes), first):
        offset += gap
        if crypto:
            if offset + size > M32:
                raise ValueError("The archive would be over 4 GB")
            name_field = encipher_name(raw, crypto.key, i)
            o, s = crypto.bf.encipher_pair(offset, size)
            o = (o - i) & M32
        else:
            name_field, o, s = raw, offset, size
        records.append(name_field.ljust(NAME_SIZE, b'\0') + struct.pack('<II', o, s))
        offset += size
    if offset > M32 + 1:
        raise ValueError("The archive would be over 4 GB")

    total = len(items)
    with atomic_write(path) as f:
        f.write(MAGIC + struct.pack('<I', count))
        f.write(b''.join(records))
        for i, ((name, src), size) in enumerate(zip(items, sizes)):
            check_cancel(cancel)
            report(progress, i, total, name)
            if gap:
                f.write(records[i])         # registro anterior (records[0] es la clave)
            written = 0
            for data in _source_chunks(src, crypto):
                check_cancel(cancel)
                f.write(data)
                written += len(data)
            if written != size:
                raise ValueError(f"{name}: the file changed while packing")
    report(progress, total, total)
    return total


def collect_files(folder) -> List[Tuple[str, Path]]:
    """Archivos de una carpeta (con subcarpetas) como (nombre en el .int, ruta), ordenados."""
    folder = Path(folder)
    files = []
    for p in sorted(folder.rglob('*')):
        if p.is_file() and not p.name.startswith('_') and not p.name.endswith('.tmp'):
            files.append(('\\'.join(p.relative_to(folder).parts), p))
    return files


def pack(folder, int_path, like=None, key: Optional[int] = None, progress=None, cancel=None) -> int:
    """
    Empaqueta los archivos de una carpeta en un .int. Con like (un .int original), las
    entradas que ya existían conservan su orden y el archivo se cifra igual que el original
    (hace falta la clave si está cifrado); las nuevas van al final. Sin like, el .int
    queda sin cifrar. Se ignoran los archivos que empiezan por '_' (registros de la herramienta).
    """
    folder, int_path = Path(folder).resolve(), Path(int_path).resolve()
    if folder in int_path.parents:
        raise ValueError("The output .int cannot be inside the input folder")
    files = collect_files(folder)
    if not files:
        raise ValueError("There are no files to pack")
    crypto = None
    if like:
        with IntArchive(like, key) as orig:
            crypto = orig.crypto
            rank = {e.name.lower(): i for i, e in enumerate(orig.entries)}
        files.sort(key=lambda it: (rank.get(it[0].lower(), len(rank)), it[0].lower()))
    return write_int(int_path, files, crypto, progress, cancel)


def replace_entries(original, out_path, replacements: dict, key: Optional[int] = None,
                    add_new: bool = True, progress=None, cancel=None) -> Tuple[int, int, int]:
    """
    Copia original en out_path cambiando las entradas de replacements ({nombre: contenido}),
    con el mismo cifrado. Las entradas sin cambios se copian sin descifrar. Los nombres que
    no existen se añaden al final si add_new. Devuelve (entradas, reemplazadas, añadidas).
    """
    if Path(original).resolve() == Path(out_path).resolve():
        raise ValueError("The output .int must be different from the original")
    repl = {k.lower(): (k, v) for k, v in replacements.items()}
    with IntArchive(original, key) as arc:
        items = []
        replaced = 0
        for e in arc.entries:
            hit = repl.pop(e.name.lower(), None)
            if hit is not None:
                items.append((e.name, hit[1]))
                replaced += 1
            else:
                items.append((e.name, ('entry', arc, e)))
        added = 0
        if add_new:
            for name, src in repl.values():
                items.append((name, src))
                added += 1
        elif repl:
            raise ValueError("Not in the original .int: " + ", ".join(n for n, _ in list(repl.values())[:5]))
        write_int(out_path, items, arc.crypto, progress, cancel)
    return len(items), replaced, added


def extract(int_path, out_dir, names: Optional[Iterable[str]] = None, key: Optional[int] = None,
            progress=None, cancel=None) -> int:
    """Guarda las entradas tal cual (descifradas). names: solo esas entradas."""
    out_dir = Path(out_dir)
    with IntArchive(int_path, key) as arc:
        entries = arc.entries
        if names is not None:
            wanted = set(names)
            entries = [e for e in entries if e.name in wanted]
        total = len(entries)
        for i, e in enumerate(entries):
            check_cancel(cancel)
            report(progress, i, total, e.name)
            arc.save(e, out_dir / safe_name(e.name))
    report(progress, total, total)
    return total
