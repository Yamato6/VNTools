"""
yeti_script - Guiones de sn.bin (motor Yeti/Regista)

sn.bin = LZSS de Yeti -> pak (u32 offset, u32 tamaño, 0, 0 por entrada) con 353 guiones.
Cada guion:
    u32 inicio del código (casi siempre 4)
    instrucciones: 1 byte de opcode y sus argumentos (little-endian); el código termina en 0x05
    16 bytes de cola y ceros hasta alinear a 16
La última entrada (352) no es un guion: es la tabla de colas de todos ellos.

Textos (Shift-JIS, terminados en nulo):
    0x45 mensaje            4 bytes, texto
    0x86 texto especial     4 bytes, texto
    0x47 nombre             u16 (si vale 0x0D, el texto va justo después; si no, otro u16), texto
    0x31/0x32 opciones      u8, u8, u8 n, 3 bytes, n x (6 bytes, u32 destino, texto)

Saltos (u32 dirección absoluta dentro del guion; 0 = ninguno):
    0x01, 0x03 directos; 0x06-0x0B con 4 bytes antes; 0x0C, 0x0D con 2 bytes antes;
    0x0E switch: u16, u16 n, n x (u16, u32 destino); y el destino de cada opción.

Tamaños de los argumentos verificados con los 352 guiones de CROSS†CHANNEL -Final Complete-:
todos se recorren completos y cada destino cae al inicio de una instrucción.
"""

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from yeti_codecs import compress_yeti_lzss, decompress_yeti_lzss
from yeti_common import atomic_write, check_cancel, report

TRAILER = 16
ALIGN = 16
FIXED_ONLY = {0}        # menú de depuración: su cabecera tiene datos que no se reubican

# Argumentos de longitud fija (bytes tras el opcode)
_FIXED = {
    0x00: 0, 0x02: 4, 0x04: 4, 0x05: 0,
    0x18: 4, 0x19: 8, 0x1A: 4, 0x1B: 0, 0x1C: 0, 0x1D: 6, 0x1E: 10, 0x1F: 12, 0x20: 6, 0x21: 6,
    0x22: 4, 0x23: 8, 0x24: 6, 0x25: 4, 0x2A: 0, 0x2B: 0, 0x2C: 2, 0x2D: 4, 0x2E: 0, 0x2F: 2,
    0x30: 10, 0x33: 0, 0x34: 10, 0x36: 3, 0x37: 0, 0x39: 4, 0x3A: 4, 0x3B: 2, 0x3C: 2,
    0x42: 8, 0x43: 4, 0x48: 2, 0x49: 4, 0x4A: 2, 0x4B: 4, 0x4C: 6, 0x4F: 4, 0x51: 6, 0x53: 2,
    0x56: 4, 0x59: 0, 0x5A: 0, 0x5F: 0, 0x66: 2, 0x68: 10, 0x69: 2, 0x6A: 4, 0x6B: 2, 0x6C: 16,
    0x6E: 4, 0x6F: 6, 0x70: 0, 0x71: 6, 0x72: 4, 0x74: 6, 0x75: 4, 0x7B: 4, 0x80: 4, 0x81: 6,
    0x82: 2, 0x83: 4, 0x84: 2, 0x87: 0, 0x8A: 2, 0x8B: 4, 0x8C: 12, 0x8D: 0, 0x8E: 10, 0x8F: 6,
    0xFF: 0,
}
_FIXED.update({op: 4 for op in range(0x10, 0x18)})
# Saltos: bytes de argumentos antes del u32 destino
_JUMPS = {0x01: 0, 0x03: 0, 0x0C: 2, 0x0D: 2}
_JUMPS.update({op: 4 for op in range(0x06, 0x0C)})
# Textos: bytes antes del texto y tipo
_TEXTS = {0x45: (4, 'm'), 0x86: (4, 'm'), 0x85: (4, 's'), 0x90: (2, 'n')}
OP_SWITCH = 0x0E
OP_CHOICES = (0x31, 0x32)
OP_VOICE = 0x44
OP_NAME = 0x47
OP_RETURN = 0x05
TEXT_KINDS = {'m': 'mensaje', 'n': 'nombre', 'o': 'opción'}


@dataclass
class Field:
    kind: str           # 'J' destino, 'm'/'n'/'o' texto traducible, 's' texto que no se traduce
    pos: int
    size: int
    value: object       # int (destino) o bytes (texto, sin el nulo)


@dataclass
class Instr:
    addr: int
    op: int
    end: int
    fields: List[Field]


@dataclass
class Script:
    data: bytes
    code_start: int
    code_end: int
    instrs: List[Instr]


def _u16(d, q):
    return struct.unpack_from('<H', d, q)[0]


def _u32(d, q):
    return struct.unpack_from('<I', d, q)[0]


def _string(d, q, kind, fields) -> int:
    end = d.index(b'\0', q)
    fields.append(Field(kind, q, end + 1 - q, d[q:end]))
    return end + 1


def _jump(d, q, fields) -> int:
    fields.append(Field('J', q, 4, _u32(d, q)))
    return q + 4


def _parse_instr(d: bytes, p: int) -> Instr:
    op = d[p]
    q = p + 1
    fields = []
    if op in _FIXED:
        q += _FIXED[op]
    elif op in _JUMPS:
        q = _jump(d, q + _JUMPS[op], fields)
    elif op in _TEXTS:
        skip, kind = _TEXTS[op]
        q = _string(d, q + skip, kind, fields)
    elif op == OP_NAME:
        q = _string(d, q + (2 if _u16(d, q) == 0x0D else 4), 'n', fields)
    elif op == OP_VOICE:
        q += 5 if _u16(d, q + 2) == 0xFFFF else 4
    elif op == OP_SWITCH:
        count = _u16(d, q + 2)
        q += 4
        for _ in range(count):
            q = _jump(d, q + 2, fields)
    elif op in OP_CHOICES:
        count = d[q + 2]
        q += 6
        for _ in range(count):
            q = _jump(d, q + 6, fields)
            q = _string(d, q, 'o', fields)
    else:
        raise ValueError(f"Opcode desconocido 0x{op:02X} en 0x{p:X}")
    if q > len(d):
        raise ValueError(f"La instrucción 0x{op:02X} en 0x{p:X} se sale del guion")
    return Instr(p, op, q, fields)


def parse(data: bytes, trailer: Optional[bytes] = None) -> Script:
    """
    Recorre un guion. Error si no es un guion o tiene un opcode desconocido.

    El código acaba en un 0x05 seguido de la cola (16 bytes) y ceros hasta alinear a 16.
    Como algunos guiones acaban en '05 05' y la cola puede empezar por 05, el final exacto
    se toma de la cola del guion (tabla de colas de sn.bin); sin ella, el último posible.
    """
    if len(data) < 4 + TRAILER or len(data) % ALIGN:
        raise ValueError("No es un guion")
    start = _u32(data, 0)
    if not 4 <= start < len(data):
        raise ValueError("No es un guion")
    ends = {e for e in range(max(start, len(data) - TRAILER - ALIGN + 1), len(data) - TRAILER + 1)
            if not any(data[e + TRAILER:])
            and (trailer is None or data[e:e + TRAILER] == trailer)}
    instrs = []
    found = None
    p = start
    while p < len(data) - TRAILER:
        if p in ends and instrs and instrs[-1].op == OP_RETURN:
            found = (p, len(instrs))
        try:
            ins = _parse_instr(data, p)
        except (ValueError, struct.error, IndexError) as e:
            if found:
                break
            raise ValueError(f"No es un guion válido: {e}") from e
        instrs.append(ins)
        p = ins.end
    if p in ends and instrs and instrs[-1].op == OP_RETURN:
        found = (p, len(instrs))
    if found is None:
        raise ValueError("No se encontró el final del código")
    return Script(data, start, found[0], instrs[:found[1]])


def trailers(scripts: List[bytes]) -> List[Optional[bytes]]:
    """Cola de cada guion según la tabla de colas (la última entrada de sn.bin)."""
    table = scripts[-1]
    return [table[TRAILER * i:TRAILER * (i + 1)] if TRAILER * (i + 1) <= len(table) else None
            for i in range(len(scripts))]


# =============================================================================
# sn.bin
# =============================================================================

def read_sn(path) -> List[bytes]:
    """Guiones de sn.bin (descomprimido y separado)."""
    data = decompress_yeti_lzss(Path(path).read_bytes())
    count = _u32(data, 0) // 16
    out = []
    for i in range(count):
        off, size = struct.unpack_from('<II', data, 16 * i)
        out.append(data[off:off + size])
    return out


def build_sn(scripts: List[bytes]) -> bytes:
    """Pak sin comprimir con los guiones (inverso de read_sn antes del LZSS)."""
    table = bytearray()
    body = bytearray()
    base = 16 * len(scripts)
    for s in scripts:
        table += struct.pack('<IIII', base + len(body), len(s), 0, 0)
        body += s
    return bytes(table + body)


def write_sn(scripts: List[bytes], path, cancel=None):
    data = compress_yeti_lzss(build_sn(scripts), cancel)
    with atomic_write(path) as f:
        f.write(data)


# =============================================================================
# Tabla de caracteres (fuente personalizada)
# =============================================================================

class CharMap:
    """
    Tabla de reemplazos de caracteres, p. ej. 'á=ｱ': al reconstruir, cada 'á'
    se escribe como 'ｱ', que la fuente personalizada dibuja como 'á'.
    """

    def __init__(self, pairs: Dict[str, str]):
        self.pairs = pairs
        self._fwd = self._pattern(pairs)
        self._rev_pairs = {v: k for k, v in pairs.items()}
        self._rev = self._pattern(self._rev_pairs)

    @staticmethod
    def _pattern(pairs):
        if not pairs:
            return None
        keys = sorted(pairs, key=len, reverse=True)
        return re.compile('|'.join(re.escape(k) for k in keys))

    @classmethod
    def load(cls, path) -> 'CharMap':
        pairs = {}
        for num, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            if '=' not in line[1:]:
                raise ValueError(f"Tabla de caracteres, línea {num}: se esperaba 'original=reemplazo'")
            # El primer carácter puede ser '=' (p. ej. '==X')
            idx = line.index('=', 1)
            src, dst = line[:idx], line[idx + 1:]
            if not src or not dst:
                raise ValueError(f"Tabla de caracteres, línea {num}: par vacío")
            try:
                dst.encode('cp932', errors='strict')
            except UnicodeEncodeError:
                raise ValueError(f"Tabla de caracteres, línea {num}: '{dst}' no existe en Shift-JIS")
            pairs[src] = dst
        return cls(pairs)

    def apply(self, text: str) -> Tuple[str, List[str]]:
        """Aplica la tabla. Devuelve (texto, caracteres reemplazados)."""
        if not self._fwd:
            return text, []
        used = []

        def sub(m):
            used.append(m.group(0))
            return self.pairs[m.group(0)]
        return self._fwd.sub(sub, text), used

    def reverse(self, text: str) -> str:
        if not self._rev:
            return text
        return self._rev.sub(lambda m: self._rev_pairs[m.group(0)], text)


# =============================================================================
# Archivo de textos (ID|ADDRESS|TEXT)
# =============================================================================

_UNESCAPE_RE = re.compile(r'\\(.)')
_UNESCAPES = {'n': '\n', 'r': '\r', '\\': '\\'}


def escape_text(text: str) -> str:
    return text.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')


def unescape_text(text: str) -> str:
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPES.get(m.group(1), m.group(0)), text)


def decode_text(raw: bytes) -> str:
    return raw.decode('cp932', errors='replace')


def collect_strings(script: Script) -> List[Tuple[int, int, str, str]]:
    """(id, dirección, tipo, texto) de cada texto traducible, en orden."""
    out = []
    for ins in script.instrs:
        for f in ins.fields:
            if f.kind in TEXT_KINDS:
                out.append((len(out), f.pos, f.kind, decode_text(f.value)))
    return out


def format_strings(strings, charmap: Optional[CharMap] = None, reverse: bool = False) -> str:
    """Una línea por texto: ID|ADDRESS|TEXT; los nombres y opciones llevan un comentario antes."""
    lines = []
    for sid, addr, kind, text in strings:
        if charmap and reverse:
            text = charmap.reverse(text)
        if kind != 'm':
            lines.append(f"# {TEXT_KINDS[kind]}\n")
        lines.append(f"{sid:04d}|0x{addr:08X}|{escape_text(text)}\n")
    return ''.join(lines)


def read_strings_file(path) -> Tuple[Dict[int, Tuple[int, str]], List[str]]:
    """Lee un archivo de textos. Devuelve ({dirección: (id, texto)}, avisos)."""
    result, warnings = {}, []
    for line_num, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line or line.startswith('#'):
            continue
        parts = line.split('|', 2)
        if len(parts) < 3:
            warnings.append(f"Línea {line_num} no válida: {line[:50]}")
            continue
        try:
            sid = int(parts[0])
            addr = int(parts[1], 16) if parts[1].lower().startswith('0x') else int(parts[1])
        except ValueError as e:
            warnings.append(f"Error en la línea {line_num}: {e}")
            continue
        result[addr] = (sid, unescape_text(parts[2]))
    return result, warnings


def decompile_text(script: Script) -> str:
    """Listado legible de un guion, con etiquetas en los destinos de salto."""
    labels = {f.value: f"label_{f.value:05X}" for ins in script.instrs for f in ins.fields
              if f.kind == 'J' and f.value}
    d = script.data
    lines = [f"# inicio del código: 0x{script.code_start:X}"]
    for ins in script.instrs:
        if ins.addr in labels:
            lines.append(f"{labels[ins.addr]}:")
        parts = []
        p = ins.addr + 1
        for f in ins.fields:
            if p < f.pos:
                parts.append(d[p:f.pos].hex(' '))
            if f.kind == 'J':
                parts.append(labels.get(f.value, '0'))
            else:
                parts.append('"' + escape_text(decode_text(f.value)).replace('"', '\\"') + '"')
            p = f.pos + f.size
        if p < ins.end:
            parts.append(d[p:ins.end].hex(' '))
        lines.append(f"  {ins.addr:05X}  op_{ins.op:02X} {' | '.join(parts)}")
    lines.append(f"# cola: {d[script.code_end:script.code_end + TRAILER].hex(' ')}")
    return '\n'.join(lines) + '\n'


# =============================================================================
# Reconstrucción
# =============================================================================

ST_GREW = 'creció'
ST_SHRANK = 'se acortó'
ST_CUT = 'cortado'
ST_PADDED = 'rellenado'
ST_UNSUPPORTED = 'caracteres no soportados'
ST_REPLACED = 'caracteres reemplazados'


@dataclass
class StringResult:
    script: str
    sid: int
    addr: int
    old_len: int
    new_len: int
    flags: List[str]
    detail: str = ''


@dataclass
class RebuildReport:
    mode: str
    scripts: int = 0
    total_strings: int = 0
    changed: int = 0
    entries: List[StringResult] = field(default_factory=list)
    relocated_refs: int = 0
    warnings: List[str] = field(default_factory=list)

    def count(self, flag: str) -> int:
        return sum(1 for e in self.entries if flag in e.flags)

    def summary(self) -> str:
        parts = [f"{self.changed} textos modificados de {self.total_strings} "
                 f"en {self.scripts} guiones"]
        for flag in (ST_GREW, ST_SHRANK, ST_CUT, ST_PADDED, ST_REPLACED, ST_UNSUPPORTED):
            n = self.count(flag)
            if n:
                parts.append(f"{n} {flag}")
        if self.mode == 'reloc':
            parts.append(f"{self.relocated_refs} saltos reubicados")
        return ', '.join(parts)


def _encodable(ch: str) -> bool:
    try:
        ch.encode('cp932', errors='strict')
        return True
    except UnicodeEncodeError:
        return False


def _truncate_cp932(text: str, max_bytes: int) -> bytes:
    """Codifica en cp932 cortando en un límite de carácter."""
    out = bytearray()
    for ch in text:
        b = ch.encode('cp932')
        if len(out) + len(b) > max_bytes:
            break
        out += b
    return bytes(out)


def _encode_replacement(script, sid, addr, old_raw: bytes, text: str, charmap,
                        pad_to: Optional[int]) -> Tuple[bytes, Optional[StringResult]]:
    """
    Codifica un texto nuevo. Devuelve (bytes con el nulo final, resultado para el
    informe o None si el texto no cambió). pad_to: tamaño fijo (con el nulo).
    """
    original = old_raw + b'\0'
    if text == decode_text(old_raw):
        return original, None

    flags, details = [], []
    if charmap:
        text, used = charmap.apply(text)
        if used:
            flags.append(ST_REPLACED)
            details.append("reemplazados: " + ''.join(sorted(set(used))))
    try:
        encoded = text.encode('cp932', errors='strict')
    except UnicodeEncodeError:
        bad = sorted({ch for ch in text if not _encodable(ch)})
        return original, StringResult(script, sid, addr, len(original), len(original), [ST_UNSUPPORTED],
                                      "no existen en Shift-JIS: " + ''.join(bad) +
                                      " (se conserva el original)")
    if b'\0' in encoded:
        return original, StringResult(script, sid, addr, len(original), len(original), [ST_UNSUPPORTED],
                                      "contiene un carácter nulo (se conserva el original)")

    if pad_to is not None:
        limit = pad_to - 1
        if len(encoded) > limit:
            encoded = _truncate_cp932(text, limit)
            flags.append(ST_CUT)
            details.append(f"máximo {limit} bytes, se perdió: «{text[len(encoded.decode('cp932')):]}»")
        if len(encoded) < limit:
            encoded += b' ' * (limit - len(encoded))
            flags.append(ST_PADDED)

    raw = encoded + b'\0'
    if len(raw) > len(original):
        flags.append(ST_GREW)
    elif len(raw) < len(original):
        flags.append(ST_SHRANK)
    return raw, StringResult(script, sid, addr, len(original), len(raw), flags, '; '.join(details))


def rebuild_script(script: Script, replacements: Dict[int, Tuple[int, str]], rep: RebuildReport,
                   name: str = '', charmap: Optional[CharMap] = None, fixed: bool = False) -> bytes:
    """
    Reconstruye un guion con los textos reemplazados (por dirección). Sin fixed, los textos
    ocupan lo que necesiten y se corrigen todos los destinos de salto; la cola se conserva
    y se vuelve a alinear a 16 bytes.
    """
    fixed = fixed or rep.mode == 'fixed'
    d = script.data
    out = bytearray(d[:script.code_start])
    addr_map: Dict[int, int] = {}
    patches = []        # (offset en out, campo)
    for ins in script.instrs:
        addr_map[ins.addr] = len(out)
        p = ins.addr
        for f in ins.fields:
            out += d[p:f.pos]
            if f.kind in TEXT_KINDS:
                rep.total_strings += 1
                if f.pos in replacements:
                    sid, text = replacements[f.pos]
                    raw, result = _encode_replacement(name, sid, f.pos, f.value, text, charmap,
                                                      f.size if fixed else None)
                    if result:
                        rep.entries.append(result)
                        if ST_UNSUPPORTED not in result.flags:
                            rep.changed += 1
                    out += raw
                else:
                    out += d[f.pos:f.pos + f.size]
            else:
                if f.kind == 'J' and f.value:
                    patches.append((len(out), f))
                out += d[f.pos:f.pos + f.size]
            p = f.pos + f.size
        out += d[p:ins.end]
    addr_map[script.code_end] = len(out)

    for offset, f in patches:
        if f.value not in addr_map:
            raise ValueError(f"{name}: un salto apunta a 0x{f.value:X}, que no es el inicio de una "
                             "instrucción. Usa el modo de tamaño fijo.")
        new = addr_map[f.value]
        if new != f.value:
            rep.relocated_refs += 1
        struct.pack_into('<I', out, offset, new)

    out += d[script.code_end:script.code_end + TRAILER]
    out += bytes(-len(out) % ALIGN)
    if fixed and len(out) != len(d):
        raise RuntimeError(f"{name}: error interno, el modo de tamaño fijo cambió el tamaño del guion")
    return bytes(out)


# =============================================================================
# sn.bin completo
# =============================================================================

def script_name(index: int) -> str:
    return f"{index:03d}"


def extract_strings(sn_path, out_dir, charmap: Optional[CharMap] = None, reverse: bool = False,
                    with_listing: bool = False, progress=None, cancel=None) -> Tuple[int, int]:
    """
    Guarda los textos de cada guion en <out_dir>/<NNN>.txt (solo los que tienen texto)
    y, con with_listing, el listado completo en <out_dir>/listado/<NNN>.txt.
    Devuelve (guiones con texto, textos).
    """
    out_dir = Path(out_dir)
    scripts = read_sn(sn_path)
    tails = trailers(scripts)
    n_files = n_strings = 0
    for i, data in enumerate(scripts):
        check_cancel(cancel)
        report(progress, i, len(scripts), script_name(i))
        try:
            script = parse(data, tails[i])
        except ValueError:
            continue                # la tabla de colas (352) no es un guion
        strings = collect_strings(script)
        if strings:
            with atomic_write(out_dir / f"{script_name(i)}.txt") as f:
                f.write(format_strings(strings, charmap, reverse).encode('utf-8'))
            n_files += 1
            n_strings += len(strings)
        if with_listing:
            with atomic_write(out_dir / 'listado' / f"{script_name(i)}.txt") as f:
                f.write(decompile_text(script).encode('utf-8'))
    report(progress, len(scripts), len(scripts))
    return n_files, n_strings


def rebuild(sn_path, strings_dir, out_path, charmap: Optional[CharMap] = None,
            mode: str = 'reloc', progress=None, cancel=None) -> RebuildReport:
    """
    Crea un sn.bin nuevo con los textos de <strings_dir>/<NNN>.txt. Los guiones sin archivo
    de textos se copian tal cual.
    """
    if mode not in ('reloc', 'fixed'):
        raise ValueError(f"Modo no válido: {mode}")
    strings_dir = Path(strings_dir)
    rep = RebuildReport(mode=mode)
    scripts = read_sn(sn_path)
    tails = trailers(scripts)
    new = []
    for i, data in enumerate(scripts):
        check_cancel(cancel)
        report(progress, i, len(scripts) + 1, script_name(i))
        path = strings_dir / f"{script_name(i)}.txt"
        if path.exists():
            replacements, warnings = read_strings_file(path)
            rep.warnings += [f"{script_name(i)}: {w}" for w in warnings]
            if i in FIXED_ONLY and mode == 'reloc':
                rep.warnings.append(f"{script_name(i)}: guion del menú de depuración, se reconstruye "
                                    "con tamaño fijo")
            data = rebuild_script(parse(data, tails[i]), replacements, rep, script_name(i), charmap,
                                  fixed=i in FIXED_ONLY)
            rep.scripts += 1
        new.append(data)
    report(progress, len(scripts), len(scripts) + 1, "Comprimiendo sn.bin…")
    write_sn(new, out_path, cancel)
    report(progress, len(scripts) + 1, len(scripts) + 1)
    return rep
