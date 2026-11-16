"""
siglus_pck - Scene.pck de SiglusEngine (todas las escenas compiladas del juego)

    u32 tamaño de cabecera
    10 × (u32 offset, u32 cantidad): var_info, var_name_index, var_name, cmd_info,
        cmd_name_index, cmd_name, scn_name_index, scn_name, scn_info, scn_data
    u32 extra_key_use · u32 source_header_size

scn_name_index da (offset, longitud) en caracteres UTF-16 dentro de scn_name; scn_info da
(offset, tamaño) de cada escena dentro de scn_data. Cada escena: XOR con la clave de 16 bytes
del juego (si extra_key_use), XOR con TABLE_A y LZ. El resultado es un .ss (siglus_ss).

Al escribir, las escenas cambiadas se guardan con LZ de solo literales y se vuelven a cifrar;
el resto del archivo se copia tal cual.
Referencia del formato: hiroshil/SiglusEngine y siglus_rs (xmoezzz, MPL-2.0).
"""

import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from siglus_common import atomic_write
from siglus_crypto import TABLE_A, candidate_keys, find_key, lz_pack, lz_unpack, lz_unpack_raw, xor_repeat

FIELDS = ("var_info", "var_name_index", "var_name", "cmd_info", "cmd_name_index", "cmd_name",
          "scn_name_index", "scn_name", "scn_info", "scn_data")
SS_HEADER_SIZE = 0x84


class ScenePck:
    """Scene.pck en memoria. key: clave de 16 bytes; si falta, se busca (conocidas y find_key)."""

    def __init__(self, path, key: Optional[bytes] = None):
        self.path = Path(path)
        self.b = self.path.read_bytes()
        b = self.b
        if len(b) < 92:
            raise ValueError(f"{self.path.name} is not a Scene.pck")
        self.header_size, = struct.unpack_from('<I', b, 0)
        self.t = {name: struct.unpack_from('<II', b, 4 + 8 * k) for k, name in enumerate(FIELDS)}
        self.extra_key_use, self.source_header_size = struct.unpack_from('<II', b, 4 + 8 * len(FIELDS))
        off, cnt = self.t['scn_name_index']
        base = self.t['scn_name'][0]
        self.names: List[str] = []
        for i in range(cnt):
            o, n = struct.unpack_from('<II', b, off + 8 * i)
            self.names.append(b[base + 2 * o:base + 2 * (o + n)].decode('utf-16le', 'replace'))
        off, cnt = self.t['scn_info']
        self.info: List[Tuple[int, int]] = [struct.unpack_from('<II', b, off + 8 * i) for i in range(cnt)]
        data = self.t['scn_data'][0]
        for o, n in self.info:
            if data + o + n > len(b):
                raise ValueError(f"{self.path.name}: a scene is outside the file")
        self.key = None
        if self.extra_key_use:
            self.key = key if key and self._valid_key(key) else self._detect_key()
            if self.key is None:
                raise ValueError(f"The key of {self.path.name} was not found")

    def __len__(self):
        return len(self.info)

    def raw(self, i: int) -> bytes:
        o, n = self.info[i]
        s = self.t['scn_data'][0] + o
        return self.b[s:s + n]

    def _decrypt(self, d: bytes, key: Optional[bytes]) -> bytes:
        if self.extra_key_use:
            d = xor_repeat(d, key)
        return xor_repeat(d, TABLE_A)

    def scene(self, i: int) -> bytes:
        """El .ss descomprimido de la escena i."""
        d = self._decrypt(self.raw(i), self.key)
        return lz_unpack(d)

    def index(self, name: str) -> int:
        return self.names.index(name)

    # -- clave ---------------------------------------------------------------------

    def _valid_key(self, key: bytes, sample: Optional[List[int]] = None) -> bool:
        """La clave descifra escenas cuyo tamaño comprimido cuadra y que empiezan por 0x84."""
        indexes = sample if sample is not None else range(len(self.info))
        for i in indexes:
            raw = self.raw(i)
            if len(raw) < 12:
                continue
            d = self._decrypt(raw, key)
            packed, size = struct.unpack_from('<II', d, 0)
            if packed != len(raw) or size < SS_HEADER_SIZE:
                return False
            try:
                head = lz_unpack_raw(d[8:72], 4)        # basta el principio
            except (ValueError, IndexError):
                return False
            if head != struct.pack('<I', SS_HEADER_SIZE):
                return False
        return True

    def _detect_key(self) -> Optional[bytes]:
        sample = list(range(0, len(self.info), max(1, len(self.info) // 12)))
        for key in candidate_keys():
            if self._valid_key(key, sample) and self._valid_key(key):
                return key
        raws = [self.raw(i) for i in range(len(self.info))]
        return find_key(raws, check=lambda k: self._valid_key(k, sample) and self._valid_key(k))

    # -- escritura -----------------------------------------------------------------

    def encode_scene(self, ss: bytes) -> bytes:
        packed = lz_pack(ss)
        packed = xor_repeat(packed, TABLE_A)
        return xor_repeat(packed, self.key) if self.extra_key_use else packed

    def write(self, out_path, scenes: Dict[int, bytes]):
        """Escribe un Scene.pck con las escenas de scenes ({índice: .ss}) y el resto sin cambios."""
        info_off, count = self.t['scn_info']
        data_off = self.t['scn_data'][0]
        if not (info_off + 8 * count <= data_off):
            raise ValueError("Unexpected Scene.pck layout")
        end = data_off + max((o + n for o, n in self.info), default=0)
        datas = [self.encode_scene(scenes[i]) if i in scenes else self.raw(i) for i in range(count)]
        table = bytearray()
        pos = 0
        for d in datas:
            table += struct.pack('<II', pos, len(d))
            pos += len(d)
        with atomic_write(out_path) as f:
            f.write(self.b[:info_off])
            f.write(table)
            f.write(self.b[info_off + len(table):data_off])
            for d in datas:
                f.write(d)
            f.write(self.b[end:])
