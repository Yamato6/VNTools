"""
bgi_script - Guiones de BGI / Ethornell (BurikoCompiledScriptVer1.00)

Estructura:
    0x00  "BurikoCompiledScriptVer1.00\\0"
    0x1C  u32 tamaño de la cabecera (incluido este campo): funciones importadas
    código: palabras u32. "03 <offset>" mete una cadena en la pila; el offset es relativo al
            inicio del código y apunta a la zona de cadenas (cp932, terminadas en \\0) del final.
            "7E <offset>" y "7F <offset>" son nombres de función y del guion (depuración).

Tipos de cadena según la instrucción siguiente:
    texto   "03 <texto> 140"             (la frase que se muestra)
    nombre  "03 <nombre> 03 <texto> 140" (quien habla)
    otras   opciones ("03 <opción> 09 02"), mensajes simultáneos… se traducen si llevan
            caracteres no ASCII; las cadenas ASCII son recursos (imágenes, voces) y no se tocan.

Al reconstruir, las cadenas nuevas se añaden al final y solo cambian las referencias "03":
el resto del guion queda igual, como hace el parche en inglés de Daitoshokan no Hitsujikai.
Las líneas pueden tener cualquier longitud. Cada línea se identifica por el número de su
referencia: el mismo guion traducido (p. ej. el parche) tiene las mismas referencias en el
mismo orden, así que sirve como referencia línea a línea.
"""

import json
import re
import struct
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from bgi_arc import BgiArc, write_arc
from bgi_codecs import dsc_pack, is_dsc, unpack
from bgi_common import CharMap, atomic_write, check_cancel, report, safe_name

SCRIPT_MAGIC = b'BurikoCompiledScriptVer1.00\0'
OP_PUSH_STRING = 0x03
OP_FUNC_NAME = 0x7E
OP_MESSAGE = 0x140

K_TEXT, K_NAME, K_OTHER = 'text', 'name', 'other'


def is_script(data: bytes) -> bool:
    return data[:28] == SCRIPT_MAGIC


@dataclass
class Ref:
    number: int         # número de la referencia en el guion (clave de la línea)
    word: int           # índice de la palabra "03" en el código
    offset: int         # offset de la cadena (relativo al código)
    raw: bytes
    kind: str = ''      # text, name, other ('' = recurso: no se traduce)
    voice: str = ''


@dataclass
class Script:
    data: bytes
    code: int           # offset del código en el archivo
    refs: List[Ref]

    @property
    def lines(self) -> List[Ref]:
        return [r for r in self.refs if r.kind]


def _cstring(data: bytes, pos: int) -> bytes:
    end = data.find(b'\0', pos)
    return data[pos:end if end >= 0 else len(data)]


def parse_script(data: bytes) -> Script:
    if not is_script(data):
        raise ValueError("Not a BGI script (BurikoCompiledScriptVer1.00)")
    head, = struct.unpack_from('<I', data, 0x1C)
    code = 0x1C + head
    if code > len(data):
        raise ValueError("The script header is invalid")
    words = array('I')
    words.frombytes(data[code:code + (len(data) - code) // 4 * 4])
    if words.itemsize != 4:
        raise ValueError("Unsupported platform")
    n = len(words)
    size = len(data) - code
    pushes = [i for i in range(n - 1) if words[i] == OP_PUSH_STRING]
    # La zona de cadenas empieza en la cadena más baja a la que apunta un "03" desde antes de ella
    forward = [words[i + 1] for i in pushes if i * 4 < words[i + 1] < size]
    if not forward:
        return Script(data, code, [])
    pool = min(forward)
    refs = []
    for i in pushes:
        off = words[i + 1]
        if i * 4 < pool <= off < size and data[code + off - 1] == 0:
            refs.append(Ref(len(refs), i, off, _cstring(data, code + off)))

    # Nombre de la función que se está preparando (para encontrar las voces)
    funcs, last = {}, ''
    at = {r.word: r for r in refs}
    for i in range(min(pool // 4, n)):
        if words[i] == OP_FUNC_NAME and i + 1 < n and words[i + 1] < size:
            try:
                last = _cstring(data, code + words[i + 1]).decode('ascii')
            except UnicodeDecodeError:
                pass
        elif i in at:
            funcs[i] = last

    voice = ''
    for r in refs:
        nxt = words[r.word + 2] if r.word + 2 < n else None
        after = at.get(r.word + 2)
        if nxt == OP_MESSAGE:
            r.kind = K_TEXT
        elif after is not None and r.word + 4 < n and words[r.word + 4] == OP_MESSAGE:
            r.kind = K_NAME
        elif any(b >= 0x80 for b in r.raw) and not any(b < 0x20 for b in r.raw):
            r.kind = K_OTHER
        elif funcs.get(r.word) == 'Voice':
            voice = r.raw.decode('cp932', 'replace')
            continue
        if r.kind in (K_NAME, K_TEXT):
            r.voice = voice
            if r.kind == K_TEXT:
                voice = ''
    return Script(data, code, refs)


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


def parse_text_file(path) -> Tuple[Dict[int, str], List[str]]:
    """Lee un archivo de textos. Devuelve ({número de línea: texto}, avisos)."""
    result, warnings = {}, []
    for num, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line or line.startswith('#'):
            continue
        key, sep, text = line.partition('|')
        key = key.strip()
        if not sep or not key.isdigit():
            warnings.append(f"invalid line {num}: {line[:50]}")
            continue
        result[int(key)] = unescape_text(text)
    return result, warnings


@dataclass
class ExtractResult:
    files: int = 0
    lines: int = 0
    warnings: List[str] = field(default_factory=list)


class _Reference:
    """Guiones de referencia: una carpeta (archivos sueltos) o un .arc."""

    def __init__(self, path):
        self.path = Path(path) if path else None
        self.arc = None
        self.by_name = {}
        if self.path and self.path.is_file():
            self.arc = BgiArc(self.path)
            self.by_name = {e.name: e for e in self.arc.entries}

    def get(self, name: str) -> Optional[Script]:
        if not self.path:
            return None
        try:
            if self.arc:
                e = self.by_name.get(name)
                data = self.arc.read(e) if e else None
            else:
                p = self.path / name
                data = unpack(p.read_bytes()) if p.is_file() else None
            return parse_script(data) if data and is_script(data) else None
        except (OSError, ValueError, struct.error):
            return None

    def close(self):
        if self.arc:
            self.arc.close()


def script_entries(arc: BgiArc):
    """[(entrada, datos)] de los guiones del .arc."""
    for e in arc.entries:
        data = arc.read(e)
        if is_script(data):
            yield e, data


def extract_text(arc_path, out_dir, reference=None, charmap: Optional[CharMap] = None,
                 reverse: bool = False, with_json: bool = False,
                 progress=None, cancel=None) -> ExtractResult:
    """
    Escribe <out_dir>/<guion>.txt por cada guion del .arc, con líneas 'NÚMERO|TEXTO'. Los
    nombres y las otras cadenas van marcados con un comentario, y la voz con '# voice:'.
    reference: carpeta o .arc con los mismos guiones en otro idioma; su texto se añade como
    '#> …' antes de cada línea. Con with_json, además <out_dir>/json/<guion>.json. Con
    charmap y reverse, deshace la tabla de caracteres (guion ya traducido).
    """
    out_dir = Path(out_dir)
    res = ExtractResult()
    ref_src = _Reference(reference)
    try:
        with BgiArc(arc_path) as arc:
            total = len(arc.entries)
            found = 0
            for i, (e, data) in enumerate(script_entries(arc)):
                check_cancel(cancel)
                report(progress, i, total, e.name)
                found += 1
                try:
                    script = parse_script(data)
                except (ValueError, struct.error) as ex:
                    res.warnings.append(f"{e.name}: {ex}")
                    continue
                ref = ref_src.get(e.name)
                if ref is not None and len(ref.refs) != len(script.refs):
                    res.warnings.append(f"{e.name}: the reference has {len(ref.refs)} strings and the "
                                        f"script {len(script.refs)}; reference not used")
                    ref = None
                lines, records = [], []
                last_voice = ''
                for r in script.lines:
                    text = decode_text(r.raw)
                    if charmap and reverse:
                        text = charmap.reverse(text)
                    rec = {'line': r.number, 'type': r.kind, 'text': text}
                    if r.voice and r.voice != last_voice:
                        lines.append(f"# voice: {r.voice}\n")
                    last_voice = r.voice
                    if r.voice:
                        rec['voice'] = r.voice
                    if ref is not None:
                        rec['reference'] = decode_text(ref.refs[r.number].raw)
                        lines.append(f"#> {escape_text(rec['reference'])}\n")
                    if r.kind != K_TEXT:
                        lines.append(f"# {r.kind}\n")
                    lines.append(f"{r.number:04d}|{escape_text(text)}\n")
                    records.append(rec)
                if not lines:
                    continue
                with atomic_write(out_dir / text_file_name(e.name)) as f:
                    f.write(''.join(lines).encode('utf-8'))
                if with_json:
                    with atomic_write(out_dir / 'json' / (safe_name(e.name) + '.json')) as f:
                        f.write(json.dumps(records, ensure_ascii=False, indent=1).encode('utf-8'))
                res.files += 1
                res.lines += len(records)
            if not found:
                raise ValueError(f"{Path(arc_path).name} has no scripts")
    finally:
        ref_src.close()
    report(progress, total, total)
    return res


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
        parts = [f"{self.changed} texts changed in {len(self.changed_entries)} scripts "
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


def encode_line(entry: str, key: str, text: str, charmap: Optional[CharMap],
                rep: RebuildReport) -> Optional[bytes]:
    """Codifica un texto nuevo; si no se puede, devuelve None y lo anota."""
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
        return None
    if b'\0' in encoded:
        rep.entries.append(LineResult(entry, key, [ST_UNSUPPORTED],
                                      "contains a null character (the original is kept)"))
        return None
    if flags:
        rep.entries.append(LineResult(entry, key, flags, '; '.join(details)))
    return encoded


def rebuild_script(name: str, data: bytes, texts: Dict[int, str], charmap, rep: RebuildReport) -> bytes:
    """Devuelve el guion con los textos nuevos (o data si nada cambió)."""
    script = parse_script(data)
    lines = {r.number: r for r in script.lines}
    out = bytearray(data)
    if out and out[-1] != 0:
        out += b'\0'
    added: Dict[bytes, int] = {}
    changed = 0
    for num, text in sorted(texts.items()):
        r = lines.get(num)
        if r is None:
            rep.warnings.append(f"{name}: line {num} is not a text line of the script")
            continue
        if text == decode_text(r.raw):
            continue
        if r.kind == K_OTHER and not text.strip():
            rep.warnings.append(f"{name}: line {num} cannot be empty (the original is kept)")
            continue
        encoded = encode_line(name, f"{num:04d}", text, charmap, rep)
        if encoded is None or encoded == r.raw:
            continue
        off = added.get(encoded)
        if off is None:
            off = added[encoded] = len(out) - script.code
            out += encoded + b'\0'
        struct.pack_into('<I', out, script.code + (r.word + 1) * 4, off)
        changed += 1
    rep.changed += changed
    return bytes(out) if changed else data


def rebuild(arc_path, text_dir, out_arc, charmap: Optional[CharMap] = None,
            progress=None, cancel=None) -> RebuildReport:
    """
    Crea out_arc con los textos de <text_dir>/<guion>.txt. Los guiones cambiados se guardan
    en un DSC sin comprimir; el resto se copia tal cual. Si nada cambió, no se escribe.
    """
    text_dir = Path(text_dir)
    rep = RebuildReport()
    if Path(arc_path).resolve() == Path(out_arc).resolve():
        raise ValueError("The output .arc must be different from the original")
    with BgiArc(arc_path) as arc:
        items = []
        total = len(arc.entries)
        for i, e in enumerate(arc.entries):
            check_cancel(cancel)
            report(progress, i, total, e.name)
            path = text_dir / text_file_name(e.name)
            new = None
            if path.is_file():
                raw = arc.read_raw(e)
                data = unpack(raw)
                if is_script(data):
                    texts, warnings = parse_text_file(path)
                    rep.files += 1
                    rep.lines += len(texts)
                    rep.warnings += [f"{e.name}: {w}" for w in warnings]
                    try:
                        out = rebuild_script(e.name, data, texts, charmap, rep)
                    except (ValueError, struct.error) as ex:
                        rep.warnings.append(f"{e.name}: {ex} (copied unchanged)")
                        out = data
                    if out != data:
                        new = dsc_pack(out) if is_dsc(raw) else out
                        rep.changed_entries.append(e.name)
            items.append((e.name, new if new is not None else (str(arc.path), e.offset, e.size)))
        if not rep.changed_entries:
            return rep                  # nada cambió: no se escribe ningún archivo
        rep.output_entries = len(items)
        write_arc(out_arc, items, arc.version, [e.extra for e in arc.entries], cancel=cancel)
    report(progress, total, total)
    return rep
