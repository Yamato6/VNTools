"""
fvp_nvsg - Conversor NVSG <-> PNG (Favorite View Point System)

Estructura:
    hzc1 header (12 bytes): "hzc1", u32 uncompressed_size, u32 header_size (0x20)
    NVSG header (32 bytes): "NVSG", u16 256, u16 format, u16 width, u16 height,
                            u16 x, u16 y, u16 unk1, u16 unk2, u32 image_count,
                            u32 unk3, u32 unk4
    datos comprimidos con zlib (BGR / BGRA / escala de grises)
"""

import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

from fvp_common import check_cancel, report

LOG_NAME = 'decode_log.txt'

FORMAT_NAMES = {
    0: 'BGR 24-bit',
    1: 'BGRA 32-bit',
    2: 'BGRA multi-frame',
    3: 'Escala de grises',
}


@dataclass
class NVSGHeader:
    fmt: int
    width: int
    height: int
    x: int
    y: int
    unk1: int
    unk2: int
    image_count: int
    unk3: int
    unk4: int

    @property
    def frames(self) -> int:
        return self.image_count if self.image_count > 0 else 1


def is_nvsg(data: bytes) -> bool:
    return data[:4] == b'hzc1' and data[12:16] == b'NVSG'


def parse_header(data: bytes) -> NVSGHeader:
    """Lee las cabeceras hzc1 + NVSG de los primeros 44 bytes."""
    if data[:4] != b'hzc1':
        raise ValueError(f"No es un archivo NVSG válido (magic: {data[:4]!r})")
    if data[12:16] != b'NVSG':
        raise ValueError("Falta la cabecera NVSG")
    (_always_256, fmt, width, height, x, y, unk1, unk2,
     image_count, unk3, unk4) = struct.unpack_from('<HHHHHHHHIII', data, 16)
    return NVSGHeader(fmt, width, height, x, y, unk1, unk2, image_count, unk3, unk4)


def read_header(path) -> NVSGHeader:
    with open(path, 'rb') as f:
        return parse_header(f.read(44))


def decode_bytes(data: bytes) -> Tuple[Image.Image, NVSGHeader]:
    """Convierte los bytes de un NVSG en una imagen PIL."""
    h = parse_header(data)
    pixels = zlib.decompress(data[44:])

    if h.fmt == 0:      # BGR 24-bit
        img = Image.frombytes('RGB', (h.width, h.height), pixels, 'raw', 'BGR')
    elif h.fmt == 1:    # BGRA 32-bit
        img = Image.frombytes('RGBA', (h.width, h.height), pixels, 'raw', 'BGRA')
    elif h.fmt == 2:    # BGRA con varios frames apilados en vertical
        img = Image.frombytes('RGBA', (h.width, h.height * h.frames), pixels, 'raw', 'BGRA')
    elif h.fmt == 3:    # Escala de grises
        img = Image.frombytes('L', (h.width, h.height), pixels)
    else:
        raise ValueError(f"Formato no soportado: {h.fmt}")
    return img, h


def decode(nvsg_path, png_path) -> NVSGHeader:
    """Convierte un NVSG en PNG. Devuelve la cabecera (metadatos)."""
    img, h = decode_bytes(Path(nvsg_path).read_bytes())
    img.save(png_path, 'PNG')
    return h


def encode(png_path, nvsg_path, x: int, y: int, image_count: int = 1,
           fmt: Optional[int] = None, unk: Tuple[int, int, int, int] = (0, 0, 0, 0)) -> NVSGHeader:
    """
    Convierte un PNG en NVSG. Si fmt es None se deduce de la imagen
    (escala de grises -> 3, varios frames -> 2, con alfa -> 1, si no -> 0).
    """
    img = Image.open(png_path)
    width, height = img.size
    # Algunos originales guardan 0: se escribe tal cual pero se calcula con 1
    frames = image_count if image_count > 0 else 1

    if fmt is None:
        if frames > 1:
            fmt = 2
        elif img.mode in ('L', 'LA', 'I', 'I;16', '1'):
            fmt = 3
        elif img.mode in ('RGBA', 'LA', 'PA') or 'transparency' in img.info:
            fmt = 1
        else:
            fmt = 0

    if fmt == 2:
        if height % frames:
            raise ValueError(f"La altura ({height}) no es múltiplo del número de frames ({frames})")
        height //= frames
        data = img.convert('RGBA').tobytes('raw', 'BGRA')
    elif fmt == 1:
        data = img.convert('RGBA').tobytes('raw', 'BGRA')
    elif fmt == 3:
        data = img.convert('L').tobytes()
    elif fmt == 0:
        data = img.convert('RGB').tobytes('raw', 'BGR')
    else:
        raise ValueError(f"Formato no soportado: {fmt}")

    compressed = zlib.compress(data, level=9)
    header = NVSGHeader(fmt, width, height, x, y, unk[0], unk[1], image_count, unk[2], unk[3])

    with open(nvsg_path, 'wb') as f:
        f.write(b'hzc1')
        f.write(struct.pack('<II', len(data), 0x20))
        f.write(b'NVSG')
        f.write(struct.pack('<HHHHHHHHIII', 256, fmt, width, height, x, y,
                            header.unk1, header.unk2, image_count, header.unk3, header.unk4))
        f.write(compressed)
    return header


# =============================================================================
# decode_log.txt
# =============================================================================

_LOG_PAIR_RE = re.compile(r'\s+(\w+)=(-?\d+)')


def format_log_line(png_name: str, h: NVSGHeader) -> str:
    return (f"{png_name} x={h.x} y={h.y} image_count={h.image_count} width={h.width} "
            f"height={h.height} format={h.fmt} unk1={h.unk1} unk2={h.unk2} "
            f"unk3={h.unk3} unk4={h.unk4}")


def parse_log_line(line: str) -> Optional[Tuple[str, Dict[str, int]]]:
    """
    Separa una línea del log en (nombre_png, valores). Los pares clave=valor
    se leen desde el final, así el nombre puede contener espacios.
    """
    line = line.rstrip()
    m = re.search(r'\s+x=-?\d+', line)
    if not m:
        return None
    name = line[:m.start()]
    values = {k: int(v) for k, v in _LOG_PAIR_RE.findall(line[m.start():])}
    if not name or 'x' not in values or 'y' not in values:
        return None
    return name, values


def read_log(log_path) -> Dict[str, Dict[str, int]]:
    result = {}
    for line in Path(log_path).read_text(encoding='utf-8').splitlines():
        parsed = parse_log_line(line)
        if parsed:
            result[parsed[0]] = parsed[1]
    return result


# =============================================================================
# Conversión por lotes
# =============================================================================

def batch_decode(input_folder, output_folder, progress=None, cancel=None) -> Tuple[int, List[str]]:
    """Convierte todos los NVSG de una carpeta en PNG. Devuelve (convertidos, errores)."""
    input_folder = Path(input_folder)
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)

    # Ignorar el log y los archivos de información (_bininfo.json)
    files = sorted(f for f in input_folder.iterdir() if f.is_file() and f.name != LOG_NAME
                   and not (f.name.startswith('_') and f.suffix == '.json'))
    log_entries, errors = [], []
    total = len(files)
    for i, f in enumerate(files):
        check_cancel(cancel)
        report(progress, i, total, f.name)
        png_name = f.stem + ".png"
        try:
            h = decode(f, output_folder / png_name)
            log_entries.append(format_log_line(png_name, h))
        except Exception as e:
            errors.append(f"{f.name}: {e}")

    (output_folder / LOG_NAME).write_text('\n'.join(log_entries), encoding='utf-8')
    report(progress, total, total)
    return len(log_entries), errors


def batch_encode(input_folder, output_folder, log_path, progress=None,
                 cancel=None) -> Tuple[int, List[str]]:
    """Convierte todos los PNG en NVSG usando el log. Devuelve (convertidos, sin entrada)."""
    input_folder = Path(input_folder)
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    log_map = read_log(log_path)
    if not log_map:
        raise ValueError("El log está vacío o no es válido")

    files = sorted(input_folder.glob('*.png'))
    done, missing = 0, []
    total = len(files)
    for i, f in enumerate(files):
        check_cancel(cancel)
        report(progress, i, total, f.name)
        vals = log_map.get(f.name)
        if vals is None:
            missing.append(f.name)
            continue
        encode(f, output_folder / f.stem, vals['x'], vals['y'], vals.get('image_count', 1),
               fmt=vals.get('format'),
               unk=tuple(vals.get(k, 0) for k in ('unk1', 'unk2', 'unk3', 'unk4')))
        done += 1
    report(progress, total, total)
    return done, missing
