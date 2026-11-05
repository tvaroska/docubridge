"""Declarative vector PDF renderer for scenario sample documents.

Pages are a list of primitive drawing ops (`text`, `rect_fill`, `rect_stroke`,
`hline`) read from `scenarios/<name>/pdf/*.yaml`; this module only knows how to
turn those ops into a searchable PDF-1.4 byte stream (Helvetica / Helvetica-Bold /
Courier Type1 text layers, US Letter) with no external rendering dependency.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from typing import Any

from contract.scenario import ScenarioPdfAsset

Rgb = tuple[float, float, float]


@dataclass(frozen=True)
class RenderedPdf:
    """A scenario PDF asset together with its rendered bytes."""

    asset: ScenarioPdfAsset
    pdf_bytes: bytes
    sha256: str

    @property
    def sample_id(self) -> str:
        return self.asset.asset_id

    @property
    def filename(self) -> str:
        return self.asset.filename


def _escape_pdf_text(text: str) -> str:
    ascii_clean = (
        text.replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u00b7", "|")
        .encode("ascii", errors="replace")
        .decode("ascii")
    )
    return ascii_clean.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


class _PdfPageCanvas:
    def __init__(self) -> None:
        self._ops: list[str] = []

    def rect_fill(self, x: float, y: float, w: float, h: float, rgb: Rgb) -> None:
        r, g, b = rgb
        self._ops.append(
            f"q {r:.3f} {g:.3f} {b:.3f} rg {x:.1f} {y:.1f} {w:.1f} {h:.1f} re f Q"
        )

    def rect_stroke(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        rgb: Rgb = (0.82, 0.85, 0.89),
        line_width: float = 0.8,
    ) -> None:
        r, g, b = rgb
        self._ops.append(
            f"q {line_width:.2f} w {r:.3f} {g:.3f} {b:.3f} RG "
            f"{x:.1f} {y:.1f} {w:.1f} {h:.1f} re S Q"
        )

    def hline(
        self,
        x1: float,
        x2: float,
        y: float,
        rgb: Rgb = (0.85, 0.87, 0.90),
        line_width: float = 0.6,
    ) -> None:
        r, g, b = rgb
        self._ops.append(
            f"q {line_width:.2f} w {r:.3f} {g:.3f} {b:.3f} RG "
            f"{x1:.1f} {y:.1f} m {x2:.1f} {y:.1f} l S Q"
        )

    def text(
        self,
        x: float,
        y: float,
        value: str,
        *,
        font: str = "F1",
        size: float = 10.0,
        rgb: Rgb = (0.12, 0.14, 0.18),
    ) -> None:
        r, g, b = rgb
        self._ops.append(
            f"BT /{font} {size:.1f} Tf {r:.3f} {g:.3f} {b:.3f} rg "
            f"{x:.1f} {y:.1f} Td ({_escape_pdf_text(value)}) Tj ET"
        )

    def build_stream(self) -> bytes:
        return ("\n".join(self._ops) + "\n").encode("ascii")


def _assemble_pdf(pages: list[_PdfPageCanvas], *, title: str) -> bytes:
    # Object layout: 1 Catalog, 2 Pages, 3-5 fonts (F1..F3), 6 Info, then
    # (Page, ContentStream) pairs from object 7.
    objects: list[bytes] = []
    page_obj_ids = [7 + 2 * i for i in range(len(pages))]
    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode("ascii")
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    objects.append(f"<< /Title ({_escape_pdf_text(title)}) >>".encode("ascii"))

    for page_id, canvas in zip(page_obj_ids, pages, strict=True):
        stream_bytes = canvas.build_stream()
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R /F2 4 0 R /F3 5 0 R >> >> "
                f"/Contents {page_id + 1} 0 R >>"
            ).encode("ascii")
        )
        objects.append(
            f"<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
            + stream_bytes
            + b"endstream"
        )

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for obj_num, body in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{obj_num} 0 obj\n".encode("ascii"))
        buf.write(body)
        buf.write(b"\nendobj\n")
    xref_pos = buf.tell()
    total = len(objects) + 1
    buf.write(f"xref\n0 {total}\n".encode("ascii"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(
        f"trailer\n<< /Size {total} /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return buf.getvalue()


def _draw(canvas: _PdfPageCanvas, element: dict[str, Any]) -> None:
    kind = element.get("op")
    if kind == "text":
        canvas.text(
            float(element["x"]),
            float(element["y"]),
            str(element["value"]),
            font=str(element.get("font", "F1")),
            size=float(element.get("size", 10.0)),
            rgb=tuple(element.get("rgb", (0.12, 0.14, 0.18))),
        )
    elif kind == "rect_fill":
        canvas.rect_fill(
            float(element["x"]),
            float(element["y"]),
            float(element["w"]),
            float(element["h"]),
            tuple(element["rgb"]),
        )
    elif kind == "rect_stroke":
        canvas.rect_stroke(
            float(element["x"]),
            float(element["y"]),
            float(element["w"]),
            float(element["h"]),
            tuple(element.get("rgb", (0.82, 0.85, 0.89))),
            float(element.get("line_width", 0.8)),
        )
    elif kind == "hline":
        canvas.hline(
            float(element["x1"]),
            float(element["x2"]),
            float(element["y"]),
            tuple(element.get("rgb", (0.85, 0.87, 0.90))),
            float(element.get("line_width", 0.6)),
        )
    else:
        raise ValueError(f"unknown PDF drawing op {kind!r}")


def render_pdf_asset(asset: ScenarioPdfAsset) -> RenderedPdf:
    pages: list[_PdfPageCanvas] = []
    for page in asset.pages:
        canvas = _PdfPageCanvas()
        for element in page.get("elements", []):
            _draw(canvas, element)
        pages.append(canvas)
    if len(pages) != asset.page_count:
        raise ValueError(
            f"pdf asset {asset.asset_id!r} declares page_count={asset.page_count} "
            f"but has {len(pages)} page specs"
        )
    raw = _assemble_pdf(pages, title=asset.document_title)
    return RenderedPdf(
        asset=asset, pdf_bytes=raw, sha256=hashlib.sha256(raw).hexdigest()
    )


def pdf_metadata(rendered: RenderedPdf, *, url_base: str) -> dict[str, Any]:
    a = rendered.asset
    return {
        "sample_id": a.asset_id,
        "filename": a.filename,
        "title": a.title,
        "subtitle": a.subtitle,
        "batch_stage": a.batch_stage,
        "page_count": a.page_count,
        "highlight_page": a.highlight_page,
        "size_bytes": len(rendered.pdf_bytes),
        "sha256": rendered.sha256,
        "pdf_url": f"{url_base}/{a.filename}",
    }


def build_pdf_zip(assets: list[RenderedPdf]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rendered in assets:
            zf.writestr(rendered.filename, rendered.pdf_bytes)
    return buf.getvalue()


__all__ = ["RenderedPdf", "build_pdf_zip", "pdf_metadata", "render_pdf_asset"]
