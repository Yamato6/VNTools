"""
fvp_font - Genera una fuente personalizada a partir de una tabla de caracteres

El motor solo admite Shift-JIS, así que los caracteres que no existen en
Shift-JIS (á, ñ, ¿...) se escriben como otros que sí existen (katakana de
ancho simple). Este módulo modifica la fuente para que esos caracteres se
dibujen con el glyph del carácter original de la propia fuente: el texto
acentuado queda con el mismo estilo y ancho que el resto.

Usa la misma tabla que hcb-rebuild --charmap (p. ej. 'á=ｧ').
"""

from pathlib import Path
from typing import List, Tuple

from fontTools.ttLib import TTFont

from fvp_hcb import CharMap


def rename_family(font: TTFont, family: str):
    """
    Cambia el nombre de la familia para que la fuente no choque con la original
    instalada (Windows usa la del sistema si hay dos con el mismo nombre).
    """
    names = font['name']
    old = names.getDebugName(1)
    ps_name = family.replace(' ', '').replace('+', 'Plus') + '-Regular'
    for rec in names.names:
        if rec.nameID in (1, 3, 4, 16, 18):
            text = rec.toUnicode()
            rec.string = text.replace(old, family) if old and old in text else family
        elif rec.nameID == 6:
            rec.string = ps_name


def patch_font(base_font, charmap_path, output_font, family=None) -> List[Tuple[str, str, str]]:
    """
    Copia base_font en output_font haciendo que cada reemplazo de la tabla
    use el glyph del carácter original. Si se indica family, también cambia
    el nombre de la fuente. Devuelve [(original, reemplazo, glyph)].
    """
    charmap = CharMap.load(charmap_path)
    if not charmap.pairs:
        raise ValueError("La tabla de caracteres está vacía")

    font = TTFont(base_font)
    best = font.getBestCmap()
    subtables = [t for t in font['cmap'].tables if t.isUnicode() and t.format in (4, 12)]
    if not subtables:
        raise ValueError("La fuente no tiene una tabla cmap Unicode compatible")

    changes = []
    for src, dst in charmap.pairs.items():
        if len(src) != 1 or len(dst) != 1:
            raise ValueError(f"Para la fuente cada pareja debe ser de un carácter: '{src}={dst}'")
        glyph = best.get(ord(src))
        if glyph is None:
            raise ValueError(f"La fuente base no tiene el carácter '{src}' (U+{ord(src):04X})")
        for table in subtables:
            table.cmap[ord(dst)] = glyph
        changes.append((src, dst, glyph))

    if family:
        rename_family(font, family)
    Path(output_font).parent.mkdir(parents=True, exist_ok=True)
    font.save(output_font)
    return changes
