"""
cs2_hg3 - Imágenes HG-3 de CatSystem2: lectura de todos los fotogramas y reemplazo

Formato (little-endian):
    char "HG-3" · u32 tamaño de cabecera (0x0C) · u32 versión (0x300)
    fotogramas: u32 tamaño del fotograma (0 = el último) · u32 id · secciones
    sección:    char nombre[8] · u32 tamaño de la sección (0 = la última) · u32 tamaño
                del cuerpo · cuerpo
    stdinfo:    u32 ancho, alto, bpp, x, y, ancho del lienzo, alto del lienzo, ...
    img0000:    u32 fila inicial, filas, zlib de datos, tamaño de datos, zlib de control,
                tamaño de control · datos (zlib) · control (zlib)
    img_jpg:    JPEG; img_al: u32 zlib, u32 tamaño, alfa (zlib), un byte por píxel
    Otras (ats0001, cptype, imgmode…) se conservan tal cual.

img0000: el control es un flujo de bits LSB que empieza con un bit (copiar o no) y el
tamaño total, y sigue con longitudes en código Elias-gamma que alternan bytes copiados
de los datos y ceros. El resultado se divide en 4 planos que se recombinan de 2 en 2
bits, cada byte pasa por v >> 1 (^ 0xFF si v es impar) y se deshace un delta
horizontal en la primera fila y vertical en las demás. Píxeles BGR(A).

Usa _cs2.dll si está disponible (cs2_crypto.load_native); si no, Python.
Referencia: GARbro ArcFormats/CatSystem/ImageHG3.cs (MIT).
"""

import ctypes
import io
import re
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from PIL import Image

from cs2_crypto import load_native

MAGIC = b'HG-3'
SECTION_HEADER = 16


@dataclass
class Hg3Frame:
    index: int                  # posición en el archivo
    id: int
    width: int = 0
    height: int = 0
    bpp: int = 32
    x: int = 0
    y: int = 0
    canvas_w: int = 0
    canvas_h: int = 0
    sections: List[Tuple[str, int, int]] = field(default_factory=list)   # (nombre, inicio, fin)

    def section(self, name):
        for n, start, end in self.sections:
            if n == name:
                return start, end
        return None


def is_hg3(data: bytes) -> bool:
    return data[:4] == MAGIC


def read_frames(data: bytes) -> List[Hg3Frame]:
    if not is_hg3(data):
        raise ValueError("Not an HG-3 image")
    pos = struct.unpack_from('<I', data, 4)[0]
    frames = []
    while True:
        if pos + 8 > len(data):
            raise ValueError("HG-3: damaged frame table")
        size, fid = struct.unpack_from('<II', data, pos)
        end = pos + size if size else len(data)
        fr = Hg3Frame(len(frames), fid)
        q = pos + 8
        while q < end:
            name = data[q:q + 8].split(b'\0')[0].decode('ascii', 'replace')
            ssize = struct.unpack_from('<I', data, q + 8)[0]
            send = q + ssize if ssize else end
            if send > end or send <= q:
                raise ValueError(f"HG-3: damaged section {name}")
            fr.sections.append((name, q, send))
            if name == 'stdinfo':
                (fr.width, fr.height, fr.bpp, fr.x, fr.y,
                 fr.canvas_w, fr.canvas_h) = struct.unpack_from('<7I', data, q + SECTION_HEADER)
            q = send
        frames.append(fr)
        if not size:
            return frames
        pos = end


# =============================================================================
# Núcleo de img0000 en Python (si no hay _cs2.dll)
# =============================================================================

def _conv_tables():
    conv = bytes(((v >> 1) ^ 0xFF) if v & 1 else v >> 1 for v in range(256))
    unconv = bytearray(256)
    for v in range(256):
        unconv[conv[v]] = v
    # Cada byte de un plano aporta 2 bits a cada uno de los 4 bytes de salida
    spread = [[0] * 256 for _ in range(4)]
    for p in range(4):
        shift = 6 - 2 * p
        for i in range(256):
            v = 0
            for k in range(4):
                v |= ((i >> (2 * k)) & 3) << (8 * k + shift)
            spread[p][i] = v
    return conv, bytes(unconv), spread


_CONV, _UNCONV, _SPREAD = _conv_tables()
# Para codificar: aportación de un byte de salida (posición k) al plano p
_PLANE_BITS = [[bytes((((v >> (6 - 2 * p)) & 3) << (2 * k)) for v in range(256)) for k in range(4)]
               for p in range(4)]
_RUN_RE = re.compile(rb'\x00+|[^\x00]+')


def _row_add(a: bytes, b: bytes) -> bytes:
    """Suma byte a byte (mod 256) de dos filas, con enteros grandes."""
    n = len(a)
    hi = int.from_bytes(b'\x80' * n, 'little')
    lo = hi ^ int.from_bytes(b'\xff' * n, 'little')
    x, y = int.from_bytes(a, 'little'), int.from_bytes(b, 'little')
    return (((x & lo) + (y & lo)) ^ ((x ^ y) & hi)).to_bytes(n, 'little')


def _row_sub(a: bytes, b: bytes) -> bytes:
    """Resta byte a byte (mod 256): a - b."""
    n = len(a)
    hi = int.from_bytes(b'\x80' * n, 'little')
    lo = hi ^ int.from_bytes(b'\xff' * n, 'little')
    x, y = int.from_bytes(a, 'little'), int.from_bytes(b, 'little')
    return (((x | hi) - (y & lo)) ^ ((x ^ ~y) & hi)).to_bytes(n, 'little')


def _unpack_py(data: bytes, ctl: bytes, w: int, h: int, ps: int) -> bytes:
    bits = ''.join(format(b, '08b')[::-1] for b in ctl)
    pos = 0

    def count():
        nonlocal pos
        one = bits.find('1', pos)
        if one < 0 or one - pos >= 32:
            raise ValueError("HG-3: damaged control stream")
        n = one - pos
        v = int(bits[one:one + n + 1], 2)
        pos = one + n + 1
        return v

    copy = bits[0] == '1'
    pos = 1
    raw_size = count()
    raw = bytearray(raw_size)
    src = dst = 0
    while dst < raw_size:
        n = count()
        if copy:
            raw[dst:dst + n] = data[src:src + n]
            src += n
        dst += n
        copy = not copy

    q = raw_size // 4
    s0, s1, s2, s3 = _SPREAD
    p0, p1, p2, p3 = raw[:q], raw[q:2 * q], raw[2 * q:3 * q], raw[3 * q:4 * q]
    vals = [s0[a] | s1[b] | s2[c] | s3[d] for a, b, c, d in zip(p0, p1, p2, p3)]
    out = bytearray(b''.join(v.to_bytes(4, 'little') for v in vals).translate(_CONV))
    total = w * h * ps
    out = out[:total] + bytes(max(0, total - len(out)))

    stride = w * ps
    for x in range(ps, stride):
        out[x] = (out[x] + out[x - ps]) & 0xFF
    prev = bytes(out[:stride])
    for y in range(1, h):
        row = _row_add(out[y * stride:(y + 1) * stride], prev)
        out[y * stride:(y + 1) * stride] = row
        prev = row
    return bytes(out)


def _put_count(bits: list, v: int):
    b = format(v, 'b')
    bits.append('0' * (len(b) - 1))
    bits.append(b)


def _pack_py(pix: bytes, w: int, h: int, ps: int) -> Tuple[bytes, bytes, int]:
    stride = w * ps
    total = w * h * ps
    raw_size = (total + 3) // 4 * 4
    buf = bytearray(pix)
    for y in range(h - 1, 0, -1):
        buf[y * stride:(y + 1) * stride] = _row_sub(pix[y * stride:(y + 1) * stride],
                                                    pix[(y - 1) * stride:y * stride])
    for x in range(stride - 1, ps - 1, -1):
        buf[x] = (buf[x] - buf[x - ps]) & 0xFF
    buf = bytes(buf).translate(_UNCONV) + bytes(raw_size - total)

    # Plano p, byte i: los bits (6-2p, 7-2p) de los bytes 4i..4i+3, en los bits 2k
    q = raw_size // 4
    cols = [buf[k::4] for k in range(4)]
    planes = []
    for p in range(4):
        acc = 0
        for k in range(4):
            acc |= int.from_bytes(cols[k].translate(_PLANE_BITS[p][k]), 'little')
        planes.append(acc.to_bytes(q, 'little'))
    raw = b''.join(planes)

    bits = []
    copy = raw[0] != 0
    bits.append('1' if copy else '0')
    _put_count(bits, raw_size)
    for m in _RUN_RE.finditer(raw):
        _put_count(bits, m.end() - m.start())
    data = raw.replace(b'\0', b'')
    s = ''.join(bits)
    s += '0' * (-len(s) % 8)
    ctl = bytes(int(s[k:k + 8][::-1], 2) for k in range(0, len(s), 8))
    return bytes(data), ctl, raw_size


def unpack_img0000(data: bytes, ctl: bytes, w: int, h: int, ps: int, force_python=False) -> bytes:
    so = None if force_python else load_native()
    if so is not None:
        out = ctypes.create_string_buffer(w * h * ps)
        err = so.hg3_unpack(data, len(data), ctl, len(ctl), out, w, h, ps)
        if err:
            raise ValueError(f"HG-3: damaged image data (error {err})")
        return out.raw
    return _unpack_py(data, ctl, w, h, ps)


def pack_img0000(pix: bytes, w: int, h: int, ps: int, force_python=False) -> Tuple[bytes, bytes, int]:
    """Devuelve (datos, control, tamaño descomprimido), sin zlib."""
    so = None if force_python else load_native()
    if so is not None:
        bound = so.hg3_pack_bound(w, h, ps)
        d = ctypes.create_string_buffer(bound)
        c = ctypes.create_string_buffer(bound)
        sizes = (ctypes.c_int64 * 3)()
        if so.hg3_pack(pix, w, h, ps, d, c, sizes):
            raise MemoryError("HG-3: out of memory")
        return d.raw[:sizes[0]], c.raw[:sizes[1]], sizes[2]
    return _pack_py(pix, w, h, ps)


# =============================================================================
# Decodificación
# =============================================================================

def _mode(bpp):
    if bpp == 32:
        return 'RGBA', 'BGRA', 4
    if bpp == 24:
        return 'RGB', 'BGR', 3
    raise ValueError(f"HG-3: unsupported {bpp}-bit image")


def decode_frame(data: bytes, fr: Hg3Frame) -> Image.Image:
    sec = fr.section('img0000')
    if sec is not None:
        q = sec[0] + SECTION_HEADER
        _y0, _rows, pd, du, pc, cu = struct.unpack_from('<6I', data, q)
        q += 24
        body = zlib.decompress(data[q:q + pd])
        ctl = zlib.decompress(data[q + pd:q + pd + pc])
        mode, raw_mode, ps = _mode(fr.bpp)
        pix = unpack_img0000(body, ctl, fr.width, fr.height, ps)
        # Las filas están guardadas de abajo arriba
        return Image.frombuffer(mode, (fr.width, fr.height), pix, 'raw', raw_mode, 0, -1).copy()
    sec = fr.section('img_jpg')
    if sec is not None:
        q = sec[0] + SECTION_HEADER
        img = Image.open(io.BytesIO(data[q:sec[1]])).convert('RGB')
        al = fr.section('img_al')
        if al is not None:
            q = al[0] + SECTION_HEADER
            psz, _asz = struct.unpack_from('<II', data, q)
            alpha = zlib.decompress(data[q + 8:q + 8 + psz])[:fr.width * fr.height]
            img.putalpha(Image.frombytes('L', (fr.width, fr.height), alpha))
        return img
    raise ValueError("HG-3: frame without image data (" + ", ".join(n for n, _, _ in fr.sections) + ")")


def decode(data: bytes) -> List[Tuple[Image.Image, Hg3Frame]]:
    return [(decode_frame(data, fr), fr) for fr in read_frames(data)]


def frame_names(stem: str, count: int) -> List[str]:
    """Nombres de los PNG: NOMBRE.png con un fotograma, NOMBRE_NN.png con varios."""
    if count == 1:
        return [stem + '.png']
    width = max(2, len(str(count - 1)))
    return [f"{stem}_{i:0{width}d}.png" for i in range(count)]


# =============================================================================
# Codificación
# =============================================================================

def encode_img0000(img: Image.Image, bpp: int, level: int = 6) -> bytes:
    """Cuerpo de una sección img0000 (sin la cabecera de sección) para la imagen."""
    mode, raw_mode, ps = _mode(bpp)
    w, h = img.size
    if img.mode != mode:
        img = img.convert(mode)
    pix = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes('raw', raw_mode)
    body, ctl, _raw_size = pack_img0000(pix, w, h, ps)
    zb, zc = zlib.compress(body, level), zlib.compress(ctl, level)
    return struct.pack('<6I', 0, h, len(zb), len(body), len(zc), len(ctl)) + zb + zc


def _section(name: bytes, body: bytes, last: bool) -> bytes:
    size = 0 if last else SECTION_HEADER + len(body)
    return name.ljust(8, b'\0') + struct.pack('<II', size, len(body)) + body


def replace_frames(data: bytes, images: Dict[int, Image.Image]) -> bytes:
    """
    Nuevo HG-3 con las imágenes de images ({índice del fotograma: imagen}) en lugar de
    las originales, que deben tener el mismo tamaño. Solo cambia la sección de imagen de
    esos fotogramas (las imágenes JPEG pasan a img0000); el resto se copia tal cual.
    """
    frames = read_frames(data)
    for i, img in images.items():
        if not 0 <= i < len(frames):
            raise ValueError(f"HG-3: there is no frame {i}")
        fr = frames[i]
        if img.size != (fr.width, fr.height):
            raise ValueError(f"frame {i}: the image is {img.size[0]}x{img.size[1]}, "
                             f"it must be {fr.width}x{fr.height}")
    out = bytearray(data[:struct.unpack_from('<I', data, 4)[0]])
    for fr in frames:
        img = images.get(fr.index)
        if img is None:
            start = fr.sections[0][1] - 8
            end = fr.sections[-1][2]
            chunk = bytearray(data[start:end])
        else:
            parts = []
            secs = [s for s in fr.sections if s[0] != 'img_al']
            for k, (name, s, e) in enumerate(secs):
                last = k == len(secs) - 1
                if name in ('img0000', 'img_jpg'):
                    parts.append(_section(b'img0000', encode_img0000(img, fr.bpp), last))
                elif last and struct.unpack_from('<I', data, s + 8)[0] != 0:
                    parts.append(data[s:s + 8] + struct.pack('<I', 0) + data[s + 12:e])
                elif not last and struct.unpack_from('<I', data, s + 8)[0] == 0:
                    parts.append(data[s:s + 8] + struct.pack('<I', e - s) + data[s + 12:e])
                else:
                    parts.append(data[s:e])
            chunk = bytearray(struct.pack('<II', 0, fr.id) + b''.join(parts))
        last_frame = fr.index == len(frames) - 1
        struct.pack_into('<I', chunk, 0, 0 if last_frame else len(chunk))
        out += chunk
    return bytes(out)
