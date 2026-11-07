"""
bgi_image - Imágenes de BGI / Ethornell y tipos de archivo

- CompressedBG (CBG) v1: Huffman + tramos de ceros + predicción por promedio (izquierda, arriba).
- CompressedBG v2: DCT 8×8 (IDCT AAN), YCbCr y canal alfa con LZ en 2D.
- Imagen BGI cruda: i16 ancho, alto, bpp (8/24/32), flag (0), 8 bytes a cero y BGR(A).
  Es lo que usan el parche en inglés (archivos sueltos) y sysgrp.arc (dentro de un DSC);
  las imágenes editadas se guardan así.

Port de GARbro ArcFormats/Ethornell/ImageCBG.cs e ImageBGI.cs (MIT). Los bucles lentos usan
_bgi.dll si está disponible (ver bgi_codecs).
"""

import io
import struct

import numpy as np
from PIL import Image

from bgi_codecs import KeyGen, MsbBits, load_native, unpack

CBG_MAGIC = b'CompressedBG___'
SCRIPT_MAGIC = b'BurikoCompiledScriptVer1.00\0'
IMAGE_KINDS = {'cbg', 'bgi', 'png', 'bmp'}

# =============================================================================
# Tipos
# =============================================================================


def bgi_raw_info(b: bytes):
    """(ancho, alto, bpp, flag) de una imagen BGI cruda, o None."""
    if len(b) < 16:
        return None
    w, h, bpp, flag = struct.unpack_from('<hhhh', b, 0)
    if w <= 0 or h <= 0 or bpp not in (8, 24, 32) or flag not in (0, 1) or b[8:16] != bytes(8):
        return None
    if len(b) < 16 + w * h * bpp // 8 and len(b) > 64:
        return None
    return w, h, bpp, flag


def detect(head: bytes) -> str:
    """Tipo por el contenido (head: al menos los 32 primeros bytes, ya sin BSE ni DSC)."""
    if head[:15] == CBG_MAGIC:
        return 'cbg'
    if head[:28] == SCRIPT_MAGIC:
        return 'script'
    if head[4:8] == b'bw  ':
        return 'bw'
    if head[:4] == b'OTTO':
        return 'otf'
    if head[:4] == b'\0\1\0\0':
        return 'ttf'
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if head[:2] == b'BM':
        return 'bmp'
    if head[:4] == b'OggS':
        return 'ogg'
    if head[:8] == b'BF_Movie':
        return 'bmv'
    if head[:4] == b'\0\0\1\xba':
        return 'mpg'
    if head[4:8] == b'ftyp':
        return 'mp4'
    if bgi_raw_info(head[:16]):
        return 'bgi'
    if len(head) >= 8 and struct.unpack_from('<I', head, 0)[0] == 0x10:
        return 'bp'             # bytecode de sistema (._bp)
    return 'data'


# Extensión con la que se guarda cada tipo al extraer en crudo
EXTENSIONS = {'cbg': '', 'bgi': '', 'script': '', 'bw': '', 'bp': '', 'data': '',
              'otf': '.otf', 'ttf': '.ttf', 'png': '.png', 'bmp': '.bmp', 'ogg': '.ogg',
              'bmv': '.bmv', 'mpg': '.mpg', 'mp4': '.mp4'}


# =============================================================================
# Imagen BGI cruda
# =============================================================================

def bgi_to_image(b: bytes) -> Image.Image:
    info = bgi_raw_info(b)
    if not info:
        raise ValueError("Not a BGI image")
    w, h, bpp, flag = info
    if flag:
        raise ValueError("Scrambled BGI images (flag 1) are not supported")
    ps = bpp // 8
    px = b[16:16 + w * h * ps]
    if len(px) < w * h * ps:
        raise ValueError("The BGI image is truncated")
    if ps == 4:
        return Image.frombytes('RGBA', (w, h), px, 'raw', 'BGRA')
    if ps == 3:
        return Image.frombytes('RGB', (w, h), px, 'raw', 'BGR')
    return Image.frombytes('L', (w, h), px)


def image_to_bgi(img: Image.Image, bpp: int) -> bytes:
    """Imagen BGI cruda con la profundidad del original (8, 24 o 32 bits)."""
    if bpp == 32:
        raw = img.convert('RGBA').tobytes('raw', 'BGRA')
    elif bpp == 24:
        raw = img.convert('RGB').tobytes('raw', 'BGR')
    elif bpp == 8:
        raw = img.convert('L').tobytes()
    else:
        raise ValueError(f"Unsupported bit depth: {bpp}")
    return struct.pack('<hhhh', img.width, img.height, bpp, 0) + bytes(8) + raw


# =============================================================================
# CompressedBG
# =============================================================================

def cbg_info(b: bytes):
    """(ancho, alto, bpp, versión) de un CBG."""
    w, h = struct.unpack_from('<HH', b, 0x10)
    bpp, = struct.unpack_from('<i', b, 0x14)
    ver, = struct.unpack_from('<H', b, 0x2E)
    return w, h, bpp, ver


def _varint(d, p):
    v = sh = 0
    while True:
        c = d[p]
        p += 1
        v |= (c & 0x7F) << sh
        sh += 7
        if not c & 0x80:
            return v, p


class Huff:
    def __init__(self, weights, v2=False):
        nodes = [[w != 0, False, w, -1, -1] for w in weights]   # válido, interno, peso, izq, der
        total = sum(weights)
        while True:
            weight = 0
            ch = [-1, -1]
            for i in range(2):
                mn = 0xFFFFFFFF
                n = 0
                if v2:
                    while n < len(nodes):
                        if nodes[n][0]:
                            mn = nodes[n][2]
                            ch[i] = n
                            n += 1
                            break
                        n += 1
                    n = max(n, i + 1)
                while n < len(nodes):
                    if nodes[n][0] and nodes[n][2] < mn:
                        mn = nodes[n][2]
                        ch[i] = n
                    n += 1
                if ch[i] == -1:
                    continue
                nodes[ch[i]][0] = False
                weight += nodes[ch[i]][2]
            nodes.append([True, True, weight, ch[0], ch[1]])
            if weight >= total:
                break
        self.nodes = nodes

    def decode(self, bits):
        n = len(self.nodes) - 1
        while True:
            b = bits.bit()
            if b < 0:
                raise ValueError("The CBG image is truncated")
            n = self.nodes[n][4] if b else self.nodes[n][3]
            if not self.nodes[n][1]:
                return n


FILL = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7,
        14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46,
        53, 60, 61, 54, 47, 55, 62, 63]
DCT_T = np.array([
    1.00000000, 1.38703990, 1.30656302, 1.17587554, 1.00000000, 0.78569496, 0.54119611, 0.27589938,
    1.38703990, 1.92387950, 1.81225491, 1.63098633, 1.38703990, 1.08979023, 0.75066054, 0.38268343,
    1.30656302, 1.81225491, 1.70710683, 1.53635550, 1.30656302, 1.02655995, 0.70710677, 0.36047992,
    1.17587554, 1.63098633, 1.53635550, 1.38268340, 1.17587554, 0.92387950, 0.63637930, 0.32442334,
    1.00000000, 1.38703990, 1.30656302, 1.17587554, 1.00000000, 0.78569496, 0.54119611, 0.27589938,
    0.78569496, 1.08979023, 1.02655995, 0.92387950, 0.78569496, 0.61731654, 0.42521504, 0.21677275,
    0.54119611, 0.75066054, 0.70710677, 0.63637930, 0.54119611, 0.42521504, 0.29289323, 0.14931567,
    0.27589938, 0.38268343, 0.36047992, 0.32442334, 0.27589938, 0.21677275, 0.14931567, 0.07612047],
    dtype=np.float32)


def _idct_cols(v):
    v1, v2, v3, v4, v5, v6, v7, v8 = v
    v10 = v1 + v5
    v11 = v1 - v5
    v12 = v3 + v7
    v13 = (v3 - v7) * np.float32(1.414213562) - v12
    a1 = v10 + v12
    a7 = v10 - v12
    a3 = v11 + v13
    a5 = v11 - v13
    v14 = v2 + v8
    v15 = v2 - v8
    v16 = v6 + v4
    v17 = v6 - v4
    b8 = v14 + v16
    b11 = (v14 - v16) * np.float32(1.414213562)
    v9 = (v17 + v15) * np.float32(1.847759065)
    b10 = np.float32(1.0823922) * v15 - v9
    b13 = np.float32(-2.61312593) * v17 + v9
    b6 = b13 - b8
    b4 = b11 - b6
    b2 = b10 + b4
    return [a1 + b8, a3 + b6, a5 + b4, a7 - b2, a7 + b2, a5 - b4, a3 - b6, a1 - b8]


def _idct_rows(t):
    v10 = t[0] + t[4]
    v11 = t[0] - t[4]
    v12 = t[2] + t[6]
    v13 = t[2] - t[6]
    v14 = t[1] + t[7]
    v15 = t[1] - t[7]
    v16 = t[5] + t[3]
    v17 = t[5] - t[3]
    v13 = np.float32(1.414213562) * v13 - v12
    a1 = v10 + v12
    a7 = v10 - v12
    a3 = v11 + v13
    a5 = v11 - v13
    b8 = v14 + v16
    b11 = (v14 - v16) * np.float32(1.414213562)
    v9 = (v17 + v15) * np.float32(1.847759065)
    b10 = v9 - v15 * np.float32(1.0823922)
    b13 = v9 - v17 * np.float32(2.61312593)
    b6 = b13 - b8
    b4 = b11 - b6
    b2 = b10 - b4
    return [a1 + b8, a3 + b6, a5 + b4, a7 + b2, a7 - b2, a5 - b4, a3 - b6, a1 - b8]


def _to_byte(f):
    a = 0x80 + (np.trunc(f).astype(np.int32) >> 3)
    return np.where(a <= 0, 0, np.where(a <= 0xFF, a, np.where(a < 0x180, 0xFF, 0))).astype(np.int16)


def _decode_dct(blocks, q):
    """blocks: (n, 64) coeficientes; q: (64,) cuantización. Devuelve (n, 64) valores 0..255."""
    c = (blocks.astype(np.float32) * q[None, :]).reshape(-1, 8, 8)
    tmp = np.stack(_idct_cols([c[:, k, :] for k in range(8)]), axis=1)
    out = np.stack(_idct_rows([tmp[:, :, j] for j in range(8)]), axis=2)
    return _to_byte(out.reshape(-1, 64))


def cbg_to_image(b: bytes) -> Image.Image:
    if b[:15] != CBG_MAGIC:
        raise ValueError("Not a CompressedBG image")
    w, h, bpp, ver = cbg_info(b)
    inter_len, key, enc_len = struct.unpack_from('<iIi', b, 0x20)
    csum, cxor = b[0x2C], b[0x2D]
    kg = KeyGen(key, 0)
    enc = bytearray(b[0x30:0x30 + enc_len])
    s = x = 0
    for i in range(len(enc)):
        enc[i] = (enc[i] - kg.next()) & 0xFF
        s = (s + enc[i]) & 0xFF
        x ^= enc[i]
    if s != csum or x != cxor:
        raise ValueError("Wrong CBG checksum")
    pos = 0x30 + enc_len
    if ver < 2:
        return _v1(b, pos, bytes(enc), w, h, bpp, inter_len)
    return _v2(b, pos, bytes(enc), w, h, bpp)


def _to_pil(img, ps):
    if ps == 4:
        return Image.fromarray(img[:, :, [2, 1, 0, 3]].copy(), 'RGBA')
    if ps == 3:
        return Image.fromarray(img[:, :, [2, 1, 0]].copy(), 'RGB')
    return Image.fromarray(img[:, :, 0].copy(), 'L')


def _v1(b, pos, enc, w, h, bpp, inter_len):
    weights, p = [], 0
    for _ in range(256):
        v, p = _varint(enc, p)
        weights.append(v)
    ps = bpp // 8
    native = load_native()
    if native:
        out = np.zeros(w * h * ps, np.uint8)
        wts = np.array(weights, np.uint32)
        rc = native.cbg_v1(b, len(b), pos, wts.ctypes.data, inter_len, out.ctypes.data, w, h, ps)
        if rc:
            raise ValueError(f"The CBG image could not be decoded ({rc})")
        return _to_pil(out.reshape(h, w, ps), ps)

    tree = Huff(weights)
    bits = MsbBits(b, pos)
    packed = bytes(tree.decode(bits) for _ in range(inter_len))
    out = np.zeros(w * h * ps, np.uint8)
    src = dst = 0
    zero = False
    while dst < out.size and src < len(packed):
        cnt, src = _varint(packed, src)
        if dst + cnt > out.size:
            break
        if not zero:
            out[dst:dst + cnt] = np.frombuffer(packed, np.uint8, cnt, src)
            src += cnt
        dst += cnt
        zero = not zero
    img = out.reshape(h, w, ps).astype(np.int32)
    for y in range(h):
        row = img[y]
        up = img[y - 1] if y else None
        for xx in range(w):
            if xx and y:
                avg = (row[xx - 1] + up[xx]) // 2
            elif xx:
                avg = row[xx - 1]
            elif y:
                avg = up[xx]
            else:
                continue
            row[xx] = (row[xx] + avg) & 0xFF
    return _to_pil(img.astype(np.uint8), ps)


def _v2(b, pos, enc, w0, h0, bpp):
    W = (w0 + 7) & -8
    H = (h0 + 7) & -8
    q = [np.frombuffer(enc, np.uint8, 64, 0).astype(np.float32) * DCT_T,
         np.frombuffer(enc, np.uint8, 64, 64).astype(np.float32) * DCT_T]
    base = pos
    wt1 = []
    for _ in range(0x10):
        v, pos = _varint(b, pos)
        wt1.append(v)
    wt2 = []
    for _ in range(0xB0):
        v, pos = _varint(b, pos)
        wt2.append(v)
    yb = H // 8
    offs_raw = struct.unpack_from(f'<{yb + 1}i', b, pos)
    pos += 4 * (yb + 1)
    input_base = pos - base
    data = b[pos:]
    offs = [o - input_base for o in offs_raw]
    pad_skip = ((W >> 3) + 7) >> 3
    out = np.zeros((H, W, 4), np.uint8)
    native = load_native()
    trees = None if native else (Huff(wt1, True), Huff(wt2, True))
    w1, w2 = np.array(wt1, np.uint32), np.array(wt2, np.uint32)
    for i in range(yb):
        start = offs[i] + pad_skip
        end = len(data) if i + 1 == yb else offs[i + 1]
        size, p = _varint(data, start)
        if native:
            color = np.zeros(size, np.int32)
            if native.cbg_v2_row(data, end, p, size, color.ctypes.data, w1.ctypes.data, w2.ctypes.data):
                raise ValueError("The CBG image is truncated")
        else:
            color = _row_coeffs(data, p, end, size, trees)
        need = W * 8 * (1 if bpp == 8 else 3)
        if color.size < need:       # filas que guardan menos coeficientes: el resto es cero
            color = np.concatenate([color, np.zeros(need - color.size, np.int32)])
        _block_row(color.astype(np.int16), q, W, bpp, out[i * 8:(i + 1) * 8])
    has_alpha = bpp == 32 and _alpha(data, offs[yb], out, W, native)
    out = out[:h0, :w0]
    return _to_pil(out, 4 if has_alpha else 3)


def _row_coeffs(data, p, end, size, trees):
    t1, t2 = trees
    bits = MsbBits(data, p, end)
    color = np.zeros(size, np.int32)
    acc = 0
    for i in range(0, size, 64):
        if bits.p >= end and bits.n == 0:
            break
        cnt = t1.decode(bits)
        if cnt:
            v = bits.bits(cnt)
            if (v >> (cnt - 1)) == 0:
                v = ((-1 << cnt) | v) + 1
            acc += v
        color[i] = acc
    bits.n = 0
    for i in range(0, size, 64):
        if bits.p >= end:
            break
        idx = 1
        while idx < 64 and bits.p < end:
            code = t2.decode(bits)
            if code == 0:
                break
            if code == 0xF:
                idx += 0x10
                continue
            idx += code & 0xF
            if idx >= 64:
                break
            code >>= 4
            v = bits.bits(code)
            if code and (v >> (code - 1)) == 0:
                v = ((-1 << code) | v) + 1
            if i + FILL[idx] < size:
                color[i + FILL[idx]] = ((v + 0x8000) & 0xFFFF) - 0x8000
            idx += 1
    return color


def _block_row(color, q, W, bpp, dst):
    nb = W // 8
    if bpp == 8:
        blk = color[:nb * 64].reshape(nb, 64)
        yv = _decode_dct(blk, q[0]).reshape(nb, 8, 8).transpose(1, 0, 2).reshape(8, W).astype(np.uint8)
        dst[:, :, 0] = dst[:, :, 1] = dst[:, :, 2] = yv
        return
    ch = []
    for c in range(3):
        blk = color[c * W * 8:(c + 1) * W * 8].reshape(nb, 64)
        ch.append(_decode_dct(blk, q[0 if c == 0 else 1]).astype(np.float32))
    cy, cb, cr = ch
    r = cy + np.float32(1.402) * cr - np.float32(178.956)
    g = cy - np.float32(0.34414) * cb - np.float32(0.71414) * cr + np.float32(135.95984)
    bl = cy + np.float32(1.772) * cb - np.float32(226.316)
    for k, arr in ((0, bl), (1, g), (2, r)):
        dst[:, :, k] = np.clip(arr, 0, 255).astype(np.uint8).reshape(nb, 8, 8).transpose(1, 0, 2).reshape(8, W)


def _alpha(data, off, out, W, native):
    if off + 4 > len(data) or struct.unpack_from('<i', data, off)[0] != 1:
        return False
    flat = out.reshape(-1)
    if native:
        native.cbg_alpha(data, len(data), off + 4, flat.ctypes.data, flat.size, W)
        return True
    p = off + 4
    dst = 3
    ctl = 2
    n = flat.size
    while dst < n:
        ctl >>= 1
        if ctl == 1:
            ctl = data[p] | 0x100
            p += 1
        if ctl & 1:
            v = data[p] | (data[p + 1] << 8)
            p += 2
            x = v & 0x3F
            if x > 0x1F:
                x |= -0x40
            y = (v >> 6) & 7
            if y:
                y |= -8
            cnt = ((v >> 9) & 0x7F) + 3
            src = dst + (x + y * W) * 4
            if src < 0 or src >= dst:
                return True
            for _ in range(cnt):
                if dst >= n:
                    break
                flat[dst] = flat[src]
                src += 4
                dst += 4
        else:
            flat[dst] = data[p]
            p += 1
            dst += 4
    return True


# =============================================================================
# Entrada general
# =============================================================================

def image_info(data: bytes):
    """(ancho, alto, bpp, descripción) de una imagen (sin DSC), o None."""
    kind = detect(data[:32])
    if kind == 'cbg':
        w, h, bpp, ver = cbg_info(data)
        return w, h, bpp, f"CompressedBG v{max(ver, 1)}"
    if kind == 'bgi':
        w, h, bpp, _ = bgi_raw_info(data)
        return w, h, bpp, "BGI image"
    return None


def decode(data: bytes) -> Image.Image:
    """Imagen PIL de una entrada (con o sin BSE/DSC): CBG, BGI cruda, PNG o BMP."""
    data = unpack(data)
    kind = detect(data[:32])
    if kind == 'cbg':
        return cbg_to_image(data)
    if kind == 'bgi':
        return bgi_to_image(data)
    if kind in ('png', 'bmp'):
        img = Image.open(io.BytesIO(data))
        img.load()
        return img
    raise ValueError("Not an image")
