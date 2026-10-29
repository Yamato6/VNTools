"""
yeti_utf - Tablas @UTF de CRIWARE (cabecera y tablas de contenido de un CPK)

Estructura (big-endian):
    "@UTF", u32 tamaño
    u32 rows_offset, u32 strings_offset, u32 data_offset, u32 table_name
        (offsets relativos a 8 bytes tras la firma)
    u16 num_columns, u16 row_length, u32 num_rows
    columnas: u8 flags + u32 offset al nombre (+ valor si es constante)
"""

import struct

STORAGE_MASK = 0xF0
STORAGE_ZERO = 0x10
STORAGE_CONSTANT = 0x30
STORAGE_PERROW = 0x50
TYPE_MASK = 0x0F


def decrypt_utf(data: bytes) -> bytes:
    """Descifrado XOR de tablas @UTF cifradas (m=0x655F, t=0x4115)."""
    out = bytearray(len(data))
    m = 0x655F
    for i, b in enumerate(data):
        out[i] = b ^ (m & 0xFF)
        m = (m * 0x4115) & 0xFFFFFFFF
    return bytes(out)


def _cstring(buf: bytes, off: int) -> str:
    end = buf.index(b'\0', off)
    raw = buf[off:end]
    for enc in ('utf-8', 'cp932'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode('latin-1')


class UTFTable:
    """Tabla @UTF ya parseada: self.name, self.columns y self.rows (lista de dicts)."""

    def __init__(self, buf: bytes):
        if buf[:4] != b'@UTF':
            buf = decrypt_utf(buf)
            if buf[:4] != b'@UTF':
                raise ValueError("No es una tabla @UTF")
        self.buf = buf
        (_size, rows_off, str_off, data_off, name_off,
         ncols, row_len, nrows) = struct.unpack_from('>IIIIIHHI', buf, 4)
        base = 8
        self.rows_off, self.str_off, self.data_off = rows_off + base, str_off + base, data_off + base
        self.name = _cstring(buf, self.str_off + name_off)

        pos = 32
        cols = []
        for _ in range(ncols):
            flags = buf[pos]
            pos += 1
            if flags == 0:  # relleno que aparece en algunas tablas
                pos += 3
                flags = buf[pos]
                pos += 1
            (noff,) = struct.unpack_from('>I', buf, pos)
            pos += 4
            col = {'name': _cstring(buf, self.str_off + noff), 'flags': flags, 'const': None}
            if flags & STORAGE_MASK == STORAGE_CONSTANT:
                col['const'], pos = self._read_value(flags & TYPE_MASK, pos)
            cols.append(col)
        self.columns = cols

        self.rows = []
        for r in range(nrows):
            p = self.rows_off + r * row_len
            row = {}
            for col in cols:
                st = col['flags'] & STORAGE_MASK
                if st == STORAGE_PERROW:
                    row[col['name']], p = self._read_value(col['flags'] & TYPE_MASK, p)
                elif st == STORAGE_CONSTANT:
                    row[col['name']] = col['const']
                elif st == STORAGE_ZERO:
                    row[col['name']] = 0
                else:
                    row[col['name']] = None
            self.rows.append(row)

    def to_bytes(self) -> bytes:
        return write_table(self.name, self.columns, self.rows)

    def _read_value(self, t: int, p: int):
        b = self.buf
        if t in (0, 1):
            return b[p], p + 1
        if t in (2, 3):
            return struct.unpack_from('>H', b, p)[0], p + 2
        if t in (4, 5):
            return struct.unpack_from('>I', b, p)[0], p + 4
        if t in (6, 7):
            return struct.unpack_from('>Q', b, p)[0], p + 8
        if t == 8:
            return struct.unpack_from('>f', b, p)[0], p + 4
        if t == 0xA:
            (o,) = struct.unpack_from('>I', b, p)
            return _cstring(b, self.str_off + o), p + 4
        if t == 0xB:
            o, n = struct.unpack_from('>II', b, p)
            s = self.data_off + o
            return bytes(b[s:s + n]), p + 8
        raise ValueError(f"Tipo de columna desconocido: {t:#x}")


# =============================================================================
# Escritura
# =============================================================================

_INT_FORMATS = {0: '>B', 1: '>B', 2: '>H', 3: '>H', 4: '>I', 5: '>I', 6: '>Q', 7: '>Q', 8: '>f'}
_TYPE_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 8, 7: 8, 8: 4, 0xA: 4, 0xB: 8}


def column(name: str, flags: int, const=None) -> dict:
    return {'name': name, 'flags': flags, 'const': const}


def write_table(name: str, columns, rows) -> bytes:
    """
    Inverso de UTFTable: columns es una lista de dicts {'name', 'flags', 'const'} y rows una
    lista de dicts. Mismo orden que las tablas de CRIWARE: cadenas '<NULL>', nombre de la
    tabla, nombres de columna y valores; luego los blobs; todo alineado a 8 bytes.
    """
    strings = bytearray(b'<NULL>\0')
    string_offsets = {'<NULL>': 0}
    blobs = bytearray()

    def add_string(s: str) -> int:
        if s not in string_offsets:
            string_offsets[s] = len(strings)
            strings.extend(s.encode('utf-8') + b'\0')
        return string_offsets[s]

    def value(t: int, v) -> bytes:
        if t in _INT_FORMATS:
            return struct.pack(_INT_FORMATS[t], v)
        if t == 0xA:
            return struct.pack('>I', add_string(v))
        if t == 0xB:
            off = len(blobs)
            blobs.extend(v)
            return struct.pack('>II', off, len(v))
        raise ValueError(f"Tipo de columna desconocido: {t:#x}")

    name_off = add_string(name)
    col_defs = bytearray()
    for col in columns:
        col_defs += bytes([col['flags']]) + struct.pack('>I', add_string(col['name']))
        if col['flags'] & STORAGE_MASK == STORAGE_CONSTANT:
            col_defs += value(col['flags'] & TYPE_MASK, col['const'])
    row_data = bytearray()
    for row in rows:
        for col in columns:
            if col['flags'] & STORAGE_MASK == STORAGE_PERROW:
                row_data += value(col['flags'] & TYPE_MASK, row[col['name']])
    row_len = sum(_TYPE_SIZES[c['flags'] & TYPE_MASK] for c in columns
                  if c['flags'] & STORAGE_MASK == STORAGE_PERROW)

    rows_off = 32 + len(col_defs)
    strings_off = rows_off + len(row_data)
    strings += bytes(-(strings_off + len(strings)) % 8)
    data_off = strings_off + len(strings)
    body = bytes(col_defs + row_data + strings + blobs)
    body += bytes(-(32 + len(body)) % 8)
    header = b'@UTF' + struct.pack('>IIIIIHHI', 24 + len(body), rows_off - 8, strings_off - 8,
                                   data_off - 8, name_off, len(columns), row_len, len(rows))
    return header + body
