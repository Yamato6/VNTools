"""
yeti_xtx - Contenedores "pak" del motor Yeti y texturas XTX (Xbox 360, tiled) -> PNG

pak: tabla de entradas de 16 bytes (u32 offset, u32 tamaño, u32 0, u32 0), little-endian.
     El primer offset es el tamaño de la tabla. Puede contener otros paks.
XTX: "xtx\\0", u8 formato (+3 relleno), u32 BE ancho/alto alineados, ancho/alto reales;
     datos desde 0x20, en orden tiled de Xbox 360.
"""

import io
import struct
from typing import Dict, Iterator, List, Tuple

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
    img, w, h = _decode_full(b)
    return img.crop((0, 0, w, h))


def _decode_full(b: bytes):
    """Textura completa (tamaño alineado) y su tamaño real."""
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
    return img, w, h


def _tile(linear: np.ndarray, w: int, h: int, pitch: int) -> np.ndarray:
    """Inverso de _untile: array (n, pitch) en orden lineal -> orden tiled."""
    x, y = _x360_tiled_xy(w * h, w, pitch)
    ok = (x < w) & (y < h)
    out = np.zeros_like(linear)
    out[ok] = linear[y[ok] * w + x[ok]]
    return out


def _dxt5_blocks(img: Image.Image) -> bytes:
    """Bloques BC3 (DXT5) de una imagen RGBA, en orden lineal, con el codificador de Pillow."""
    buf = io.BytesIO()
    img.save(buf, format='DDS', pixel_format='DXT5')
    data = buf.getvalue()
    (header_size,) = struct.unpack_from('<I', data, 4)
    return data[4 + header_size:]


def image_to_xtx(img: Image.Image, original: bytes) -> bytes:
    """
    Codifica una imagen en el mismo formato y tamaño que la textura original (cabecera y
    zona de relleno incluidas). La imagen debe medir lo mismo que la original.
    """
    full, w, h = _decode_full(original)
    if img.size != (w, h):
        raise ValueError(f"mide {img.size[0]}x{img.size[1]} y la textura original {w}x{h}")
    fmt = original[4]
    aw, ah = full.size
    full = full.convert('RGBA')
    full.paste(img.convert('RGBA'), (0, 0))
    rgba = np.asarray(full, dtype=np.uint32)
    r, g, b, a = (rgba[..., k] for k in range(4))
    if fmt == FMT_ARGB8888:
        v = (a << 24) | (r << 16) | (g << 8) | b
        body = _tile(v.reshape(-1, 1), aw, ah, 4).reshape(-1).astype('>u4').tobytes()
    elif fmt == FMT_ARGB4444:
        q = [((c * 15 + 127) // 255) for c in (a, r, g, b)]
        v = (q[0] << 12) | (q[1] << 8) | (q[2] << 4) | q[3]
        body = _tile(v.reshape(-1, 1), aw, ah, 2).reshape(-1).astype('>u2').tobytes()
    elif fmt == FMT_DXT5:
        bw, bh = aw // 4, ah // 4
        blocks = np.frombuffer(_dxt5_blocks(full), dtype=np.uint8).reshape(-1, 16).copy()
        # Los bloques 4x4 sin cambios conservan los bytes originales (DXT5 pierde calidad)
        old_full = np.asarray(_decode_full(original)[0].convert('RGBA'), dtype=np.uint8)
        new_full = np.asarray(full, dtype=np.uint8)
        same = (old_full == new_full).reshape(bh, 4, bw, 4, 4).all(axis=(1, 3, 4)).reshape(-1)
        old_body = np.frombuffer(original[XTX_HEADER:XTX_HEADER + bw * bh * 16], dtype=np.uint8)
        old_blocks = _untile(old_body.reshape(-1, 2)[:, ::-1].reshape(-1, 16), bw, bh, 16)
        blocks[same] = old_blocks[same]
        tiled = _tile(blocks, bw, bh, 16)
        body = tiled.reshape(-1, 2)[:, ::-1].tobytes()          # intercambio de 16 bits
    else:
        raise ValueError(f"Formato XTX no soportado: {fmt}")
    rest = original[XTX_HEADER + len(body):]                    # datos tras la imagen, si los hay
    return original[:XTX_HEADER] + body + rest


# =============================================================================
# Reconstruir paks
# =============================================================================

def pak_entries(b: bytes) -> List[bytes]:
    """Datos de cada entrada de un pak (b'' en las vacías)."""
    n = struct.unpack_from('<I', b, 0)[0] // 16
    out = []
    for i in range(n):
        o, s = struct.unpack_from('<II', b, i * 16)
        out.append(b[o:o + s] if s else b'')
    return out


def build_pak(entries: List[bytes]) -> bytes:
    """Inverso de pak_entries: tabla y datos seguidos, cada entrada alineada a 16 bytes."""
    table = bytearray()
    body = bytearray()
    base = 16 * len(entries)
    for e in entries:
        if e:
            body += bytes(-len(body) % 16)
            table += struct.pack('<IIII', base + len(body), len(e), 0, 0)
            body += e
        else:
            table += bytes(16)
    return bytes(table + body)


def replace_leaves(b: bytes, replacements: Dict[Tuple[int, ...], bytes]) -> bytes:
    """
    Sustituye hojas de un pak (con anidados). Las claves son la ruta de índices de la hoja,
    como en los nombres de walk_pak: '00012_003_001' -> (3, 1).
    """
    if () in replacements:
        return replacements[()]
    entries = pak_entries(b)
    for i in sorted({path[0] for path in replacements}):
        sub = {path[1:]: data for path, data in replacements.items() if path[0] == i}
        entries[i] = replace_leaves(entries[i], sub)
    return build_pak(entries)
