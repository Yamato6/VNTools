"""
advhd_wipf - Imágenes WIPF del motor AdvHD (tipos WIP, MSK y MOS de los .arc)

Estructura (little-endian):
    "WIPF", u16 n_fotogramas, u16 bpp (8 o 24)
    n_fotogramas x (u32 ancho, alto, x, y, desconocido, tamaño_comprimido)
    por fotograma, en orden:
        si bpp == 8: paleta de 1024 bytes (256 x BGRA)
        datos comprimidos con LZSS

Al descomprimir: 8 bits = un índice de paleta por píxel; 24 bits = planos B, G y R
completos uno detrás de otro (no intercalados).
"""

import struct
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from PIL import Image

WIPF_MAGIC = b'WIPF'
FRAME_HEADER = 24
PALETTE_SIZE = 1024
GRAY_RAMP = bytes(v for i in range(256) for v in (i, i, i))


@dataclass
class Frame:
    width: int
    height: int
    x: int
    y: int
    unknown: int
    bpp: int
    palette: Optional[bytes]    # 1024 bytes BGRA (solo 8 bits)
    data: bytes


def is_wipf(data: bytes) -> bool:
    return data[:4] == WIPF_MAGIC


def lzss(src: bytes, out_len: int) -> bytes:
    """
    LZSS con ventana circular de 0x1000 bytes a ceros y posición inicial 1.
    Flags de 8 bits desde el bit bajo: 1 = literal; 0 = b0, b1 con
    offset (b0 << 4) | (b1 >> 4) en la ventana y longitud (b1 & 0x0F) + 2.
    """
    win = bytearray(0x1000)
    wpos = 1
    out = bytearray(out_len)
    op = 0
    i = 0
    n_src = len(src)
    while op < out_len and i < n_src:
        flags = src[i]
        i += 1
        for _ in range(8):
            if op >= out_len or i >= n_src:
                break
            if flags & 1:
                c = src[i]
                i += 1
                out[op] = c
                op += 1
                win[wpos] = c
                wpos = (wpos + 1) & 0xFFF
            else:
                if i + 1 >= n_src:
                    break
                b0, b1 = src[i], src[i + 1]
                i += 2
                off = (b0 << 4) | (b1 >> 4)
                for _ in range(min((b1 & 0x0F) + 2, out_len - op)):
                    c = win[off]
                    off = (off + 1) & 0xFFF
                    out[op] = c
                    op += 1
                    win[wpos] = c
                    wpos = (wpos + 1) & 0xFFF
            flags >>= 1
    if op < out_len:
        raise ValueError(f"Datos comprimidos incompletos ({op} de {out_len} bytes)")
    return bytes(out)


LZSS_WINDOW = 0x1000
LZSS_MIN = 2
LZSS_MAX = 17
LZSS_CHAIN = 16             # candidatos que se prueban por posición


def lzss_compress(data: bytes) -> bytes:
    """
    Inverso de lzss(). Busca coincidencias en los últimos 4095 bytes: el byte k de la
    salida está en la posición (k + 1) & 0xFFF de la ventana. Las coincidencias pueden
    solaparse con lo que se está escribiendo (se copia byte a byte).
    """
    n = len(data)
    out = bytearray()
    heads = {}              # 2 bytes -> posiciones recientes (la última al final)
    i = 0
    while i < n:
        flag_pos = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if i >= n:
                break
            best_len = 0
            best_pos = 0
            if i + 1 < n:
                key = data[i:i + 2]
                limit = min(LZSS_MAX, n - i)
                # Repetición del byte anterior: caso muy frecuente en las máscaras
                if i > 0 and data[i - 1] == data[i]:
                    k = 0
                    c = data[i]
                    while k < limit and data[i + k] == c:
                        k += 1
                    best_len, best_pos = k, i - 1
                for j in reversed(heads.get(key, ())):
                    if i - j >= LZSS_WINDOW:
                        break
                    if best_len == limit:
                        break
                    k = 2
                    while k < limit and data[j + k] == data[i + k]:
                        k += 1
                    if k > best_len:
                        best_len, best_pos = k, j
            if best_len >= LZSS_MIN:
                off = (best_pos + 1) & 0xFFF
                out.append(off >> 4)
                out.append(((off & 0x0F) << 4) | (best_len - 2))
                step = best_len
            else:
                flags |= 1 << bit
                out.append(data[i])
                step = 1
            for p in range(i, min(i + step, n - 1)):
                chain = heads.setdefault(data[p:p + 2], [])
                chain.append(p)
                if len(chain) > LZSS_CHAIN:
                    del chain[0]
            i += step
        out[flag_pos] = flags
    return bytes(out)


def write_wipf(frames: List[Frame]) -> bytes:
    """Escribe una imagen WIPF. Todos los fotogramas deben tener el mismo bpp."""
    bpp = frames[0].bpp
    head = bytearray(WIPF_MAGIC + struct.pack('<HH', len(frames), bpp))
    body = bytearray()
    for fr in frames:
        if fr.bpp != bpp or len(fr.data) != fr.width * fr.height * (bpp // 8):
            raise ValueError("Fotograma con datos de tamaño incorrecto")
        packed = lzss_compress(fr.data)
        head += struct.pack('<6I', fr.width, fr.height, fr.x, fr.y, fr.unknown, len(packed))
        if bpp == 8:
            body += fr.palette
        body += packed
    return bytes(head + body)


def image_to_planes(img: Image.Image) -> bytes:
    """RGB -> datos de 24 bits planar (plano B, luego G, luego R)."""
    r, g, b = img.convert('RGB').split()
    return b.tobytes() + g.tobytes() + r.tobytes()


def gray_to_indices(img: Image.Image, palette: bytes) -> bytes:
    """
    Escala de grises (alfa o máscara) -> índices de la paleta original. Con la rampa
    0..255 el valor ya es el índice; si no, se usa la entrada de paleta más cercana.
    """
    gray = img.convert('L')
    rgb = _palette_rgb(palette)
    if rgb == GRAY_RAMP:
        return gray.tobytes()
    levels = np.frombuffer(rgb[0::3], dtype=np.uint8).astype(np.int16)
    lut = np.abs(np.arange(256, dtype=np.int16)[:, None] - levels[None, :]).argmin(axis=1)
    return gray.point(lut.astype(np.uint8).tolist()).tobytes()


def read_wipf(data: bytes) -> List[Frame]:
    if not is_wipf(data):
        raise ValueError("No es una imagen WIPF")
    n, bpp = struct.unpack_from('<HH', data, 4)
    if bpp not in (8, 24):
        raise ValueError(f"Profundidad no soportada: {bpp} bits")
    heads = [struct.unpack_from('<6I', data, 8 + FRAME_HEADER * k) for k in range(n)]
    p = 8 + FRAME_HEADER * n
    frames = []
    for w, h, x, y, unknown, size in heads:
        palette = None
        if bpp == 8:
            palette = data[p:p + PALETTE_SIZE]
            p += PALETTE_SIZE
        pixels = lzss(data[p:p + size], w * h * (bpp // 8))
        p += size
        frames.append(Frame(w, h, x, y, unknown, bpp, palette, pixels))
    return frames


def _palette_rgb(palette: bytes) -> bytes:
    """Paleta BGRA de 1024 bytes -> RGB de 768 bytes para Image.putpalette."""
    bgra = np.frombuffer(palette, dtype=np.uint8).reshape(256, 4)
    return bgra[:, [2, 1, 0]].tobytes()


def frame_to_image(fr: Frame) -> Image.Image:
    """8 bits con paleta de grises -> L (el índice es el valor); otra paleta -> RGB; 24 bits -> RGB."""
    size = (fr.width, fr.height)
    if fr.bpp == 24:
        n = fr.width * fr.height
        b, g, r = (Image.frombytes('L', size, fr.data[k * n:(k + 1) * n]) for k in range(3))
        return Image.merge('RGB', (r, g, b))
    img = Image.frombytes('L', size, fr.data)
    rgb = _palette_rgb(fr.palette)
    if rgb == GRAY_RAMP:
        return img
    img = img.convert('P')
    img.putpalette(rgb)
    return img.convert('RGB')


def mask_to_alpha(fr: Frame) -> Image.Image:
    """
    Máscara MSK -> canal alfa (L). Casi siempre la paleta es la rampa 0..255 y el índice
    ya es la opacidad; algunas (CHATWND, RAIN) usan otra escala de grises y hay que aplicarla.
    """
    img = Image.frombytes('L', (fr.width, fr.height), fr.data)
    rgb = _palette_rgb(fr.palette)
    if rgb == GRAY_RAMP:
        return img
    return img.point(list(rgb[0::3]))
