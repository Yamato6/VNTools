"""
krkr_image - Tipos de archivo, imágenes y sprites por capas de KiriKiri

Sprites de KAGEX (fgimage): cada pose y tamaño es un PSD exportado en capas.
    <pose>_<tamaño>.txt   tabla de capas: layer_type, name, left, top, width, height, type
                          (13 = normal, 16 = multiplicar), opacity, visible, layer_id...
                          La segunda fila da el tamaño del lienzo. Orden: de arriba a abajo.
    <pose>_<tamaño>_<layer_id>.tlg   cada capa
    <pose>_info.txt       'dress <ropa> diff <n> <capa>' y 'face <n> base <capa>': qué capas
                          forman cada ropa/variante y cada expresión.
"""

import io
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from krkr_psb import is_psb
from krkr_tlg import decode_tlg, is_tlg, tlg_info

IMAGE_KINDS = ('tlg', 'png', 'jpg', 'bmp')


def detect(head: bytes) -> str:
    """Tipo por el contenido (head: al menos los 16 primeros bytes)."""
    if is_tlg(head):
        return 'tlg'
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if head[:3] == b'\xff\xd8\xff':
        return 'jpg'
    if head[:2] == b'BM':
        return 'bmp'
    if head[:4] == b'OggS':
        return 'ogg'
    if head[:4] == b'RIFF' and head[8:12] == b'WAVE':
        return 'wav'
    if is_psb(head):
        return 'psb'
    if head[:16] == b'SQLite format 3\0':
        return 'sdb'
    if head[:4] == b'\x30\x26\xb2\x75':
        return 'video'          # ASF / WMV
    if head[:4] in (b'\0\0\1\xba', b'\0\0\1\xb3') or head[4:8] == b'ftyp':
        return 'video'
    if head[:4] == b'TVP ':
        return 'font'           # fuente prerrenderizada (.tft)
    if head[:4] in (b'OTTO', b'\0\1\0\0'):
        return 'font'
    if head[:3] == b'#2.':
        return 'loop'           # puntos de bucle (.sli)
    return 'data'


def kind_of(name: str, head: bytes) -> str:
    """Tipo para la lista: por contenido, y si no se sabe, texto según la extensión."""
    k = detect(head)
    if k == 'data' and name.lower().rsplit('.', 1)[-1] in ('ks', 'tjs', 'txt', 'csv', 'ini', 'func', 'asd'):
        return 'text'
    return k


def decode(data: bytes) -> Image.Image:
    """Imagen PIL de un TLG, PNG, JPEG o BMP."""
    if is_tlg(data[:16]):
        return decode_tlg(data)
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def image_info(data: bytes) -> Optional[Tuple[int, int, int, str]]:
    """(ancho, alto, bits, formato) sin decodificar, o None."""
    info = tlg_info(data[:64])
    if info:
        version, w, h, colors, _ = info
        return w, h, colors * 8, f"TLG{version}"
    if detect(data[:16]) in ('png', 'jpg', 'bmp'):
        try:
            img = Image.open(io.BytesIO(data))
            bits = {'RGBA': 32, 'RGB': 24, 'L': 8, 'P': 8, 'LA': 16}.get(img.mode, 24)
            return img.width, img.height, bits, img.format
        except OSError:
            return None
    return None


# =============================================================================
# Sprites por capas
# =============================================================================

_LAYER_TXT = re.compile(r'^(.*)_(\d+)\.txt$', re.I)

BLEND_NORMAL = 13
BLEND_MULTIPLY = 16


@dataclass
class Layer:
    name: str
    left: int
    top: int
    width: int
    height: int
    blend: int
    opacity: int
    visible: bool
    layer_id: int


@dataclass
class LayerSheet:
    width: int
    height: int
    layers: List[Layer]                    # de arriba a abajo, como en el archivo

    def by_name(self, name: str) -> Optional[Layer]:
        for layer in self.layers:
            if layer.name == name:
                return layer
        return None


def _decode_text(data: bytes) -> str:
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return data.decode('utf-16')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('cp932', 'replace')


def is_layer_sheet(name: str, head: bytes) -> bool:
    return bool(_LAYER_TXT.match(name)) and head.startswith(b'#layer_type')


def parse_layers(data: bytes) -> LayerSheet:
    rows = [line.split('\t') for line in _decode_text(data).splitlines()]
    width = height = 0
    layers = []
    for f in rows:
        if not f or f[0].startswith('#'):
            continue
        if f[0] == '' and len(f) > 5 and f[4].strip().isdigit():
            width, height = int(f[4]), int(f[5])      # fila del lienzo: solo ancho y alto
            continue
        if len(f) < 10 or not f[9].strip().lstrip('-').isdigit():
            continue
        try:
            layers.append(Layer(f[1], int(f[2]), int(f[3]), int(f[4]), int(f[5]),
                                int(f[6] or BLEND_NORMAL), int(f[7] or 255), f[8].strip() == '1', int(f[9])))
        except ValueError:
            continue
    if not width:
        width = max((l.left + l.width for l in layers), default=1)
        height = max((l.top + l.height for l in layers), default=1)
    return LayerSheet(width, height, layers)


@dataclass
class SpriteInfo:
    dresses: List[Tuple[str, str, List[str]]] = field(default_factory=list)    # (ropa, variante, capas)
    faces: List[Tuple[str, List[str]]] = field(default_factory=list)          # (expresión, capas)


def parse_info(data: bytes) -> SpriteInfo:
    dresses: Dict[Tuple[str, str], List[str]] = {}
    faces: Dict[str, List[str]] = {}
    for line in _decode_text(data).splitlines():
        f = line.split('\t')
        if len(f) >= 5 and f[0] == 'dress' and f[2] == 'diff':
            dresses.setdefault((f[1], f[3]), []).append(f[4])
        elif len(f) >= 4 and f[0] == 'face':
            faces.setdefault(f[1], []).append(f[3])
    return SpriteInfo([(d, n, ls) for (d, n), ls in dresses.items()], list(faces.items()))


def sprite_names(sheet_name: str) -> Optional[Tuple[str, str]]:
    """'dir/ひなたa_1.txt' → ('dir/ひなたa', '1') o None."""
    m = _LAYER_TXT.match(sheet_name)
    return (m.group(1), m.group(2)) if m and not sheet_name.lower().endswith('_info.txt') else None


def layer_file(sheet_name: str, layer: Layer) -> str:
    base = sheet_name[:-4]
    return f"{base}_{layer.layer_id}.tlg"


def info_file(sheet_name: str) -> Optional[str]:
    names = sprite_names(sheet_name)
    return f"{names[0]}_info.txt" if names else None


def compose(sheet: LayerSheet, names: List[str], load: Callable[[Layer], Optional[Image.Image]]) -> Image.Image:
    """
    Compone las capas con esos nombres sobre un lienzo transparente, de abajo a arriba.
    load(capa) devuelve la imagen de la capa (o None si no está).
    """
    canvas = np.zeros((sheet.height, sheet.width, 4), np.float32)
    wanted = set(names)
    for layer in reversed(sheet.layers):
        if layer.name not in wanted:
            continue
        img = load(layer)
        if img is None:
            continue
        src = np.asarray(img.convert('RGBA'), np.float32) / 255.0
        x0, y0 = max(layer.left, 0), max(layer.top, 0)
        x1 = min(layer.left + src.shape[1], sheet.width)
        y1 = min(layer.top + src.shape[0], sheet.height)
        if x1 <= x0 or y1 <= y0:
            continue
        s = src[y0 - layer.top:y1 - layer.top, x0 - layer.left:x1 - layer.left]
        d = canvas[y0:y1, x0:x1]
        a = s[..., 3:4] * (layer.opacity / 255.0)
        if layer.blend == BLEND_MULTIPLY:
            color = d[..., :3] * s[..., :3]
            d[..., :3] = d[..., :3] * (1 - a) + color * a
        else:
            out_a = a + d[..., 3:4] * (1 - a)
            safe = np.where(out_a > 0, out_a, 1)
            d[..., :3] = (s[..., :3] * a + d[..., :3] * d[..., 3:4] * (1 - a)) / safe
            d[..., 3:4] = out_a
    return Image.fromarray((np.clip(canvas, 0, 1) * 255 + 0.5).astype(np.uint8), 'RGBA')


def sprite_variants(info: SpriteInfo, all_faces: bool = True) -> List[Tuple[str, List[str]]]:
    """[(etiqueta, capas)] de cada ropa/variante con cada expresión (o solo la primera)."""
    faces = info.faces if all_faces else info.faces[:1]
    if not faces:
        faces = [('', [])]
    out = []
    for dress, diff, layers in info.dresses or [('', '', [])]:
        for face, face_layers in faces:
            label = ' '.join(p for p in (dress, diff and f"diff {diff}", face and f"face {face}") if p)
            out.append((label, layers + face_layers))
    return out
