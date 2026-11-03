"""
nexas_compress - Compresiones de los archivos PAC del motor NeXAS

Huffman (índice de los PAC y entradas de tipo 2), bits MSB primero:
    árbol en preorden: bit 1 = nodo interno (izquierda y luego derecha),
                       bit 0 = hoja seguida de 8 bits con el símbolo
    datos: se recorre el árbol desde la raíz (0 = izquierda, 1 = derecha)

LZSS (entradas de tipo 1): ventana de 4 KB que empieza en 0xFEE, byte de banderas
leído desde el bit bajo (1 = literal; 0 = 2 bytes: posición de 12 bits, longitud + 3).

Referencia: GARbro (ArcFormats/Nexas/ArcPAC.cs, HuffmanCompression.cs), licencia MIT.
"""

import heapq
from typing import Dict, List, Tuple

# =============================================================================
# Huffman
# =============================================================================


class _Bits:
    """Lee bits MSB primero."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.end = len(data) * 8

    def bit(self) -> int:
        if self.pos >= self.end:
            raise ValueError("Huffman: unexpected end of data")
        b = (self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1
        self.pos += 1
        return b

    def bits(self, n: int) -> int:
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v


def _read_tree(br: _Bits) -> Tuple[int, List[int], List[int]]:
    """Devuelve (raíz, hijos izquierdos, hijos derechos); los nodos internos van de 256 a 511."""
    lhs, rhs = [0] * 512, [0] * 512
    next_node = 256
    # Recorrido en preorden sin recursión: pila de (nodo, lado pendiente)
    b = br.bit()
    if not b:
        return br.bits(8), lhs, rhs
    root = next_node
    next_node += 1
    stack = [[root, 0]]
    while stack:
        top = stack[-1]
        if top[1] == 2:
            stack.pop()
            continue
        if br.bit():
            if next_node >= 512:
                raise ValueError("Huffman: invalid tree")
            child = next_node
            next_node += 1
            stack.append([child, 0])
        else:
            child = br.bits(8)
        (lhs if top[1] == 0 else rhs)[top[0]] = child
        top[1] += 1
    return root, lhs, rhs


def huffman_decode(data: bytes, out_len: int) -> bytes:
    br = _Bits(data)
    root, lhs, rhs = _read_tree(br)
    if root < 256:                      # un solo símbolo
        return bytes([root]) * out_len
    out = bytearray()

    # Hasta el siguiente límite de byte, bit a bit
    node = root
    while br.pos & 7 and len(out) < out_len:
        node = rhs[node] if br.bit() else lhs[node]
        if node < 256:
            out.append(node)
            node = root

    # Después, una tabla por (nodo, byte) que se llena a medida que se usa
    table: Dict[int, list] = {}
    data_mv = data
    i = br.pos >> 3
    n = len(data_mv)
    while len(out) < out_len and i < n:
        row = table.get(node)
        if row is None:
            row = table[node] = [None] * 256
        cell = row[data_mv[i]]
        if cell is None:
            emitted = bytearray()
            s = node
            byte = data_mv[i]
            for k in range(7, -1, -1):
                s = rhs[s] if (byte >> k) & 1 else lhs[s]
                if s < 256:
                    emitted.append(s)
                    s = root
            cell = row[byte] = (bytes(emitted), s)
        out += cell[0]
        node = cell[1]
        i += 1
    if len(out) < out_len:
        raise ValueError("Huffman: unexpected end of data")
    return bytes(out[:out_len])


def huffman_encode(data: bytes) -> bytes:
    """Codifica con un árbol de Huffman propio, en el formato que lee huffman_decode."""
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    used = [s for s in range(256) if freq[s]]
    if len(used) < 2:
        # El árbol necesita al menos dos hojas
        used = (used + [s for s in (0, 1) if s not in used])[:2]

    # Nodos: hojas 0..255, internos como tuplas (izquierdo, derecho)
    heap = [(freq[s], s, s) for s in used]
    heapq.heapify(heap)
    order = 256
    while len(heap) > 1:
        f1, _o1, n1 = heapq.heappop(heap)
        f2, _o2, n2 = heapq.heappop(heap)
        heapq.heappush(heap, (f1 + f2, order, (n1, n2)))
        order += 1
    tree = heap[0][2]

    header: List[str] = []
    codes: Dict[int, str] = {}
    stack = [(tree, '')]
    while stack:
        node, code = stack.pop()
        if isinstance(node, tuple):
            header.append('1')
            stack.append((node[1], code + '1'))    # se procesa después del izquierdo
            stack.append((node[0], code + '0'))
        else:
            header.append('0' + format(node, '08b'))
            codes[node] = code

    table = [codes.get(s, '') for s in range(256)]
    bits = ''.join(header) + ''.join(map(table.__getitem__, data))
    bits += '0' * (-len(bits) % 8)
    return int(bits, 2).to_bytes(len(bits) // 8, 'big') if bits else b''


# =============================================================================
# LZSS
# =============================================================================

def lzss_decode(src: bytes, out_len: int) -> bytes:
    win = bytearray(0x1000)
    wp = 0xFEE
    out = bytearray()
    i, n = 0, len(src)
    while i < n and len(out) < out_len:
        flags = src[i] | 0xFF00
        i += 1
        while flags & 0x100 and i < n and len(out) < out_len:
            if flags & 1:
                c = src[i]
                i += 1
                out.append(c)
                win[wp] = c
                wp = (wp + 1) & 0xFFF
            else:
                if i + 1 >= n:
                    break
                lo, hi = src[i], src[i + 1]
                i += 2
                off = lo | ((hi & 0xF0) << 4)
                for k in range((hi & 0x0F) + 3):
                    c = win[(off + k) & 0xFFF]
                    out.append(c)
                    win[wp] = c
                    wp = (wp + 1) & 0xFFF
            flags >>= 1
    return bytes(out[:out_len])


def lzss_encode(data: bytes) -> bytes:
    """LZSS válido solo con literales (no comprime, pero el motor lo lee igual)."""
    out = bytearray()
    for i in range(0, len(data), 8):
        chunk = data[i:i + 8]
        out.append((1 << len(chunk)) - 1)
        out += chunk
    return bytes(out)
