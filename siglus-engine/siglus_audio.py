"""
siglus_audio - Audio y vídeo de RealLive / SiglusEngine

NWA (música y efectos): cabecera de 0x2C bytes
    u16 canales · u16 bits · u32 frecuencia · i32 nivel (-1 = PCM sin comprimir) · u32 RLE
    · u32 bloques · u32 tamaño PCM · u32 tamaño comprimido · u32 muestras · u32 muestras por
    bloque · u32 muestras del último bloque · u32
    · u32 offsets[bloques] · por bloque: muestra inicial de cada canal y diferencias en un flujo
    de bits (el ancho depende del nivel), con repeticiones si hay RLE.
OVK (voces): u32 n · n × (u32 tamaño, u32 offset, u32 id, u32 muestras): cada entrada es un
    Ogg Vorbis completo.
OMV (vídeo): Ogg Theora con una cabecera propia delante; desde el primer 'OggS' es un .ogv.
Referencia: GARbro ArcFormats/RealLive (AudioNWA.cs, ArcOVK.cs), MIT.
"""

import ctypes
import io
import struct
import wave
from pathlib import Path
from typing import List, Tuple

from siglus_crypto import load_native

NWA_HEADER = 0x2C
COPY_CHUNK = 1 << 20


# =============================================================================
# NWA
# =============================================================================

def nwa_header(data: bytes):
    """(canales, bits, frecuencia, nivel, rle, bloques, tamaño PCM, muestras, por bloque, último)."""
    ch, bps, rate, level, rle, blocks, pcm, _packed, samples, bsize, final = \
        struct.unpack_from('<HHIiiiiiiii', data, 0)
    return ch, bps, rate, level, rle, blocks, pcm, samples, bsize, final


def is_nwa(data: bytes) -> bool:
    if len(data) < NWA_HEADER:
        return False
    ch, bps, rate, level, *_ = nwa_header(data)
    return ch in (1, 2) and bps in (8, 16) and 0 < rate <= 192000 and -1 <= level <= 5


def nwa_info(data: bytes) -> Tuple[int, int, int, float]:
    """(canales, bits, frecuencia, segundos)."""
    ch, bps, rate, _level, _rle, _blocks, pcm, *_ = nwa_header(data)
    return ch, bps, rate, pcm / max(1, ch * bps // 8 * rate)


class _Bits:
    def __init__(self, b: bytes, pos: int):
        self.b, self.p, self.cache, self.n = b, pos, 0, 0

    def get(self, k: int) -> int:
        while self.n < k:
            self.cache |= (self.b[self.p] if self.p < len(self.b) else 0) << self.n
            self.p += 1
            self.n += 8
        v = self.cache & ((1 << k) - 1)
        self.cache >>= k
        self.n -= k
        return v


def _s16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _nwa_python(data: bytes, offsets, ch, bps, level, rle, bsize, final, pcm_size) -> bytes:
    out = bytearray()
    blocks = len(offsets)
    for i, p in enumerate(offsets):
        size = final if (i == blocks - 1 and final > 0) else bsize
        smp = [0, 0]
        for c in range(ch):
            if bps == 8:
                smp[c] = data[p]
                p += 1
            else:
                smp[c] = struct.unpack_from('<h', data, p)[0]
                p += 2
        bits = _Bits(data, p)
        c = rep = 0
        for _ in range(size):
            if rep == 0:
                ctl = bits.get(3)
                if ctl == 7:
                    if bits.get(1):
                        smp[c] = 0
                    else:
                        nb, sh = (8 - level, 9 + level) if level < 3 else (8, 9)
                        v = bits.get(nb)
                        sign = 1 << (nb - 1)
                        d = (v & (sign - 1)) << sh
                        smp[c] = _s16(smp[c] - d if v & sign else smp[c] + d)
                elif ctl:
                    nb, sh = (5 - level, 2 + ctl + level) if level < 3 else (3 + level, 1 + ctl)
                    v = bits.get(nb)
                    sign = 1 << (nb - 1)
                    d = (v & (sign - 1)) << sh
                    smp[c] = _s16(smp[c] - d if v & sign else smp[c] + d)
                elif rle:
                    rep = bits.get(1)
                    if rep:
                        rep = bits.get(2)
                        if rep == 3:
                            rep = bits.get(8)
            else:
                rep -= 1
            out += struct.pack('<h', smp[c]) if bps == 16 else bytes([smp[c] & 0xFF])
            if ch == 2:
                c ^= 1
            if len(out) >= pcm_size:
                return bytes(out[:pcm_size])
    return bytes(out)


def nwa_to_pcm(data: bytes) -> Tuple[bytes, int, int, int]:
    """(PCM, canales, bits, frecuencia)."""
    ch, bps, rate, level, rle, blocks, pcm_size, _samples, bsize, final = nwa_header(data)
    if level == -1:
        return data[NWA_HEADER:NWA_HEADER + pcm_size], ch, bps, rate
    if not 0 <= level <= 5 or blocks <= 0:
        raise ValueError("unsupported NWA compression")
    offsets = struct.unpack_from(f'<{blocks}I', data, NWA_HEADER)
    so = load_native()
    if so:
        out = ctypes.create_string_buffer(pcm_size)
        arr = (ctypes.c_uint32 * blocks)(*offsets)
        n = so.nwa_decode(data, len(data), arr, blocks, ch, bps, level, rle, bsize, final, out, pcm_size)
        if n < 0:
            raise ValueError("damaged NWA data")
        return out.raw[:n], ch, bps, rate
    return _nwa_python(data, offsets, ch, bps, level, rle, bsize, final, pcm_size), ch, bps, rate


def pcm_to_wav(pcm: bytes, ch: int, bps: int, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(ch)
        w.setsampwidth(bps // 8)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def nwa_to_wav(data: bytes) -> bytes:
    return pcm_to_wav(*nwa_to_pcm(data))


# =============================================================================
# OVK
# =============================================================================

def read_ovk_index(data: bytes) -> List[Tuple[int, int, int, int]]:
    """[(id, muestras, offset, tamaño)] de un .ovk (basta con su cabecera)."""
    n, = struct.unpack_from('<i', data, 0)
    if not 0 <= n <= 0x10000 or len(data) < 4 + 16 * n:
        raise ValueError("Not an OVK archive")
    out = []
    for k in range(n):
        size, off, vid, samples = struct.unpack_from('<IIII', data, 4 + 16 * k)
        out.append((vid, samples, off, size))
    return out


def is_ovk(data: bytes, file_size: int) -> bool:
    try:
        entries = read_ovk_index(data)
    except (ValueError, struct.error):
        return False
    return bool(entries) and all(off + size <= file_size for _, _, off, size in entries)


def ovk_entry_name(stem: str, vid: int) -> str:
    return f"{stem}_{vid:05d}.ogg"


# =============================================================================
# OMV
# =============================================================================

def omv_ogg_offset(path, limit: int = 64 << 20) -> int:
    """Posición del primer 'OggS' de un .omv (delante va una tabla de índices de tamaño variable)."""
    with open(path, 'rb') as f:
        base = 0
        tail = b''
        while base < limit:
            chunk = f.read(COPY_CHUNK)
            if not chunk:
                break
            data = tail + chunk
            pos = data.find(b'OggS')
            if pos >= 0:
                return base - len(tail) + pos
            tail = data[-3:]
            base += len(chunk)
    raise ValueError("no Ogg stream in this OMV")


def omv_to_ogv(src, dst_file):
    """Copia el flujo Ogg Theora de un .omv a dst_file (abierto en binario), sin cargarlo entero."""
    start = omv_ogg_offset(src)
    with open(src, 'rb') as f:
        f.seek(start)
        for chunk in iter(lambda: f.read(COPY_CHUNK), b''):
            dst_file.write(chunk)


def ogg_seconds(data: bytes, rate_hint: int = 0) -> float:
    """Duración aproximada de un Ogg Vorbis (granulepos de la última página / frecuencia)."""
    try:
        rate = rate_hint
        i = data.find(b'\x01vorbis')
        if i >= 0:
            rate = struct.unpack_from('<I', data, i + 12)[0]
        last = data.rfind(b'OggS')
        granule = struct.unpack_from('<q', data, last + 6)[0]
        return granule / rate if rate else 0.0
    except (struct.error, ValueError):
        return 0.0


def file_size(path) -> int:
    return Path(path).stat().st_size
