"""
cs2_script - Guiones .cst de CatSystem2: texto, extracción y reconstrucción

Formato (little-endian):
    char "CatScene" · u32 tamaño comprimido · u32 tamaño real · zlib(guion)
guion:
    u32 tamaño (sin estos 16 bytes) · u32 bloques de entrada · u32 offset de la tabla de
    líneas · u32 offset de las cadenas (offsets desde el byte 16)
    bloques de entrada: n × { u32 líneas, u32 primera línea }
    tabla de líneas: un u32 por línea, offset de la línea desde el inicio de las cadenas
    línea: u8 0x01 · u8 tipo · texto Shift-JIS + \\0

Tipos: 0x20 texto, 0x21 nombre de quien habla, 0x02 esperar clic, 0x03 nueva página,
0x30 comando ("bg 0 bg03 0 0 0", "pcm AMA_ama_001_001", "scene <título>"…).

Las líneas se referencian por número, así que el texto traducido puede tener cualquier
longitud: al reconstruir solo se regeneran las cadenas y la tabla de offsets.
"""

import json
import re
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cs2_common import CharMap, atomic_write, check_cancel, report, safe_name
from cs2_int import IntArchive, write_int

MAGIC = b'CatScene'

T_WAIT, T_PAGE, T_TEXT, T_NAME, T_COMMAND = 0x02, 0x03, 0x20, 0x21, 0x30

K_TEXT, K_NAME, K_TITLE, K_CHOICE = 'text', 'name', 'title', 'choice'

# Comandos con texto visible: (prefijo que se conserva, texto)
_SCENE_RE = re.compile(r'^(scene\s+)(.+)$', re.DOTALL)
_STR_RE = re.compile(r'^(str\s+\d+\s+)(.*[^\x00-\x7f].*)$', re.DOTALL)
_CHOICE_RE = re.compile(r'^(\d+\s+\S+\s+)(.+)$', re.DOTALL)
_SELECT_RE = re.compile(r'^f?select\b')
_PCM_RE = re.compile(r'^pcm\s+(\S+)')


# =============================================================================
# Formato
# =============================================================================

@dataclass
class Cst:
    entry_blocks: bytes                         # bloques de entrada, sin cambios
    lines: List[Tuple[int, int, bytes]]         # (marca, tipo, texto en cp932)


def is_cst(data: bytes) -> bool:
    return data[:8] == MAGIC


def parse_cst(data: bytes) -> Cst:
    if not is_cst(data):
        raise ValueError("Not a CatScene script")
    zsize, usize = struct.unpack_from('<II', data, 8)
    u = zlib.decompress(data[16:16 + zsize])
    if len(u) != usize:
        raise ValueError("CatScene: wrong unpacked size")
    size, _blocks, table, strings = struct.unpack_from('<4I', u, 0)
    if size + 16 != len(u) or not 0 <= table <= strings <= size:
        raise ValueError("CatScene: damaged header")
    count = (strings - table) // 4
    offsets = struct.unpack_from(f'<{count}I', u, 16 + table)
    base = 16 + strings
    lines = []
    for off in offsets:
        p = base + off
        end = u.index(b'\0', p + 2)
        lines.append((u[p], u[p + 1], u[p + 2:end]))
    return Cst(u[16:16 + table], lines)


def build_cst(cst: Cst, level: int = 9) -> bytes:
    nblocks = len(cst.entry_blocks) // 8
    table = len(cst.entry_blocks)
    strings = table + 4 * len(cst.lines)
    offsets = []
    body = bytearray()
    for mark, kind, text in cst.lines:
        offsets.append(len(body))
        body += bytes((mark, kind)) + text + b'\0'
    u = (struct.pack('<4I', strings + len(body), nblocks, table, strings) + cst.entry_blocks +
         struct.pack(f'<{len(offsets)}I', *offsets) + body)
    z = zlib.compress(u, level)
    return MAGIC + struct.pack('<II', len(z), len(u)) + z


def decode_text(raw: bytes) -> str:
    return raw.decode('cp932', errors='replace')


@dataclass
class TextLine:
    index: int          # número de línea del guion
    kind: str           # text, name, title o choice
    prefix: str         # parte del comando que se conserva ('' en texto y nombres)
    text: str
    voice: str = ''


def text_lines(cst: Cst) -> List[TextLine]:
    """Líneas traducibles del guion, con la voz que suena en cada una."""
    out = []
    voice = ''
    in_select = False
    for i, (_mark, kind, raw) in enumerate(cst.lines):
        s = decode_text(raw)
        if kind == T_COMMAND:
            m = _PCM_RE.match(s)
            if m:
                voice = m.group(1)
            if _SELECT_RE.match(s):
                in_select = True
                continue
            if in_select:
                m = _CHOICE_RE.match(s)
                if m:
                    out.append(TextLine(i, K_CHOICE, m.group(1), m.group(2)))
                    continue
                in_select = False
            m = _SCENE_RE.match(s) or _STR_RE.match(s)
            if m:
                out.append(TextLine(i, K_TITLE, m.group(1), m.group(2)))
            continue
        in_select = False
        if kind == T_NAME and s:
            out.append(TextLine(i, K_NAME, '', s, voice))
        elif kind == T_TEXT:
            if s:
                out.append(TextLine(i, K_TEXT, '', s, voice))
            voice = ''
    return out


# =============================================================================
# Archivos de texto
# =============================================================================

def text_file_name(entry_name: str) -> str:
    return safe_name(entry_name) + '.txt'


def parse_text_file(path) -> Tuple[Dict[int, str], List[str]]:
    """Lee un archivo de textos. Devuelve ({línea: texto}, avisos)."""
    result, warnings = {}, []
    for num, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line or line.startswith('#'):
            continue
        key, sep, text = line.partition('|')
        key = key.strip()
        if not sep or not key.isdigit():
            warnings.append(f"invalid line {num}: {line[:50]}")
            continue
        result[int(key)] = text
    return result, warnings


@dataclass
class ExtractResult:
    files: int = 0
    lines: int = 0
    warnings: List[str] = field(default_factory=list)


def _cst_entries(arc: IntArchive):
    return [e for e in arc.entries if e.ext == 'cst']


def extract_text(int_path, out_dir, key: Optional[int] = None, charmap: Optional[CharMap] = None,
                 reverse: bool = False, with_json: bool = False,
                 progress=None, cancel=None) -> ExtractResult:
    """
    Escribe un archivo de textos por guion del .int: <out_dir>/<guion>.cst.txt con
    líneas 'LÍNEA|TEXTO'. Los nombres, títulos y opciones van marcados con un comentario,
    y la voz de cada frase con '# voice: ARCHIVO'. Con with_json, además
    <out_dir>/json/<guion>.cst.json. Con charmap y reverse, deshace la tabla de
    caracteres (para volver a extraer un guion ya traducido).
    """
    out_dir = Path(out_dir)
    res = ExtractResult()
    with IntArchive(int_path, key) as arc:
        entries = _cst_entries(arc)
        if not entries:
            raise ValueError(f"{Path(int_path).name} has no .cst scripts")
        total = len(entries)
        for i, e in enumerate(entries):
            check_cancel(cancel)
            report(progress, i, total, e.name)
            try:
                cst = parse_cst(arc.read(e))
            except (ValueError, zlib.error, struct.error) as ex:
                res.warnings.append(f"{e.name}: {ex}")
                continue
            lines, records = [], []
            last_voice = ''
            for tl in text_lines(cst):
                text = charmap.reverse(tl.text) if charmap and reverse else tl.text
                if tl.voice and tl.voice != last_voice:
                    lines.append(f"# voice: {tl.voice}\n")
                last_voice = tl.voice
                if tl.kind != K_TEXT:
                    lines.append(f"# {tl.kind}\n")
                lines.append(f"{tl.index:04d}|{text}\n")
                rec = {'line': tl.index, 'type': tl.kind, 'text': text}
                if tl.voice:
                    rec['voice'] = tl.voice
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


def encode_line(entry: str, key: str, old_text: str, text: str, charmap: Optional[CharMap],
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
    cst = parse_cst(data)
    known = {tl.index: tl for tl in text_lines(cst)}
    changed = False
    for idx, text in texts.items():
        tl = known.get(idx)
        if tl is None:
            rep.warnings.append(f"{name}: line {idx} is not a text line of the script")
            continue
        if text == tl.text:
            continue
        if tl.kind != K_TEXT and tl.kind != K_NAME and not text.strip():
            rep.warnings.append(f"{name}: line {idx} ({tl.kind}) cannot be empty (the original is kept)")
            continue
        encoded = encode_line(name, f"{idx:04d}", tl.text, text, charmap, rep)
        if encoded is None:
            continue
        mark, kind, old = cst.lines[idx]
        new = tl.prefix.encode('cp932') + encoded
        if new != old:
            cst.lines[idx] = (mark, kind, new)
            rep.changed += 1
            changed = True
    return build_cst(cst) if changed else data


def rebuild(int_path, text_dir, out_int, key: Optional[int] = None, charmap: Optional[CharMap] = None,
            progress=None, cancel=None) -> RebuildReport:
    """
    Crea out_int con los textos de <text_dir>/<guion>.cst.txt. Los guiones sin archivo de
    textos o sin cambios se copian tal cual (sin descifrar). Si nada cambió, no se escribe.
    """
    text_dir = Path(text_dir)
    rep = RebuildReport()
    if Path(int_path).resolve() == Path(out_int).resolve():
        raise ValueError("The output .int must be different from the original")
    with IntArchive(int_path, key) as arc:
        items = []
        total = len(arc.entries)
        for i, e in enumerate(arc.entries):
            check_cancel(cancel)
            report(progress, i, total, e.name)
            path = text_dir / text_file_name(e.name)
            new = None
            if e.ext == 'cst' and path.is_file():
                texts, warnings = parse_text_file(path)
                rep.files += 1
                rep.lines += len(texts)
                rep.warnings += [f"{e.name}: {w}" for w in warnings]
                data = arc.read(e)
                try:
                    out = rebuild_script(e.name, data, texts, charmap, rep)
                except (ValueError, zlib.error, struct.error) as ex:
                    rep.warnings.append(f"{e.name}: {ex} (copied unchanged)")
                    out = data
                if out != data:
                    new = out
                    rep.changed_entries.append(e.name)
            items.append((e.name, new if new is not None else ('entry', arc, e)))
        if not rep.changed_entries:
            return rep                  # nada cambió: no se escribe ningún archivo
        rep.output_entries = len(items)
        write_int(out_int, items, arc.crypto, cancel=cancel)
    report(progress, total, total)
    return rep
