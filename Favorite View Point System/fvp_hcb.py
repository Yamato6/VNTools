"""
fvp_hcb - Scripts HCB (Favorite View Point System)

Estructura:
    u32 sysdesc_offset      (fin de la sección de código)
    código                  (bytecode de una VM de pila)
    sysdesc                 u32 entry_point (dirección de la función principal),
                            contadores de globales, título, tabla de syscalls...

Los textos están dentro del código (opcode 0x0E: 1 byte de longitud + texto
Shift-JIS terminado en nulo). Como call/jmp/jmpcond usan direcciones absolutas,
cambiar la longitud de un texto obliga a reubicar todas las referencias al
código: eso es lo que hace rebuild() en modo "reloc".
"""

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fvp_common import atomic_write, check_cancel, report

# =============================================================================
# Opcodes y definiciones de la VM
# =============================================================================

# Opcode argument types
OPARG_NULL = 0      # No arguments
OPARG_X32 = 1       # 32-bit offset/address
OPARG_I32 = 2       # 32-bit signed integer
OPARG_I16 = 3       # 16-bit signed integer
OPARG_I8 = 4        # 8-bit signed integer
OPARG_I8I8 = 5      # Two 8-bit integers
OPARG_STRING = 6    # Length-prefixed string

# HCB Opcode definitions: (opcode, name, arg_type)
HCB_OPCODES = [
    (0x00, "nop", OPARG_NULL),
    (0x01, "initstack", OPARG_I8I8),
    (0x02, "call", OPARG_X32),
    (0x03, "syscall", OPARG_I16),
    (0x04, "ret", OPARG_NULL),
    (0x05, "ret2", OPARG_NULL),
    (0x06, "jmp", OPARG_X32),
    (0x07, "jmpcond", OPARG_X32),
    (0x08, "pushtrue", OPARG_NULL),
    (0x09, "pushfalse", OPARG_NULL),
    (0x0A, "pushint", OPARG_I32),
    (0x0B, "pushint", OPARG_I16),
    (0x0C, "pushint", OPARG_I8),
    (0x0D, "pushfloat", OPARG_X32),
    (0x0E, "pushstring", OPARG_STRING),
    (0x0F, "pushglobal", OPARG_I16),
    (0x10, "pushstack", OPARG_I8),
    (0x11, "unk_11", OPARG_I16),
    (0x12, "unk_12", OPARG_I8),
    (0x13, "pushtop", OPARG_NULL),
    (0x14, "pushtemp", OPARG_NULL),
    (0x15, "popglobal", OPARG_I16),
    (0x16, "copystack", OPARG_I8),
    (0x17, "unk_17", OPARG_I16),
    (0x18, "unk_18", OPARG_I8),
    (0x19, "neg", OPARG_NULL),
    (0x1A, "add", OPARG_NULL),
    (0x1B, "sub", OPARG_NULL),
    (0x1C, "mul", OPARG_NULL),
    (0x1D, "div", OPARG_NULL),
    (0x1E, "mod", OPARG_NULL),
    (0x1F, "test", OPARG_NULL),
    (0x20, "logand", OPARG_NULL),
    (0x21, "logor", OPARG_NULL),
    (0x22, "eq", OPARG_NULL),
    (0x23, "neq", OPARG_NULL),
    (0x24, "gt", OPARG_NULL),
    (0x25, "le", OPARG_NULL),
    (0x26, "lt", OPARG_NULL),
    (0x27, "ge", OPARG_NULL),
]

HCB_LAST_OPCODE = 0x27

OP_INITSTACK = 0x01
OP_PUSHINT32 = 0x0A
OP_PUSHSTRING = 0x0E
JUMP_OPCODES = (0x02, 0x06, 0x07)      # call, jmp, jmpcond
RAW = -1                                # byte que no es un opcode válido

ARG_SIZES = {OPARG_NULL: 0, OPARG_X32: 4, OPARG_I32: 4, OPARG_I16: 2, OPARG_I8: 1, OPARG_I8I8: 2}

# Un texto: 1 byte de longitud que incluye el nulo final
MAX_STRING_BYTES = 254


def get_opcode_info(opcode: int) -> Tuple[str, int]:
    """Returns (name, arg_type) for an opcode."""
    if opcode > HCB_LAST_OPCODE:
        return (None, None)
    return (HCB_OPCODES[opcode][1], HCB_OPCODES[opcode][2])


def get_opcode_by_name(name: str) -> List[Tuple[int, int]]:
    """Returns list of (opcode, arg_type) matching the name."""
    return [(op, arg) for op, n, arg in HCB_OPCODES if n == name]


# =============================================================================
# Parser común
# =============================================================================

Instr = Tuple[int, int, bytes]      # (dirección, opcode o RAW, bytes del argumento)


def parse_code(data: bytes, progress=None, cancel=None) -> Tuple[int, List[Instr]]:
    """
    Recorre la sección de código. Devuelve (sysdesc_offset, instrucciones).
    Los argumentos de pushstring incluyen el byte de longitud.
    """
    if len(data) < 4:
        raise ValueError("Archivo demasiado pequeño para ser un HCB")
    code_end = struct.unpack_from('<I', data, 0)[0]
    if code_end > len(data):
        raise ValueError("Cabecera no válida: la sección de código supera el tamaño del archivo")

    sizes = [ARG_SIZES.get(t, 0) for _, _, t in HCB_OPCODES]
    instrs = []
    append = instrs.append
    pos = 4
    next_report = 0
    while pos < code_end:
        if pos >= next_report:
            check_cancel(cancel)
            report(progress, pos, code_end)
            next_report = pos + 0x40000
        op = data[pos]
        if op > HCB_LAST_OPCODE:
            append((pos, RAW, data[pos:pos + 1]))
            pos += 1
            continue
        if op == OP_PUSHSTRING:
            end = pos + 2 + data[pos + 1]
        else:
            end = pos + 1 + sizes[op]
        append((pos, op, data[pos + 1:end]))
        pos = end
    return code_end, instrs


def string_of(arg: bytes) -> bytes:
    """Bytes del texto de un pushstring (sin el byte de longitud)."""
    return arg[1:]


def decode_text(raw: bytes) -> str:
    return raw.decode('cp932', errors='replace').rstrip('\x00')


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


def format_strings(strings, charmap: Optional[CharMap] = None, reverse: bool = False) -> str:
    lines = []
    for sid, addr, text in strings:
        if charmap and reverse:
            text = charmap.reverse(text)
        lines.append(f"{sid:04d}|0x{addr:08X}|{escape_text(text)}\n")
    return ''.join(lines)


def read_strings_file(path) -> Tuple[Dict[int, Tuple[int, str]], List[str]]:
    """Lee un archivo de textos. Devuelve ({dirección: (id, texto)}, avisos)."""
    result, warnings = {}, []
    with open(path, 'r', encoding='utf-8-sig') as f:
        for line_num, line in enumerate(f, 1):
            line = line.rstrip('\n\r')
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


# =============================================================================
# Extracción y descompilación
# =============================================================================

def collect_strings(instrs: List[Instr]) -> List[Tuple[int, int, str]]:
    strings = []
    for addr, op, arg in instrs:
        if op == OP_PUSHSTRING:
            strings.append((len(strings), addr, decode_text(string_of(arg))))
    return strings


def extract_strings(hcb_path, output_path, charmap: Optional[CharMap] = None,
                    reverse: bool = False, progress=None, cancel=None) -> int:
    """Extrae solo los textos de un HCB para traducirlos."""
    data = Path(hcb_path).read_bytes()
    _, instrs = parse_code(data, progress, cancel)
    strings = collect_strings(instrs)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(format_strings(strings, charmap, reverse), encoding='utf-8')
    return len(strings)


def decompile(hcb_path, output_path, strings_path=None, charmap: Optional[CharMap] = None,
              reverse: bool = False, progress=None, cancel=None) -> Tuple[int, int]:
    """
    Descompila un HCB a texto legible. Opcionalmente exporta los textos.
    Devuelve (funciones, textos).
    """
    data = Path(hcb_path).read_bytes()
    code_end, instrs = parse_code(data, progress, cancel)

    # Primera pasada: inicios de función y destinos de salto
    functions: Dict[int, int] = {}
    labels: Dict[int, str] = {}
    for addr, op, arg in instrs:
        if op == OP_INITSTACK:
            functions[addr] = len(functions)
        elif op in JUMP_OPCODES and len(arg) == 4:
            target = struct.unpack('<I', arg)[0]
            if target < code_end and target not in labels and target not in functions:
                labels[target] = f"label_{target:08x}"

    # Segunda pasada: texto
    lines = []
    strings = []
    current_func = -1
    for addr, op, arg in instrs:
        if addr in functions:
            if current_func >= 0:
                lines.append("")
            current_func = functions[addr]
            lines.append(f"# ===== FUNCTION {current_func} =====")
        if addr in labels:
            lines.append(f"{labels[addr]}:")
        if op == RAW:
            continue

        name, arg_type = get_opcode_info(op)
        if arg_type == OPARG_NULL:
            lines.append(f"  {name}")
        elif arg_type == OPARG_X32:
            if len(arg) < 4:
                break
            val = struct.unpack('<I', arg)[0]
            if name in ("jmp", "jmpcond"):
                target = labels.get(val, functions.get(val))
                if isinstance(target, int):
                    lines.append(f"  {name} FUNCTION_{target}")
                elif target is not None:
                    lines.append(f"  {name} {target}")
                else:
                    lines.append(f"  {name} 0x{val:08X}")
            elif name == "call":
                func_id = functions.get(val)
                lines.append(f"  {name} FUNCTION_{func_id}" if func_id is not None
                             else f"  {name} 0x{val:08X}")
            elif name == "pushfloat":
                lines.append(f"  {name} {struct.unpack('<f', arg)[0]}")
            else:
                lines.append(f"  {name} 0x{val:08X}")
        elif arg_type == OPARG_I32:
            if len(arg) < 4:
                break
            lines.append(f"  {name} {struct.unpack('<i', arg)[0]}")
        elif arg_type == OPARG_I16:
            if len(arg) < 2:
                break
            lines.append(f"  {name} {struct.unpack('<h', arg)[0]}")
        elif arg_type == OPARG_I8:
            if len(arg) < 1:
                break
            lines.append(f"  {name} {struct.unpack('<b', arg)[0]}")
        elif arg_type == OPARG_I8I8:
            if len(arg) < 2:
                break
            v1, v2 = struct.unpack('<bb', arg)
            lines.append(f"  {name} {v1}, {v2}")
        elif arg_type == OPARG_STRING:
            text = decode_text(string_of(arg))
            shown = charmap.reverse(text) if charmap and reverse else text
            escaped = shown.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')
            lines.append(f'  {name} "{escaped}"  ; [STR_{len(strings):04d}]')
            strings.append((len(strings), addr, text))

    lines.append("")
    lines.append(f"# ENTRY_POINT: 0x{code_end:08X}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('\n'.join(lines), encoding='utf-8')

    if strings_path:
        Path(strings_path).write_text(format_strings(strings, charmap, reverse), encoding='utf-8')
    return len(functions), len(strings)


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
    sid: int
    addr: int
    old_len: int
    new_len: int
    flags: List[str]
    detail: str = ''


@dataclass
class RebuildReport:
    mode: str
    total_strings: int = 0
    changed: int = 0
    entries: List[StringResult] = field(default_factory=list)
    relocated_refs: int = 0
    function_pointers: int = 0
    entry_point_relocated: bool = False
    raw_bytes: int = 0
    unknown_addresses: int = 0
    warnings: List[str] = field(default_factory=list)
    original_size: int = 0
    new_size: int = 0

    def count(self, flag: str) -> int:
        return sum(1 for e in self.entries if flag in e.flags)

    def summary(self) -> str:
        parts = [f"{self.changed} textos modificados de {self.total_strings}"]
        for flag in (ST_GREW, ST_SHRANK, ST_CUT, ST_PADDED, ST_REPLACED, ST_UNSUPPORTED):
            n = self.count(flag)
            if n:
                parts.append(f"{n} {flag}")
        if self.mode == 'reloc':
            parts.append(f"{self.relocated_refs} referencias reubicadas")
            parts.append(f"{self.function_pointers} punteros a función")
        return ', '.join(parts)


def _truncate_cp932(text: str, max_bytes: int) -> bytes:
    """Codifica en cp932 cortando en un límite de carácter."""
    out = bytearray()
    for ch in text:
        b = ch.encode('cp932')
        if len(out) + len(b) > max_bytes:
            break
        out += b
    return bytes(out)


def _encode_replacement(sid, addr, old_raw: bytes, text: str, charmap, max_bytes: int,
                        pad_to: Optional[int]) -> Tuple[bytes, Optional[StringResult]]:
    """
    Codifica un texto nuevo. Devuelve (bytes del argumento con longitud y nulo,
    resultado para el informe o None si el texto no cambió).
    """
    old_text = decode_text(old_raw)
    if text == old_text:
        return bytes([len(old_raw)]) + old_raw, None
    if pad_to is not None and pad_to < 1:
        return bytes([len(old_raw)]) + old_raw, StringResult(
            sid, addr, 0, 0, [ST_CUT], "el original no tiene espacio (0 bytes); se conserva")

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
        result = StringResult(sid, addr, len(old_raw), len(old_raw), [ST_UNSUPPORTED],
                              "no existen en Shift-JIS: " + ''.join(bad) + " (se conserva el original)")
        return bytes([len(old_raw)]) + old_raw, result

    limit = max_bytes if pad_to is None else pad_to - 1
    if len(encoded) > limit:
        encoded = _truncate_cp932(text, limit)
        flags.append(ST_CUT)
        details.append(f"máximo {limit} bytes, se perdió: «{text[len(encoded.decode('cp932')):]}»")
    if pad_to is not None and len(encoded) < pad_to - 1:
        encoded += b' ' * (pad_to - 1 - len(encoded))
        flags.append(ST_PADDED)

    raw = encoded + b'\x00'
    if len(raw) > len(old_raw) and ST_CUT not in flags:
        flags.append(ST_GREW)
    elif len(raw) < len(old_raw):
        flags.append(ST_SHRANK)
    return bytes([len(raw)]) + raw, StringResult(sid, addr, len(old_raw), len(raw), flags,
                                                 '; '.join(details))


def _encodable(ch: str) -> bool:
    try:
        ch.encode('cp932', errors='strict')
        return True
    except UnicodeEncodeError:
        return False


def rebuild(original_hcb, strings_path, output_hcb, charmap: Optional[CharMap] = None,
            mode: str = 'reloc', progress=None, cancel=None) -> RebuildReport:
    """
    Reconstruye un HCB con los textos reemplazados.

    mode='reloc': los textos ocupan lo que necesiten (hasta 254 bytes) y se
                  reubican todas las referencias al código.
    mode='fixed': cada texto conserva su tamaño original (rellena o corta),
                  como la versión anterior de la herramienta.
    """
    if mode not in ('reloc', 'fixed'):
        raise ValueError(f"Modo no válido: {mode}")

    data = Path(original_hcb).read_bytes()
    replacements, warnings = read_strings_file(strings_path)
    rep = RebuildReport(mode=mode, warnings=warnings, original_size=len(data))

    def parse_progress(i, total, name):
        report(progress, i // 2, total, "Analizando…")
    code_end, instrs = parse_code(data, parse_progress, cancel)

    functions = {addr for addr, op, _ in instrs if op == OP_INITSTACK}
    rep.raw_bytes = sum(1 for _, op, _ in instrs if op == RAW)

    out = bytearray(4)
    addr_map: Dict[int, int] = {}
    patches: List[Tuple[int, int, bool]] = []      # (offset en out, destino antiguo, es salto)
    sid = -1
    total = len(instrs)
    step = max(1, total // 200)

    for i, (addr, op, arg) in enumerate(instrs):
        if i % step == 0:
            check_cancel(cancel)
            report(progress, total // 2 + i // 2, total, "Reconstruyendo…")
        addr_map[addr] = len(out)
        if op == RAW:
            out += arg
            continue
        out.append(op)
        if op == OP_PUSHSTRING:
            sid += 1
            rep.total_strings += 1
            old_raw = string_of(arg)
            if addr in replacements:
                new_arg, result = _encode_replacement(
                    sid, addr, old_raw, replacements[addr][1], charmap, MAX_STRING_BYTES,
                    len(old_raw) if mode == 'fixed' else None)
                if result:
                    result.sid = replacements[addr][0]
                    rep.entries.append(result)
                    if ST_UNSUPPORTED not in result.flags:
                        rep.changed += 1
                out += new_arg
            else:
                out += arg
        elif mode == 'reloc' and op in JUMP_OPCODES:
            patches.append((len(out), struct.unpack('<I', arg)[0], True))
            out += arg
        elif mode == 'reloc' and op == OP_PUSHINT32 and struct.unpack('<i', arg)[0] in functions:
            # Puntero a función (ThreadStart y similares)
            patches.append((len(out), struct.unpack('<I', arg)[0], False))
            out += arg
        else:
            out += arg

    new_code_end = len(out)
    addr_map[code_end] = new_code_end

    if mode == 'fixed' and new_code_end != code_end:
        raise RuntimeError("Error interno: el modo de tamaño fijo cambió el tamaño del código")

    if mode == 'reloc':
        for offset, target, is_jump in patches:
            new = addr_map.get(target)
            if new is None:
                if is_jump:
                    raise ValueError(
                        f"Un salto en 0x{offset:08X} apunta a 0x{target:08X}, que no es el inicio de "
                        "una instrucción. No se puede reubicar este script; usa el modo de tamaño fijo.")
                continue
            struct.pack_into('<I', out, offset, new)
            if is_jump:
                rep.relocated_refs += 1
            else:
                rep.function_pointers += 1

    struct.pack_into('<I', out, 0, new_code_end)
    sysdesc = bytearray(data[code_end:])

    # El primer u32 del sysdesc es la dirección de la función principal
    if mode == 'reloc' and len(sysdesc) >= 4:
        entry = struct.unpack_from('<I', sysdesc, 0)[0]
        if entry in functions:
            struct.pack_into('<I', sysdesc, 0, addr_map[entry])
            rep.entry_point_relocated = True
        else:
            rep.warnings.append(
                f"El primer valor del sysdesc (0x{entry:08X}) no apunta a una función; no se modificó. "
                "Si el juego no arranca, prueba el modo de tamaño fijo.")

    if rep.raw_bytes:
        rep.warnings.append(f"{rep.raw_bytes} bytes no reconocidos como opcodes se copiaron tal cual")

    out += sysdesc
    rep.new_size = len(out)
    check_cancel(cancel)
    with atomic_write(output_hcb) as f:
        f.write(out)
    report(progress, total, total)
    return rep


# =============================================================================
# Dividir / unir archivos de textos
# =============================================================================

def split_strings(strings_path) -> List[str]:
    """
    Divide un archivo de textos en partes según las etiquetas <part>.

    <part name="Capítulo 1" filename="cap1.txt">
    0001|0x00001234|Texto
    </part>

    Devuelve la lista de mensajes (una línea por parte).
    """
    strings_path = Path(strings_path)
    base_dir = strings_path.parent
    content = strings_path.read_text(encoding='utf-8-sig')

    part_pattern = re.compile(
        r'<part\s+(?:name="([^"]*)")?\s*(?:filename="([^"]*)")?\s*>(.*?)</part>',
        re.DOTALL | re.IGNORECASE
    )
    parts = part_pattern.findall(content)
    if not parts:
        raise ValueError(f"No hay etiquetas <part> en {strings_path.name}. Formato: "
                         '<part name="Nombre" filename="salida.txt">...textos...</part>')

    messages = []
    for name, filename, part_content in parts:
        if not filename:
            messages.append(f"[AVISO] La parte '{name}' no tiene filename, se omite")
            continue
        output_file = base_dir / filename
        output_file.parent.mkdir(parents=True, exist_ok=True)
        lines = [line.strip() for line in part_content.strip().split('\n') if line.strip()]
        output_file.write_text(f"# Part: {name}\n" + '\n'.join(lines) + '\n', encoding='utf-8')
        messages.append(f"Creado: {filename} ({len(lines)} líneas)")
    return messages


def merge_strings(build_script_path, output_path) -> Tuple[int, List[str]]:
    """
    Une varios archivos de textos según un script de build:
        <part filename="cap1.txt">      o una ruta por línea.
    Devuelve (textos unidos, mensajes).
    """
    build_script_path = Path(build_script_path)
    output_path = Path(output_path)
    base_dir = build_script_path.parent
    content = build_script_path.read_text(encoding='utf-8-sig')

    files_to_merge = re.findall(r'<part\s+filename="([^"]+)"', content, re.IGNORECASE)
    if not files_to_merge:
        for line in content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#') and not line.startswith('<'):
                files_to_merge.append(line)
    if not files_to_merge:
        raise ValueError(f"No hay archivos en el script de build {build_script_path.name}")

    all_strings, messages = [], []
    for filename in files_to_merge:
        file_path = base_dir / filename
        if not file_path.exists():
            messages.append(f"[AVISO] No existe: {filename}")
            continue
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    all_strings.append(line)
        messages.append(f"Unido: {filename}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"# Merged from {len(files_to_merge)} files\n" + '\n'.join(all_strings) + '\n',
                           encoding='utf-8')
    return len(all_strings), messages
