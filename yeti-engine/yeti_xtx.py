"""
yeti_xtx - Contenedores "pak" del motor Yeti y texturas XTX (Xbox 360, tiled) -> PNG

pak: tabla de entradas de 16 bytes (u32 offset, u32 tamaño, u32 0, u32 0), little-endian.
     El primer offset es el tamaño de la tabla. Puede contener otros paks.
XTX: "xtx\\0", u8 formato (+3 relleno), u32 BE ancho/alto alineados, ancho/alto reales;
     datos desde 0x20, en orden tiled de Xbox 360.
"""

import struct
from typing import Iterator, Tuple

import numpy as np
from PIL import Image

XTX_MAGIC = b'xtx\0'
XTX_HEADER = 0x20
FMT_ARGB8888 = 0    # 4 bytes por píxel (CG y sprites de bg.cpk)
FMT_ARGB4444 = 1    # 2 bytes por píxel (ventanas de texto, font48.xtx)
FMT_DXT5 = 2        # 1 byte por píxel (bloques 4x4 de 16 bytes, casi todo sys.cpk)


def is_xtx(b: bytes) -> bool:
    return b[:4] == XTX_MAGIC


# =============================================================================
# Contenedor pak
# =============================================================================

def is_pak(b: bytes) -> bool:
    if len(b) < 16:
        return False
    first = struct.unpack_from('<I', b, 0)[0]
    if first == 0 or first % 16 or first > len(b):
        return False
    for i in range(first // 16):
        o, s = struct.unpack_from('<II', b, i * 16)
        if (o, s) != (0, 0) and (o < first or o + s > len(b)):
            return False
    return True


def walk_pak(b: bytes, prefix: str = '') -> Iterator[Tuple[str, bytes]]:
    """Recorre un pak (con los anidados) y devuelve (nombre, datos) de cada hoja."""
    n = struct.unpack_from('<I', b, 0)[0] // 16
    for i in range(n):
        o, s = struct.unpack_from('<II', b, i * 16)
        if s == 0:
            continue
        name = f"{prefix}{i:03d}"
        sub = b[o:o + s]
        if not is_xtx(sub) and is_pak(sub):
            yield from walk_pak(sub, name + '_')
        else:
            yield name, sub


# =============================================================================
# Untile de Xbox 360 (XGAddress2DTiledX/Y, vectorizado)
# =============================================================================

def _x360_tiled_xy(n: int, width: int, pitch: int):
    """Coordenadas (x, y) lineales de cada elemento en orden tiled."""
    off = np.arange(n, dtype=np.int64)
    aw = (width + 31) & ~31
    lb = (pitch >> 2) + ((pitch >> 1) >> (pitch >> 2))
    ob = off << lb
    ot = ((ob & ~4095) >> 3) + ((ob & 1792) >> 2) + (ob & 63)
    om = ot >> (7 + lb)
    # X
    mx = (om % (aw >> 5)) << 2
    tile = (((ot >> (5 + lb)) & 2) + (ob >> 6)) & 3
    x = ((mx + tile) << 3) + (((((ot >> 1) & ~15) + (ot & 15)) & ((pitch << 3) - 1)) >> lb)
    # Y
    my = (om // (aw >> 5)) << 2
    tile = ((ot >> (6 + lb)) & 1) + ((ob & 2048) >> 10)
    y = ((my + tile) << 3) + ((((ot & (((pitch << 6) - 1) & ~31)) + ((ot & 15) << 1)) >> (3 + lb)) & ~1) \
        + ((ot & 16) >> 4)
    return x, y


def _untile(units: np.ndarray, w: int, h: int, pitch: int) -> np.ndarray:
    """units: array (n, pitch) en orden tiled -> mismo array en orden lineal."""
    x, y = _x360_tiled_xy(w * h, w, pitch)
    ok = (x < w) & (y < h)
    out = np.zeros_like(units)
    out[y[ok] * w + x[ok]] = units[ok]
    return out


# =============================================================================
# XTX
# =============================================================================

def xtx_info(b: bytes) -> Tuple[int, int, int]:
    """(formato, ancho, alto) de una textura XTX."""
    if not is_xtx(b):
        raise ValueError("No es una textura XTX")
    _aw, _ah, w, h = struct.unpack_from('>IIII', b, 8)
    return b[4], w, h


def xtx_to_image(b: bytes) -> Image.Image:
    if not is_xtx(b):
        raise ValueError("No es una textura XTX")
    fmt = b[4]
    aw, ah, w, h = struct.unpack_from('>IIII', b, 8)
    body = b[XTX_HEADER:]
    if fmt == FMT_DXT5:
        bw, bh = aw // 4, ah // 4
        data = np.frombuffer(body[:bw * bh * 16], dtype=np.uint8).copy()
        data = data.reshape(-1, 2)[:, ::-1].reshape(-1)          # intercambio de 16 bits
        blocks = _untile(data.reshape(-1, 16), bw, bh, 16)
        img = Image.frombytes('RGBA', (aw, ah), blocks.tobytes(), 'bcn', 3)
    elif fmt == FMT_ARGB8888:
        v = np.frombuffer(body[:aw * ah * 4], dtype='>u4').astype(np.uint32)
        v = _untile(v.reshape(-1, 1), aw, ah, 4).reshape(ah, aw)
        rgba = np.dstack([(v >> 16) & 255, (v >> 8) & 255, v & 255, (v >> 24) & 255])
        img = Image.fromarray(rgba.astype(np.uint8), 'RGBA')
    elif fmt == FMT_ARGB4444:
        v = np.frombuffer(body[:aw * ah * 2], dtype='>u2').astype(np.uint16)
        v = _untile(v.reshape(-1, 1), aw, ah, 2).reshape(ah, aw).astype(np.uint32)
        rgba = np.dstack([((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17,
                          ((v >> 12) & 15) * 17])
        img = Image.fromarray(rgba.astype(np.uint8), 'RGBA')
    else:
        raise ValueError(f"Formato XTX no soportado: {fmt}")
    return img.crop((0, 0, w, h))
