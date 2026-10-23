"""
fvp_common - Utilidades compartidas por los módulos de FVP Tools
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

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
