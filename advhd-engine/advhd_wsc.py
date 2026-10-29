"""
advhd_wsc - Guiones WSC del motor AdvHD (Rio.arc)

Cifrado: cada byte rotado 2 bits a la izquierda (se descifra rotando a la derecha).
Descifrado, el guion es una secuencia de instrucciones: 1 byte de opcode y sus
argumentos (little-endian), terminada por 0xFF y una cola de 9 bytes.

Textos (cp932, terminados en nulo):
    0x41 mensaje            u16 id, u8, u8, texto
    0x42 mensaje con nombre u16 id, u8, u8, u8, nombre, texto
    0x02 menú de opciones   u16 n, n x (u16 id, texto, 11 bytes)
    0xE0 título de escena

Saltos que hay que reubicar si un texto cambia de tamaño:
    0x06 salto absoluto     u32 destino, u8
    0x01 si (var cmp val)   u8, u16, u16, u32 bytes del bloque que se salta, u8

La tabla de opcodes se dedujo de los 324 guiones de CROSS†CHANNEL (edición WillPlus):
todos se recorren completos y cada salto cae al inicio de una instrucción.
"""

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from advhd_arc import ArcEntry, read_entry, read_index, write_arc
from advhd_common import atomic_write, check_cancel, report

# Formato de los argumentos:
#   B, H, I  enteros de 1, 2 y 4 bytes      s  cadena (nombres de archivo, no se traduce)
#   m  mensaje   n  nombre   t  título      (cadenas traducibles)
#   J  u32 destino absoluto                  K  u32 bytes del bloque que sigue
#   C  menú de opciones                      E  fin del guion (el resto es la cola)
OPCODES = {
    0x01: 'BHHKB', 0x02: 'C', 0x03: 'BHI', 0x04: '', 0x06: 'JB', 0x07: 's', 0x09: 's',
    0x0A: 'B', 0x0B: 'HBH', 0x0D: 'HBI',
    0x21: 'HBBHIs', 0x22: 'BHB', 0x23: 'BHIBBs', 0x25: 'BBBHHIs', 0x26: 'H',
    0x41: 'HBBm', 0x42: 'HBBBnm', 0x43: 'HIs', 0x45: 'BHB', 0x46: 'BIIs', 0x47: 'H',
    0x48: 'BIIHs', 0x49: 'BH', 0x4A: 'BHHB', 0x4D: 'BHIIH', 0x4F: 'BHB',
    0x50: 's', 0x51: 'HHB', 0x52: 'H', 0x54: 's', 0x55: 'B',
    0x61: 'Bs', 0x64: 'BHHHB', 0x65: 'HHB', 0x66: 'BHHHBHIII', 0x67: 'HHI', 0x68: 'HHHHB',
    0x69: 'H', 0x72: 'HI', 0x74: 'H',
    0x82: 'HB', 0x83: 'B', 0x85: 'H', 0x89: 'B', 0x8B: 'B', 0x8C: 'HB', 0x8D: 'B', 0x8E: 'B',
    0xA1: 'BHI', 0xAB: 'B', 0xB2: 'Hs', 0xB3: 'H', 0xB4: 'IIHH', 0xB8: 'BH', 0xB9: 'BH',
    0xBD: 'H', 0xE0: 't', 0xE2: 'B', 0xE4: 'H',
    0xFF: 'E',
}
OP_IF = 0x01
OP_JUMP = 0x06
CHOICE_TAIL = 11
TEXT_KINDS = {'m': 'mensaje', 'n': 'nombre', 't': 'título', 'o': 'opción'}


def decrypt(data: bytes) -> bytes:
    return bytes(((b >> 2) | (b << 6)) & 0xFF for b in data)


def encrypt(data: bytes) -> bytes:
    return bytes(((b << 2) | (b >> 6)) & 0xFF for b in data)


@dataclass
class Field:
    kind: str           # letra del formato ('o' = texto de una opción)
    pos: int
    size: int
    value: object       # int o bytes (cadenas, sin el nulo)


@dataclass
class Instr:
    addr: int
    op: int
    end: int
    fields: List[Field]


def parse(data: bytes) -> List[Instr]:
    """Recorre un guion descifrado. Error si hay un opcode desconocido o se sale del archivo."""
    instrs = []
    p = 0
    n = len(data)
    while p < n:
        op = data[p]
        fmt = OPCODES.get(op)
        if fmt is None:
            raise ValueError(f"Opcode desconocido 0x{op:02X} en 0x{p:X}")
        q = p + 1
        fields = []

        def string(kind):
            nonlocal q
            end = data.index(b'\0', q)
            fields.append(Field(kind, q, end + 1 - q, data[q:end]))
            q = end + 1

        for c in fmt:
            if c == 'B':
                fields.append(Field(c, q, 1, data[q]))
                q += 1
            elif c == 'H':
                fields.append(Field(c, q, 2, struct.unpack_from('<H', data, q)[0]))
                q += 2
            elif c in 'IJK':
                fields.append(Field(c, q, 4, struct.unpack_from('<I', data, q)[0]))
                q += 4
            elif c in 'smnt':
                string(c)
            elif c == 'C':
                (count,) = struct.unpack_from('<H', data, q)
                fields.append(Field('H', q, 2, count))
                q += 2
                for _ in range(count):
                    fields.append(Field('H', q, 2, struct.unpack_from('<H', data, q)[0]))
                    q += 2
                    string('o')
                    q += CHOICE_TAIL
            elif c == 'E':
                q = n
        if q > n:
            raise ValueError(f"Instrucción 0x{op:02X} en 0x{p:X} se sale del archivo")
        instrs.append(Instr(p, op, q, fields))
        p = q
    if not instrs or instrs[-1].op != 0xFF:
        raise ValueError("El guion no termina en 0xFF")
    return instrs


def jump_target(ins: Instr, f: Field) -> int:
    """Dirección a la que lleva un campo J (absoluto) o K (fin del bloque)."""
    return f.value if f.kind == 'J' else ins.end + f.value


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


def collect_strings(instrs: List[Instr]) -> List[Tuple[int, int, str, str]]:
    """(id, dirección, tipo, texto) de cada texto traducible, en orden."""
    out = []
    for ins in instrs:
        for f in ins.fields:
            if f.kind in TEXT_KINDS:
                out.append((len(out), f.pos, f.kind, decode_text(f.value)))
    return out


def format_strings(strings, charmap: Optional[CharMap] = None, reverse: bool = False) -> str:
    """
    Una línea por texto: ID|ADDRESS|TEXT. Antes de cada nombre de personaje va un
    comentario '# nombre' para distinguirlo del mensaje que lo sigue.
    """
    lines = []
    for sid, addr, kind, text in strings:
        if charmap and reverse:
            text = charmap.reverse(text)
        if kind != 'm':
            lines.append(f"# {TEXT_KINDS[kind]}\n")
        lines.append(f"{sid:04d}|0x{addr:08X}|{escape_text(text)}\n")
    return ''.join(lines)


def parse_strings(text: str) -> Tuple[Dict[int, Tuple[int, str]], List[str]]:
    """Lee un archivo de textos. Devuelve ({dirección: (id, texto)}, avisos)."""
    result, warnings = {}, []
    for line_num, line in enumerate(text.splitlines(), 1):
        if not line or line.startswith('#') or line.lstrip().startswith('<'):
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


def read_strings_file(path) -> Tuple[Dict[int, Tuple[int, str]], List[str]]:
    return parse_strings(Path(path).read_text(encoding='utf-8-sig'))


# =============================================================================
# Descompilación
# =============================================================================

def decompile_text(data: bytes, charmap: Optional[CharMap] = None, reverse: bool = False) -> str:
    """Listado legible de un guion descifrado, con etiquetas en los destinos de salto."""
    instrs = parse(data)
    labels = {}
    for ins in instrs:
        for f in ins.fields:
            if f.kind in 'JK':
                labels.setdefault(jump_target(ins, f), f"label_{jump_target(ins, f):05X}")
    lines = []
    for ins in instrs:
        if ins.addr in labels:
            lines.append(f"{labels[ins.addr]}:")
        if ins.op == 0xFF:
            lines.append(f"  {ins.addr:05X}  end  ; cola: {data[ins.addr + 1:].hex(' ')}")
            break
        args = []
        for f in ins.fields:
            if f.kind in 'JK':
                args.append(labels[jump_target(ins, f)])
            elif isinstance(f.value, bytes):
                text = decode_text(f.value)
                if charmap and reverse and f.kind in TEXT_KINDS:
                    text = charmap.reverse(text)
                args.append('"' + escape_text(text).replace('"', '\\"') + '"')
            else:
                args.append(str(f.value))
        name = {OP_IF: 'if', OP_JUMP: 'jmp', 0x41: 'msg', 0x42: 'msgname', 0x02: 'choice',
                0xE0: 'title'}.get(ins.op, f"op_{ins.op:02X}")
        lines.append(f"  {ins.addr:05X}  {name} {', '.join(args)}")
    return '\n'.join(lines) + '\n'


# =============================================================================
# Reconstrucción
# =============================================================================

# Estados de un texto en el informe
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


def rebuild_script(data: bytes, replacements: Dict[int, Tuple[int, str]], rep: RebuildReport,
                   script: str = '', charmap: Optional[CharMap] = None) -> bytes:
    """
    Reconstruye un guion descifrado con los textos reemplazados (por dirección).
    En modo 'reloc' los textos ocupan lo que necesiten y se corrigen los saltos;
    en 'fixed' cada texto conserva su tamaño (se rellena con espacios o se corta).
    """
    instrs = parse(data)
    out = bytearray()
    addr_map: Dict[int, int] = {}
    patches = []        # (offset en out, instrucción, campo)
    for ins in instrs:
        addr_map[ins.addr] = len(out)
        p = ins.addr
        for f in ins.fields:
            if f.kind in TEXT_KINDS:
                out += data[p:f.pos]
                rep.total_strings += 1
                if f.pos in replacements:
                    sid, text = replacements[f.pos]
                    raw, result = _encode_replacement(
                        script, sid, f.pos, f.value, text, charmap,
                        f.size if rep.mode == 'fixed' else None)
                    if result:
                        rep.entries.append(result)
                        if ST_UNSUPPORTED not in result.flags:
                            rep.changed += 1
                    out += raw
                else:
                    out += data[f.pos:f.pos + f.size]
                p = f.pos + f.size
            elif f.kind in 'JK':
                out += data[p:f.pos]
                patches.append((len(out), ins, f))
                out += data[f.pos:f.pos + 4]
                p = f.pos + 4
        out += data[p:ins.end]
    addr_map[len(data)] = len(out)

    if rep.mode == 'fixed' and len(out) != len(data):
        raise RuntimeError("Error interno: el modo de tamaño fijo cambió el tamaño del guion")

    for offset, ins, f in patches:
        target = jump_target(ins, f)
        if target not in addr_map:
            raise ValueError(f"{script}: un salto en 0x{ins.addr:X} apunta a 0x{target:X}, que no es "
                             "el inicio de una instrucción. Usa el modo de tamaño fijo.")
        new = addr_map[target] if f.kind == 'J' else addr_map[target] - addr_map[ins.end]
        if new != f.value:
            rep.relocated_refs += 1
        struct.pack_into('<I', out, offset, new)
    return bytes(out)


# =============================================================================
# Rio.arc completo
# =============================================================================

def read_scripts(arc_path) -> List[Tuple[ArcEntry, bytes]]:
    """(entrada, guion descifrado) de cada WSC de un .arc."""
    entries = [e for e in read_index(arc_path) if e.ext.upper() == 'WSC']
    with open(arc_path, 'rb') as f:
        return [(e, decrypt(read_entry(f, e))) for e in entries]


def extract_strings(arc_path, out_dir, charmap: Optional[CharMap] = None, reverse: bool = False,
                    with_listing: bool = False, progress=None, cancel=None) -> Tuple[int, int]:
    """
    Guarda los textos de cada guion en <out_dir>/<GUION>.txt (solo los que tienen texto)
    y, con with_listing, el listado completo en <out_dir>/listado/<GUION>.txt.
    Devuelve (guiones con texto, textos).
    """
    out_dir = Path(out_dir)
    scripts = read_scripts(arc_path)
    n_files = n_strings = 0
    for i, (e, data) in enumerate(scripts):
        check_cancel(cancel)
        report(progress, i, len(scripts), e.filename)
        strings = collect_strings(parse(data))
        if strings:
            with atomic_write(out_dir / f"{e.name}.txt") as f:
                f.write(format_strings(strings, charmap, reverse).encode('utf-8'))
            n_files += 1
            n_strings += len(strings)
        if with_listing:
            with atomic_write(out_dir / 'listado' / f"{e.name}.txt") as f:
                f.write(decompile_text(data, charmap, reverse).encode('utf-8'))
    report(progress, len(scripts), len(scripts))
    return n_files, n_strings


def rebuild(arc_path, strings_dir, out_arc, charmap: Optional[CharMap] = None,
            mode: str = 'reloc', progress=None, cancel=None) -> RebuildReport:
    """
    Crea out_arc a partir de arc_path (Rio.arc) con los textos de <strings_dir>/<GUION>.txt.
    Los guiones sin archivo de textos se copian tal cual.
    """
    if mode not in ('reloc', 'fixed'):
        raise ValueError(f"Modo no válido: {mode}")
    strings_dir = Path(strings_dir)
    rep = RebuildReport(mode=mode)
    scripts = read_scripts(arc_path)
    items = []
    for i, (e, data) in enumerate(scripts):
        check_cancel(cancel)
        report(progress, i, len(scripts), e.filename)
        path = strings_dir / f"{e.name}.txt"
        if path.exists():
            replacements, warnings = read_strings_file(path)
            rep.warnings += [f"{e.name}: {w}" for w in warnings]
            data = rebuild_script(data, replacements, rep, e.name, charmap)
            rep.scripts += 1
        items.append((e.name, e.ext, encrypt(data)))
    write_arc(out_arc, items, cancel=cancel)
    report(progress, len(scripts), len(scripts))
    return rep
