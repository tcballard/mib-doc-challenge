"""Trusted-text extraction from PDF packets.

The core adversarial-robustness idea: only *visible* document evidence is
trusted. Hidden PDF text (white-on-white, tiny fonts, text painted outside the
page crop) is captured separately and never used to fill fields. This mirrors
the MIB field manual's evidence-precedence rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None


# A span is "hidden" (untrusted) if any of these hold.
WHITE_THRESHOLD = 235  # channel value above which we treat ink as white/near-bg
MIN_FONT_SIZE = 5.0    # spans smaller than this are decoys, not readable evidence


@dataclass
class Line:
    text: str
    size: float
    x0: float
    y0: float
    x1: float
    y1: float
    hidden: bool


@dataclass
class Page:
    index: int
    rotation: int
    title: str
    lines: List[Line] = field(default_factory=list)
    hidden_lines: List[str] = field(default_factory=list)
    n_images: int = 0
    ocr_used: bool = False

    @property
    def visible_lines(self) -> List[str]:
        return [ln.text for ln in self.lines if not ln.hidden]

    @property
    def visible_text(self) -> str:
        return "\n".join(self.visible_lines)


def _is_white(color: int) -> bool:
    r = (color >> 16) & 255
    g = (color >> 8) & 255
    b = color & 255
    return r > WHITE_THRESHOLD and g > WHITE_THRESHOLD and b > WHITE_THRESHOLD


def _page_lines(page) -> List[Line]:
    """Reconstruct lines with a trusted/hidden classification per line."""
    crop = page.rect
    data = page.get_text("dict")
    out: List[Line] = []
    for block in data.get("blocks", []):
        if "lines" not in block:
            continue
        for line in block["lines"]:
            spans = line.get("spans", [])
            if not spans:
                continue
            parts = []
            sizes = []
            any_visible = False
            x0 = y0 = 1e9
            x1 = y1 = -1e9
            for s in spans:
                txt = s.get("text", "")
                if not txt.strip():
                    continue
                color = s.get("color", 0)
                size = s.get("size", 0.0)
                bbox = fitz.Rect(s.get("bbox"))
                hidden = (
                    _is_white(color)
                    or size < MIN_FONT_SIZE
                    or not crop.intersects(bbox)
                )
                parts.append(txt)
                sizes.append(size)
                if not hidden:
                    any_visible = True
                x0 = min(x0, bbox.x0)
                y0 = min(y0, bbox.y0)
                x1 = max(x1, bbox.x1)
                y1 = max(y1, bbox.y1)
            text = "".join(parts).strip()
            if not text:
                continue
            out.append(
                Line(
                    text=text,
                    size=max(sizes) if sizes else 0.0,
                    x0=x0, y0=y0, x1=x1, y1=y1,
                    hidden=not any_visible,
                )
            )
    # Reading order: top-to-bottom, then left-to-right.
    out.sort(key=lambda l: (round(l.y0, 1), l.x0))
    return out


def _title(visible: List[str]) -> str:
    for t in visible:
        if t.startswith("Packet ") or t == "Synthetic hiring challenge document":
            continue
        return t
    return ""


def extract_pages(path: str, ocr_fn=None) -> List[Page]:
    """Extract structured, trust-classified pages from a PDF.

    ``ocr_fn`` (optional) is called as ``ocr_fn(fitz_page) -> List[str]`` to
    recover visible text from rasterized/scanned pages that carry no usable
    text layer. OCR output is treated as *visible* evidence.
    """
    doc = fitz.open(path)
    pages: List[Page] = []
    for i in range(doc.page_count):
        p = doc[i]
        lines = _page_lines(p)
        n_images = len(p.get_images())
        visible = [l.text for l in lines if not l.hidden]
        # Strip boilerplate for the "meaningful visible content" test.
        meaningful = [
            t for t in visible
            if not t.startswith("Packet ") and t != "Synthetic hiring challenge document"
        ]
        page = Page(
            index=i,
            rotation=p.rotation,
            title=_title(visible),
            lines=lines,
            hidden_lines=[l.text for l in lines if l.hidden],
            n_images=n_images,
        )
        # Scanned page: has an image but no meaningful text layer -> OCR.
        if ocr_fn is not None and not meaningful and n_images:
            ocr_lines = ocr_fn(p)
            if ocr_lines:
                page.ocr_used = True
                for t in ocr_lines:
                    page.lines.append(
                        Line(text=t, size=10.0, x0=0, y0=0, x1=0, y1=0, hidden=False)
                    )
                if not page.title:
                    page.title = _title(page.visible_lines)
        pages.append(page)
    doc.close()
    return pages
