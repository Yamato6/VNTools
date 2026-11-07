"""
bgi_audio - Audio "bw" de BGI / Ethornell

Cabecera de 0x40 bytes y luego un Ogg Vorbis:
    u32 tamaño de la cabecera (0x40) · "bw  " · u32 tamaño del Ogg · u32 muestras
    · u32 frecuencia · u32 canales · ceros (salvo u32@0x30, que suele valer 3)
La música va en pares NOMBRE_head / NOMBRE_loop: la parte que suena una vez y la que se repite.
"""

import struct
from typing import Tuple

BW_MAGIC = b'bw  '


def is_bw(data: bytes) -> bool:
    return data[4:8] == BW_MAGIC


def bw_to_ogg(data: bytes) -> bytes:
    if not is_bw(data):
        raise ValueError("Not a bw audio file")
    off, = struct.unpack_from('<I', data, 0)
    if data[off:off + 4] != b'OggS':
        raise ValueError("The bw audio does not contain an Ogg stream")
    return data[off:]


def ogg_info(ogg: bytes) -> Tuple[int, int, int]:
    """(muestras, frecuencia, canales) de un Ogg Vorbis."""
    if ogg[:4] != b'OggS':
        raise ValueError("Not an Ogg file")
    nseg = ogg[26]
    body = 27 + nseg
    if ogg[body:body + 7] != b'\x01vorbis':
        raise ValueError("The Ogg file is not Vorbis")
    channels = ogg[body + 11]
    rate, = struct.unpack_from('<I', ogg, body + 12)
    last = ogg.rfind(b'OggS')
    samples, = struct.unpack_from('<q', ogg, last + 6)
    return max(samples, 0), rate, channels


def ogg_to_bw(ogg: bytes, like: bytes = None) -> bytes:
    """bw con el Ogg dado. like: el bw original, del que se copia el resto de la cabecera."""
    samples, rate, channels = ogg_info(ogg)
    head = struct.pack('<I4sIIII', 0x40, BW_MAGIC, len(ogg), samples, rate, channels)
    if like is not None and is_bw(like) and struct.unpack_from('<I', like, 0)[0] == 0x40:
        rest = like[len(head):0x40]
    else:
        rest = bytes(0x30 - len(head)) + struct.pack('<I', 3)
    return (head + rest).ljust(0x40, b'\0') + ogg
