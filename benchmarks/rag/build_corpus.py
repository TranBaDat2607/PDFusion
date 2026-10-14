"""Build the chat-retrieval benchmark corpus into `benchmarks/rag/.data/`.

Three documents, each chosen for a failure a text-only chat index has:

- `report.pdf` — a synthetic 14-page annual report whose ground truth is known
  by construction: prose with one fact per page, vector-drawn tables, two
  charts whose numbers exist only as pixels, two scanned (image-only) pages,
  a Vietnamese page, and distractor pages that share the question's words but
  not its answer.
- `attention.pdf` — "Attention Is All You Need" (arXiv 1706.03762v7), a real
  paper with tables, figures and equations.
- `attention_scanned.pdf` — the same paper rasterized to images, no text
  layer: what a scanned copy of it looks like to an index.

Deterministic: the same corpus every run, except the paper, which is
downloaded once. Run with the app's interpreter:

    python benchmarks/rag/build_corpus.py
"""

from __future__ import annotations

import html
import io
import random
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF

DATA = Path(__file__).resolve().parent / ".data"
PAPER_URL = "https://arxiv.org/pdf/1706.03762v7"

W, H = 595, 842  # A4 in points
MARGIN = 56


def _html(page: fitz.Page, rect: fitz.Rect, text: str, size: float, bold: bool = False) -> float:
    """Write `text` into `rect`; return the height it used.

    `insert_htmlbox`, not `insert_textbox`: with an embedded TrueType font the
    latter's text layer reads back with every space a no-break space and `;`
    as U+037E, which no question would match. The HTML path falls back to
    fonts with Vietnamese glyphs and reads back as written.
    """
    weight = "bold" if bold else "normal"
    spare, _scale = page.insert_htmlbox(
        rect,
        f'<p style="font-size:{size}pt;font-weight:{weight};margin:0">{html.escape(text)}</p>',
        scale_low=1,
    )
    return rect.height - spare


def _text(page: fitz.Page, y: float, text: str, size: float = 11, bold: bool = False) -> float:
    """Write a paragraph from `y` down; return the y below it."""
    used = _html(page, fitz.Rect(MARGIN, y, W - MARGIN, H - MARGIN), text, size, bold)
    return y + used + size * 0.9


def _heading(page: fitz.Page, y: float, text: str) -> float:
    return _text(page, y, text, size=16, bold=True)


def _table(page: fitz.Page, y: float, rows: list[list[str]], widths: list[float]) -> float:
    """A ruled table, the kind `Page.find_tables` reads from its lines."""
    row_h = 22
    x0 = MARGIN
    x_edges = [x0]
    for w in widths:
        x_edges.append(x_edges[-1] + w)
    for r, row in enumerate(rows):
        top = y + r * row_h
        for c, cell in enumerate(row):
            rect = fitz.Rect(x_edges[c], top, x_edges[c + 1], top + row_h)
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            _html(page, rect + (4, 4, -4, 0), cell, 10)
    return y + len(rows) * row_h + 14


def _chart_png(kind: str, labels: list[str], values: list[float], title: str) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 3.6), dpi=110)
    if kind == "bar":
        bars = ax.bar(labels, values, color="#4c78a8")
        for bar, value in zip(bars, values):
            ax.annotate(
                f"{value:g}",
                (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                ha="center", va="bottom", fontsize=10,
            )
        ax.set_ylabel("Claims")
    else:
        ax.pie(values, labels=[f"{lab} {v:g}%" for lab, v in zip(labels, values)], startangle=90)
        ax.axis("equal")
    ax.set_title(title)
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def _scan(page_builder, rng: random.Random, dpi: int = 110) -> fitz.Document:
    """A one-page PDF whose only content is a slightly rotated, noisy photo of
    the page `page_builder` draws: no text layer at all."""
    from PIL import Image, ImageFilter

    source = fitz.open()
    page = source.new_page(width=W, height=H)
    page_builder(page)
    pix = page.get_pixmap(dpi=dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    img = img.rotate(rng.uniform(-1.2, 1.2), expand=False, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    noisy = img.point(lambda v: max(0, min(255, v + rng.randint(-18, 18))))
    buf = io.BytesIO()
    noisy.save(buf, format="JPEG", quality=70)

    scanned = fitz.open()
    out = scanned.new_page(width=W, height=H)
    out.insert_image(out.rect, stream=buf.getvalue())
    return scanned


def build_report(path: Path) -> None:
    rng = random.Random(7)
    doc = fitz.open()

    def page():
        return doc.new_page(width=W, height=H)

    # 1 — title and introduction
    p = page()
    y = _heading(p, MARGIN, "Northwind Instruments — Annual Report 2025")
    y = _text(p, y, "Northwind Instruments designs and manufactures industrial sensors, gateways and "
                    "measurement probes. This report covers the fiscal year that ended on 31 December 2025.")
    y = _text(p, y, "Contents: Letter to shareholders; Operations; Financial results; Quality; Board "
                    "decisions; Sustainability; Research and development; People; Risk factors; Products; "
                    "Glossary; Supplier performance.")

    # 2 — letter: headcount
    p = page()
    y = _heading(p, MARGIN, "Letter to shareholders")
    y = _text(p, y, "Dear shareholders, 2025 was a year of disciplined growth. We opened two customer "
                    "centres and kept investing in our people: headcount grew to 1,240 employees by year end, "
                    "up from 1,105 a year earlier. Our order backlog closed the year at a record level.")
    y = _text(p, y, "Chief Executive Officer Mara Lindqvist")

    # 3 — operations: Da Nang plant share
    p = page()
    y = _heading(p, MARGIN, "Operations")
    y = _text(p, y, "Production is spread across three plants. The Da Nang plant, opened in 2021, now "
                    "produces 38% of total unit output, ahead of the Tampere plant and the Monterrey plant. "
                    "Average plant utilisation was 81% across the year.")

    # 4 — revenue table
    p = page()
    y = _heading(p, MARGIN, "Financial results")
    y = _text(p, y, "Group revenue rose to USD 1,068.0 million. Table 2 breaks revenue down by region.")
    y = _text(p, y, "Table 2: Revenue by region (USD millions)", size=10)
    y = _table(p, y, [
        ["Region", "Revenue", "Growth"],
        ["Asia-Pacific", "412.6", "18.2%"],
        ["Europe", "305.1", "4.7%"],
        ["North America", "288.9", "-2.3%"],
        ["Latin America", "61.4", "9.9%"],
    ], [170, 120, 120])

    # 5 — bar chart, numbers only in the image
    p = page()
    y = _heading(p, MARGIN, "Quality")
    y = _text(p, y, "Warranty claims varied over the year; one quarter stood out after a firmware issue "
                    "in the gateway line, which was fixed in the following release. Figure 3 shows the "
                    "claims received in each quarter.")
    png = _chart_png("bar", ["Q1", "Q2", "Q3", "Q4"], [132, 97, 154, 88], "Warranty claims per quarter")
    p.insert_image(fitz.Rect(MARGIN, y, W - MARGIN, y + 300), stream=png)
    _text(p, y + 310, "Figure 3: Warranty claims per quarter, 2025", size=10)

    # 6 — distractor: warranty policy prose
    p = page()
    y = _heading(p, MARGIN, "Warranty policy")
    y = _text(p, y, "Every Northwind product carries a warranty. Warranty claims are filed through the "
                    "customer portal and reviewed by the quality team within five working days. Claims that "
                    "are approved are settled by repair or replacement. The warranty claims process is audited "
                    "every quarter, and the warranty terms were unchanged in 2025.")

    # 7 — scanned board memo
    def memo(pg: fitz.Page) -> None:
        yy = _heading(pg, MARGIN, "BOARD MEMORANDUM")
        yy = _text(pg, yy, "Subject: Dividend", size=13)
        yy = _text(pg, yy, "The Board of Directors approved a final dividend of USD 0.42 per share, "
                           "payable on 15 March 2026 to shareholders on record on 27 February 2026.", size=13)
        _text(pg, yy, "Signed: Corporate Secretary", size=13)

    doc.insert_pdf(_scan(memo, rng))

    # 8 — Vietnamese sustainability page
    p = page()
    y = _heading(p, MARGIN, "Báo cáo phát triển bền vững")
    y = _text(p, y, "Năm 2025, Northwind đã giảm 27% lượng phát thải CO2 so với năm 2022. "
                    "64% điện năng tiêu thụ tại các nhà máy đến từ năng lượng tái tạo. "
                    "Nhà máy Đà Nẵng lắp đặt hệ thống điện mặt trời áp mái công suất 3,2 MW.")

    # 9 — R&D prose
    p = page()
    y = _heading(p, MARGIN, "Research and development")
    y = _text(p, y, "Research and development spending reached 9.8% of revenue. The engineering teams filed "
                    "57 patent applications in 2025, most of them for low-power wireless sensing.")

    # 10 — pie chart, labels only in the image
    p = page()
    y = _heading(p, MARGIN, "People")
    y = _text(p, y, "Figure 5 shows how the workforce is split across functions at year end.")
    png = _chart_png("pie", ["Engineering", "Manufacturing", "Sales", "Admin"], [41, 33, 16, 10],
                     "Workforce by function")
    p.insert_image(fitz.Rect(MARGIN, y, W - MARGIN, y + 320), stream=png)
    _text(p, y + 330, "Figure 5: Workforce by function", size=10)

    # 11 — distractor: risks mention revenue and Europe
    p = page()
    y = _heading(p, MARGIN, "Risk factors")
    y = _text(p, y, "Revenue in Europe and North America depends on industrial capital spending, which "
                    "follows the economic cycle. Currency movements affect reported revenue growth. A "
                    "slowdown in Asia-Pacific would weigh on group revenue.")

    # 12 — product table
    p = page()
    y = _heading(p, MARGIN, "Products")
    y = _text(p, y, "Table 4: Product lines", size=10)
    y = _table(p, y, [
        ["Product", "Units shipped", "Average price (USD)"],
        ["Sensor S2", "84,000", "129"],
        ["Gateway G7", "12,500", "899"],
        ["Probe P1", "230,000", "19"],
    ], [150, 130, 150])

    # 13 — glossary distractor
    p = page()
    y = _heading(p, MARGIN, "Glossary")
    y = _text(p, y, "Backlog: orders received but not yet shipped. Utilisation: output as a share of "
                    "capacity. Warranty claim: a request to repair or replace a product. Dividend: a "
                    "distribution of profit to shareholders.")

    # 14 — scanned supplier table
    def scorecard(pg: fitz.Page) -> None:
        yy = _heading(pg, MARGIN, "Supplier scorecard")
        yy = _text(pg, yy, "On-time delivery by supplier, 2025", size=12)
        _table(pg, yy, [
            ["Supplier", "On-time delivery", "Defect rate"],
            ["Hanoi Components", "96.4%", "0.3%"],
            ["Osaka Metals", "88.1%", "0.9%"],
            ["Baltic Plastics", "92.7%", "0.5%"],
        ], [170, 130, 120])

    doc.insert_pdf(_scan(scorecard, rng))

    doc.save(path)


def build_scanned_copy(source: Path, path: Path, dpi: int = 120) -> None:
    src = fitz.open(source)
    out = fitz.open()
    for page in src:
        pix = page.get_pixmap(dpi=dpi)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=pix.tobytes("jpeg", jpg_quality=75))
    out.save(path)


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    paper = DATA / "attention.pdf"
    if not paper.exists():
        urllib.request.urlretrieve(PAPER_URL, paper)
    build_report(DATA / "report.pdf")
    build_scanned_copy(paper, DATA / "attention_scanned.pdf")
    for name in ("report.pdf", "attention.pdf", "attention_scanned.pdf"):
        doc = fitz.open(DATA / name)
        chars = sum(len(p.get_text()) for p in doc)
        print(f"{name}: {doc.page_count} pages, {chars} text-layer characters")


if __name__ == "__main__":
    main()
