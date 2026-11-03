"""
nexas_formats - Formatos internos de los .pac del motor NeXAS

FIL  (máscaras de transición, System.pac)
    u32 ancho, u32 alto, ancho × alto bytes de gris

NMV  (animaciones de efectos, Effect.pac)
    u16 versión, "GraF", u32 ancho, u32 alto, u8 bytes por píxel
    fotogramas: el primero sin cabecera; los demás "GraK" + u32 tiempo en ms
    versión 4: u32 tamaño, u32 tamaño del JPEG de alfa, JPEG de alfa (gris), JPEG de color
    versiones 0 y 3: u32 tamaño, zlib con un segundo códec (no soportado)

SPM VER-2.00  (composición de sprites, Stand.pac y System.pac)
    char firma[13] = "SPM VER-2.00\\0"
    u32 patrones; por patrón:
        u32 partes, i32 ancho, alto, caja (izq, arriba, der, abajo), 4 × u32
        partes × 14 i32: imagen, destino (izq, arriba, der, abajo), ancho, alto,
                         origen (izq, arriba, der, abajo), 3 × u32 (banderas)
    u32 imágenes, nombres de PNG (Shift-JIS, terminados en nulo)
    datos sin descifrar hasta el final

DAT  (tablas de Config.pac)
    u32 columnas, columnas × u32 tipo (1 = cadena Shift-JIS terminada en nulo, 2 = i32),
    filas hasta el final del archivo
"""

import io
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from PIL import Image

# =============================================================================
# FIL
# =============================================================================


def fil_to_image(data: bytes) -> Image.Image:
    if len(data) < 8:
        raise ValueError("FIL file too small")
    w, h = struct.unpack_from('<II', data, 0)
    if w * h != len(data) - 8:
        raise ValueError(f"FIL size mismatch ({w}x{h}, {len(data) - 8} bytes)")
    return Image.frombytes('L', (w, h), data[8:])


def image_to_fil(img: Image.Image) -> bytes:
    gray = img.convert('L')
    return struct.pack('<II', gray.width, gray.height) + gray.tobytes()


# =============================================================================
# NMV
# =============================================================================

@dataclass
class NmvFrame:
    time: int           # ms desde el inicio
    data: bytes         # datos del fotograma (sin el tamaño)


@dataclass
class Nmv:
    version: int
    width: int
    height: int
    bpp: int
    frames: List[NmvFrame]


def read_nmv(data: bytes) -> Nmv:
    if len(data) < 15 or data[2:6] != b'GraF':
        raise ValueError("Not an NMV file")
    version = struct.unpack_from('<H', data, 0)[0]
    w, h = struct.unpack_from('<II', data, 6)
    bpp = data[14]
    pos = 15
    frames = []
    while pos < len(data):
        t = 0
        if frames:
            if data[pos:pos + 4] != b'GraK':
                raise ValueError(f"NMV: frame {len(frames)} not found at 0x{pos:X}")
            t = struct.unpack_from('<I', data, pos + 4)[0]
            pos += 8
        (size,) = struct.unpack_from('<I', data, pos)
        pos += 4
        if pos + size > len(data):
            raise ValueError("NMV: truncated frame")
        frames.append(NmvFrame(t, data[pos:pos + size]))
        pos += size
    return Nmv(version, w, h, bpp, frames)


def nmv_frame_image(nmv: Nmv, frame: NmvFrame) -> Image.Image:
    """Fotograma como RGBA. Solo la versión 4 (JPEG de color + JPEG de alfa)."""
    if nmv.version != 4:
        raise ValueError(f"NMV version {nmv.version} is not supported (only version 4)")
    (alpha_size,) = struct.unpack_from('<I', frame.data, 0)
    alpha = Image.open(io.BytesIO(frame.data[4:4 + alpha_size])).convert('L')
    color = Image.open(io.BytesIO(frame.data[4 + alpha_size:])).convert('RGB')
    if alpha.size != color.size:
        raise ValueError("NMV: the alpha and color frames have different sizes")
    color.putalpha(alpha)
    return color


# =============================================================================
# SPM
# =============================================================================

SPM_MAGIC = b'SPM VER-2.00\0'


@dataclass
class SpmPart:
    image: int
    dest: Tuple[int, int, int, int]      # izq, arriba, der, abajo (respecto al origen del sprite)
    size: Tuple[int, int]
    src: Tuple[int, int, int, int]       # rectángulo dentro de la imagen
    flags: Tuple[int, int, int]


@dataclass
class SpmPattern:
    size: Tuple[int, int]
    box: Tuple[int, int, int, int]
    extra: Tuple[int, int, int, int]
    parts: List[SpmPart] = field(default_factory=list)


@dataclass
class Spm:
    patterns: List[SpmPattern]
    images: List[str]
    tail: bytes                          # sin descifrar


def read_spm(data: bytes) -> Spm:
    if not data.startswith(SPM_MAGIC):
        raise ValueError("Not an SPM VER-2.00 file")
    pos = len(SPM_MAGIC)

    def ints(n):
        nonlocal pos
        if pos + 4 * n > len(data):
            raise ValueError("SPM: truncated file")
        v = struct.unpack_from(f'<{n}i', data, pos)
        pos += 4 * n
        return v

    patterns = []
    for _ in range(ints(1)[0]):
        n = ints(1)[0]
        h = ints(10)
        pat = SpmPattern((h[0], h[1]), tuple(h[2:6]), tuple(h[6:10]))
        for _ in range(n):
            p = ints(14)
            pat.parts.append(SpmPart(p[0], tuple(p[1:5]), (p[5], p[6]), tuple(p[7:11]), tuple(p[11:14])))
        patterns.append(pat)
    images = []
    for _ in range(ints(1)[0]):
        end = data.index(b'\0', pos)
        images.append(data[pos:end].decode('cp932', errors='replace'))
        pos = end + 1
    for pat in patterns:
        for part in pat.parts:
            if not 0 <= part.image < len(images):
                raise ValueError(f"SPM: image index {part.image} out of range")
    return Spm(patterns, images, data[pos:])


def spm_to_dict(spm: Spm) -> dict:
    return {
        'images': spm.images,
        'patterns': [{
            'size': list(p.size), 'box': list(p.box), 'extra': list(p.extra),
            'parts': [{'image': q.image, 'dest': list(q.dest), 'size': list(q.size),
                       'src': list(q.src), 'flags': list(q.flags)} for q in p.parts],
        } for p in spm.patterns],
    }


def compose_spm(spm: Spm, pattern: int, load_image) -> Image.Image:
    """
    Compone un patrón: cada parte se recorta de su imagen y se pega en su destino.
    load_image(nombre) devuelve la imagen (PIL) o None si no existe.
    """
    pat = spm.patterns[pattern]
    left, top = pat.box[0], pat.box[1]
    w, h = pat.size
    if w <= 0 or h <= 0:
        raise ValueError("SPM: empty pattern")
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    cache: Dict[int, Image.Image] = {}
    for part in pat.parts:
        img = cache.get(part.image)
        if img is None:
            img = load_image(spm.images[part.image])
            if img is None:
                raise ValueError(f"SPM: image not found: {spm.images[part.image]}")
            img = cache[part.image] = img.convert('RGBA')
        piece = img.crop(part.src)
        dw, dh = part.dest[2] - part.dest[0], part.dest[3] - part.dest[1]
        if piece.size != (dw, dh) and dw > 0 and dh > 0:
            piece = piece.resize((dw, dh))
        layer = Image.new('RGBA', canvas.size, (0, 0, 0, 0))
        layer.paste(piece, (part.dest[0] - left, part.dest[1] - top))
        canvas.alpha_composite(layer)
    return canvas


# =============================================================================
# DAT
# =============================================================================

DAT_STRING, DAT_INT = 1, 2


@dataclass
class DatTable:
    types: List[int]
    rows: List[list]


def read_dat(data: bytes, raw_strings: bool = False) -> DatTable:
    """raw_strings: las cadenas quedan como bytes (para reescribir la tabla sin cambios)."""
    if len(data) < 4:
        raise ValueError("DAT file too small")
    (ncol,) = struct.unpack_from('<I', data, 0)
    if not 0 < ncol < 100000 or 4 + 4 * ncol > len(data):
        raise ValueError("Not a NeXAS .dat table")
    types = list(struct.unpack_from(f'<{ncol}I', data, 4))
    if any(t not in (DAT_STRING, DAT_INT) for t in types):
        raise ValueError("Unknown column type in the .dat table")
    pos = 4 + 4 * ncol
    rows = []
    while pos < len(data):
        row = []
        for t in types:
            if t == DAT_STRING:
                end = data.find(b'\0', pos)
                if end < 0:
                    raise ValueError("DAT: unterminated string")
                s = data[pos:end]
                row.append(s if raw_strings else s.decode('cp932', errors='replace'))
                pos = end + 1
            else:
                if pos + 4 > len(data):
                    raise ValueError("DAT: truncated row")
                row.append(struct.unpack_from('<i', data, pos)[0])
                pos += 4
        rows.append(row)
    return DatTable(types, rows)


def write_dat(table: DatTable) -> bytes:
    """Las cadenas pueden ser str (se codifican en Shift-JIS) o bytes."""
    out = bytearray(struct.pack('<I', len(table.types)))
    out += struct.pack(f'<{len(table.types)}I', *table.types)
    for row in table.rows:
        for t, v in zip(table.types, row):
            if t == DAT_STRING:
                out += (v if isinstance(v, bytes) else v.encode('cp932')) + b'\0'
            else:
                out += struct.pack('<i', v)
    return bytes(out)


def dat_to_csv(table: DatTable) -> str:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow([f"col{i}:{'str' if t == DAT_STRING else 'int'}" for i, t in enumerate(table.types)])
    w.writerows(table.rows)
    return buf.getvalue()
