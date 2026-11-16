"""
krkr_psb - PSB (Packaged Struct Binary, de M2/E-mote) a estructuras de Python / JSON

    ["mdf\\0" · u32 tamaño · zlib(...)]     envoltorio comprimido opcional
    "PSB\\0" · u16 versión · u16 cifrado · u32 tamaño de cabecera · u32 nombres · u32 cadenas
    · u32 datos de cadenas · u32 offsets de recursos · u32 tamaños de recursos · u32 datos de
    recursos · u32 raíz [· u32 checksum (v3+) · u32 × 3 recursos extra (v4+)]

Los valores llevan el tipo en el primer byte: 0x01 null, 0x02/0x03 false/true, 0x04-0x0C
enteros de 0-8 bytes, 0x0D-0x14 arrays de enteros, 0x15-0x18 cadenas (índice), 0x19-0x1C
recursos, 0x1D-0x1F float, 0x20 lista, 0x21 objeto, 0x22-0x25 recursos extra (v4).
Los nombres de las claves están en un trie (charset, datos, índices).
Referencia: FreeMote (https://github.com/UlyssesWu/FreeMote), Psb.cs y PsbValues.cs.

No admite PSB con la cabecera cifrada (E-mote cifrado).
"""

import base64
import json
import struct
import zlib
from typing import Any, List

T_NULL, T_FALSE, T_TRUE = 0x01, 0x02, 0x03
T_INT0, T_INT8 = 0x04, 0x0C
T_ARR1, T_ARR8 = 0x0D, 0x14
T_STR1, T_STR4 = 0x15, 0x18
T_RES1, T_RES4 = 0x19, 0x1C
T_FLOAT0, T_FLOAT, T_DOUBLE = 0x1D, 0x1E, 0x1F
T_LIST, T_OBJECT = 0x20, 0x21
T_XRES1, T_XRES4 = 0x22, 0x25


def is_psb(data: bytes) -> bool:
    return data[:4] == b'PSB\0' or is_mdf(data)


def is_mdf(data: bytes) -> bool:
    return data[:4] == b'mdf\0'


def unpack_mdf(data: bytes) -> bytes:
    """Quita el envoltorio 'mdf' (zlib) si lo hay."""
    if is_mdf(data):
        return zlib.decompress(data[8:])
    return data


class Resource:
    """Recurso binario de un PSB (imagen, sonido...): en JSON va como base64."""

    def __init__(self, data: bytes, extra: bool = False):
        self.data = data
        self.extra = extra

    def __repr__(self):
        return f"<resource {len(self.data)} bytes>"


class PsbReader:
    def __init__(self, data: bytes):
        data = unpack_mdf(data)
        if data[:4] != b'PSB\0':
            raise ValueError("Not a PSB file")
        self.b = data
        self.version, encrypted = struct.unpack_from('<HH', data, 4)
        if encrypted:
            raise ValueError("Encrypted PSB files are not supported")
        (_hlen, self.o_names, self.o_strings, self.o_strdata, self.o_choffs, self.o_chlens,
         self.o_chdata, self.o_root) = struct.unpack_from('<8I', data, 8)
        self.o_xoffs = self.o_xlens = self.o_xdata = 0
        if self.version > 3:
            self.o_xoffs, self.o_xlens, self.o_xdata = struct.unpack_from('<3I', data, 0x2C)
        self.string_offsets, _ = self._array(self.o_strings)
        self.names = self._load_names()
        self.chunk_offsets, _ = self._array(self.o_choffs)
        self.chunk_lengths, _ = self._array(self.o_chlens)
        if self.version > 3:
            self.xchunk_offsets, _ = self._array(self.o_xoffs)
            self.xchunk_lengths, _ = self._array(self.o_xlens)

    # -- primitivas -------------------------------------------------------------

    def _uint(self, pos: int, n: int) -> int:
        return int.from_bytes(self.b[pos:pos + n], 'little')

    def _int(self, pos: int, n: int) -> int:
        return int.from_bytes(self.b[pos:pos + n], 'little', signed=True)

    def _array(self, pos: int):
        """(lista de enteros sin signo, posición tras el array). En pos está el byte de tipo."""
        n = self.b[pos] - T_ARR1 + 1
        if not 1 <= n <= 8:
            raise ValueError(f"Bad PSB array at {pos:#x}")
        count = self._uint(pos + 1, n)
        pos += 1 + n
        width = self.b[pos] - T_INT8
        pos += 1
        if not 1 <= width <= 8:
            raise ValueError(f"Bad PSB array at {pos:#x}")
        b = self.b
        if width == 1:
            values = list(b[pos:pos + count])
        else:
            values = [int.from_bytes(b[pos + i * width:pos + (i + 1) * width], 'little') for i in range(count)]
        return values, pos + count * width

    def _load_names(self) -> List[str]:
        if self.version == 1:
            raise ValueError("PSB version 1 is not supported")
        charset, pos = self._array(self.o_names)
        data, pos = self._array(pos)
        indexes, _ = self._array(pos)
        names = []
        for idx in indexes:
            out = bytearray()
            chr_ = data[idx]
            while chr_ != 0:
                code = data[chr_]
                out.append((chr_ - charset[code]) & 0xFF)
                chr_ = code
            out.reverse()
            names.append(out.decode('utf-8', 'replace'))
        return names

    def _string(self, index: int) -> str:
        start = self.o_strdata + self.string_offsets[index]
        end = self.b.index(b'\0', start)
        return self.b[start:end].decode('utf-8', 'replace')

    def _resource(self, index: int, extra: bool) -> Resource:
        if extra:
            off = self.o_xdata + self.xchunk_offsets[index]
            return Resource(self.b[off:off + self.xchunk_lengths[index]], True)
        off = self.o_chdata + self.chunk_offsets[index]
        return Resource(self.b[off:off + self.chunk_lengths[index]])

    # -- valores ----------------------------------------------------------------

    def value(self, pos: int, depth: int = 0) -> Any:
        if depth > 200:
            raise ValueError("PSB nesting is too deep")
        t = self.b[pos]
        if t == T_NULL or t == 0:
            return None
        if t == T_FALSE:
            return False
        if t == T_TRUE:
            return True
        if T_INT0 <= t <= T_INT8:
            return self._int(pos + 1, t - T_INT0) if t > T_INT0 else 0
        if T_ARR1 <= t <= T_ARR8:
            return self._array(pos)[0]
        if T_STR1 <= t <= T_STR4:
            return self._string(self._uint(pos + 1, t - T_STR1 + 1))
        if T_RES1 <= t <= T_RES4:
            return self._resource(self._uint(pos + 1, t - T_RES1 + 1), False)
        if T_XRES1 <= t <= T_XRES4:
            return self._resource(self._uint(pos + 1, t - T_XRES1 + 1), True)
        if t == T_FLOAT0:
            return 0.0
        if t == T_FLOAT:
            return struct.unpack_from('<f', self.b, pos + 1)[0]
        if t == T_DOUBLE:
            return struct.unpack_from('<d', self.b, pos + 1)[0]
        if t == T_LIST:
            offsets, start = self._array(pos + 1)
            return [self.value(start + o, depth + 1) for o in offsets]
        if t == T_OBJECT:
            keys, p = self._array(pos + 1)
            offsets, start = self._array(p)
            return {self.names[k]: self.value(start + o, depth + 1) for k, o in zip(keys, offsets)}
        raise ValueError(f"Unknown PSB value type {t:#x} at {pos:#x}")

    def root(self) -> Any:
        return self.value(self.o_root)


def load(data: bytes) -> Any:
    """Contenido de un PSB (o mdf con un PSB dentro) como dict/list/str/int..."""
    return PsbReader(data).root()


def _json_default(obj):
    if isinstance(obj, Resource):
        return {'_resource': base64.b64encode(obj.data).decode('ascii'), '_extra': obj.extra}
    raise TypeError(type(obj).__name__)


def to_json(data: bytes, indent: int = 1) -> str:
    return json.dumps(load(data), ensure_ascii=False, indent=indent, default=_json_default)
