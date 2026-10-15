"""Render reports/rod_momentum/REPORT.md as the PDF "Quantitative Day Trading Strategy: Research & Specification Report".

    python reports/rod_momentum/build_pdf.py        # needs reportlab and matplotlib (not in the app's requirements)

The PDF is built from the Markdown, so the two cannot drift: title page, the "Executive summary" section on its own
page, a table of contents, the numbered sections, and the "Risk disclaimer" section on the last page. Supported
Markdown: ## and ### headings, paragraphs, "- " bullets, "1. " numbered items, "> " highlighted boxes, pipe tables, ![caption](image.png),
**bold**, `code`, [links](url), and <!-- pagebreak -->. A table directly after <!-- params --> is drawn as a
highlighted parameter table. The equity chart is drawn from equity_primary.csv.
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

HERE = Path(__file__).resolve().parent
TITLE = "Quantitative Day Trading Strategy: Research & Specification Report"
VERSION = "1.0"
FONT_DIR = Path("/System/Library/Fonts/Supplemental")

INK, MUTED, ACCENT, RULE, SHADE, HILITE = (colors.HexColor(c) for c in
                                           ("#1b1f24", "#5b6470", "#1f4e79", "#c9d1da", "#f2f5f8", "#fff6d6"))


def fonts():
    try:
        pdfmetrics.registerFont(TTFont("Body", str(FONT_DIR / "Arial.ttf")))
        pdfmetrics.registerFont(TTFont("Body-Bold", str(FONT_DIR / "Arial Bold.ttf")))
        pdfmetrics.registerFont(TTFont("Body-Italic", str(FONT_DIR / "Arial Italic.ttf")))
        pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic", boldItalic="Body-Bold")
        return "Body", "Body-Bold"
    except Exception:  # noqa: BLE001 - fall back to the built-in fonts (no >=, sigma glyphs)
        return "Helvetica", "Helvetica-Bold"


BODY, BOLD = fonts()
S = {
    "body": ParagraphStyle("body", fontName=BODY, fontSize=9.5, leading=13.2, textColor=INK, spaceAfter=6),
    "bullet": ParagraphStyle("bullet", fontName=BODY, fontSize=9.5, leading=13, textColor=INK, leftIndent=14,
                             bulletIndent=4, spaceAfter=3),
    "h1": ParagraphStyle("h1", fontName=BOLD, fontSize=15, leading=19, textColor=ACCENT, spaceBefore=6, spaceAfter=8,
                         keepWithNext=1),
    "h2": ParagraphStyle("h2", fontName=BOLD, fontSize=11.5, leading=15, textColor=INK, spaceBefore=8, spaceAfter=5,
                         keepWithNext=1),
    "cell": ParagraphStyle("cell", fontName=BODY, fontSize=7.6, leading=9.6, textColor=INK),
    "cellh": ParagraphStyle("cellh", fontName=BOLD, fontSize=7.6, leading=9.6, textColor=colors.white),
    "box": ParagraphStyle("box", fontName=BODY, fontSize=9.5, leading=13, textColor=INK),
    "caption": ParagraphStyle("caption", fontName=BODY, fontSize=8, leading=10, textColor=MUTED, alignment=TA_CENTER),
    "toc1": ParagraphStyle("toc1", fontName=BODY, fontSize=10.5, leading=17, leftIndent=8),
    "toc2": ParagraphStyle("toc2", fontName=BODY, fontSize=9, leading=13, leftIndent=24, textColor=MUTED),
}


def inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<link href="\2" color="#1f4e79"><u>\1</u></link>', text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r'<font name="Courier" size="8.6">\1</font>', text)
    return text


class Doc(BaseDocTemplate):
    def __init__(self, path):
        super().__init__(str(path), pagesize=LETTER, leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                         topMargin=0.8 * inch, bottomMargin=0.8 * inch, title=TITLE, author="llamaOS research",
                         subject="Rest-of-day market intraday momentum")
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
        self.addPageTemplates([PageTemplate("title", [frame], onPage=self.title_page),
                               PageTemplate("body", [frame], onPage=self.chrome)])

    def afterFlowable(self, f):
        if isinstance(f, Paragraph) and getattr(f, "toc_level", None) is not None:
            key = f"h{id(f)}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (f.toc_level, f.getPlainText(), self.page, key))

    def title_page(self, c, doc):
        w, h = LETTER
        c.saveState()
        c.setFillColor(ACCENT)
        c.rect(0, h - 3.1 * inch, w, 3.1 * inch, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(BOLD, 24)
        c.drawString(0.8 * inch, h - 1.55 * inch, "Quantitative Day Trading Strategy:")
        c.drawString(0.8 * inch, h - 1.95 * inch, "Research & Specification Report")
        c.setFont(BODY, 12)
        c.drawString(0.8 * inch, h - 2.5 * inch, "Rest-of-day market intraday momentum in SPY, QQQ and IWM")
        c.setFillColor(INK)
        c.setFont(BODY, 11)
        y = h - 4.0 * inch
        for label, value in (("Date", date(2026, 10, 16).strftime("%B %d, %Y")), ("Report version", VERSION),
                             ("Scope", "US equity ETFs, 1-minute bars, Alpaca paper trading"),
                             ("Repository", "llamaOS: strategies/rod_momentum_long.v1.yaml, rod_momentum_short.v1.yaml")):
            c.setFillColor(MUTED)
            c.drawString(0.8 * inch, y, label)
            c.setFillColor(INK)
            c.drawString(2.4 * inch, y, value)
            y -= 0.32 * inch
        c.setFillColor(MUTED)
        c.setFont(BODY, 8.5)
        c.drawString(0.8 * inch, 0.9 * inch, "For research and education only. Not investment advice. See the risk disclaimer on the final page.")
        c.restoreState()

    def chrome(self, c, doc):
        w, h = LETTER
        c.saveState()
        c.setStrokeColor(RULE)
        c.line(0.8 * inch, h - 0.6 * inch, w - 0.8 * inch, h - 0.6 * inch)
        c.setFont(BODY, 7.5)
        c.setFillColor(MUTED)
        c.drawString(0.8 * inch, h - 0.52 * inch, TITLE)
        c.drawRightString(w - 0.8 * inch, h - 0.52 * inch, f"Version {VERSION}")
        c.drawRightString(w - 0.8 * inch, 0.5 * inch, f"Page {doc.page}")
        c.restoreState()


def table(rows: list[list[str]], width: float, params=False) -> Table:
    ncol = len(rows[0])
    data = [[Paragraph(inline(x), S["cellh"] if r == 0 else S["cell"]) for x in row] for r, row in enumerate(rows)]
    # weight each column by its longest word (so no word breaks) and, more loosely, by its longest cell
    word = [max((len(w) for row in rows if i < len(row) for w in row[i].split()), default=4) for i in range(ncol)]
    cell = [max(len(row[i]) for row in rows if i < len(row)) for i in range(ncol)]
    lens = [max(word[i] * 1.15, min(cell[i], 70) ** 0.8 * 2.2, 5) for i in range(ncol)]
    widths = [width * n / sum(lens) for n in lens]
    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [("BACKGROUND", (0, 0), (-1, 0), ACCENT), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("GRID", (0, 0), (-1, -1), 0.4, RULE), ("TOPPADDING", (0, 0), (-1, -1), 2.5),
             ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("LEFTPADDING", (0, 0), (-1, -1), 3.5)]
    for r in range(1, len(rows)):
        if params:
            style.append(("BACKGROUND", (0, r), (-1, r), HILITE))
        elif r % 2 == 0:
            style.append(("BACKGROUND", (0, r), (-1, r), SHADE))
    if params:
        style.append(("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#c99a06")))
    t.setStyle(TableStyle(style))
    return t


def box(text: str, width: float) -> Table:
    t = Table([[Paragraph(inline(text), S["box"])]], colWidths=[width])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HILITE), ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#c99a06")),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    return t


def equity_chart(path: Path) -> Path | None:
    src = HERE / "equity_primary.csv"
    if not src.exists():
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from datetime import datetime
    rows = list(csv.DictReader(src.open()))
    x = [datetime.fromisoformat(r["day"]) for r in rows]
    y = [(float(r["equity_account_view"]) - 1) * 100 for r in rows]
    fig, ax = plt.subplots(figsize=(7.0, 2.6), dpi=200)
    ax.plot(x, y, color="#1f4e79", linewidth=1.1)
    ax.axhline(0, color="#9aa4ae", linewidth=0.6)
    ax.set_ylabel("Cumulative return, % of equity", fontsize=7.5)
    ax.tick_params(labelsize=7)
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", color="#e3e8ee", linewidth=0.6)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def story_from(md: str, width: float) -> tuple[list, list]:
    """Returns (executive summary flowables, body flowables)."""
    lines = md.splitlines()
    out, summary, cur = [], [], None
    target = out
    para: list[str] = []
    params_next = False

    def flush():
        nonlocal para
        if para:
            target.append(Paragraph(inline(" ".join(para)), S["body"]))
            para = []

    i = 0
    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("# "):
            i += 1
            continue
        if ln.strip() == "<!-- pagebreak -->":
            flush()
            target.append(PageBreak())
        elif ln.strip() == "<!-- params -->":
            flush()
            params_next = True
        elif ln.startswith("## "):
            flush()
            title = ln[3:].strip()
            if title.lower() == "executive summary":
                target = summary
                target.append(Paragraph(title, S["h1"]))
            else:
                target = out
                if title.lower().startswith("risk disclaimer"):
                    out.append(PageBreak())
                p = Paragraph(inline(title), S["h1"])
                p.toc_level = 0
                target.append(p)
        elif ln.startswith("### "):
            flush()
            p = Paragraph(inline(ln[4:].strip()), S["h2"])
            p.toc_level = 1
            target.append(p)
        elif ln.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            target.append(table(rows, width, params_next))
            target.append(Spacer(1, 8))
            params_next = False
            continue
        elif ln.startswith("- "):
            flush()
            target.append(Paragraph(inline(ln[2:]), S["bullet"], bulletText="•"))
        elif m := re.match(r"(\d+)\. (.*)", ln):
            flush()
            target.append(Paragraph(inline(m.group(2)), S["bullet"], bulletText=f"{m.group(1)}."))
        elif ln.startswith("> "):
            flush()
            text = [ln[2:]]
            while i + 1 < len(lines) and lines[i + 1].startswith("> "):
                i += 1
                text.append(lines[i][2:])
            target.append(box(" ".join(text), width))
            target.append(Spacer(1, 8))
        elif m := re.match(r"!\[([^\]]*)\]\(([^)]+)\)", ln):
            flush()
            img = HERE / m.group(2)
            if m.group(2) == "equity.png":
                img = equity_chart(HERE / "equity.png") or img
            if img.exists():
                im = Image(str(img), width=width, height=width * 2.6 / 7.0)
                target.append(KeepTogether([im, Paragraph(inline(m.group(1)), S["caption"]), Spacer(1, 8)]))
        elif not ln.strip():
            flush()
        else:
            para.append(ln.strip())
        i += 1
    flush()
    return summary, out


def main():
    md_path = HERE / "REPORT.md"
    out_path = HERE / "Quantitative_Day_Trading_Strategy_Report.pdf"
    doc = Doc(out_path)
    summary, body = story_from(md_path.read_text(), doc.width)
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    story = [NextPageTemplate("body"), PageBreak(), *summary, PageBreak(),
             Paragraph("Table of contents", S["h1"]), toc, PageBreak(), *body]
    doc.multiBuild(story)
    print(f"wrote {out_path.relative_to(HERE.parent.parent)}")


if __name__ == "__main__":
    sys.exit(main())
