"""
krkr_script - Texto de los juegos KiriKiri con base de datos de escenas (KAGEX / Yuzusoft)

El texto no está en los .ks sino en tres bases de datos SQLite:
    scene.sdb       name(id, name)                     nombres de personaje
                    storage(id, name, ...)             archivos .ks
                    scene(id, storage, label, title, state, data, ...)
    scenedata.sdb   text(scene, idx, name, disp, text, voice, flag, state)
                    next(scene, idx, next, eval, exp, type, text, tag, onceflag)   elecciones
                    eval(scene, type, idx, exp)
    scenepatch.sdb  las mismas tablas que scenedata: una escena que esté en el parche se lee
                    entera del parche (KAGEnvSceneDB.tjs: "scene not in patchdb.text").

Cada .sdb se toma del último .xp3 que la tenga (base, patch, patch2...). Así, el texto que
muestra el juego es el de scenedata con las escenas de scenepatch encima.

Archivos de texto (uno por .ks, UTF-8). Lo que va tras '|' es lo que se traduce:
    S<escena>|<título de la escena>
    T<escena>.<idx>|<línea>
    N<escena>.<idx>|<nombre mostrado, si la línea lo cambia>
    C<escena>.<idx>|<texto de una elección>
    _names.txt:  M<id>|<nombre de personaje>
Comentarios: '# name: ...' (quién habla), '# voice: ...', '#> ...' (texto de referencia).

La reconstrucción escribe scene.sdb y scenepatch.sdb nuevas en un parche (p. ej. patch4.xp3):
las escenas con cambios pasan al parche completas, como hace el propio motor.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from krkr_common import atomic_write, check_cancel, report, safe_name
from krkr_xp3 import Xp3, find_xp3, merge_into_xp3

SDB_FILES = ('scene.sdb', 'scenedata.sdb', 'scenepatch.sdb')
NAMES_FILE = '_names.txt'

_UNESCAPE_RE = re.compile(r'\\(.)')
_UNESCAPES = {'n': '\n', 'r': '\r', '\\': '\\'}
_KEY_RE = re.compile(r'^([STNCM])(\d+)(?:\.(\d+))?$')


def escape_text(text: str) -> str:
    return text.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r')


def unescape_text(text: str) -> str:
    return _UNESCAPE_RE.sub(lambda m: _UNESCAPES.get(m.group(1), m.group(0)), text)


# =============================================================================
# Bases de datos
# =============================================================================

def _connect(data: bytes) -> sqlite3.Connection:
    con = sqlite3.connect(':memory:')
    con.deserialize(data)
    return con


def find_databases(game, exclude: Optional[str] = None) -> Dict[str, Tuple[str, bytes]]:
    """
    {nombre de la .sdb: (.xp3 de donde sale, datos)} con la versión que usa el juego (la del
    último .xp3 que la tiene). exclude: nombre de un .xp3 que no se tiene en cuenta.
    """
    found: Dict[str, Tuple[str, bytes]] = {}
    for path in find_xp3(game):
        if exclude and path.name.lower() == exclude.lower():
            continue
        with Xp3(path) as x:
            for e in x.files():
                base = e.name.replace('\\', '/').rsplit('/', 1)[-1].lower()
                if base in SDB_FILES:
                    found[base] = (path.name, x.read(e))
    return found


def text_patch_archive(game) -> Optional[str]:
    """El .xp3 que trae el scenepatch.sdb que usa el juego (p. ej. un parche de traducción)."""
    dbs = find_databases(game)
    return dbs['scenepatch.sdb'][0] if 'scenepatch.sdb' in dbs else None


def _voice(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            s = value.decode('utf-8')
            return s if s.isprintable() else ''
        except UnicodeDecodeError:
            return ''
    return ''


@dataclass
class Line:
    scene: int
    idx: int
    name: int               # id del personaje (0: narración)
    disp: Optional[str]     # nombre mostrado en lugar del de la tabla name
    text: str
    voice: str = ''


@dataclass
class Choice:
    scene: int
    idx: int
    text: str


class GameText:
    """El texto que muestra el juego (scenedata con las escenas de scenepatch encima)."""

    def __init__(self, dbs: Dict[str, Tuple[str, bytes]]):
        if 'scene.sdb' not in dbs or 'scenedata.sdb' not in dbs:
            raise ValueError("scene.sdb and scenedata.sdb were not found: this game does not keep "
                             "its text in a scene database")
        self.sources = {k: v[0] for k, v in dbs.items()}
        self.scene_db = _connect(dbs['scene.sdb'][1])
        self.data_db = _connect(dbs['scenedata.sdb'][1])
        self.patch_db = _connect(dbs['scenepatch.sdb'][1]) if 'scenepatch.sdb' in dbs else None
        s = self.scene_db
        self.names = dict(s.execute("select id, name from name"))
        self.storages = dict(s.execute("select id, name from storage"))
        self.scenes = {sid: (st, label, title) for sid, st, label, title in
                       s.execute("select id, storage, label, title from scene order by id")}
        self.lines: Dict[int, List[Line]] = {}
        self.choices: Dict[int, List[Choice]] = {}
        self.text_from_patch = set()      # escenas cuyo texto sale del parche
        self.next_from_patch = set()
        for db, from_patch in ((self.data_db, False), (self.patch_db, True)):
            if db is None:
                continue
            lines: Dict[int, List[Line]] = {}
            for sc, idx, name, disp, text, voice in db.execute(
                    "select scene, idx, name, disp, text, voice from text order by scene, idx"):
                lines.setdefault(sc, []).append(Line(sc, idx, name or 0, disp, text or '', _voice(voice)))
            choices: Dict[int, List[Choice]] = {}
            for sc, idx, text in db.execute("select scene, idx, text from next order by scene, idx"):
                if text is not None:
                    choices.setdefault(sc, []).append(Choice(sc, idx, text))
            if from_patch:
                patched_next = {r[0] for r in db.execute("select distinct scene from next")}
                self.text_from_patch = set(lines)
                self.next_from_patch = patched_next
                for sc in patched_next:
                    self.choices.pop(sc, None)
            self.lines.update(lines)
            self.choices.update(choices)

    def close(self):
        for db in (self.scene_db, self.data_db, self.patch_db):
            if db is not None:
                db.close()

    def speaker(self, line: Line) -> str:
        return line.disp or self.names.get(line.name, '') if line.name or line.disp else ''

    def by_storage(self) -> Dict[str, List[int]]:
        """{archivo .ks: [escenas]} en orden."""
        out: Dict[str, List[int]] = {}
        for sid, (st, _label, _title) in self.scenes.items():
            out.setdefault(self.storages.get(st) or f'scene_{sid}', []).append(sid)
        for sid in sorted(set(self.lines) - set(self.scenes)):
            out.setdefault(f'scene_{sid}', []).append(sid)
        return out

    def values(self) -> Dict[str, str]:
        """{clave: texto} de todo lo traducible."""
        out = {f"M{k}": v for k, v in self.names.items() if v}
        for sid, (_st, _label, title) in self.scenes.items():
            if title:
                out[f"S{sid}"] = title
        for sc, lines in self.lines.items():
            for ln in lines:
                out[f"T{sc}.{ln.idx}"] = ln.text
                if ln.disp:
                    out[f"N{sc}.{ln.idx}"] = ln.disp
        for sc, choices in self.choices.items():
            for ch in choices:
                out[f"C{sc}.{ch.idx}"] = ch.text
        return out


def text_file_name(storage: str) -> str:
    return safe_name(Path(storage).stem or storage) + '.txt'


# =============================================================================
# Extracción
# =============================================================================

@dataclass
class ExtractResult:
    files: int = 0
    lines: int = 0
    reference: str = ''         # .xp3 que se quitó para el texto de referencia
    warnings: List[str] = field(default_factory=list)


def extract_text(game, out_dir, reference: bool = False, with_json: bool = False,
                 progress=None, cancel=None) -> ExtractResult:
    """
    Escribe un .txt por cada .ks en out_dir y _names.txt. reference: añade como '#> ...' el
    texto del juego sin el parche que trae scenepatch.sdb (p. ej. el japonés debajo de un parche
    de traducción). Con with_json, además <out_dir>/json/<ks>.json.
    """
    out_dir = Path(out_dir)
    res = ExtractResult()
    dbs = find_databases(game)
    text = GameText(dbs)
    ref_values: Dict[str, str] = {}
    try:
        if reference:
            patch = dbs.get('scenepatch.sdb', (None,))[0]
            if not patch:
                res.warnings.append("No text patch (scenepatch.sdb) found: no reference")
            else:
                try:
                    ref = GameText(find_databases(game, exclude=patch))
                    ref_values = ref.values()
                    ref.close()
                    res.reference = patch
                except ValueError as ex:
                    res.warnings.append(f"Reference without {patch}: {ex}")

        def ref_line(key, value):
            r = ref_values.get(key)
            return [f"#> {escape_text(r)}\n"] if r is not None and r != value else []

        groups = text.by_storage()
        total = len(groups)
        for i, (storage, scenes) in enumerate(groups.items()):
            check_cancel(cancel)
            report(progress, i, total, storage)
            out, records = [], []
            for sid in scenes:
                _st, label, title = text.scenes.get(sid, (None, None, None))
                lines = text.lines.get(sid, [])
                choices = text.choices.get(sid, [])
                if not lines and not choices and not title:
                    continue
                out.append(f"\n## scene {sid} {label or ''}\n")
                if title:
                    out += ref_line(f"S{sid}", title)
                    out.append(f"S{sid}|{escape_text(title)}\n")
                for ln in lines:
                    who = text.speaker(ln)
                    key = f"T{sid}.{ln.idx}"
                    rec = {'scene': sid, 'idx': ln.idx, 'name': who, 'text': ln.text}
                    if who:
                        out.append(f"# name: {who}\n")
                    if ln.voice:
                        out.append(f"# voice: {ln.voice}\n")
                        rec['voice'] = ln.voice
                    if key in ref_values:
                        rec['reference'] = ref_values[key]
                    out += ref_line(key, ln.text)
                    out.append(f"{key}|{escape_text(ln.text)}\n")
                    if ln.disp:
                        out += ref_line(f"N{sid}.{ln.idx}", ln.disp)
                        out.append(f"N{sid}.{ln.idx}|{escape_text(ln.disp)}\n")
                    records.append(rec)
                for ch in choices:
                    key = f"C{sid}.{ch.idx}"
                    out.append("# choice\n")
                    out += ref_line(key, ch.text)
                    out.append(f"{key}|{escape_text(ch.text)}\n")
                    records.append({'scene': sid, 'choice': ch.idx, 'text': ch.text,
                                    **({'reference': ref_values[key]} if key in ref_values else {})})
            if not records and not out:
                continue
            name = text_file_name(storage)
            with atomic_write(out_dir / name) as f:
                f.write((f"# {storage}\n" + ''.join(out)).encode('utf-8'))
            if with_json:
                with atomic_write(out_dir / 'json' / (Path(name).stem + '.json')) as f:
                    f.write(json.dumps(records, ensure_ascii=False, indent=1).encode('utf-8'))
            res.files += 1
            res.lines += len(records)

        names = []
        for nid, value in sorted(text.names.items()):
            if value:
                names += ref_line(f"M{nid}", value)
                names.append(f"M{nid}|{escape_text(value)}\n")
        with atomic_write(out_dir / NAMES_FILE) as f:
            f.write(("# Character names (the 'name: ...' of each line)\n" + ''.join(names)).encode('utf-8'))
    finally:
        text.close()
    report(progress, total, total)
    return res


# =============================================================================
# Reconstrucción
# =============================================================================

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
    scenes: int = 0             # escenas que pasan al parche
    entries: List[LineResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    output_entries: int = 0     # archivos del parche escrito (0: no se escribió)

    def count(self, flag: str) -> int:
        return sum(1 for e in self.entries if flag in e.flags)

    def summary(self) -> str:
        if not self.changed:
            return f"No text changed ({self.files} text files read): no file was written"
        return (f"{self.changed} texts changed, {self.scenes} scenes in the patch "
                f"({self.files} text files read)")


ST_CHANGED = 'changed'
ST_UNKNOWN = 'unknown line'


def parse_text_file(path) -> Tuple[Dict[str, str], List[str]]:
    """({clave: texto}, avisos)."""
    result, warnings = {}, []
    for num, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip() or line.startswith('#'):
            continue
        key, sep, text = line.partition('|')
        key = key.strip()
        if not sep or not _KEY_RE.match(key):
            warnings.append(f"invalid line {num}: {line[:50]}")
            continue
        result[key] = unescape_text(text)
    return result, warnings


def _copy_scene(src: sqlite3.Connection, dst: sqlite3.Connection, table: str, scene: int):
    cols = [r[1] for r in src.execute(f"pragma table_info({table})")]
    rows = src.execute(f"select {', '.join(cols)} from {table} where scene = ?", (scene,)).fetchall()
    dst.executemany(f"insert or replace into {table} ({', '.join(cols)}) values "
                    f"({', '.join('?' * len(cols))})", rows)


def _empty_patch(data_db: sqlite3.Connection) -> sqlite3.Connection:
    con = sqlite3.connect(':memory:')
    for (sql,) in data_db.execute("select sql from sqlite_master where sql is not null"):
        con.execute(sql)
    return con


def rebuild(game, text_dir, out_xp3, progress=None, cancel=None) -> RebuildReport:
    """
    Escribe scene.sdb y scenepatch.sdb con los textos de text_dir en out_xp3 (un parche, p. ej.
    patch4.xp3, que va en la carpeta del juego). Si out_xp3 ya existe, se conservan sus otros
    archivos. Si nada cambió, no se escribe.
    """
    text_dir = Path(text_dir)
    rep = RebuildReport()
    files = sorted(p for p in text_dir.glob('*.txt') if p.is_file())
    if not files:
        raise ValueError(f"There are no text files in {text_dir}")
    dbs = find_databases(game)
    text = GameText(dbs)
    try:
        current = text.values()
        new: Dict[str, Tuple[str, str]] = {}            # clave: (texto, archivo)
        for i, path in enumerate(files):
            check_cancel(cancel)
            report(progress, i, len(files) + 1, path.name)
            values, warnings = parse_text_file(path)
            rep.files += 1
            rep.lines += len(values)
            rep.warnings += [f"{path.name}: {w}" for w in warnings]
            for key, value in values.items():
                if key not in current:
                    rep.entries.append(LineResult(path.name, key, [ST_UNKNOWN], "not in the game (ignored)"))
                elif value != current[key]:
                    new[key] = (value, path.name)
        if not new:
            return rep

        patch = text.patch_db
        if patch is not None:
            out_patch = _connect(patch.serialize())
        else:
            out_patch = _empty_patch(text.data_db)
        out_scene = _connect(text.scene_db.serialize())
        moved_text, moved_next = set(), set()
        for key, (value, fname) in sorted(new.items()):
            kind, a, b = _KEY_RE.match(key).groups()
            a = int(a)
            if kind == 'M':
                out_scene.execute("update name set name = ? where id = ?", (value, a))
            elif kind == 'S':
                out_scene.execute("update scene set title = ? where id = ?", (value, a))
            elif kind in 'TN':
                if a not in text.text_from_patch and a not in moved_text:
                    _copy_scene(text.data_db, out_patch, 'text', a)
                    moved_text.add(a)
                col = 'text' if kind == 'T' else 'disp'
                out_patch.execute(f"update text set {col} = ? where scene = ? and idx = ?", (value, a, int(b)))
            else:
                if a not in text.next_from_patch and a not in moved_next:
                    _copy_scene(text.data_db, out_patch, 'next', a)
                    moved_next.add(a)
                out_patch.execute("update next set text = ? where scene = ? and idx = ?", (value, a, int(b)))
            rep.entries.append(LineResult(fname, key, [ST_CHANGED], value[:80]))
            rep.changed += 1
        out_patch.commit()
        out_scene.commit()
        rep.scenes = len(text.text_from_patch | moved_text)

        report(progress, len(files), len(files) + 1, Path(out_xp3).name)
        items = {'scene.sdb': out_scene.serialize(), 'scenepatch.sdb': out_patch.serialize()}
        out_patch.close()
        out_scene.close()
        rep.output_entries = merge_into_xp3(out_xp3, items, cancel=cancel)
    finally:
        text.close()
    report(progress, len(files) + 1, len(files) + 1)
    return rep

