"""
yeti_codecs - Descompresores: CRILAYLA (CRIWARE) y el LZSS propio del motor Yeti/Regista

CRILAYLA usa _crilayla.dll (o .so) si está junto al módulo o dentro del .exe;
si no, la versión en Python (~75 veces más lenta). Se compila con:
    gcc -O2 -shared -static-libgcc -o _crilayla.dll _crilayla.c
"""

import ctypes
import struct
import sys
from pathlib import Path

from yeti_common import check_cancel

CRILAYLA_MAGIC = b'CRILAYLA'
LIB_NAME = '_crilayla.dll' if sys.platform == 'win32' else '_crilayla.so'

_native = None


def is_crilayla(data: bytes) -> bool:
    return data[:8] == CRILAYLA_MAGIC


def _load_native():
    """Carga la biblioteca nativa una vez; None si no está disponible."""
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
            so.crilayla_decompress.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
            so.crilayla_decompress.restype = ctypes.c_int
            _native = so
            break
    return _native or None


def native_available() -> bool:
    return _load_native() is not None


def decompress_crilayla(data: bytes) -> bytes:
    """
    Cabecera: "CRILAYLA", u32 tamaño descomprimido, u32 offset de los 0x100 bytes
    sin comprimir que van al inicio de la salida. El flujo se lee de atrás hacia adelante.
    """
    usize, _hdr_off = struct.unpack_from('<II', data, 8)
    so = _load_native()
    if so is not None:
        buf = ctypes.create_string_buffer(usize + 0x100)
        if so.crilayla_decompress(data, len(data), buf) == 0:
            return buf.raw
    return decompress_crilayla_py(data)


def decompress_crilayla_py(data: bytes) -> bytes:
    usize, hdr_off = struct.unpack_from('<II', data, 8)
    out = bytearray(usize + 0x100)
    out[0:0x100] = data[hdr_off + 0x10:hdr_off + 0x10 + 0x100]

    pos = len(data) - 0x100 - 1
    pool = 0
    left = 0

    def bits(n):
        nonlocal pos, pool, left
        v = 0
        got = 0
        while got < n:
            if left == 0:
                pool = data[pos]
                left = 8
                pos -= 1
            take = min(left, n - got)
            v = (v << take) | ((pool >> (left - take)) & ((1 << take) - 1))
            left -= take
            got += take
        return v

    end = 0x100 + usize - 1
    written = 0
    while written < usize:
        if bits(1):
            ref = end - written + bits(13) + 3
            length = 3
            for w in (2, 3, 5, 8):
                lv = bits(w)
                length += lv
                if lv != (1 << w) - 1:
                    break
            else:
                while True:
                    lv = bits(8)
                    length += lv
                    if lv != 255:
                        break
            for _ in range(length):
                out[end - written] = out[ref]
                ref -= 1
                written += 1
        else:
            out[end - written] = bits(8)
            written += 1
    return bytes(out)


LZSS_WINDOW = 0x1000
LZSS_MIN = 3
LZSS_MAX = 18
LZSS_CHAIN = 8              # candidatos que se prueban por posición


def compress_yeti_lzss(data: bytes, cancel=None) -> bytes:
    """
    Inverso de decompress_yeti_lzss. Las referencias copian desde op - d (1 <= d < 4096);
    el decompresor calcula d como (op - 18 - disp) & 0xFFF, así que disp = (op - 18 - d) & 0xFFF.
    """
    n = len(data)
    out = bytearray(struct.pack('<I', n))
    heads = {}              # 3 bytes -> posiciones recientes (la última al final)
    i = 0
    while i < n:
        if cancel is not None and not i & 0xFFFF:
            check_cancel(cancel)
        flag_pos = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if i >= n:
                break
            best_len = 0
            best_pos = 0
            limit = min(LZSS_MAX, n - i)
            if limit >= LZSS_MIN:
                # Repetición del byte anterior (rellenos de ceros, etc.)
                if i > 0 and data[i - 1] == data[i]:
                    k = 0
                    c = data[i]
                    while k < limit and data[i + k] == c:
                        k += 1
                    best_len, best_pos = k, i - 1
                for j in reversed(heads.get(data[i:i + 3], ())):
                    if i - j >= LZSS_WINDOW or best_len == limit:
                        break
                    k = 3
                    while k < limit and data[j + k] == data[i + k]:
                        k += 1
                    if k > best_len:
                        best_len, best_pos = k, j
            if best_len >= LZSS_MIN:
                disp = (i - 18 - (i - best_pos)) & 0xFFF
                out += struct.pack('<H', ((disp & 0xF00) << 4) | ((best_len - 3) << 8) | (disp & 0xFF))
                step = best_len
            else:
                flags |= 1 << bit
                out.append(data[i])
                step = 1
            for p in range(i, min(i + step, n - 2)):
                chain = heads.setdefault(data[p:p + 3], [])
                chain.append(p)
                if len(chain) > LZSS_CHAIN:
                    del chain[0]
            i += step
        out[flag_pos] = flags
    return bytes(out)


def decompress_yeti_lzss(data: bytes) -> bytes:
    """
    LZSS del motor Yeti: u32 tamaño + bloques con un byte de flags (bit 1 = literal).
    Referencia: u16 con longitud 3 + ((info & 0x0F00) >> 8) y distancia
    ((info & 0xF000) >> 4) | (info & 0xFF), ventana de 4 KB.
    """
    (size,) = struct.unpack_from('<I', data, 0)
    out = bytearray(size)
    ip, op = 4, 0
    while op < size and ip < len(data):
        flags = data[ip]
        ip += 1
        for _ in range(8):
            if op >= size or ip >= len(data):
                break
            if flags & 1:
                out[op] = data[ip]
                ip += 1
                op += 1
            else:
                (info,) = struct.unpack_from('<H', data, ip)
                ip += 2
                n = 3 + ((info & 0x0F00) >> 8)
                disp = ((info & 0xF000) >> 4) | (info & 0xFF)
                ptr = op - ((op - 18 - disp) & 0xFFF)
                for _ in range(n):
                    out[op] = out[ptr] if ptr >= 0 else 0
                    op += 1
                    ptr += 1
                    if op >= size:
                        break
            flags >>= 1
    return bytes(out)
