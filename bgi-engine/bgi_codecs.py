"""
bgi_codecs - Cifrado y compresión de BGI / Ethornell

- KeyGen: generador de claves de DSC y CompressedBG.
- DSC FORMAT 1.00: Huffman canónico (512 códigos: 256 literales y 256 longitudes de copia)
  + copias LZ de distancia de 12 bits. Las profundidades del árbol van cifradas con KeyGen.
- dsc_pack escribe un DSC válido solo con literales de 8 bits: no comprime, pero el motor
  lo lee como cualquier otro (así se guardan los archivos editados).

Los bucles lentos de CompressedBG usan _bgi.dll (o .so) si está junto al módulo o dentro
del .exe; si no, la versión en Python. Referencia: GARbro ArcFormats/Ethornell (MIT).
"""

import ctypes
import struct
import sys
from pathlib import Path

DSC_MAGIC = b'DSC FORMAT 1.00\0'
LIB_NAME = '_bgi.dll' if sys.platform == 'win32' else '_bgi.so'

# =============================================================================
# Biblioteca nativa
# =============================================================================

_native = None


def load_native():
    """Carga _bgi.dll una vez; None si no está disponible."""
    global _native
    if _native is None:
        _native = False
        dirs = [Path(__file__).resolve().parent]
        if hasattr(sys, '_MEIPASS'):
            dirs.insert(0, Path(sys._MEIPASS))
        for d in dirs:
            lib = d / LIB_NAME
            if not lib.exists():
                continue
            try:
                so = ctypes.CDLL(str(lib))
            except OSError:
                continue
            so.cbg_v1.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_int64, ctypes.c_void_p,
                                  ctypes.c_int64, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            so.cbg_v1.restype = ctypes.c_int
            so.cbg_v2_row.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_int64, ctypes.c_int,
                                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            so.cbg_v2_row.restype = ctypes.c_int
            so.cbg_alpha.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_int64, ctypes.c_void_p,
                                     ctypes.c_int64, ctypes.c_int]
            so.cbg_alpha.restype = ctypes.c_int
            _native = so
            break
    return _native or None


def native_available() -> bool:
    return load_native() is not None


# =============================================================================
# Cifra y bits
# =============================================================================

class KeyGen:
    def __init__(self, key: int, magic: int = 0):
        self.key = key & 0xFFFFFFFF
        self.magic = magic

    def next(self) -> int:
        k = self.key
        v0 = (20021 * (k & 0xFFFF)) & 0xFFFFFFFF
        v1 = (self.magic | (k >> 16)) & 0xFFFFFFFF
        v1 = (v1 * 20021 + k * 346) & 0xFFFFFFFF
        v1 = (v1 + (v0 >> 16)) & 0xFFFF
        self.key = ((v1 << 16) + (v0 & 0xFFFF) + 1) & 0xFFFFFFFF
        return v1 & 0xFF


class MsbBits:
    """Lector de bits, del más significativo al menos. bit()/bits() devuelven -1 al acabar."""

    def __init__(self, data, pos: int = 0, end: int = None):
        self.d, self.p, self.cache, self.n = data, pos, 0, 0
        self.end = len(data) if end is None else end

    def bit(self) -> int:
        if self.n == 0:
            if self.p >= self.end:
                return -1
            self.cache = self.d[self.p]
            self.p += 1
            self.n = 8
        self.n -= 1
        return (self.cache >> self.n) & 1

    def bits(self, k: int) -> int:
        v = 0
        for _ in range(k):
            b = self.bit()
            if b < 0:
                return -1
            v = (v << 1) | b
        return v


# =============================================================================
# DSC FORMAT 1.00
# =============================================================================

def is_dsc(data: bytes) -> bool:
    return data[:16] == DSC_MAGIC


def _dsc_tree(codes):
    """Árbol canónico a partir de [(profundidad, código)] ordenados. Nodo 0 = raíz."""
    nodes = {}
    idx = [[0] * 1024, [0] * 1024]
    nxt, depth_nodes, depth, child, n = 1, 1, 0, 0, 0
    while n < len(codes):
        cur = child
        child ^= 1
        existed = 0
        while n < len(codes) and codes[n][0] == depth:
            nodes[idx[cur][existed]] = (True, codes[n][1], 0)
            n += 1
            existed += 1
        create = depth_nodes - existed
        for i in range(create):
            left, right = nxt, nxt + 1
            nxt += 2
            idx[child][i * 2], idx[child][i * 2 + 1] = left, right
            nodes[idx[cur][existed + i]] = (False, left, right)
        depth += 1
        depth_nodes = create * 2
    return nodes


def dsc_unpack(b: bytes, limit: int = None) -> bytes:
    """Datos de un bloque DSC; con limit, solo los primeros bytes (para ver el tipo)."""
    if not is_dsc(b):
        raise ValueError("Not a DSC FORMAT 1.00 block")
    magic = struct.unpack_from('<H', b, 0)[0] << 16
    key, out_size, dec_count = struct.unpack_from('<III', b, 0x10)
    kg = KeyGen(key, magic)
    codes = []
    for i in range(512):
        depth = (b[0x20 + i] - kg.next()) & 0xFF
        if depth:
            codes.append((depth, i))
    codes.sort()
    nodes = _dsc_tree(codes)
    bits = MsbBits(b, 0x220)
    if limit is not None:
        out_size = min(out_size, limit + 0x200)
    out = bytearray(out_size)
    dst = 0
    for _ in range(dec_count):
        ni = 0
        while True:
            bt = bits.bit()
            if bt < 0:
                return bytes(out[:dst])
            node = nodes[ni]
            ni = node[2] if bt else node[1]
            if nodes[ni][0]:
                break
        code = nodes[ni][1]
        if code >= 256:
            off = bits.bits(12)
            if off < 0:
                break
            cnt = min((code & 0xFF) + 2, out_size - dst)
            src = dst - (off + 2)
            if cnt <= off + 2:
                out[dst:dst + cnt] = out[src:src + cnt]
            else:
                for k in range(cnt):
                    out[dst + k] = out[src + k]
            dst += cnt
        else:
            out[dst] = code
            dst += 1
        if limit is not None and dst >= limit:
            break
    return bytes(out[:dst])


def dsc_pack(data: bytes, key: int = 0x2F1B7A5C) -> bytes:
    """
    DSC con los 256 literales a profundidad 8 y sin copias: el código de cada byte es el
    propio byte, así que el flujo de bits es data tal cual.
    """
    magic = struct.unpack_from('<H', DSC_MAGIC, 0)[0] << 16
    kg = KeyGen(key, magic)
    depths = bytes(((8 if i < 256 else 0) + kg.next()) & 0xFF for i in range(512))
    head = DSC_MAGIC + struct.pack('<IIII', key, len(data), len(data), 0)
    return head + depths + data


# ---------------------------------------------------------------------------
# BSE 1.0 / 1.1 (juegos posteriores, p. ej. Koichoco): cifra solo los 0x40 bytes que siguen
# a su cabecera de 0x10; el resto de la entrada va tal cual (como GARbro, ArcBGI.cs)

BSE_MAGIC = b'BSE 1.'


def is_bse(data: bytes) -> bool:
    return len(data) >= 0x50 and data[:6] == BSE_MAGIC and \
        struct.unpack_from('<H', data, 8)[0] in (0x100, 0x101)


def _s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v & 0x80000000 else v


def _bse_keys(key: int, version: int):
    """Generador de BSE (aritmética con signo de 32 bits, como el motor)."""
    k = _s32(key)
    if version == 0x101:
        mul_a, sh, mul_b, add, xor = 127, 7, 83, 53, 0xB97A7E5C
    else:
        mul_a, sh, mul_b, add, xor = 257, 8, 97, 23, 0xA6CD9B75
    while True:
        v = ((_s32(k * mul_a) >> sh) + k * mul_b + add) & 0xFFFFFFFF
        v ^= xor
        k = _s32(((v >> 16) | (v << 16)) & 0xFFFFFFFF)
        yield k


def bse_decrypt(data: bytes) -> bytes:
    """Datos de una entrada BSE: los 0x40 bytes descifrados seguidos del resto."""
    version, _checksum, key = struct.unpack_from('<HHI', data, 8)
    head = bytearray(data[0x10:0x50])
    keys = _bse_keys(key, version)
    done = [False] * 0x40
    for _ in range(0x40):
        dst = next(keys) & 0x3F
        while done[dst]:
            dst = (dst + 1) & 0x3F
        shift = next(keys) & 7
        right = (next(keys) & 1) == 0
        b = (head[dst] - next(keys)) & 0xFF
        if right:
            b = ((b >> shift) | (b << (8 - shift))) & 0xFF
        else:
            b = ((b << shift) | (b >> (8 - shift))) & 0xFF
        head[dst] = b
        done[dst] = True
    return bytes(head) + data[0x50:]


def unpack_head(data: bytes, n: int = 32) -> bytes:
    """Los primeros n bytes de los datos (sin las capas BSE y DSC)."""
    if is_bse(data):
        data = bse_decrypt(data)
    return dsc_unpack(data, n)[:n] if is_dsc(data) else data[:n]


def unpack(data: bytes) -> bytes:
    """Quita las capas BSE y DSC si las hay."""
    if is_bse(data):
        data = bse_decrypt(data)
    return dsc_unpack(data) if is_dsc(data) else data
