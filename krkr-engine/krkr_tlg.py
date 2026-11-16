"""
krkr_tlg - Imágenes TLG5 / TLG6 de KiriKiri

    "TLG5.0\\0raw\\x1a" · u8 colores (3/4) · u32 ancho · u32 alto · u32 alto de bloque
        · u32 tamaños de bloque[n] · por bloque y color: u8 marca · u32 tamaño · datos
        (marca 0: LZSS de ventana 4096; otra: sin comprimir). Los valores son diferencias con
        el píxel de la izquierda y con la fila de arriba, con B y R relativos a G.
    "TLG6.0\\0raw\\x1a" · u8 colores (1/3/4) · 3 bytes 0 · u32 ancho · u32 alto · u32 bits
        · filtros por bloque de 8×8 (LZSS) · por fila de bloques y color: valores Golomb
        · predicción MED/promedio y filtros de correlación de color.
    "TLG0.0\\0sds\\x1a" · u32 tamaño · TLG5/6 · etiquetas: envoltorio con metadatos.

Las imágenes editadas se escriben como TLG5 con bloques sin comprimir.

Los bucles usan _krkr.dll (o .so) si está junto al módulo o dentro del .exe; si no, la
versión en Python (mucho más lenta). Referencia: GARbro ArcFormats/KiriKiri/ImageTLG.cs (MIT).
"""

import ctypes
import struct
import sys
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image

LIB_NAME = '_krkr.dll' if sys.platform == 'win32' else '_krkr.so'
TLG5_MAGIC = b'TLG5.0\0raw\x1a'
TLG6_MAGIC = b'TLG6.0\0raw\x1a'
SDS_MAGIC = b'TLG0.0\0sds\x1a'

# =============================================================================
# Biblioteca nativa
# =============================================================================

_native = None


def load_native():
    """Carga _krkr.dll una vez; None si no está disponible."""
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
            so.tlg5_decode.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            so.tlg5_decode.restype = ctypes.c_int
            so.tlg6_decode.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_void_p]
            so.tlg6_decode.restype = ctypes.c_int
            _native = so
            break
    return _native or None


def native_available() -> bool:
    return load_native() is not None


# =============================================================================
# Cabecera
# =============================================================================

def is_tlg(head: bytes) -> bool:
    return head[:11] in (TLG5_MAGIC, TLG6_MAGIC, SDS_MAGIC)


def tlg_info(data: bytes) -> Optional[Tuple[int, int, int, int, int]]:
    """(versión, ancho, alto, colores, offset de los datos) o None."""
    off = 0xF if data[:11] == SDS_MAGIC else 0
    magic = data[off:off + 11]
    if magic == TLG5_MAGIC and len(data) >= off + 24:
        colors = data[off + 11]
        w, h = struct.unpack_from('<II', data, off + 12)
        return 5, w, h, colors, off + 20
    if magic == TLG6_MAGIC and len(data) >= off + 27:
        colors = data[off + 11]
        w, h = struct.unpack_from('<II', data, off + 15)
        return 6, w, h, colors, off + 23
    return None


# =============================================================================
# TLG5
# =============================================================================

def _lzss(src: bytes, out_size: int, text: bytearray, r: int) -> Tuple[bytearray, int]:
    out = bytearray(out_size)
    o = i = 0
    flags = 0
    n = len(src)
    while i < n:
        flags >>= 1
        if not flags & 256:
            flags = src[i] | 0xff00
            i += 1
        if flags & 1:
            mpos = src[i] | ((src[i + 1] & 0xf) << 8)
            mlen = ((src[i + 1] & 0xf0) >> 4) + 3
            i += 2
            if mlen == 18:
                mlen += src[i]
                i += 1
            for _ in range(mlen):
                c = text[mpos]
                out[o] = c
                text[r] = c
                o += 1
                mpos = (mpos + 1) & 4095
                r = (r + 1) & 4095
        else:
            c = src[i]
            i += 1
            out[o] = c
            text[r] = c
            o += 1
            r = (r + 1) & 4095
    return out, r


def _tlg5_python(data: bytes, pos: int, w: int, h: int, colors: int, blockheight: int) -> np.ndarray:
    blockcount = (h - 1) // blockheight + 1
    pos += blockcount * 4
    text = bytearray(4096)
    r = 0
    rows = []
    for yb in range(0, h, blockheight):
        bh = min(blockheight, h - yb)
        chans = []
        for _ in range(colors):
            mark = data[pos]
            size, = struct.unpack_from('<I', data, pos + 1)
            pos += 5
            raw = data[pos:pos + size]
            pos += size
            if mark == 0:
                buf, r = _lzss(raw, blockheight * w, text, r)
            else:
                buf = bytearray(raw)
            chans.append(np.frombuffer(bytes(buf[:bh * w]), np.uint8).reshape(bh, w))
        rows.append(np.stack(chans, axis=-1))
    d = np.concatenate(rows).astype(np.uint8)          # (h, w, colors) en orden B G R [A]
    d[..., 0] += d[..., 1]
    d[..., 2] += d[..., 1]
    d = np.cumsum(d, axis=1, dtype=np.uint8)            # diferencias con el píxel de la izquierda
    d = np.cumsum(d, axis=0, dtype=np.uint8)            # y con la fila de arriba
    return d


def _tlg5_encode_planes(bgra: np.ndarray) -> np.ndarray:
    """Inverso de _tlg5_python: píxeles (h, w, c) → valores guardados (h, w, c)."""
    d = bgra.astype(np.uint8)
    d = np.diff(d, axis=0, prepend=np.zeros((1,) + d.shape[1:], np.uint8)).astype(np.uint8)
    d = np.diff(d, axis=1, prepend=np.zeros((d.shape[0], 1, d.shape[2]), np.uint8)).astype(np.uint8)
    d[..., 0] -= d[..., 1]
    d[..., 2] -= d[..., 1]
    return d


def write_tlg5(img: Image.Image, blockheight: int = 4) -> bytes:
    """TLG5 sin comprimir: 4 colores si la imagen tiene alfa, si no 3."""
    alpha = img.mode in ('RGBA', 'LA', 'PA') or (img.mode == 'P' and 'transparency' in img.info)
    rgba = np.asarray(img.convert('RGBA' if alpha else 'RGB'))
    h, w = rgba.shape[:2]
    colors = 4 if alpha else 3
    bgra = rgba[..., [2, 1, 0, 3] if alpha else [2, 1, 0]]
    planes = _tlg5_encode_planes(bgra)
    blocks = []
    for yb in range(0, h, blockheight):
        part = planes[yb:yb + blockheight]
        blk = bytearray()
        for c in range(colors):
            raw = np.ascontiguousarray(part[..., c]).tobytes()
            blk += b'\x01' + struct.pack('<I', len(raw)) + raw
        blocks.append(bytes(blk))
    head = TLG5_MAGIC + bytes([colors]) + struct.pack('<III', w, h, blockheight)
    sizes = b''.join(struct.pack('<I', len(b)) for b in blocks)
    return head + sizes + b''.join(blocks)


# =============================================================================
# TLG6 (respaldo en Python: lento, para cuando no está _krkr.dll)
# =============================================================================

_GOLOMB = None
_LZ = None


def _tables():
    global _GOLOMB, _LZ
    if _GOLOMB is None:
        comp = [[3, 7, 15, 27, 63, 108, 223, 448, 130], [3, 5, 13, 24, 51, 95, 192, 384, 257],
                [2, 5, 12, 21, 39, 86, 155, 320, 384], [2, 3, 9, 18, 33, 61, 129, 258, 511]]
        _GOLOMB = [[0] * 4 for _ in range(1024)]
        for n in range(4):
            a = 0
            for i in range(9):
                for _ in range(comp[n][i]):
                    _GOLOMB[a][n] = i
                    a += 1
        _LZ = bytearray(4096)
        for i in range(4096):
            cnt, j = 0, 1
            while j != 4096 and not i & j:
                j <<= 1
                cnt += 1
            _LZ[i] = 0 if j == 4096 else cnt + 1
    return _GOLOMB, _LZ


def _golomb(pool: bytes, pixelbuf: list, count_px: int, first: bool, offset: int):
    golomb, _ = _tables()
    mask = ~(0xff << offset) & 0xFFFFFFFF
    big = int.from_bytes(pool, 'little')
    bitpos = 1          # posición absoluta en bits
    zero = not pool[0] & 1
    n, a = 3, 0
    pixel = 0
    while pixel < count_px:
        # cantidad: gamma (ceros hasta el primer 1, luego esos bits menos uno)
        t = big >> bitpos
        b = (t & -t).bit_length()
        if not b:
            raise ValueError("damaged TLG6 data")
        bitpos += b
        bc = b - 1
        count = (1 << bc) + ((big >> bitpos) & ((1 << bc) - 1))
        bitpos += bc
        if zero:
            for _ in range(count):
                pixelbuf[pixel] = 0 if first else pixelbuf[pixel] & mask
                pixel += 1
        else:
            for _ in range(count):
                k = golomb[a][n]
                window = (big >> bitpos) & (0xFFFFFFFF >> (bitpos & 7))
                if window:
                    b = (window & -window).bit_length()
                    bitpos += b
                    v = ((b - 1) << k) + ((big >> bitpos) & ((1 << k) - 1))
                else:
                    # escape: 5 bytes más adelante, el número de bits en un byte
                    idx = (bitpos >> 3) + 5
                    bc = pool[idx - 1]
                    bitpos = idx * 8
                    v = (bc << k) + ((big >> bitpos) & ((1 << k) - 1))
                bitpos += k
                sign = (v & 1) - 1
                v >>= 1
                a = min(a + v, 1023)
                val = ((v ^ sign) + sign + 1) & 0xff
                pixelbuf[pixel] = val if first else (pixelbuf[pixel] & mask) | (val << offset)
                pixel += 1
                n -= 1
                if n < 0:
                    a >>= 1
                    n = 3
        zero = not zero


def _chroma(f: int, v: int) -> int:
    B, G, R, A = v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, v & 0xff000000
    k = f >> 1
    table = {
        0: (B, G, R), 1: (B + G, G, R + G), 2: (B, G + B, R + B + G), 3: (B + R + G, G + R, R),
        4: (B + R, G + B + R, R + B + R + G), 5: (B + R, G + B + R, R), 6: (B + G, G, R),
        7: (B, G + B, R), 8: (B, G, R + G), 9: (B + G + R + B, G + R + B, R + B),
        10: (B + R, G + R, R), 11: (B, G + B, R + B), 12: (B, G + R + B, R + B),
        13: (B + G, G + R + B + G, R + B + G), 14: (B + G + R, G + R, R + B + G + R),
    }
    b, g, r = table.get(k, (B, G + (B << 1), R + (B << 1)))
    return A | ((r & 0xff) << 16) | ((g & 0xff) << 8) | (b & 0xff)


def _med(a, b, c):
    out = 0
    for s in (0, 8, 16, 24):
        x, y, z = (a >> s) & 0xff, (b >> s) & 0xff, (c >> s) & 0xff
        hi, lo = max(x, y), min(x, y)
        m = lo if z >= hi else hi if z <= lo else x + y - z
        out |= (m & 0xff) << s
    return out


def _avg(a, b):
    out = 0
    for s in (0, 8, 16, 24):
        out |= ((((a >> s) & 0xff) + ((b >> s) & 0xff) + 1) >> 1) << s
    return out


def _add(a, b):
    out = 0
    for s in (0, 8, 16, 24):
        out |= ((((a >> s) & 0xff) + ((b >> s) & 0xff)) & 0xff) << s
    return out


def _tlg6_python(data: bytes, pos: int, w: int, h: int, colors: int) -> np.ndarray:
    max_bits, = struct.unpack_from('<I', data, pos)
    pos += 4
    xb, mc = (w - 1) // 8 + 1, w // 8
    fraction = w - mc * 8
    text = bytearray()
    for i in range(32):
        for j in range(16):
            text += bytes([i] * 4 + [j] * 4)
    size, = struct.unpack_from('<I', data, pos)
    pos += 4
    filters, _ = _lzss(data[pos:pos + size], xb * ((h - 1) // 8 + 1) + 64, text, 0)
    pos += size
    zerocolor = 0xff000000 if colors == 3 else 0
    image = [0] * (w * h)
    prev = [zerocolor] * w
    for y in range(0, h, 8):
        ylim = min(y + 8, h)
        pixel_count = (ylim - y) * w
        pixelbuf = [0] * (w * 8 + 1)
        for c in range(colors):
            bit_length, = struct.unpack_from('<I', data, pos)
            pos += 4
            if bit_length >> 30:
                raise ValueError("unsupported TLG6 entropy coding")
            nbytes = (bit_length + 7) // 8
            pool = data[pos:pos + nbytes] + b'\0' * 8
            pos += nbytes
            first = c == 0 and colors != 1
            _golomb(pool, pixelbuf, pixel_count, first, 0 if first else c * 8)
        ft = (y // 8) * xb
        skip = (ylim - y) * 8
        for yy in range(y, ylim):
            cur = yy * w
            dirn = (yy & 1) ^ 1
            oddskip = (ylim - yy - 1) - (yy - y)
            for start_block, limit, width_blk in ((0, mc, min(w, 8)), (mc, xb, min(fraction, 8))):
                if start_block >= limit:
                    continue
                if start_block:
                    p, up = image[cur + start_block * 8 - 1], prev[start_block * 8 - 1]
                else:
                    p = up = zerocolor
                inp = width_blk * (yy - y) + skip * start_block
                step = 1 if dirn & 1 else -1
                x = start_block * 8
                for i in range(start_block, limit):
                    ww = min(w - i * 8, 8)
                    if step == -1:
                        inp += ww - 1
                    if i & 1:
                        inp += oddskip * ww
                    f = filters[ft + i]
                    for _ in range(ww):
                        u = prev[x]
                        v = _chroma(f, pixelbuf[inp])
                        p = _add(_avg(p, u) if f & 1 else _med(p, u, up), v)
                        up = u
                        image[cur + x] = p
                        x += 1
                        inp += step
                    inp += skip - ww if step == 1 else skip + 1
                    if i & 1:
                        inp -= oddskip * ww
            prev = image[cur:cur + w]
    arr = np.array(image, dtype=np.uint32).view(np.uint8).reshape(h, w, 4).copy()
    if colors == 1:
        arr[..., 1] = arr[..., 2] = arr[..., 0]
    if colors != 4:
        arr[..., 3] = 255
    return arr


# =============================================================================
# Decodificación
# =============================================================================

def decode_tlg(data: bytes) -> Image.Image:
    """Imagen PIL (RGBA si la TLG tiene alfa, si no RGB)."""
    info = tlg_info(data)
    if not info:
        raise ValueError("Not a TLG image")
    version, w, h, colors, pos = info
    if not (0 < w <= 32768 and 0 < h <= 32768):
        raise ValueError(f"Invalid TLG size {w}×{h}")
    so = load_native()
    if version == 5:
        blockheight, = struct.unpack_from('<I', data, pos)
        if so:
            out = ctypes.create_string_buffer(w * h * 4)
            src = data[pos + 4:]
            if so.tlg5_decode(src, len(src), w, h, colors, blockheight, out) != 0:
                raise ValueError("Damaged TLG5 data")
            arr = np.frombuffer(out, np.uint8).reshape(h, w, 4)
        else:
            arr = _tlg5_python(data, pos + 4, w, h, colors, blockheight)
            if colors == 3:
                arr = np.concatenate([arr, np.full((h, w, 1), 255, np.uint8)], axis=-1)
    else:
        if so:
            out = ctypes.create_string_buffer(w * h * 4)
            src = data[pos:]
            if so.tlg6_decode(src, len(src), w, h, colors, out) != 0:
                raise ValueError("Damaged TLG6 data")
            arr = np.frombuffer(out, np.uint8).reshape(h, w, 4)
        else:
            arr = _tlg6_python(data, pos, w, h, colors)
    rgba = arr[..., [2, 1, 0, 3]]
    if colors == 4:
        return Image.fromarray(np.ascontiguousarray(rgba), 'RGBA')
    return Image.fromarray(np.ascontiguousarray(rgba[..., :3]), 'RGB')
