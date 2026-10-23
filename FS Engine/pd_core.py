"""
pd_core - Lógica del formato .PD (FS Engine / CROSS†CHANNEL)
Usado por la CLI (crosschannel_tool.py) y la GUI (crosschannel_gui.py).
"""

import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

MODES = ('PackOnly', 'PackPlus')
DEFAULT_MODE = 'PackPlus'
INFO_FILE = '_pdinfo.json'

HEADER_SIZE = 0x48          # 8 (modo) + 56 (padding) + 8 (cantidad)
ENTRY_SIZE = 144            # 128 (nombre) + 8 (offset) + 8 (tamaño)
NAME_SIZE = 128

# Tabla para invertir todos los bytes (XOR 0xFF) con bytes.translate
XOR_FF_TABLE = bytes(b ^ 0xFF for b in range(256))

ProgressFn = Callable[[int, int, str], None]


class Cancelled(Exception):
    """La operación fue cancelada por el usuario."""


@dataclass
class PDEntry:
    name: str
    offset: int
    size: int


def read_uint64_le(f):
    """Lee un entero sin signo de 64 bits en little-endian."""
    return struct.unpack('<Q', f.read(8))[0]


def write_uint64_le(f, value):
    """Escribe un entero sin signo de 64 bits en little-endian."""
    f.write(struct.pack('<Q', value))


def apply_xor(data: bytes, mode: str) -> bytes:
    """Aplica el XOR de PackPlus (no hace nada en PackOnly)."""
    return data.translate(XOR_FF_TABLE) if mode == 'PackPlus' else data


def read_index(pd_file) -> Tuple[str, List[PDEntry], List[str]]:
    """Lee la cabecera y el directorio. Devuelve (modo, entradas, advertencias)."""
    warnings = []
    with open(pd_file, 'rb') as f:
        mode = f.read(8).decode('latin-1').rstrip('\x00')
        if mode not in MODES:
            raise ValueError(f"Formato desconocido: {mode!r}")

        if f.read(56) != b'\x00' * 56:
            warnings.append("Advertencia: padding no es cero")

        file_count = read_uint64_le(f)
        table = f.read(ENTRY_SIZE * file_count)
        if len(table) != ENTRY_SIZE * file_count:
            raise ValueError("Directorio truncado o archivo corrupto")

    entries = []
    for i in range(file_count):
        base = i * ENTRY_SIZE
        name = table[base:base + NAME_SIZE].split(b'\x00', 1)[0].decode('latin-1')
        offset, size = struct.unpack_from('<QQ', table, base + NAME_SIZE)
        entries.append(PDEntry(name, offset, size))
    return mode, entries, warnings


def _safe_output_path(out_dir: Path, name: str) -> Path:
    """Convierte el nombre interno en ruta, impidiendo salir de out_dir."""
    parts = [p for p in name.replace('\\', '/').split('/') if p not in ('', '.', '..')]
    if not parts:
        raise ValueError(f"Nombre de archivo inválido: {name!r}")
    return out_dir.joinpath(*parts)


def extract(pd_file, out_dir, names: Optional[Iterable[str]] = None,
            progress: Optional[ProgressFn] = None, cancel=None) -> Tuple[str, int]:
    """
    Extrae archivos de un .PD. Si names es None extrae todos.
    Devuelve (modo, cantidad extraída).
    """
    mode, entries, _ = read_index(pd_file)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    full = names is None
    if not full:
        wanted = set(names)
        selected = [e for e in entries if e.name in wanted]
    else:
        selected = entries

    total = len(selected)
    with open(pd_file, 'rb') as f:
        for i, entry in enumerate(selected):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            if progress:
                progress(i, total, entry.name)

            f.seek(entry.offset)
            data = f.read(entry.size)
            if len(data) != entry.size:
                raise ValueError(f"Datos truncados: {entry.name}")

            out_path = _safe_output_path(out_dir, entry.name)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(apply_xor(data, mode))

    # Guardar info para reempaquetar con el mismo modo y orden
    info = {'mode': mode}
    if full:
        info['order'] = [e.name for e in entries]
    info_path = out_dir / INFO_FILE
    if not full and info_path.exists():
        # Extracción parcial sobre una completa: conservar el orden existente
        try:
            old = json.loads(info_path.read_text(encoding='utf-8'))
            if 'order' in old:
                info['order'] = old['order']
        except (ValueError, OSError):
            pass
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding='utf-8')

    if progress:
        progress(total, total, '')
    return mode, total


def read_info(in_dir) -> Optional[dict]:
    """Lee _pdinfo.json de una carpeta extraída, si existe y es válido."""
    path = Path(in_dir) / INFO_FILE
    if not path.is_file():
        return None
    try:
        info = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None
    if info.get('mode') not in MODES:
        return None
    return info


def resolve_mode(in_dir, mode: Optional[str] = None) -> Tuple[str, str]:
    """Devuelve (modo, origen) donde origen explica de dónde salió el modo."""
    if mode is not None:
        if mode not in MODES:
            raise ValueError(f"Modo inválido: {mode}. Usa 'PackOnly' o 'PackPlus'")
        return mode, 'manual'
    info = read_info(in_dir)
    if info:
        return info['mode'], INFO_FILE
    return DEFAULT_MODE, 'por defecto'


def collect_files(in_dir) -> List[Tuple[str, Path]]:
    """
    Lista (nombre_interno, ruta) de los archivos a empaquetar, recursivamente.
    Respeta el orden de _pdinfo.json si existe; los archivos nuevos van al final.
    """
    in_dir = Path(in_dir)
    on_disk = {}
    for p in in_dir.rglob('*'):
        if p.is_file():
            rel = p.relative_to(in_dir).as_posix()
            if rel != INFO_FILE:
                on_disk[rel] = p

    result = []
    info = read_info(in_dir)
    if info and 'order' in info:
        for name in info['order']:
            key = name.replace('\\', '/')
            if key in on_disk:
                result.append((name, on_disk.pop(key)))
    for rel in sorted(on_disk):
        result.append((rel, on_disk[rel]))
    return result


def pack(in_dir, pd_file, mode: Optional[str] = None,
         progress: Optional[ProgressFn] = None, cancel=None) -> Tuple[str, int]:
    """
    Empaqueta una carpeta en un .PD. Si mode es None se detecta automáticamente.
    Devuelve (modo usado, cantidad de archivos).
    """
    mode, _ = resolve_mode(in_dir, mode)
    files = collect_files(in_dir)
    if not files:
        raise ValueError("Directorio vacío o inválido")

    encoded_names = []
    for name, _ in files:
        try:
            name_bytes = name.encode('latin-1')
        except UnicodeEncodeError:
            raise ValueError(f"Nombre con caracteres no soportados (latin-1): {name}")
        if len(name_bytes) > NAME_SIZE:
            raise ValueError(f"Nombre demasiado largo: {name}")
        encoded_names.append(name_bytes)

    pd_file = Path(pd_file)
    # Escribir en un temporal: si falla o se cancela, el .PD existente no se toca
    tmp_file = pd_file.with_name(pd_file.name + '.tmp')
    total = len(files)
    try:
        with open(tmp_file, 'wb') as f:
            # Header
            f.write(mode.encode('latin-1').ljust(8, b'\x00'))
            f.write(b'\x00' * 56)
            write_uint64_le(f, total)

            # Reservar espacio para el directorio
            directory_start = f.tell()
            f.write(b'\x00' * (ENTRY_SIZE * total))

            # Escribir datos de archivos
            entries = []
            for i, (name, path) in enumerate(files):
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if progress:
                    progress(i, total, name)
                offset = f.tell()
                data = path.read_bytes()
                f.write(apply_xor(data, mode))
                entries.append((offset, len(data)))

            # Volver al directorio y escribir entradas
            f.seek(directory_start)
            for name_bytes, (offset, length) in zip(encoded_names, entries):
                f.write(name_bytes.ljust(NAME_SIZE, b'\x00'))
                write_uint64_le(f, offset)
                write_uint64_le(f, length)
        os.replace(tmp_file, pd_file)
    except BaseException:
        # No dejar un .PD a medio escribir
        try:
            tmp_file.unlink()
        except OSError:
            pass
        raise

    if progress:
        progress(total, total, '')
    return mode, total
