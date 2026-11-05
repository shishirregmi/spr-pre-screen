"""PDF text extraction with word boxes. Page text is rebuilt from words so every offset maps to a bbox."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pymupdf

from app import config
from app.schemas import BBox


@dataclass
class Word:
    start: int
    end: int
    bbox: BBox
    line: tuple[int, int]          # (block_no, line_no)


@dataclass
class Page:
    no: int                        # 1-based
    text: str
    words: list[Word]
    blocks: list[tuple[int, int]]  # (start, end) of each text block
    width: float
    height: float
    ok: bool = True
    error: str | None = None
    is_ocr: bool = False
    images_unread: int = 0         # images that may hold text but could not be read (no OCR): never "green"


@dataclass
class Document:
    pages: list[Page]
    title: str | None = None
    metadata: dict = field(default_factory=dict)


def _page_from_words(no: int, raw_words: list, width: float, height: float) -> Page:
    raw_words = sorted(raw_words, key=lambda w: (w[5], w[6], w[7]))
    parts: list[str] = []
    words: list[Word] = []
    blocks: list[tuple[int, int]] = []
    pos = 0
    prev_line = None
    block_start = 0
    for w in raw_words:
        line = (w[5], w[6])
        if prev_line is not None:
            if line == prev_line:
                sep = " "
            elif line[0] == prev_line[0]:
                sep = "\n"
            else:
                sep = "\n\n"
                blocks.append((block_start, pos))
            parts.append(sep)
            pos += len(sep)
            if sep == "\n\n":
                block_start = pos
        text = w[4]
        words.append(Word(pos, pos + len(text), (w[0], w[1], w[2], w[3]), line))
        parts.append(text)
        pos += len(text)
        prev_line = line
    if words:
        blocks.append((block_start, pos))
    return Page(no=no, text="".join(parts), words=words, blocks=blocks, width=width, height=height)


# An image at least this big (points) may hold readable text, e.g. a screenshot of a line with an SSN.
MIN_IMAGE_W, MIN_IMAGE_H = 40, 10


@lru_cache(maxsize=1)
def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def _text_images(pg) -> list[dict]:
    """Pictures on the page big enough to hold readable text (with their xref and where they are drawn)."""
    out = []
    for info in pg.get_image_info(xrefs=True):
        x0, y0, x1, y1 = info["bbox"]
        if x1 - x0 >= MIN_IMAGE_W and y1 - y0 >= MIN_IMAGE_H:
            out.append(info)
    return out[:MAX_IMAGES_PER_PAGE]


MAX_IMAGES_PER_PAGE = 30
OCR_LONG_SIDE = 1500          # small pictures are enlarged to about this many pixels before OCR
OCR_MIN_CONFIDENCE = 30       # Tesseract word confidence; drops noise from borders and photo texture


def _ocr_image(pg, info: dict, block_base: int) -> list[tuple]:
    """Read one picture with Tesseract at its own pixels (greyscale, enlarged when small), and return its words in
    page coordinates, in PyMuPDF's word format. Rendering the page instead reads small pictures unreliably
    (a screenshot's "445 56781" came out as "445567018")."""
    if info.get("xref"):
        pix = pymupdf.Pixmap(pg.parent, info["xref"])
    else:                                       # inline image: no object to read, so render just its area
        pix = pg.get_pixmap(clip=pymupdf.Rect(info["bbox"]), dpi=300)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    if pix.colorspace is None or pix.colorspace.n != 1:
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pix)
    scale = max(1, min(4, OCR_LONG_SIDE // max(pix.width, pix.height, 1)))
    if scale > 1:
        pix = pymupdf.Pixmap(pix, pix.width * scale, pix.height * scale, None)
    tsv = subprocess.run(["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6", "--dpi", "300", "tsv"],
                         input=pix.tobytes("png"), capture_output=True, timeout=60, check=True).stdout.decode("utf-8")
    x0, y0, x1, y1 = info["bbox"]
    sx, sy = (x1 - x0) / pix.width, (y1 - y0) / pix.height
    words = []
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) != 12 or not f[11].strip() or float(f[10]) < OCR_MIN_CONFIDENCE:
            continue
        left, top, w, h = (int(v) for v in f[6:10])
        block, par, line, word = (int(v) for v in f[2:6])
        words.append((x0 + left * sx, y0 + top * sy, x0 + (left + w) * sx, y0 + (top + h) * sy, f[11],
                      block_base + block * 10 + par, line, word))
    return words


def extract_pdf(path: Path) -> Document:
    pages: list[Page] = []
    with pymupdf.open(path) as pdf:
        if pdf.needs_pass or pdf.is_encrypted:
            raise ValueError("the PDF is password-protected or encrypted; it cannot be screened")
        if pdf.page_count > config.MAX_PAGES:
            raise ValueError(f"the document has {pdf.page_count} pages; the limit is {config.MAX_PAGES}")
        meta = dict(pdf.metadata or {})
        for i, pg in enumerate(pdf, start=1):
            rect = pg.rect
            try:
                images = _text_images(pg)
                raw, is_ocr, unread = pg.get_text("words"), False, 0
                # Text inside pictures (screenshots, scans) is read with OCR, picture by picture, and added to the
                # page's words where the picture is drawn, so findings there are highlighted like any other text.
                for k, info in enumerate(images):
                    try:
                        if not ocr_available():
                            raise RuntimeError("OCR not available")
                        raw = raw + _ocr_image(pg, info, block_base=100_000 * (k + 1))
                        is_ocr = True
                    except Exception:
                        unread += 1
                page = _page_from_words(i, raw, rect.width, rect.height)
                page.is_ocr, page.images_unread = is_ocr, unread
                if not page.words and unread:
                    # Image-only page that could not be read: nothing on it was checked.
                    page.ok = False
                    page.error = "no text layer (scanned/image page) and OCR is not available"
            except Exception as e:  # keep going; coverage records the gap
                page = Page(no=i, text="", words=[], blocks=[], width=rect.width, height=rect.height,
                            ok=False, error=f"extraction error: {e}")
            pages.append(page)
    title = (meta.get("title") or "").strip() or None
    if not title:
        for p in pages:
            if p.blocks:
                s, e = p.blocks[0]
                title = " ".join(p.text[s:e].split())[:120]
                break
    return Document(pages=pages, title=title, metadata=meta)


def bboxes_for_span(page: Page, start: int, end: int) -> list[BBox]:
    """Union of word boxes overlapping [start, end), merged per text line."""
    by_line: dict[tuple[int, int], list[float]] = {}
    for w in page.words:
        if w.end <= start or w.start >= end:
            continue
        b = by_line.get(w.line)
        if b is None:
            by_line[w.line] = list(w.bbox)
        else:
            b[0], b[1] = min(b[0], w.bbox[0]), min(b[1], w.bbox[1])
            b[2], b[3] = max(b[2], w.bbox[2]), max(b[3], w.bbox[3])
    return [tuple(round(v, 2) for v in b) for b in by_line.values()]
