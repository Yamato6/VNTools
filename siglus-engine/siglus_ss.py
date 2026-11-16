"""
siglus_ss - Escenas compiladas de SiglusEngine (.ss)

    u32 0x84 (tamaño de cabecera)
    16 × (u32 offset, u32 cantidad): bytecode (offset, tamaño), índice de cadenas, datos de
        cadenas, etiquetas, z-etiquetas, etiquetas de comando, propiedades, comandos, nombres...,
        read flags (el último par)
    Cadenas: índice de (u32 offset, u32 longitud) en caracteres; cada carácter UTF-16 va con
        XOR (28807 * número de cadena) & 0xFFFF.

El bytecode se recorre entero (opcodes de CD_NL a CD_SEL_BLOCK_END). Una cadena empujada
(CD_PUSH con forma str) seguida de CD_TEXT es una línea de texto; seguida de CD_NAME, el nombre
de quien habla. Las demás cadenas que se pasan a comandos (elecciones, títulos...) también son
traducibles salvo las que parecen nombres de recursos. La voz es un entero escena*100000 + n
empujado antes de la línea (koe\\z<escena>.ovk, entrada n).

Las cadenas se referencian por número: para traducir basta con reescribir la tabla de cadenas,
así que una línea puede tener cualquier longitud y el bytecode no cambia.
Referencia de los opcodes: siglus_rs (xmoezzz, MPL-2.0), siglus_ss_decompiler.
"""

import re
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

STRING_KEY = 28807

CD_NL, CD_PUSH, CD_POP, CD_COPY, CD_PROPERTY, CD_COPY_ELM, CD_DEC_PROP, CD_ELM_POINT, CD_ARG = range(1, 10)
CD_GOTO, CD_GOTO_TRUE, CD_GOTO_FALSE, CD_GOSUB, CD_GOSUBSTR, CD_RETURN, CD_EOF = range(0x10, 0x17)
CD_ASSIGN, CD_OPERATE_1, CD_OPERATE_2 = 0x20, 0x21, 0x22
CD_COMMAND, CD_TEXT, CD_NAME, CD_SEL_BLOCK_START, CD_SEL_BLOCK_END = 0x30, 0x31, 0x32, 0x33, 0x34
FM_INT, FM_STR, FM_LIST = 10, 20, -1

K_TEXT, K_NAME, K_OTHER = 'text', 'name', 'other'

# Cadenas que son nombres de recursos o identificadores (no se traducen)
_RESOURCE_RE = re.compile(r'^[\x21-\x7e]*$')
_MARK_RE = re.compile(r'_st_\d+$')          # marcas de estado ('【Marika】_st_0108')


def is_resource(s: str, scene_names=()) -> bool:
    """Identificadores ASCII sin espacios ('cg_mr01_0101'), nombres de escena y marcas."""
    if s in scene_names or _MARK_RE.search(s):
        return True
    return bool(_RESOURCE_RE.match(s)) and (not s or not s.isalpha() or s.islower() or '_' in s)


@dataclass
class Line:
    index: int              # número de la cadena
    kind: str
    voice: int = 0          # escena*100000 + entrada, o 0


@dataclass
class Scene:
    data: bytes
    hl: tuple                       # los 32 campos de la cabecera
    strings: List[str]
    lines: List[Line] = field(default_factory=list)     # traducibles, en orden de aparición


def header(ss: bytes) -> tuple:
    if len(ss) < 0x84 or struct.unpack_from('<I', ss, 0)[0] != 0x84:
        raise ValueError("Not a Siglus scene (.ss)")
    return struct.unpack_from('<32i', ss, 4)


def read_strings(ss: bytes, hl: Optional[tuple] = None) -> List[str]:
    hl = hl or header(ss)
    idx_off, count, data_off = hl[2], hl[3], hl[4]
    out = []
    for x in range(count):
        o, n = struct.unpack_from('<II', ss, idx_off + 8 * x)
        k = (STRING_KEY * x) & 0xFFFF
        raw = struct.unpack_from(f'<{n}H', ss, data_off + 2 * o)
        out.append(struct.pack(f'<{n}H', *[c ^ k for c in raw]).decode('utf-16le', 'replace'))
    return out


def write_strings(ss: bytes, strings: List[str]) -> bytes:
    """El .ss con una tabla de cadenas nueva (mismo número de cadenas)."""
    hl = list(header(ss))
    idx_off, count, data_off = hl[2], hl[3], hl[4]
    if len(strings) != count:
        raise ValueError("The number of strings cannot change")
    old_len = 0
    for x in range(count):
        o, n = struct.unpack_from('<II', ss, idx_off + 8 * x)
        old_len = max(old_len, o + n)
    out = bytearray(ss)
    pool = bytearray()
    pos = 0
    for x, s in enumerate(strings):
        units = struct.unpack(f'<{len(s.encode("utf-16le")) // 2}H', s.encode('utf-16le'))
        k = (STRING_KEY * x) & 0xFFFF
        pool += struct.pack(f'<{len(units)}H', *[c ^ k for c in units])
        struct.pack_into('<II', out, idx_off + 8 * x, pos, len(units))
        pos += len(units)
    if len(pool) <= 2 * old_len:
        # Cabe en el sitio de la tabla vieja (el resto queda a cero)
        out[data_off:data_off + 2 * old_len] = pool + bytes(2 * old_len - len(pool))
        new_off = data_off
    elif data_off + 2 * old_len >= len(ss):
        del out[data_off:]                  # la tabla vieja era lo último: se sustituye
        new_off = data_off
        out += pool
    else:
        new_off = len(out)                  # la nueva va al final
        out += pool
    hl[4] = new_off
    struct.pack_into('<32i', out, 4, *hl)
    return bytes(out)


# =============================================================================
# Bytecode
# =============================================================================

def disassemble(ss: bytes, hl: Optional[tuple] = None):
    """
    [(offset, opcode, argumentos)] de todo el bytecode. Algunos CD_COMMAND llevan detrás un
    read flag opcional: se reconoce porque es el siguiente número de la lista de read flags y
    su línea coincide con la actual (como hace siglus_rs).
    """
    hl = hl or header(ss)
    code = ss[hl[0]:hl[0] + hl[1]]
    flags = list(struct.unpack_from(f'<{hl[31]}i', ss, hl[30])) if hl[31] > 0 else []
    n_code = len(code)
    p = 0
    out = []
    line = 0
    next_flag = 0

    def i32():
        nonlocal p
        v = struct.unpack_from('<i', code, p)[0]
        p += 4
        return v

    def forms(depth=0):
        n = i32()
        if not 0 <= n < 1024 or depth > 16:
            raise ValueError("bad argument list")
        r = []
        for _ in range(n):
            f = i32()
            r.append(forms(depth + 1) if f == FM_LIST else f)
        return r

    while p < n_code:
        at = p
        op = code[p]
        p += 1
        if op in (0x00, CD_PROPERTY, CD_COPY_ELM, CD_ELM_POINT, CD_ARG, CD_EOF, CD_NAME,
                  CD_SEL_BLOCK_START, CD_SEL_BLOCK_END):
            a = ()
        elif op == CD_NL:
            line = i32()
            a = (line,)
        elif op in (CD_POP, CD_COPY, CD_GOTO, CD_GOTO_TRUE, CD_GOTO_FALSE):
            a = (i32(),)
        elif op == CD_TEXT:
            a = (i32(),)
            next_flag = a[0] + 1
        elif op in (CD_PUSH, CD_DEC_PROP):
            a = (i32(), i32())
        elif op in (CD_GOSUB, CD_GOSUBSTR):
            a = (i32(), forms())
        elif op == CD_RETURN:
            a = (forms(),)
        elif op == CD_ASSIGN:
            a = (i32(), i32(), i32())
        elif op == CD_OPERATE_1:
            a = (i32(), code[p])
            p += 1
        elif op == CD_OPERATE_2:
            a = (i32(), i32(), code[p])
            p += 1
        elif op == CD_COMMAND:
            alid = i32()
            arg_forms = forms()
            named = [i32() for _ in range(i32())]
            ret = i32()
            flag = None
            if next_flag < len(flags) and flags[next_flag] == line and p + 4 <= n_code \
                    and struct.unpack_from('<i', code, p)[0] == next_flag:
                flag = i32()
                next_flag += 1
            a = (alid, arg_forms, named, ret, flag)
        else:
            raise ValueError(f"unknown opcode {op:#04x} at {at:#x}")
        out.append((at, op, a))
    return out


def analyze(ss: bytes, voices: Optional[Dict[int, Set[int]]] = None, scene_names=()) -> Scene:
    """
    Cadenas y líneas traducibles de una escena. voices: {escena de voz: {entradas}} de los .ovk,
    para reconocer los ids de voz; sin él, cualquier entero >= 100000 que lo parezca.
    scene_names: nombres de las escenas del juego (se pasan a comandos de salto: no se traducen).
    """
    hl = header(ss)
    strings = read_strings(ss, hl)
    scene = Scene(ss, hl, strings)
    kinds: Dict[int, str] = {}
    order: List[int] = []
    voice_of: Dict[int, int] = {}
    pending_voice = 0
    pushed: List[int] = []           # cadenas empujadas desde el último comando
    for _at, op, a in disassemble(ss, hl):
        if op == CD_PUSH:
            form, value = a
            if form == FM_STR and 0 <= value < len(strings):
                pushed.append(value)
            elif form == FM_INT and value >= 100000:
                sc, n = divmod(value, 100000)
                if voices is None or n in voices.get(sc, ()):
                    pending_voice = value
        elif op in (CD_TEXT, CD_NAME) and pushed:
            idx = pushed.pop()
            kind = K_TEXT if op == CD_TEXT else K_NAME
            if kinds.get(idx) != K_TEXT:
                kinds[idx] = kind
            if idx not in order:
                order.append(idx)
            if op == CD_TEXT:
                if pending_voice and idx not in voice_of:
                    voice_of[idx] = pending_voice
                pending_voice = 0
            pushed.clear()
        elif op in (CD_COMMAND, CD_GOSUB, CD_GOSUBSTR, CD_RETURN, CD_ASSIGN):
            for idx in pushed:
                if idx not in kinds and strings[idx].strip() and not is_resource(strings[idx], scene_names):
                    kinds[idx] = K_OTHER
                    order.append(idx)
            pushed.clear()
    scene.lines = [Line(i, kinds[i], voice_of.get(i, 0)) for i in order]
    return scene
