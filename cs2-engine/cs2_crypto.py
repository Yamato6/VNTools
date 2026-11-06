"""
cs2_crypto - Claves y cifrado de los archivos .int de CatSystem2

- Frase del juego: recurso V_CODE2/DATA del .exe, descifrado con Blowfish (la clave es
  el recurso KEY_CODE/KEY con cada byte ^ 0xCD), o la etiqueta <v_code> de
  config/startup.xml.
- Clave principal: CRC-32 (polinomio 0x04C11DB7, tabla MSB) sobre la frase en cp932.
- Mersenne Twister con la siembra antigua (Knuth 69069): solo se usa el primer número.
- Blowfish ECB con cada bloque leído como dos u32 little-endian; la cola de menos de
  8 bytes queda sin cifrar.

Blowfish usa _cs2.dll (o .so) si está junto al módulo o dentro del .exe; si no, la
versión en Python (mucho más lenta). Referencia: GARbro ArcFormats/CatSystem/ArcINT.cs (MIT).
"""

import ctypes
import re
import struct
import sys
from array import array
from pathlib import Path
from typing import Optional

M32 = 0xFFFFFFFF
LIB_NAME = '_cs2.dll' if sys.platform == 'win32' else '_cs2.so'

# =============================================================================
# Biblioteca nativa
# =============================================================================

_native = None


def load_native():
    """Carga _cs2.dll una vez; None si no está disponible."""
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
            for fn in (so.bf_decipher, so.bf_encipher):
                fn.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int64]
                fn.restype = None
            so.hg3_raw_size.argtypes = [ctypes.c_char_p, ctypes.c_int64]
            so.hg3_raw_size.restype = ctypes.c_int64
            so.hg3_unpack.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_char_p, ctypes.c_int64,
                                      ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
            so.hg3_unpack.restype = ctypes.c_int
            so.hg3_pack_bound.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
            so.hg3_pack_bound.restype = ctypes.c_int64
            so.hg3_pack.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int64)]
            so.hg3_pack.restype = ctypes.c_int
            _native = so
            break
    return _native or None


def native_available() -> bool:
    return load_native() is not None


# =============================================================================
# Blowfish
# =============================================================================

_PI_WORDS = None


def _pi_words():
    """
    Tablas iniciales de Blowfish: los primeros 18 + 1024 u32 de la parte fraccionaria
    de pi en hexadecimal. Se calculan con la fórmula de Machin (unos milisegundos).
    """
    global _PI_WORDS
    if _PI_WORDS is None:
        n = 18 + 4 * 256
        bits = 32 * n + 64
        one = 1 << bits

        def arctan_inv(x):
            total = term = one // x
            x2, k, sign = x * x, 1, -1
            while term:
                term //= x2
                k += 2
                total += sign * (term // k)
                sign = -sign
            return total

        pi = 16 * arctan_inv(5) - 4 * arctan_inv(239)
        frac = (pi - 3 * one) >> 64
        _PI_WORDS = [(frac >> (32 * (n - 1 - i))) & M32 for i in range(n)]
    return _PI_WORDS


class Blowfish:
    def __init__(self, key: bytes):
        words = _pi_words()
        P = list(words[:18])
        S = [list(words[18 + 256 * i:18 + 256 * (i + 1)]) for i in range(4)]
        j = 0
        for i in range(18):
            d = 0
            for _ in range(4):
                d = (d << 8) | key[j % len(key)]
                j += 1
            P[i] ^= d
        self.P, self.S = P, S
        l = r = 0
        for i in range(0, 18, 2):
            l, r = self.encipher_pair(l, r)
            P[i], P[i + 1] = l, r
        for s in S:
            for i in range(0, 256, 2):
                l, r = self.encipher_pair(l, r)
                s[i], s[i + 1] = l, r
        self._state = array('I', P + S[0] + S[1] + S[2] + S[3])

    def _f(self, x):
        S = self.S
        return ((((S[0][x >> 24] + S[1][(x >> 16) & 255]) & M32) ^ S[2][(x >> 8) & 255]) + S[3][x & 255]) & M32

    def encipher_pair(self, l, r):
        P, f = self.P, self._f
        for i in range(16):
            l ^= P[i]
            r ^= f(l)
            l, r = r, l
        l, r = r, l
        return l ^ P[17], r ^ P[16]

    def decipher_pair(self, l, r):
        P, f = self.P, self._f
        for i in range(17, 1, -1):
            l ^= P[i]
            r ^= f(l)
            l, r = r, l
        l, r = r, l
        return l ^ P[0], r ^ P[1]

    def _apply(self, data, native_name, pair_fn, force_python=False) -> bytes:
        so = None if force_python else load_native()
        if so is not None:
            buf = ctypes.create_string_buffer(bytes(data), len(data))
            addr = self._state.buffer_info()[0]
            getattr(so, native_name)(ctypes.c_void_p(addr), buf, len(data))
            return buf.raw
        out = bytearray(data)
        n = len(data) // 8 * 8
        for i in range(0, n, 8):
            l, r = struct.unpack_from('<II', out, i)
            struct.pack_into('<II', out, i, *pair_fn(l, r))
        return bytes(out)

    def decipher(self, data, force_python=False) -> bytes:
        """Bloques de 8 bytes leídos como dos u32 little-endian; la cola (<8) queda igual."""
        return self._apply(data, 'bf_decipher', self.decipher_pair, force_python)

    def encipher(self, data, force_python=False) -> bytes:
        return self._apply(data, 'bf_encipher', self.encipher_pair, force_python)


# =============================================================================
# Mersenne Twister con siembra 69069
# =============================================================================

def _lcg_jump(n):
    """(a, c) tales que n pasos de s = 69069 * s + 1 equivalen a s = a * s + c."""
    a, c = 1, 0
    for _ in range(n):
        a, c = (69069 * a) & M32, (69069 * c + 1) & M32
    return a, c


# mt[i] se forma con los estados 2i y 2i + 1 del generador lineal. El primer número
# del Mersenne Twister solo depende de mt[0], mt[1] y mt[397].
_JUMPS = [_lcg_jump(n) for n in (0, 1, 2, 3, 794, 795)]


def mt_first(seed: int) -> int:
    """Primer número de MT19937 sembrado con el método antiguo (Knuth 69069)."""
    s = [(a * seed + c) & M32 for a, c in _JUMPS]
    m0 = (s[0] & 0xFFFF0000) | (s[1] >> 16)
    m1 = (s[2] & 0xFFFF0000) | (s[3] >> 16)
    m397 = (s[4] & 0xFFFF0000) | (s[5] >> 16)
    y = (m0 & 0x80000000) | (m1 & 0x7FFFFFFF)
    y = m397 ^ (y >> 1) ^ (0x9908B0DF if y & 1 else 0)
    y ^= y >> 11
    y ^= (y << 7) & 0x9D2C5680
    y ^= (y << 15) & 0xEFC60000
    y ^= y >> 18
    return y & M32


# =============================================================================
# Clave principal y frase
# =============================================================================

def _crc_table():
    t = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
        t.append(c & M32)
    return t


_CRC = _crc_table()


def key_from_passphrase(phrase) -> int:
    if isinstance(phrase, str):
        phrase = phrase.encode('cp932')
    k = M32
    for c in phrase:
        k = (~_CRC[(k >> 24) ^ c] ^ (k << 8)) & M32
    return k


def pe_resource(data: bytes, rtype: str, rname: str) -> Optional[bytes]:
    """Lector mínimo de recursos PE con tipo y nombre en texto (p. ej. V_CODE2/DATA)."""
    try:
        pe = struct.unpack_from('<I', data, 0x3C)[0]
        if data[pe:pe + 4] != b'PE\0\0':
            return None
        nsec = struct.unpack_from('<H', data, pe + 6)[0]
        opt = struct.unpack_from('<H', data, pe + 20)[0]
        magic = struct.unpack_from('<H', data, pe + 24)[0]
        dd = pe + 24 + (96 if magic == 0x10B else 112)
        rva = struct.unpack_from('<I', data, dd + 2 * 8)[0]
        if rva == 0:
            return None
        secs = []
        for i in range(nsec):
            s = pe + 24 + opt + 40 * i
            vsz, va, rawsz, raw = struct.unpack_from('<IIII', data, s + 8)
            secs.append((va, max(vsz, rawsz), raw))

        def off(r):
            for va, sz, raw in secs:
                if va <= r < va + sz:
                    return r - va + raw
            raise ValueError("RVA outside the sections")

        base = off(rva)

        def entries(d):
            nn, ni = struct.unpack_from('<HH', data, base + d + 12)
            for k in range(nn + ni):
                name, ptr = struct.unpack_from('<II', data, base + d + 16 + 8 * k)
                if name & 0x80000000:
                    p = base + (name & 0x7FFFFFFF)
                    ln = struct.unpack_from('<H', data, p)[0]
                    name = data[p + 2:p + 2 + 2 * ln].decode('utf-16le')
                yield name, ptr

        for tname, tptr in entries(0):
            if tname != rtype:
                continue
            for nname, nptr in entries(tptr & 0x7FFFFFFF):
                if nname != rname:
                    continue
                for _lang, lptr in entries(nptr & 0x7FFFFFFF):
                    drva, dsz = struct.unpack_from('<II', data, base + lptr)
                    o = off(drva)
                    return data[o:o + dsz]
    except (struct.error, ValueError, UnicodeDecodeError):
        return None
    return None


def passphrase_from_exe(path) -> Optional[str]:
    data = Path(path).read_bytes()
    code = pe_resource(data, 'V_CODE2', 'DATA')
    if code is None or len(code) < 8:
        return None
    key = pe_resource(data, 'KEY_CODE', 'KEY')
    key = bytes(c ^ 0xCD for c in key) if key else b'windmill'
    dec = Blowfish(key).decipher(code)
    return dec.split(b'\0')[0].decode('cp932', 'replace')


def passphrase_from_startup(path) -> Optional[str]:
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    m = re.search(r'<v_code>\s*([^<\s]+)\s*</v_code>', text)
    return m.group(1) if m else None


def find_passphrase(game_dir):
    """
    Busca la frase en la carpeta del juego: primero en los .exe y después en
    config/startup.xml. Devuelve (frase, origen) o (None, '').
    """
    game_dir = Path(game_dir)
    if game_dir.is_file():
        game_dir = game_dir.parent
    try:
        exes = sorted(p for p in game_dir.iterdir() if p.is_file() and p.suffix.lower() == '.exe')
    except OSError:
        exes = []
    for exe in exes:
        try:
            phrase = passphrase_from_exe(exe)
        except OSError:
            continue
        if phrase:
            return phrase, exe.name
    for xml in (game_dir / 'config' / 'startup.xml', game_dir / 'startup.xml'):
        phrase = passphrase_from_startup(xml)
        if phrase:
            return phrase, str(xml.relative_to(game_dir))
    return None, ''
