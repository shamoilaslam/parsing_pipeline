"""A minimal TrueType font whose cmap covers private-use code points.

PyMuPDF's built-in fonts replace those code points with U+FFFD, so a test that
needs a text layer full of legacy InPage-style glyphs has to embed its own font.
"""

from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen


def build_pua_font(path: Path, codepoints: list[int]) -> Path:
    names = [".notdef"] + [f"uni{codepoint:04X}" for codepoint in codepoints]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({codepoint: f"uni{codepoint:04X}" for codepoint in codepoints})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        pen.moveTo((100, 0))
        pen.lineTo((100, 700))
        pen.lineTo((500, 700))
        pen.lineTo((500, 0))
        pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 100) for name in names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "PuaTest", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.save(str(path))
    return path
