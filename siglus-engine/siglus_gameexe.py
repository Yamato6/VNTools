"""
siglus_gameexe - Gameexe.dat (configuración del juego) y tablas .dbs de SiglusEngine

Gameexe.dat: u32 0 · u32 usa clave · datos con XOR TABLE_B (y la clave de 16 bytes del juego si
    usa clave) · LZ · texto UTF-16LE ('#GAMENAME = "..."', '#SCREEN_SIZE = ...').
.dbs: u32 (0 = Shift-JIS, otro = UTF-16) · datos con XOR 2D 62 F4 89 · LZ · XOR con dos claves de
    4 bytes alternadas según una secuencia fija · tabla: u32 tamaño · u32 filas · u32 columnas ·
    u32 offsets (números de fila, formato de columnas, datos, texto) · por columna (u32 número,
    u32 tipo 0x53 = texto, 0x56 = entero).
Referencia: hiroshil/SiglusEngine (GameexeUnpacker.py, dbsDecrypt.py).
"""

import csv
import io
import struct
from typing import List, Optional, Tuple

from siglus_crypto import TABLE_B, lz_pack, lz_unpack, xor_repeat

DBS_KEY = bytes([0x2D, 0x62, 0xF4, 0x89])
_DBS_KEYS = (bytes([0x0E, 0xC7, 0x90, 0x71]), bytes([0x35, 0xF1, 0x9B, 0x49]))
_DBS_ORDER = (0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0,
              1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1,
              0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0,
              1, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1, 1,
              1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1)
DBS_TEXT, DBS_INT = 0x53, 0x56


# =============================================================================
# Gameexe.dat
# =============================================================================

def read_gameexe(data: bytes, key: Optional[bytes] = None) -> str:
    """Texto de Gameexe.dat (con los saltos de línea originales)."""
    if len(data) < 16:
        raise ValueError("Not a Gameexe.dat")
    uses_key, = struct.unpack_from('<I', data, 4)
    body = xor_repeat(data[8:], TABLE_B)
    if uses_key:
        if not key:
            raise ValueError("this Gameexe.dat uses the game key: it was not found")
        body = xor_repeat(body, key)
    return lz_unpack(body).decode('utf-16le')


def write_gameexe(text: str, original: bytes, key: Optional[bytes] = None) -> bytes:
    """Gameexe.dat con el texto nuevo y la cabecera del original."""
    uses_key, = struct.unpack_from('<I', original, 4)
    body = lz_pack(text.encode('utf-16le'))
    if uses_key:
        if not key:
            raise ValueError("this Gameexe.dat uses the game key: it was not found")
        body = xor_repeat(body, key)
    return original[:8] + xor_repeat(body, TABLE_B)


# =============================================================================
# .dbs
# =============================================================================

def _dbs_unscramble(data: bytes) -> bytes:
    key = bytearray()
    for i in range(-(-len(data) // 4)):
        key += _DBS_KEYS[_DBS_ORDER[i % len(_DBS_ORDER)]]
    return xor_repeat(data, bytes(key[:len(data)])) if data else data


def read_dbs(data: bytes) -> Tuple[List[Tuple[int, int]], List[Tuple[int, list]]]:
    """([(número de columna, tipo)], [(número de fila, valores)])."""
    utf16 = struct.unpack_from('<I', data, 0)[0] != 0
    body = lz_unpack(xor_repeat(data[4:], DBS_KEY))
    t = _dbs_unscramble(body)
    _size, rows, cols, row_off, fmt_off, data_off, text_off = struct.unpack_from('<7I', t, 0)
    row_numbers = struct.unpack_from(f'<{rows}i', t, row_off)
    columns = [struct.unpack_from('<II', t, fmt_off + 8 * c) for c in range(cols)]
    out = []
    for r in range(rows):
        values = []
        for c, (_num, kind) in enumerate(columns):
            v, = struct.unpack_from('<I', t, data_off + 4 * (r * cols + c))
            if kind == DBS_TEXT:
                start = text_off + v
                if utf16:
                    end = start
                    while end + 1 < len(t) and t[end:end + 2] != b'\0\0':
                        end += 2
                    values.append(t[start:end].decode('utf-16le', 'replace'))
                else:
                    end = t.find(b'\0', start)
                    values.append(t[start:end if end >= 0 else len(t)].decode('cp932', 'replace'))
            else:
                values.append(struct.unpack('<i', struct.pack('<I', v))[0])
        out.append((row_numbers[r], values))
    return columns, out


def dbs_to_csv(data: bytes) -> str:
    columns, rows = read_dbs(data)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['#row'] + [f"{num}:{'text' if kind == DBS_TEXT else 'int'}" for num, kind in columns])
    for number, values in rows:
        w.writerow([number] + values)
    return buf.getvalue()
