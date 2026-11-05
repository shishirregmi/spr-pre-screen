"""Word (.docx) input: render the document's text to a PDF so the same pipeline and viewer work on it.

Headers and footers are included because classification markings and banners usually live there.
Layout is simplified (text, headings, tables); the original file is kept alongside for reference.
"""
from __future__ import annotations

import html
import shutil
import subprocess
import zipfile
from pathlib import Path

import docx
import pymupdf
from docx.document import Document as DocxDocument

from app import config
from docx.table import Table
from docx.text.paragraph import Paragraph

CSS = """
body { font-family: sans-serif; font-size: 10.5pt; }
h1 { font-size: 15pt; } h2 { font-size: 13pt; } h3 { font-size: 11.5pt; }
p { margin: 0 0 6pt 0; }
.hdr { font-size: 9pt; color: #444; text-align: center; margin-bottom: 8pt; }
table { border-collapse: collapse; margin: 6pt 0; }
td { border: 1px solid #999; padding: 2pt 4pt; font-size: 9.5pt; }
"""


MAX_IMAGE_PT = 468                     # text width of a letter page with 1-inch margins


def _images_html(p: Paragraph, images: dict[str, bytes]) -> str:
    """Pictures in the paragraph (screenshots can carry text such as an SSN), added to the rendering archive."""
    out = []
    for rid in p._p.xpath(".//a:blip/@r:embed"):
        part = p.part.related_parts.get(rid)
        if part is None:
            continue
        try:
            pix = pymupdf.Pixmap(part.blob)          # formats MuPDF cannot draw (EMF/WMF) are left out
        except Exception:
            continue
        name = f"img{len(images) + 1}.png"
        images[name] = pix.tobytes("png")
        w = min(MAX_IMAGE_PT, pix.width * 72 / max(pix.xres or 96, 72))
        out.append(f'<p><img src="{name}" width="{int(w)}"/></p>')
    return "".join(out)


def _para_html(p: Paragraph, images: dict[str, bytes] | None = None) -> str:
    pics = _images_html(p, images) if images is not None else ""
    text = p.text.strip()
    if not text:
        return pics
    style = (p.style.name or "").lower() if p.style is not None else ""
    t = html.escape(text)
    if style == "title":
        return f"<h1>{t}</h1>" + pics
    if style.startswith("heading"):
        level = style.replace("heading", "").strip()
        n = int(level) if level.isdigit() else 2
        return f"<h{min(max(n + 1, 2), 3)}>{t}</h{min(max(n + 1, 2), 3)}>" + pics
    return f"<p>{t}</p>" + pics


def _table_html(t: Table) -> str:
    rows = []
    for row in t.rows:
        cells = "".join(f"<td>{html.escape(c.text.strip())}</td>" for c in row.cells)
        rows.append(f"<tr>{cells}</tr>")
    return f"<table>{''.join(rows)}</table>"


def _body_html(d: DocxDocument, images: dict[str, bytes] | None = None) -> list[str]:
    parts: list[str] = []
    for block in d.iter_inner_content():
        parts.append(_table_html(block) if isinstance(block, Table) else _para_html(block, images))
    return [p for p in parts if p]


def _header_footer_text(d: DocxDocument) -> tuple[list[str], list[str]]:
    heads, foots = [], []
    for s in d.sections:
        for part, out in ((s.header, heads), (s.footer, foots)):
            text = " ".join(p.text.strip() for p in part.paragraphs if p.text.strip())
            if text and text not in out:
                out.append(text)
    return heads, foots


def docx_to_pdf(src: Path, out: Path) -> str | None:
    """Write a text PDF rendering of `src` to `out`. Returns the document title if one is set."""
    _check_zip(src)
    d = docx.Document(str(src))
    heads, foots = _header_footer_text(d)
    images: dict[str, bytes] = {}
    body = _body_html(d, images)
    content = "".join(f'<div class="hdr">{html.escape(h)}</div>' for h in heads)
    content += "".join(body) or "<p></p>"
    content += "".join(f'<div class="hdr">{html.escape(f)}</div>' for f in foots)
    render_html_pdf(content, out, images)
    title = (d.core_properties.title or "").strip()
    return title or None


def _check_zip(src: Path) -> None:
    """A .docx is a zip file: refuse archives that would expand to an unreasonable size (zip bombs)."""
    try:
        with zipfile.ZipFile(src) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as e:
        raise ValueError("not a valid Word (.docx) file") from e
    if len(infos) > 5000 or sum(i.file_size for i in infos) > config.MAX_DOCX_UNZIPPED:
        raise ValueError("the Word file expands to an unreasonable size and was refused")
    if any(i.flag_bits & 0x1 for i in infos):
        raise ValueError("the Word file is encrypted; it cannot be screened")


def doc_to_pdf(src: Path, out: Path) -> str | None:
    """Legacy Word 97-2003 (.doc): extract text with antiword (or catdoc) and render it like a .docx."""
    for tool, args in (("antiword", ["-w", "0"]), ("catdoc", ["-w"])):
        exe = shutil.which(tool)
        if exe:
            text = subprocess.run([exe, *args, str(src)], capture_output=True, timeout=60, check=True,
                                  text=True, encoding="utf-8", errors="replace").stdout
            break
    else:
        raise ValueError("legacy .doc needs antiword or catdoc (included in the Docker image)")
    paras = [" ".join(p.split()) for p in text.replace("\r", "").split("\n\n")]
    content = "".join(f"<p>{html.escape(p)}</p>" for p in paras if p) or "<p></p>"
    render_html_pdf(content, out)
    return None


def render_html_pdf(content: str, out: Path, images: dict[str, bytes] | None = None) -> None:
    archive = pymupdf.Archive()
    for name, data in (images or {}).items():
        archive.add(data, name)
    story = pymupdf.Story(html=f"<html><body>{content}</body></html>", user_css=CSS, archive=archive)
    writer = pymupdf.DocumentWriter(str(out))
    page = pymupdf.paper_rect("letter")
    where = page + (72, 72, -72, -72)
    more = True
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
