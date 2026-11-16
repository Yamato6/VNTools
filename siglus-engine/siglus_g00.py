"""
siglus_g00 - Imágenes G00 de RealLive / SiglusEngine

    u8 tipo · u16 ancho · u16 alto · ...
    tipo 0: LZ (unidad de 3 bytes, mínimo 1) con píxeles BGR de 24 bits
    tipo 1: LZ (unidad de 1 byte, mínimo 2): u16 colores · paleta BGRA · índices de 8 bits
    tipo 2: u32 regiones · regiones × (x1, y1, x2, y2, ox, oy) i32 · LZ (unidad de 1 byte) con
            u32 regiones · (offset, tamaño) por región · por región: u16 1 · u16 partes ·
            0x70 bytes · partes: u16 x · u16 y · i16 · u16 ancho · u16 alto · 0x52 bytes ·
            píxeles BGRA. Cada región es una imagen (estado de un botón, una variante...).
    tipo 3: JPEG con XOR (clave fija de 256 bytes)

Al escribir: tipo 0 sin comprimir (LZ de solo literales); tipo 2 conservando las regiones y las
partes del original y cambiando solo sus píxeles; tipo 3 como JPEG nuevo; tipo 1 con la paleta
del original si la imagen solo usa esos colores.
Referencia: GARbro ArcFormats/RealLive/ImageG00.cs e ImageG00Jpeg.cs (MIT).
"""

import io
import struct
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from PIL import Image

from siglus_crypto import lz_pack, lz_unpack, xor_repeat

JPEG_KEY = bytes([
    0x45, 0x0C, 0x85, 0xC0, 0x75, 0x14, 0xE5, 0x5D, 0x8B, 0x55, 0xEC, 0xC0, 0x5B, 0x8B, 0xC3, 0x8B,
    0x81, 0xFF, 0x00, 0x00, 0x04, 0x00, 0x85, 0xFF, 0x6A, 0x00, 0x76, 0xB0, 0x43, 0x00, 0x76, 0x49,
    0x00, 0x8B, 0x7D, 0xE8, 0x8B, 0x75, 0xA1, 0xE0, 0x0C, 0x85, 0xC0, 0xC0, 0x75, 0x78, 0x30, 0x44,
    0x00, 0x85, 0xFF, 0x76, 0x37, 0x81, 0x1D, 0xD0, 0xFF, 0x00, 0x00, 0x75, 0x44, 0x8B, 0xB0, 0x43,
    0x45, 0xF8, 0x8D, 0x55, 0xFC, 0x52, 0x00, 0x76, 0x68, 0x00, 0x00, 0x04, 0x00, 0x6A, 0x43, 0x8B,
    0xB1, 0x43, 0x00, 0x6A, 0x05, 0xFF, 0x50, 0xFF, 0xD3, 0xA1, 0xE0, 0x04, 0x00, 0x56, 0x15, 0x2C,
    0x44, 0x00, 0x85, 0xC0, 0x74, 0x09, 0xC3, 0xA1, 0x5F, 0x5E, 0x33, 0x8B, 0xE5, 0x5D, 0xE0, 0x30,
    0x04, 0x00, 0x81, 0xC6, 0x00, 0x00, 0x81, 0xEF, 0x04, 0x00, 0x85, 0x30, 0x44, 0x00, 0x00, 0x00,
    0x5D, 0xC3, 0x8B, 0x55, 0xF8, 0x8D, 0x5E, 0x5B, 0x4D, 0xFC, 0x51, 0xC4, 0x04, 0x5F, 0x8B, 0xE5,
    0x43, 0x00, 0xEB, 0xD8, 0x8B, 0x45, 0xFF, 0x15, 0xE8, 0x83, 0xC0, 0x57, 0x56, 0x52, 0x2C, 0xB1,
    0x01, 0x00, 0x8B, 0x7D, 0xE8, 0x89, 0x00, 0xE8, 0x45, 0xF4, 0x8B, 0x20, 0x50, 0x6A, 0x47, 0x28,
    0x00, 0x50, 0x53, 0xFF, 0x15, 0x34, 0xE4, 0x6A, 0xB1, 0x43, 0x00, 0x0C, 0x8B, 0x45, 0x00, 0x6A,
    0x8B, 0x4D, 0xEC, 0x89, 0x08, 0x8A, 0x85, 0xC0, 0x45, 0xF0, 0x84, 0x8B, 0x45, 0x10, 0x74, 0x05,
    0xF5, 0x28, 0x01, 0x00, 0x83, 0xC4, 0x52, 0x6A, 0x08, 0x89, 0x45, 0x83, 0xC2, 0x20, 0x00, 0xE8,
    0xE8, 0xF4, 0xFB, 0xFF, 0xFF, 0x8B, 0x8B, 0x5D, 0x45, 0x0C, 0x83, 0xC0, 0x74, 0xC5, 0xF8, 0x53,
    0xC4, 0x08, 0x85, 0xC0, 0x75, 0x56, 0x30, 0x44, 0x8B, 0x1D, 0xD0, 0xF0, 0xA1, 0xE0, 0x00, 0x83,
])

REGION_HEAD = 0x74          # u16 tipo · u16 partes · 0x70 bytes
PART_HEAD = 0x5C            # u16 x · u16 y · i16 · u16 ancho · u16 alto · 0x52 bytes


@dataclass
class Region:
    index: int
    x1: int
    y1: int
    x2: int
    y2: int
    ox: int
    oy: int

    @property
    def size(self) -> Tuple[int, int]:
        return self.x2 - self.x1 + 1, self.y2 - self.y1 + 1


def is_g00(data: bytes) -> bool:
    if len(data) < 9 or data[0] > 3:
        return False
    w, h = struct.unpack_from('<HH', data, 1)
    return 0 < w <= 0x4000 and 0 < h <= 0x4000


def g00_info(data: bytes) -> Tuple[int, int, int, int]:
    """(tipo, ancho, alto, regiones)."""
    t = data[0]
    w, h = struct.unpack_from('<HH', data, 1)
    regions = struct.unpack_from('<i', data, 5)[0] if t == 2 else 1
    return t, w, h, regions


def _regions(data: bytes) -> Tuple[List[Region], int]:
    count, = struct.unpack_from('<i', data, 5)
    if not 0 < count <= 0x1000:
        raise ValueError("bad G00 region count")
    regs = []
    p = 9
    for i in range(count):
        regs.append(Region(i, *struct.unpack_from('<6i', data, p)))
        p += 24
    return regs, p


def _region_blocks(block: bytes, count: int) -> List[Tuple[int, int]]:
    if struct.unpack_from('<i', block, 0)[0] != count:
        raise ValueError("G00 type 2: the region count does not match")
    return [struct.unpack_from('<II', block, 4 + 8 * i) for i in range(count)]


def _parts(block: bytes, off: int):
    """[(posición de los píxeles, x, y, ancho, alto)] de las partes de una región."""
    rtype, count = struct.unpack_from('<HH', block, off)
    if rtype != 1:
        raise ValueError("G00 type 2: unknown region type")
    q = off + REGION_HEAD
    out = []
    for _ in range(count):
        x, y, _t, w, h = struct.unpack_from('<HHhHH', block, q)
        q += PART_HEAD
        out.append((q, x, y, w, h))
        q += w * h * 4
    return out


def decode(data: bytes) -> List[Tuple[Image.Image, str]]:
    """[(imagen, descripción)]: una por región en el tipo 2, una sola en los demás."""
    t, w, h, _ = g00_info(data)
    if t == 0:
        px = lz_unpack(data[5:], 1, 3)
        return [(Image.frombytes('RGB', (w, h), px[:w * h * 3], 'raw', 'BGR'), f"{w}×{h}, 24-bit")]
    if t == 1:
        d = lz_unpack(data[5:], 2, 1)
        n, = struct.unpack_from('<H', d, 0)
        pal = np.frombuffer(d, np.uint8, n * 4, 2).reshape(n, 4)[:, [2, 1, 0, 3]]
        idx = np.frombuffer(d, np.uint8, w * h, 2 + n * 4).reshape(h, w)
        return [(Image.fromarray(pal[np.minimum(idx, n - 1)], 'RGBA'), f"{w}×{h}, {n} colors")]
    if t == 3:
        img = Image.open(io.BytesIO(xor_repeat(data[5:], JPEG_KEY)))
        img.load()
        return [(img, f"{w}×{h}, JPEG")]
    regs, p = _regions(data)
    block = lz_unpack(data[p:], 2, 1)
    out = []
    for reg, (off, length) in zip(regs, _region_blocks(block, len(regs))):
        if length == 0:
            continue
        rw, rh = reg.size
        canvas = np.zeros((rh, rw, 4), np.uint8)
        for q, x, y, pw, ph in _parts(block, off):
            tile = np.frombuffer(block, np.uint8, pw * ph * 4, q).reshape(ph, pw, 4)
            hh, ww = min(ph, rh - y), min(pw, rw - x)
            if hh > 0 and ww > 0:
                canvas[y:y + hh, x:x + ww] = tile[:hh, :ww]
        img = Image.fromarray(np.ascontiguousarray(canvas[..., [2, 1, 0, 3]]), 'RGBA')
        out.append((img, f"region {reg.index}: {rw}×{rh} at ({reg.x1}, {reg.y1}) of {w}×{h}"))
    return out


def region_indexes(data: bytes) -> List[int]:
    """Índices de las regiones con imagen (las vacías no se extraen)."""
    if data[0] != 2:
        return [0]
    regs, p = _regions(data)
    block = lz_unpack(data[p:], 2, 1)
    return [r.index for r, (_, n) in zip(regs, _region_blocks(block, len(regs))) if n]


def compose(data: bytes) -> Image.Image:
    """Tipo 2: todas las regiones en su sitio sobre el lienzo completo."""
    t, w, h, _ = g00_info(data)
    if t != 2:
        return decode(data)[0][0]
    canvas = Image.new('RGBA', (w, h))
    regs, _ = _regions(data)
    by_index = {r.index: r for r in regs}
    for (img, _), idx in zip(decode(data), region_indexes(data)):
        r = by_index[idx]
        canvas.alpha_composite(img, (max(0, r.x1), max(0, r.y1)))
    return canvas


def layers(data: bytes) -> List[Image.Image]:
    """Cada región en su sitio sobre un lienzo transparente del tamaño completo."""
    t, w, h, _ = g00_info(data)
    if t != 2:
        return [decode(data)[0][0].convert('RGBA')]
    regs, _ = _regions(data)
    by_index = {r.index: r for r in regs}
    out = []
    for (img, _), idx in zip(decode(data), region_indexes(data)):
        r = by_index[idx]
        canvas = Image.new('RGBA', (w, h))
        canvas.alpha_composite(img, (max(0, r.x1), max(0, r.y1)))
        out.append(canvas)
    return out


def with_face(base: bytes, face_layer: Image.Image) -> Image.Image:
    """La imagen completa de base con una capa de expresión encima (CG o sprite sin cara)."""
    img = compose(base).convert('RGBA')
    if face_layer.size != img.size:
        raise ValueError("the face does not have the size of the image")
    return Image.alpha_composite(img, face_layer)


def regions_disjoint(data: bytes) -> bool:
    """
    True si las regiones (con imagen) no se solapan y caben en el lienzo: entonces el G00 se
    puede guardar como una sola imagen y recortar de nuevo (atlas de letras, tiras de iconos).
    Las regiones que se solapan son variantes (estados de un botón) y van por separado.
    """
    t, w, h, _ = g00_info(data)
    if t != 2:
        return True
    regs, _ = _regions(data)
    used = set(region_indexes(data))
    grid = np.zeros((h, w), np.uint8)
    for r in regs:
        if r.index not in used:
            continue
        if r.x1 < 0 or r.y1 < 0 or r.x2 >= w or r.y2 >= h:
            return False
        area = grid[r.y1:r.y2 + 1, r.x1:r.x2 + 1]
        if area.any():
            return False
        area[:] = 1
    return True


def split_canvas(data: bytes, canvas: Image.Image) -> dict:
    """{región: imagen} recortadas de un lienzo completo (inverso de compose)."""
    t, w, h, _ = g00_info(data)
    if canvas.size != (w, h):
        raise ValueError(f"it is {canvas.width}×{canvas.height} and the original is {w}×{h}")
    if t != 2:
        return {0: canvas}
    regs, _ = _regions(data)
    used = set(region_indexes(data))
    return {r.index: canvas.crop((r.x1, r.y1, r.x2 + 1, r.y2 + 1)) for r in regs if r.index in used}


# =============================================================================
# Escritura
# =============================================================================

def encode(original: bytes, images: dict) -> bytes:
    """
    Un G00 nuevo a partir del original con las imágenes editadas: {región: Image} (región 0 en
    los tipos sin regiones). Las imágenes deben tener el tamaño de su región.
    """
    t, w, h, _ = g00_info(original)
    if t != 2:
        img = images.get(0)
        if img is None:
            return original
        if img.size != (w, h):
            raise ValueError(f"it is {img.width}×{img.height} and the original is {w}×{h}")
        head = original[:5]
        if t == 3:
            buf = io.BytesIO()
            img.convert('RGB').save(buf, format='JPEG', quality=95)
            return head + xor_repeat(buf.getvalue(), JPEG_KEY)
        if t == 1:
            d = lz_unpack(original[5:], 2, 1)
            n, = struct.unpack_from('<H', d, 0)
            pal = np.frombuffer(d, np.uint8, n * 4, 2).reshape(n, 4)[:, [2, 1, 0, 3]]
            rgba = np.asarray(img.convert('RGBA')).reshape(-1, 4)
            lookup = {tuple(c): i for i, c in enumerate(pal.tolist())}
            try:
                idx = bytes(lookup[tuple(c)] for c in rgba.tolist())
            except KeyError:
                raise ValueError("this image uses a palette: use only its original colors") from None
            return head + lz_pack(d[:2 + n * 4] + idx, 1)
        if 'A' in img.getbands() and img.getextrema()[-1][0] < 255:
            raise ValueError("the original has no transparency (G00 type 0): remove the transparent pixels")
        bgr = np.asarray(img.convert('RGB'))[..., ::-1].tobytes()
        return bytes([0]) + original[1:5] + lz_pack(bgr, 3)

    regs, p = _regions(original)
    block = bytearray(lz_unpack(original[p:], 2, 1))
    blocks = _region_blocks(block, len(regs))
    for reg, (off, length) in zip(regs, blocks):
        img = images.get(reg.index)
        if img is None or length == 0:
            continue
        if img.size != reg.size:
            raise ValueError(f"region {reg.index} is {img.width}×{img.height} and the original is "
                             f"{reg.size[0]}×{reg.size[1]}")
        bgra = np.asarray(img.convert('RGBA'))[..., [2, 1, 0, 3]]
        rh, rw = bgra.shape[:2]
        for q, x, y, pw, ph in _parts(block, off):
            hh, ww = min(ph, rh - y), min(pw, rw - x)
            if hh <= 0 or ww <= 0:
                continue
            tile = np.zeros((ph, pw, 4), np.uint8)
            tile[:hh, :ww] = bgra[y:y + hh, x:x + ww]
            block[q:q + pw * ph * 4] = tile.tobytes()
    return original[:p] + lz_pack(bytes(block), 1)


def lost_pixels(original: bytes, images: dict) -> int:
    """Píxeles visibles de las imágenes {región: Image} fuera de las partes del original (se pierden)."""
    if original[0] != 2:
        return 0
    regs, p = _regions(original)
    block = lz_unpack(original[p:], 2, 1)
    blocks = _region_blocks(block, len(regs))
    lost = 0
    for region, img in images.items():
        off, length = blocks[region]
        if not length:
            continue
        alpha = np.asarray(img.convert('RGBA'))[..., 3]
        covered = np.zeros(alpha.shape, bool)
        for _q, x, y, pw, ph in _parts(block, off):
            covered[y:y + ph, x:x + pw] = True
        lost += int(((alpha > 0) & ~covered).sum())
    return lost
