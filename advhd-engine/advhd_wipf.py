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
