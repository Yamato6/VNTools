"""
cs2_common - Utilidades compartidas por los módulos de CatSystem2 Tools
"""

import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, List, Tuple

ProgressFn = Callable[[int, int, str], None]


class Cancelled(Exception):
    """La operación fue cancelada por el usuario."""


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def report(progress, i, total, name=''):
    if progress:
        progress(i, total, name)


@contextmanager
def atomic_write(path):
    """
    Abre un temporal junto a path para escritura binaria. Si todo va bien lo
    renombra a path; si falla o se cancela, lo borra y path no se toca.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    try:
        with open(tmp, 'wb') as f:
            yield f
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def safe_name(name: str) -> str:
    """Nombre de entrada como ruta relativa segura (sin '..' ni unidad)."""
    parts = [p for p in name.replace('\\', '/').split('/') if p not in ('', '.', '..')]
    if not parts:
        raise ValueError(f"Invalid entry name: {name!r}")
    return '/'.join(p.replace(':', '_') for p in parts)


class CharMap:
    """
    Tabla de reemplazos de caracteres, p. ej. 'á=ｱ': al reconstruir, cada 'á'
    se escribe como 'ｱ', que la fuente personalizada dibuja como 'á'.
    Mismo formato que FVP Tools y NeXAS Tools.
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
