"""Scanned-PDF authoring tool — a raster twin of the carrier-C quote (no text layer).

The Document AI pre-pass (``bridge/extraction/docai.py``) exists for **scanned/raster**
documents: a PDF whose page is a picture, with no text a PDF reader can extract. This
module renders exactly the lines the native-digital asset prints
(``tests.corpus_pdf.quote_pdf_title`` / ``quote_pdf_lines`` for
``benefits-carrier-c-eur-fr``) into an 8-bit grayscale bitmap with an embedded 5x7
bitmap font, and wraps that bitmap as the page's only content — one ``/Image``
XObject, **no** ``/Font`` and **no** ``BT``/``ET`` text operators. ``pdftotext``
extracts nothing from it; only OCR can read it.

Same discipline as ``tests/corpus_pdf.py``:

* **Standard library only** (``zlib`` for ``/FlateDecode``) — no imaging dependency.
* **The page prints only oracle values.** Every line comes from the corpus record,
  and a character the glyph table cannot draw raises ``ValueError`` instead of being
  silently dropped (a dropped glyph would print a value the oracle does not declare).
* **Byte-deterministic** — no ``/CreationDate``, no ``/ID``, no clock read. (The
  Flate stream is deterministic for a given zlib build; a committed copy's drift test
  would pin that.)

It writes to a path given on the command line
(``uv run python -m tests.corpus_scanned_pdf /tmp/scanned.pdf``), never into
``wiki/evals/`` — adding it to the corpus is a recorded proposal (DECISIONS.md).
"""

from __future__ import annotations

import sys
import zlib
from pathlib import Path

from tests.corpus_pdf import load_records, quote_pdf_lines, quote_pdf_title

SCANNED_DOC_ID = "benefits-carrier-c-eur-fr"
DEFAULT_DEST = Path("/tmp/benefits-carrier-c-eur-fr-scanned.pdf")

# Page geometry: US Letter at 300 dpi (Document AI's recommended scan resolution).
DPI = 300
PAGE_W_PT, PAGE_H_PT = 612, 792
IMG_W, IMG_H = PAGE_W_PT * DPI // 72, PAGE_H_PT * DPI // 72  # 2550 x 3300
MARGIN_X, MARGIN_Y = 150, 240
TITLE_SCALE, BODY_SCALE = 6, 5  # pixels per font dot
CELL_W, CELL_H = 6, 12  # font dots per character cell (5x9 glyph + spacing)
TITLE_GAP_DOTS = 6  # extra dots below the title line
INK, PAPER = 0x00, 0xFF

# A 5-dot-wide bitmap font. Each glyph is space-separated rows of 5 columns, top
# down: rows 0-6 sit on the baseline (cap height 7), rows 7-8 are the descender.
# ``#`` is ink. Covers printable ASCII letters/digits, common punctuation and the
# French accents a carrier quote uses; anything else raises (never dropped).
GLYPHS: dict[str, str] = {
    " ": "..... ..... ..... ..... ..... ..... .....",
    "0": ".###. #...# #..## #.#.# ##..# #...# .###.",
    "1": "..#.. .##.. ..#.. ..#.. ..#.. ..#.. .###.",
    "2": ".###. #...# ....# ...#. ..#.. .#... #####",
    "3": "####. ....# ....# .###. ....# ....# ####.",
    "4": "...#. ..##. .#.#. #..#. ##### ...#. ...#.",
    "5": "##### #.... ####. ....# ....# #...# .###.",
    "6": "..##. .#... #.... ####. #...# #...# .###.",
    "7": "##### ....# ...#. ..#.. .#... .#... .#...",
    "8": ".###. #...# #...# .###. #...# #...# .###.",
    "9": ".###. #...# #...# .#### ....# ...#. .##..",
    "A": ".###. #...# #...# ##### #...# #...# #...#",
    "B": "####. #...# #...# ####. #...# #...# ####.",
    "C": ".###. #...# #.... #.... #.... #...# .###.",
    "D": "###.. #..#. #...# #...# #...# #..#. ###..",
    "E": "##### #.... #.... ####. #.... #.... #####",
    "F": "##### #.... #.... ####. #.... #.... #....",
    "G": ".###. #...# #.... #.### #...# #...# .####",
    "H": "#...# #...# #...# ##### #...# #...# #...#",
    "I": ".###. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    "J": "..### ...#. ...#. ...#. ...#. #..#. .##..",
    "K": "#...# #..#. #.#.. ##... #.#.. #..#. #...#",
    "L": "#.... #.... #.... #.... #.... #.... #####",
    "M": "#...# ##.## #.#.# #.#.# #...# #...# #...#",
    "N": "#...# #...# ##..# #.#.# #..## #...# #...#",
    "O": ".###. #...# #...# #...# #...# #...# .###.",
    "P": "####. #...# #...# ####. #.... #.... #....",
    "Q": ".###. #...# #...# #...# #.#.# #..#. .##.#",
    "R": "####. #...# #...# ####. #.#.. #..#. #...#",
    "S": ".#### #.... #.... .###. ....# ....# ####.",
    "T": "##### ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "U": "#...# #...# #...# #...# #...# #...# .###.",
    "V": "#...# #...# #...# #...# #...# .#.#. ..#..",
    "W": "#...# #...# #...# #.#.# #.#.# #.#.# .#.#.",
    "X": "#...# #...# .#.#. ..#.. .#.#. #...# #...#",
    "Y": "#...# #...# .#.#. ..#.. ..#.. ..#.. ..#..",
    "Z": "##### ....# ...#. ..#.. .#... #.... #####",
    "a": "..... ..... .###. ....# .#### #...# .####",
    "b": "#.... #.... #.##. ##..# #...# #...# ####.",
    "c": "..... ..... .###. #.... #.... #...# .###.",
    "d": "....# ....# .##.# #..## #...# #...# .####",
    "e": "..... ..... .###. #...# ##### #.... .###.",
    "f": "..##. .#..# .#... ###.. .#... .#... .#...",
    "g": "..... ..... .#### #...# #...# #...# .#### ....# .###.",
    "h": "#.... #.... #.##. ##..# #...# #...# #...#",
    "i": "..#.. ..... .##.. ..#.. ..#.. ..#.. .###.",
    "j": "...#. ..... ..##. ...#. ...#. ...#. ...#. #..#. .##..",
    "k": "#.... #.... #..#. #.#.. ##... #.#.. #..#.",
    "l": ".##.. ..#.. ..#.. ..#.. ..#.. ..#.. .###.",
    "m": "..... ..... ##.#. #.#.# #.#.# #.#.# #.#.#",
    "n": "..... ..... #.##. ##..# #...# #...# #...#",
    "o": "..... ..... .###. #...# #...# #...# .###.",
    "p": "..... ..... ####. #...# #...# #...# ####. #.... #....",
    "q": "..... ..... .#### #...# #...# #...# .#### ....# ....#",
    "r": "..... ..... #.##. ##..# #.... #.... #....",
    "s": "..... ..... .###. #.... .###. ....# ####.",
    "t": ".#... .#... ###.. .#... .#... .#..# ..##.",
    "u": "..... ..... #...# #...# #...# #..## .##.#",
    "v": "..... ..... #...# #...# #...# .#.#. ..#..",
    "w": "..... ..... #...# #...# #.#.# #.#.# .#.#.",
    "x": "..... ..... #...# .#.#. ..#.. .#.#. #...#",
    "y": "..... ..... #...# #...# #...# #...# .#### ....# .###.",
    "z": "..... ..... ##### ...#. ..#.. .#... #####",
    "é": "...#. ..#.. .###. #...# ##### #.... .###.",
    "è": ".#... ..#.. .###. #...# ##### #.... .###.",
    "ê": "..#.. .#.#. .###. #...# ##### #.... .###.",
    "à": ".#... ..#.. .###. ....# .#### #...# .####",
    "ç": "..... ..... .###. #.... #.... #...# .###. ..#.. .##..",
    "'": "..#.. ..#.. .#... ..... ..... ..... .....",
    '"': ".#.#. .#.#. .#.#. ..... ..... ..... .....",
    "(": "...#. ..#.. .#... .#... .#... ..#.. ...#.",
    ")": ".#... ..#.. ...#. ...#. ...#. ..#.. .#...",
    ",": "..... ..... ..... ..... ..... .##.. ..#.. .#...",
    ".": "..... ..... ..... ..... ..... .##.. .##..",
    ":": "..... .##.. .##.. ..... .##.. .##.. .....",
    ";": "..... .##.. .##.. ..... .##.. ..#.. .#...",
    "!": "..#.. ..#.. ..#.. ..#.. ..#.. ..... ..#..",
    "?": ".###. #...# ....# ...#. ..#.. ..... ..#..",
    "-": "..... ..... ..... .###. ..... ..... .....",
    "—": "..... ..... ..... ##### ..... ..... .....",
    "/": "..... ....# ...#. ..#.. .#... #.... .....",
    "%": "##... ##..# ...#. ..#.. .#... #..## ...##",
    "&": ".##.. #..#. #.#.. .#... #.#.# #..#. .##.#",
    "+": "..... ..#.. ..#.. ##### ..#.. ..#.. .....",
    "=": "..... ..... ##### ..... ##### ..... .....",
    "|": "..#.. ..#.. ..#.. ..#.. ..#.. ..#.. ..#..",
    "€": "..### .#... ####. .#... ####. .#... ..###",
    "$": "..#.. .#### #.#.. .###. ..#.# ####. ..#..",
}


def _glyph_rows(char: str) -> list[str]:
    try:
        return GLYPHS[char].split()
    except KeyError:
        raise ValueError(
            f"scanned-PDF font has no glyph for {char!r} (U+{ord(char):04X}); add it "
            f"to GLYPHS — a character is never silently dropped from the page"
        ) from None


def _check_glyphs() -> None:
    for char, spec in GLYPHS.items():
        rows = spec.split()
        assert len(rows) in (7, 8, 9), (char, len(rows))
        assert all(len(r) == 5 and set(r) <= {".", "#"} for r in rows), char


def _draw_line(canvas: bytearray, text: str, x0: int, y0: int, scale: int) -> None:
    """Stamp one line of text into the row-major grayscale ``canvas`` (in place)."""
    ink_run = bytes([INK]) * scale
    for index, char in enumerate(text):
        cx = x0 + index * CELL_W * scale
        if cx + CELL_W * scale > IMG_W - MARGIN_X // 2:
            raise ValueError(f"line too wide for the page: {text!r}")
        for row, bits in enumerate(_glyph_rows(char)):
            for col, bit in enumerate(bits):
                if bit != "#":
                    continue
                px, py = cx + col * scale, y0 + row * scale
                for dy in range(scale):
                    start = (py + dy) * IMG_W + px
                    canvas[start : start + scale] = ink_run


def render_page(title: str, lines: list[str]) -> bytes:
    """Rasterize the title + body lines onto a white 300-dpi Letter page (8-bit gray)."""
    canvas = bytearray([PAPER]) * (IMG_W * IMG_H)
    y = MARGIN_Y
    _draw_line(canvas, title, MARGIN_X, y, TITLE_SCALE)
    y += (CELL_H + TITLE_GAP_DOTS) * TITLE_SCALE
    for line in lines:
        _draw_line(canvas, line, MARGIN_X, y, BODY_SCALE)
        y += CELL_H * BODY_SCALE
    if y > IMG_H - MARGIN_Y:
        raise ValueError("too many lines for one page")
    return bytes(canvas)


def build_image_pdf(pixels: bytes, width: int, height: int) -> bytes:
    """One page whose only content is a grayscale image XObject — no text layer."""
    image = zlib.compress(pixels, 9)
    content = b"q %d 0 0 %d 0 0 cm /Im0 Do Q\n" % (PAGE_W_PT, PAGE_H_PT)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
        b"/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
        % (PAGE_W_PT, PAGE_H_PT),
        b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
        b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
        b"/Length %d >>\nstream\n" % (width, height, len(image))
        + image
        + b"\nendstream",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")  # binary marker line
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)


def build_scanned_quote_pdf(record: dict) -> bytes:
    """The raster (scanned) PDF bytes for one corpus quote record."""
    _check_glyphs()
    pixels = render_page(quote_pdf_title(record), quote_pdf_lines(record))
    return build_image_pdf(pixels, IMG_W, IMG_H)


def write_scanned_quote_pdf(record: dict, dest: Path) -> Path:
    """Write ``record``'s scanned PDF to ``dest`` (parents created). Returns ``dest``."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(build_scanned_quote_pdf(record))
    return dest


def main(argv: list[str] | None = None) -> None:
    """Write the carrier-C scanned quote to ``argv[0]`` (default under ``/tmp``)."""
    args = sys.argv[1:] if argv is None else argv
    dest = Path(args[0]) if args else DEFAULT_DEST
    write_scanned_quote_pdf(load_records()[SCANNED_DOC_ID], dest)
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
