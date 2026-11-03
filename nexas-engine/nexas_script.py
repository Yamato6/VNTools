"""
nexas_script - Guiones .bin y tablas .dat del motor NeXAS: extraer y reinsertar texto

Guion .bin (little-endian):
    u32 n1;  n1 × (u32 opcode, u32 arg)     código 1 (inicialización)
    u32 n2;  n2 × (u32 opcode, u32 arg)     código 2 (el guion)
    u32 ns;  ns × cadena Shift-JIS + \\0     tabla de cadenas
    resto: tabla de símbolos y otras tablas (se copia tal cual)

El código usa las cadenas por su índice en la tabla, nunca por su posición en bytes:
los textos traducidos pueden tener cualquier longitud y el código no cambia.

Máquina de pila: 'push k' (op 0) seguido de 'arg 1' (op 5, arg = 1) pasa la cadena k
a la siguiente llamada 'call' (op 7, arg = argc << 16 | función). El tipo de cada
cadena se deduce de la función que la recibe (comprobado con los 264 guiones del juego
y la traducción al inglés):
    0x1AA arg 0, 0x5D arg 3, 0x5E arg 0, 0x1AB arg 0   mensaje
    0x1A9 arg 0                                        nombre del personaje
    0x56 arg 1                                         opción
    0x6A, 0x1D, 0x62, 0x0F, 0x11                       avisos del sistema
"""

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nexas_common import atomic_write, check_cancel, report, safe_name
from nexas_formats import DAT_STRING, read_dat, write_dat
from nexas_pac import Pac, write_pac

OP_PUSH, OP_ARG, OP_CALL = 0, 5, 7
JUMP_OPS = {63, 64, 65}

# (función, posición del argumento) -> tipo de texto
TEXT_ARGS = {
    (0x1A9, 0): 'name',
    (0x56, 1): 'choice',
    (0x1AA, 0): 'message', (0x5D, 3): 'message', (0x5E, 0): 'message', (0x1AB, 0): 'message',
    (0x6A, 0): 'system', (0x1D, 1): 'system', (0x62, 0): 'system', (0x0F, 0): 'system',
    (0x11, 0): 'system',
}
KIND_PRIORITY = ('name', 'choice', 'message', 'system')
FUNC_NAMES = {0x1A9: 'name', 0x1AA: 'message', 0x1AB: 'message_log', 0x5D: 'message_window',
              0x5E: 'message_add', 0x56: 'choice', 0x18B: 'sound'}

_ARG_STRING = struct.pack('<II', OP_ARG, 1)
_VOICE_RE = re.compile(r'^@v([0-9A-Za-z_]+)')

# =============================================================================
# Guion
# =============================================================================


@dataclass
class Script:
    code1: bytes                # instrucciones del código 1 (sin el contador)
    code2: bytes
    strings: List[bytes]        # sin el nulo final
    rest: bytes                 # tabla de símbolos y demás, sin cambios

    @property
    def n1(self) -> int:
        return len(self.code1) // 8

    @property
    def n2(self) -> int:
        return len(self.code2) // 8


def parse_script(data: bytes) -> Script:
    def count_at(pos):
        if pos + 4 > len(data):
            raise ValueError("Truncated script")
        return struct.unpack_from('<I', data, pos)[0]

    pos = 0
    n1 = count_at(pos)
    code1 = data[pos + 4:pos + 4 + 8 * n1]
    pos += 4 + 8 * n1
    n2 = count_at(pos)
    code2 = data[pos + 4:pos + 4 + 8 * n2]
    pos += 4 + 8 * n2
    ns = count_at(pos)
    pos += 4
    if len(code1) != 8 * n1 or len(code2) != 8 * n2 or ns > len(data):
        raise ValueError("Not a NeXAS script")
    strings = []
    for _ in range(ns):
        end = data.find(b'\0', pos)
        if end < 0:
            raise ValueError("Truncated string table")
        strings.append(data[pos:end])
        pos = end + 1
    return Script(code1, code2, strings, data[pos:])


def build_script(s: Script) -> bytes:
    out = bytearray(struct.pack('<I', s.n1))
    out += s.code1
    out += struct.pack('<I', s.n2)
    out += s.code2
    out += struct.pack('<I', len(s.strings))
    for raw in s.strings:
        out += raw + b'\0'
    out += s.rest
    return bytes(out)


def string_uses(code: bytes, n_strings: int) -> List[Tuple[int, int, int, int]]:
    """
    (instrucción, índice de cadena, función, posición del argumento) de cada cadena
    pasada a una llamada ('push k; arg 1; …; call'). Función -1 si no hay llamada.
    """
    out = []
    n = len(code) // 8
    pos = code.find(_ARG_STRING)
    while pos >= 0:
        if pos % 8 == 0 and pos >= 8:
            op, k = struct.unpack_from('<II', code, pos - 8)
            if op == OP_PUSH and k < n_strings:
                i = pos // 8 + 1
                later_args = 0
                fid = argpos = -1
                while i < n:
                    o, a = struct.unpack_from('<II', code, i * 8)
                    if o == OP_CALL:
                        fid, argc = a & 0xFFFF, a >> 16
                        argpos = argc - 1 - later_args
                        break
                    if o == OP_ARG:
                        later_args += 1
                    i += 1
                out.append((pos // 8 - 1, k, fid, argpos))
        pos = code.find(_ARG_STRING, pos + 1)
    return out


def classify(s: Script) -> Dict[int, str]:
    """{índice de cadena: tipo} de las cadenas que el guion muestra como texto."""
    kinds: Dict[int, str] = {}
    for code in (s.code1, s.code2):
        for _i, k, fid, argpos in string_uses(code, len(s.strings)):
            kind = TEXT_ARGS.get((fid, argpos))
            if kind and s.strings[k]:
                old = kinds.get(k)
                if old is None or KIND_PRIORITY.index(kind) < KIND_PRIORITY.index(old):
                    kinds[k] = kind
    return kinds


def voice_of(text: str) -> Optional[str]:
    m = _VOICE_RE.match(text)
    return f"{m.group(1).lower()}.ogg" if m else None


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
                raise ValueError(f"Character table, line {num}: expected 'original=replacement'")
            idx = line.index('=', 1)
            src, dst = line[:idx], line[idx + 1:]
            if not src or not dst:
                raise ValueError(f"Character table, line {num}: empty pair")
            try:
                dst.encode('cp932', errors='strict')
            except UnicodeEncodeError:
                raise ValueError(f"Character table, line {num}: '{dst}' does not exist in Shift-JIS")
            pairs[src] = dst
        return cls(pairs)

    def apply(self, text: str) -> Tuple[str, List[str]]:
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
# Archivos de texto
# =============================================================================

_UNESCAPE_RE = re.compile(r'\\(.)')
_UNESCAPES = {'n': '\n', 'r': '\r', '\\': '\\'}


def escape_text(text: str) -> str:
    return text.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')


def unescape_text(text: str) -> str:
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPES.get(m.group(1), m.group(0)), text)


def decode_text(raw: bytes) -> str:
    return raw.decode('cp932', errors='replace')


def text_file_name(entry_name: str) -> str:
    return safe_name(entry_name) + '.txt'


def parse_text_file(path) -> Tuple[Dict[str, str], List[str]]:
    """Lee un archivo de textos. Devuelve ({clave: texto}, avisos). Clave: '0012' o '0003:05'."""
    result, warnings = {}, []
    for num, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line or line.startswith('#'):
            continue
        key, sep, text = line.partition('|')
        key = key.strip()
        if not sep or not re.fullmatch(r'\d+(:\d+)?', key):
            warnings.append(f"invalid line {num}: {line[:50]}")
            continue
        result[_norm_key(key)] = unescape_text(text)
    return result, warnings


def _norm_key(key: str) -> str:
    return ':'.join(str(int(p)) for p in key.split(':'))


# --- Tablas .dat -------------------------------------------------------------

_NOT_TEXT_RE = re.compile(
    r'^(?:[\w\-. \\/]+\.(?:png|bmp|jpg|bin|dat|spm|ogg|nmv|mpg|exe|pac|txt|fil)'
    r'|[A-Za-z0-9_\-]+|[\d.]+)$', re.IGNORECASE)


def dat_text_cells(table) -> List[Tuple[int, int, str]]:
    """(fila, columna, texto) de las celdas de texto que parecen traducibles."""
    out = []
    for r, row in enumerate(table.rows):
        for c, (t, v) in enumerate(zip(table.types, row)):
            if t == DAT_STRING and v and not _NOT_TEXT_RE.match(v):
                out.append((r, c, v))
    return out


# =============================================================================
# Extracción
# =============================================================================

@dataclass
class ExtractResult:
    files: int = 0
    lines: int = 0
    warnings: List[str] = field(default_factory=list)


def _script_entries(pac: Pac):
    return [e for e in pac.entries if e.ext in ('bin', 'dat')]


def extract_text(pac_path, out_dir, reference=None, charmap: Optional[CharMap] = None,
                 reverse: bool = False, with_listing: bool = False, with_json: bool = False,
                 progress=None, cancel=None) -> ExtractResult:
    """
    Escribe un archivo de textos por guion (.bin) o tabla (.dat) del .pac:
    <out_dir>/<entrada>.txt con líneas 'ÍNDICE|TEXTO' (guiones) o 'FILA:COL|TEXTO' (tablas).
    reference: otro .pac con los mismos guiones (p. ej. la traducción inglesa); su texto se
    añade como comentario '#> …' antes de cada línea.
    """
    out_dir = Path(out_dir)
    res = ExtractResult()
    ref_pac = Pac(reference) if reference else None
    try:
        with Pac(pac_path) as pac:
            ref_entries = {e.name.lower(): e for e in ref_pac.entries} if ref_pac else {}
            entries = _script_entries(pac)
            if not entries:
                raise ValueError("This .pac has no scripts (.bin) or tables (.dat)")
            total = len(entries)

            # Primera pasada: textos y tipos de todos los guiones
            parsed = []
            known: Dict[bytes, str] = {}
            for i, e in enumerate(entries):
                check_cancel(cancel)
                report(progress, i, total * 2, e.name)
                data = pac.read(e)
                if e.ext == 'bin':
                    try:
                        s = parse_script(data)
                    except ValueError as ex:
                        res.warnings.append(f"{e.name}: {ex}")
                        continue
                    kinds = classify(s)
                    for k, kind in kinds.items():
                        known.setdefault(s.strings[k], kind)
                    parsed.append((e, s, kinds))
                else:
                    try:
                        parsed.append((e, read_dat(data), None))
                    except ValueError as ex:
                        res.warnings.append(f"{e.name}: {ex}")

            for i, (e, obj, kinds) in enumerate(parsed):
                check_cancel(cancel)
                report(progress, total + i, total * 2, e.name)
                ref_obj = None
                ref_e = ref_entries.get(e.name.lower())
                if ref_e is not None:
                    try:
                        ref_data = ref_pac.read(ref_e)
                        ref_obj = parse_script(ref_data) if kinds is not None else read_dat(ref_data)
                    except ValueError:
                        ref_obj = None

                if kinds is not None:
                    lines, records = _script_text(e.name, obj, kinds, known, ref_obj, charmap, reverse, res)
                    if with_listing:
                        with atomic_write(out_dir / 'listing' / text_file_name(e.name)) as f:
                            f.write(disassemble(obj, kinds).encode('utf-8'))
                else:
                    lines, records = _dat_text(obj, ref_obj, charmap, reverse)
                if not records:
                    continue
                with atomic_write(out_dir / text_file_name(e.name)) as f:
                    f.write(''.join(lines).encode('utf-8'))
                if with_json:
                    with atomic_write(out_dir / 'json' / (safe_name(e.name) + '.json')) as f:
                        f.write(json.dumps(records, ensure_ascii=False, indent=1).encode('utf-8'))
                res.files += 1
                res.lines += len(records)
    finally:
        if ref_pac:
            ref_pac.close()
    report(progress, 1, 1)
    return res


def _script_text(name, s: Script, kinds, known, ref: Optional[Script], charmap, reverse, res):
    if ref is not None and len(ref.strings) != len(s.strings):
        res.warnings.append(f"{name}: the reference has {len(ref.strings)} strings and the "
                            f"script {len(s.strings)}; reference not used")
        ref = None
    lines, records = [], []
    for k, raw in enumerate(s.strings):
        kind = kinds.get(k) or (known.get(raw) if raw else None)
        if not kind:
            continue
        text = decode_text(raw)
        if charmap and reverse:
            text = charmap.reverse(text)
        rec = {'index': k, 'kind': kind, 'text': text}
        if ref is not None:
            rec['reference'] = decode_text(ref.strings[k])
            lines.append(f"#> {escape_text(rec['reference'])}\n")
        voice = voice_of(text)
        if voice:
            rec['voice'] = voice
        if kind != 'message':
            lines.append(f"# {kind}\n")
        lines.append(f"{k:04d}|{escape_text(text)}\n")
        records.append(rec)
    return lines, records


def _dat_text(table, ref, charmap, reverse):
    lines, records = [], []
    for r, c, text in dat_text_cells(table):
        if charmap and reverse:
            text = charmap.reverse(text)
        rec = {'row': r, 'column': c, 'text': text}
        if ref is not None and r < len(ref.rows) and c < len(ref.types) and ref.types[c] == DAT_STRING:
            rec['reference'] = ref.rows[r][c]
            lines.append(f"#> {escape_text(rec['reference'])}\n")
        lines.append(f"{r:04d}:{c:02d}|{escape_text(text)}\n")
        records.append(rec)
    return lines, records


# =============================================================================
# Listado
# =============================================================================

def disassemble(s: Script, kinds: Optional[Dict[int, str]] = None) -> str:
    """Listado legible: una instrucción por línea, con las cadenas y los destinos de salto."""
    lines = [f"; strings: {len(s.strings)}, code 1: {s.n1} instructions, code 2: {s.n2} instructions"]
    for title, code in (("code 1", s.code1), ("code 2", s.code2)):
        n = len(code) // 8
        ins = list(struct.iter_unpack('<II', code))
        targets = {a for o, a in ins if o in JUMP_OPS and a < n} if title == "code 2" else set()
        lines.append(f"\n[{title}]")
        for i, (o, a) in enumerate(ins):
            if i in targets:
                lines.append(f"L_{i:06d}:")
            if o == OP_PUSH and i + 1 < n and ins[i + 1] == (OP_ARG, 1) and a < len(s.strings):
                text = escape_text(decode_text(s.strings[a])).replace('"', '\\"')
                tag = f"  ; {kinds[a]}" if kinds and a in kinds else ''
                lines.append(f"  {i:06d}  push str[{a}] \"{text}\"{tag}")
            elif o == OP_PUSH:
                lines.append(f"  {i:06d}  push {a if a < 0x80000000 else a - 0x100000000}")
            elif o == OP_ARG:
                lines.append(f"  {i:06d}  arg {'str' if a == 1 else a}")
            elif o == OP_CALL:
                fid, argc = a & 0xFFFF, a >> 16
                name = FUNC_NAMES.get(fid)
                lines.append(f"  {i:06d}  call 0x{fid:X}/{argc}" + (f"  ; {name}" if name else ''))
            elif o in JUMP_OPS and title == "code 2" and a < n:
                lines.append(f"  {i:06d}  op{o} L_{a:06d}")
            else:
                lines.append(f"  {i:06d}  op{o} 0x{a:X}")
    return '\n'.join(lines) + '\n'


# =============================================================================
# Reconstrucción
# =============================================================================

ST_UNSUPPORTED = 'unsupported characters'
ST_REPLACED = 'replaced characters'


@dataclass
class LineResult:
    entry: str
    key: str
    flags: List[str]
    detail: str = ''


@dataclass
class RebuildReport:
    files: int = 0              # archivos de texto usados
    changed_entries: List[str] = field(default_factory=list)
    lines: int = 0              # líneas leídas
    changed: int = 0            # textos cambiados
    entries: List[LineResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    output_entries: int = 0     # entradas escritas (0: no se escribió ningún archivo)

    def count(self, flag: str) -> int:
        return sum(1 for e in self.entries if flag in e.flags)

    def summary(self) -> str:
        if not self.changed_entries:
            return f"No text changed ({self.files} text files read): no file was written"
        parts = [f"{self.changed} texts changed in {len(self.changed_entries)} files "
                 f"({self.files} text files read)"]
        for flag in (ST_REPLACED, ST_UNSUPPORTED):
            n = self.count(flag)
            if n:
                parts.append(f"{n} with {flag}")
        return ', '.join(parts)


def _encodable(ch: str) -> bool:
    try:
        ch.encode('cp932', errors='strict')
        return True
    except UnicodeEncodeError:
        return False


def encode_line(entry: str, key: str, old_raw: bytes, text: str, charmap: Optional[CharMap],
                rep: RebuildReport) -> bytes:
    """Codifica un texto nuevo; si no se puede, devuelve el original y lo anota."""
    if text == decode_text(old_raw):
        return old_raw
    flags, details = [], []
    if charmap:
        text, used = charmap.apply(text)
        if used:
            flags.append(ST_REPLACED)
            details.append("replaced: " + ''.join(sorted(set(used))))
    try:
        encoded = text.encode('cp932', errors='strict')
    except UnicodeEncodeError:
        bad = ''.join(sorted({ch for ch in text if not _encodable(ch)}))
        rep.entries.append(LineResult(entry, key, [ST_UNSUPPORTED],
                                      f"not in Shift-JIS: {bad} (the original is kept)"))
        return old_raw
    if b'\0' in encoded:
        rep.entries.append(LineResult(entry, key, [ST_UNSUPPORTED],
                                      "contains a null character (the original is kept)"))
        return old_raw
    if flags:
        rep.entries.append(LineResult(entry, key, flags, '; '.join(details)))
    if encoded != old_raw:
        rep.changed += 1
    return encoded


def rebuild_script(name: str, data: bytes, texts: Dict[str, str], charmap, rep: RebuildReport) -> bytes:
    s = parse_script(data)
    changed = False
    for key, text in texts.items():
        if ':' in key or int(key) >= len(s.strings):
            rep.warnings.append(f"{name}: line {key} does not exist in the script")
            continue
        k = int(key)
        new = encode_line(name, key, s.strings[k], text, charmap, rep)
        if new != s.strings[k]:
            s.strings[k] = new
            changed = True
    return build_script(s) if changed else data


def rebuild_dat(name: str, data: bytes, texts: Dict[str, str], charmap, rep: RebuildReport) -> bytes:
    table = read_dat(data, raw_strings=True)
    changed = False
    for key, text in texts.items():
        parts = key.split(':')
        if len(parts) != 2:
            rep.warnings.append(f"{name}: line {key} is not ROW:COLUMN")
            continue
        r, c = int(parts[0]), int(parts[1])
        if r >= len(table.rows) or c >= len(table.types) or table.types[c] != DAT_STRING:
            rep.warnings.append(f"{name}: cell {key} is not a text cell")
            continue
        old = table.rows[r][c]
        new = encode_line(name, key, old, text, charmap, rep)
        if new != old:
            table.rows[r][c] = new
            changed = True
    return write_dat(table) if changed else data


def rebuild(pac_path, text_dir, out_pac, charmap: Optional[CharMap] = None, patch_only: bool = False,
            progress=None, cancel=None) -> RebuildReport:
    """
    Crea out_pac con los textos de <text_dir>/<entrada>.txt. Las entradas sin archivo de
    textos o sin cambios se copian sin volver a comprimir. Con patch_only, out_pac solo
    contiene las entradas cambiadas (para usarlo como UpdateN.pac).
    """
    text_dir = Path(text_dir)
    rep = RebuildReport()
    with Pac(pac_path) as pac:
        items = []
        total = len(pac.entries)
        for i, e in enumerate(pac.entries):
            check_cancel(cancel)
            report(progress, i, total, e.name)
            path = text_dir / text_file_name(e.name)
            new = None
            if e.ext in ('bin', 'dat') and path.is_file():
                texts, warnings = parse_text_file(path)
                rep.files += 1
                rep.lines += len(texts)
                rep.warnings += [f"{e.name}: {w}" for w in warnings]
                data = pac.read(e)
                fn = rebuild_script if e.ext == 'bin' else rebuild_dat
                try:
                    out = fn(e.name, data, texts, charmap, rep)
                except ValueError as ex:
                    rep.warnings.append(f"{e.name}: {ex} (copied unchanged)")
                    out = data
                if out != data:
                    new = out
                    rep.changed_entries.append(e.name)
            if new is not None:
                items.append((e.name, new))
            elif not patch_only:
                items.append((e.name, ('raw', pac.read_raw(e), e.size)))
        if not rep.changed_entries:
            return rep                  # nada cambió: no se escribe ningún archivo
        rep.output_entries = len(items)
        write_pac(out_pac, items, pac.pack, cancel=cancel)
    report(progress, total, total)
    return rep
