"""
siglus_script - Texto de Scene.pck: extracción y reconstrucción

Un archivo .txt por escena (UTF-8), con una línea 'NÚMERO|TEXTO' por cadena traducible; el
número es el de la cadena en la escena. Comentarios antes de cada línea:
    # voice: z0605_00026     archivo de voz (koe\\z0605.ovk, entrada 26)
    #> ...                   texto de referencia (el mismo Scene.pck en otro idioma)
    # name / # other         la línea es el nombre de quien habla / otra cadena (elección,
                             mensaje del teléfono, título...)
Códigos del motor que hay que conservar: ％Ｂ (nombre del protagonista), #N (salto de línea),
【nombre】 delante de los mensajes del teléfono.

La reconstrucción cambia solo la tabla de cadenas de las escenas editadas: el bytecode queda
igual y las líneas pueden tener cualquier longitud.
"""

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

from siglus_common import atomic_write, check_cancel, report, safe_name
from siglus_pck import ScenePck
from siglus_ss import K_TEXT, analyze, read_strings, write_strings

PCK_NAME = 'Scene.pck'

_UNESCAPE_RE = re.compile(r'\\(.)')
_UNESCAPES = {'n': '\n', 'r': '\r', '\\': '\\'}


def escape_text(text: str) -> str:
    return text.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')


def unescape_text(text: str) -> str:
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPES.get(m.group(1), m.group(0)), text)


def find_pck(src) -> Path:
    """src: la carpeta del juego o un Scene.pck."""
    src = Path(src)
    if src.is_dir():
        src = src / PCK_NAME
    if not src.is_file():
        raise ValueError(f"{PCK_NAME} was not found in {src.parent}")
    return src


def voice_table(game) -> Dict[int, Set[int]]:
    """{escena de voz: {entradas}} leído de las cabeceras de koe\\z*.ovk (vacío si no hay)."""
    koe = Path(game) / 'koe'
    out: Dict[int, Set[int]] = {}
    if not koe.is_dir():
        return out
    for p in koe.glob('z*.ovk'):
        try:
            number = int(p.stem[1:])
            with open(p, 'rb') as f:
                n, = struct.unpack('<i', f.read(4))
                table = f.read(16 * max(0, n))
            out[number] = {struct.unpack_from('<IIII', table, 16 * k)[2] for k in range(len(table) // 16)}
        except (ValueError, OSError, struct.error):
            continue
    return out


def voice_name(voice: int) -> str:
    scene, entry = divmod(voice, 100000)
    return f"z{scene:04d}_{entry:05d}"


def text_file_name(scene_name: str) -> str:
    return safe_name(scene_name) + '.txt'


# =============================================================================
# Extracción
# =============================================================================

@dataclass
class ExtractResult:
    files: int = 0
    lines: int = 0
    voiced: int = 0
    warnings: List[str] = field(default_factory=list)


def extract_text(src, out_dir, reference=None, with_json: bool = False,
                 progress=None, cancel=None) -> ExtractResult:
    """
    Escribe <out_dir>/<escena>.txt por cada escena con texto. reference: otro Scene.pck (o su
    carpeta) con las mismas escenas en otro idioma; su texto va como '#> …' en cada línea.
    Con with_json, además <out_dir>/json/<escena>.json.
    """
    pck_path = find_pck(src)
    out_dir = Path(out_dir)
    res = ExtractResult()
    pck = ScenePck(pck_path)
    ref = ScenePck(find_pck(reference)) if reference else None
    ref_index = {n: i for i, n in enumerate(ref.names)} if ref else {}
    voices = voice_table(pck_path.parent)
    names = set(pck.names)
    total = len(pck)
    for i, name in enumerate(pck.names):
        check_cancel(cancel)
        report(progress, i, total, name)
        try:
            scene = analyze(pck.scene(i), voices or None, names)
        except (ValueError, struct.error) as ex:
            res.warnings.append(f"{name}: {ex}")
            continue
        if not scene.lines:
            continue
        ref_strings = None
        if ref is not None and name in ref_index:
            try:
                ref_strings = read_strings(ref.scene(ref_index[name]))
            except (ValueError, struct.error):
                ref_strings = None
            if ref_strings is not None and len(ref_strings) != len(scene.strings):
                res.warnings.append(f"{name}: the reference has {len(ref_strings)} strings and the scene "
                                    f"{len(scene.strings)}; reference not used")
                ref_strings = None
        lines, records = [], []
        for ln in scene.lines:
            text = scene.strings[ln.index]
            rec = {'line': ln.index, 'type': ln.kind, 'text': text}
            if ln.voice:
                rec['voice'] = voice_name(ln.voice)
                lines.append(f"# voice: {rec['voice']}\n")
                res.voiced += 1
            if ref_strings is not None:
                rec['reference'] = ref_strings[ln.index]
                if rec['reference'] != text:
                    lines.append(f"#> {escape_text(rec['reference'])}\n")
            if ln.kind != K_TEXT:
                lines.append(f"# {ln.kind}\n")
            lines.append(f"{ln.index:05d}|{escape_text(text)}\n")
            records.append(rec)
        with atomic_write(out_dir / text_file_name(name)) as f:
            f.write(''.join(lines).encode('utf-8'))
        if with_json:
            with atomic_write(out_dir / 'json' / (safe_name(name) + '.json')) as f:
                f.write(json.dumps(records, ensure_ascii=False, indent=1).encode('utf-8'))
        res.files += 1
        res.lines += len(records)
    report(progress, total, total)
    return res


# =============================================================================
# Reconstrucción
# =============================================================================

ST_CHANGED = 'changed'
ST_NOT_TEXT = 'not a text line'


@dataclass
class LineResult:
    entry: str
    key: str
    flags: List[str]
    detail: str = ''


@dataclass
class RebuildReport:
    files: int = 0              # archivos de texto leídos
    lines: int = 0              # líneas leídas
    changed: int = 0            # textos cambiados
    changed_scenes: List[str] = field(default_factory=list)
    entries: List[LineResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    written: bool = False

    def count(self, flag: str) -> int:
        return sum(1 for e in self.entries if flag in e.flags)

    def summary(self) -> str:
        if not self.changed:
            return f"No text changed ({self.files} text files read): no file was written"
        return (f"{self.changed} texts changed in {len(self.changed_scenes)} scenes "
                f"({self.files} text files read)")


def parse_text_file(path) -> Tuple[Dict[int, str], List[str]]:
    """({número de cadena: texto}, avisos)."""
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


def rebuild(src, text_dir, out_pck, progress=None, cancel=None) -> RebuildReport:
    """
    Crea out_pck (un Scene.pck nuevo) con los textos de <text_dir>/<escena>.txt. Las escenas sin
    cambios se copian tal cual. Si nada cambió, no se escribe.
    """
    pck_path = find_pck(src)
    if pck_path.resolve() == Path(out_pck).resolve():
        raise ValueError("The output Scene.pck must be different from the original")
    text_dir = Path(text_dir)
    rep = RebuildReport()
    pck = ScenePck(pck_path)
    names = set(pck.names)
    new_scenes: Dict[int, bytes] = {}
    total = len(pck)
    for i, name in enumerate(pck.names):
        check_cancel(cancel)
        path = text_dir / text_file_name(name)
        if not path.is_file():
            continue
        report(progress, i, total, name)
        texts, warnings = parse_text_file(path)
        rep.files += 1
        rep.lines += len(texts)
        rep.warnings += [f"{path.name}: {w}" for w in warnings]
        ss = pck.scene(i)
        scene = analyze(ss, None, names)
        allowed = {ln.index for ln in scene.lines}
        strings = list(scene.strings)
        changed = 0
        for idx, text in sorted(texts.items()):
            if idx >= len(strings) or idx not in allowed:
                rep.entries.append(LineResult(path.name, f"{idx:05d}", [ST_NOT_TEXT],
                                              "not a text line of the scene (ignored)"))
                continue
            if text == strings[idx]:
                continue
            strings[idx] = text
            changed += 1
            rep.entries.append(LineResult(path.name, f"{idx:05d}", [ST_CHANGED], text[:80]))
        if changed:
            new_scenes[i] = write_strings(ss, strings)
            rep.changed += changed
            rep.changed_scenes.append(name)
    if not new_scenes:
        return rep
    pck.write(out_pck, new_scenes)
    rep.written = True
    report(progress, total, total)
    return rep
