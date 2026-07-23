"""Rendering a result set as CSV, XLSX or PDF.

Each renderer takes headers and rows and returns bytes. They know nothing about
sessions, tenants or permissions — by the time a row arrives here it has already
been through the scope predicate, and a renderer that could reach back into the
database would be a second path to data that skipped that.

**CSV injection is handled here, once.** A cell beginning `=`, `+`, `-` or `@`
is interpreted as a formula by Excel and Sheets, and `=cmd|'/c calc'!A1` in a
CRM note becomes code execution on the machine of whoever opens the export. The
value is prefixed with a single quote, which every spreadsheet renders as text.
This is the reason `_cell` exists at all and the reason no renderer formats a
value inline.

**Money is written as a number, not a string, in XLSX** — a spreadsheet whose
totals cannot be summed is a screenshot with extra steps — but as its exact
decimal string in CSV, where there is no cell type to lose precision to.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

#: What a leading character means to a spreadsheet. Not configurable.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

#: Formats a report can be rendered as.
FORMATS = ("csv", "xlsx", "pdf")

CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}

#: PDF is a *presentation* format, and a 50,000-row PDF is not a document
#: anybody reads — it is a denial-of-service against reportlab's layout engine.
#: Beyond this the run is truncated and says so on the page.
PDF_MAX_ROWS = 2_000


def _cell(value: Any) -> str:
    """One value as display text, safe to put in a spreadsheet cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal | int | float):
        return str(value)
    if isinstance(value, UUID):
        return str(value)

    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        # Neutralised, not stripped: the reader still sees what was typed.
        return "'" + text
    return text


def render_csv(headers: list[str], rows: list[tuple[Any, ...]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    # UTF-8 with BOM. Excel on Windows opens a plain UTF-8 CSV as the system
    # code page and mangles every non-ASCII name in it; the BOM is what makes a
    # file with "Nyström" in it open correctly for the people who need it to.
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def render_xlsx(headers: list[str], rows: list[tuple[Any, ...]], title: str) -> bytes:
    """A single-sheet workbook, written streaming.

    `write_only` because the normal API builds a full cell object graph; on
    50,000 rows that is the difference between tens of megabytes and hundreds.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook(write_only=True)
    # Excel rejects sheet names over 31 chars or containing []:*?/\
    sheet = workbook.create_sheet(_sheet_name(title))

    bold = Font(bold=True)
    from openpyxl.cell import WriteOnlyCell

    header_cells = []
    for header in headers:
        cell = WriteOnlyCell(sheet, value=header)
        cell.font = bold
        header_cells.append(cell)
    sheet.append(header_cells)

    for row in rows:
        sheet.append([_xlsx_value(value) for value in row])

    # Enough width to read without a manual resize on every column.
    for index in range(1, len(headers) + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 22

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _xlsx_value(value: Any) -> Any:
    """Numbers and dates stay typed; everything else becomes safe text."""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, Decimal):
        # float, because openpyxl has no decimal cell type. Acceptable *here*
        # and nowhere else: this is a rendering boundary, the exact value is
        # already committed in Postgres, and a numeric cell the reader can sum
        # is the point of choosing XLSX over CSV.
        return float(value)
    if isinstance(value, int | float | datetime | date):
        return value
    return _cell(value)


def _sheet_name(title: str) -> str:
    cleaned = "".join(c for c in title if c not in "[]:*?/\\")
    return (cleaned or "Report")[:31]


def render_pdf(
    headers: list[str],
    rows: list[tuple[Any, ...]],
    title: str,
    *,
    subtitle: str = "",
) -> bytes:
    """A landscape table. Truncated at `PDF_MAX_ROWS`, visibly."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=title,
    )
    styles = getSampleStyleSheet()

    story: list[Any] = [Paragraph(_escape(title), styles["Title"])]
    if subtitle:
        story.append(Paragraph(_escape(subtitle), styles["Normal"]))
    story.append(Spacer(1, 6 * mm))

    shown = rows[:PDF_MAX_ROWS]
    data = [headers] + [[_pdf_cell(value) for value in row] for row in shown]

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f8fafc")],
                ),
            ]
        )
    )
    story.append(table)

    if len(rows) > PDF_MAX_ROWS:
        story.append(Spacer(1, 4 * mm))
        story.append(
            Paragraph(
                f"Showing the first {PDF_MAX_ROWS:,} of {len(rows):,} rows. "
                "Export as CSV or XLSX for the complete set.",
                styles["Italic"],
            )
        )

    document.build(story)
    return buffer.getvalue()


def _pdf_cell(value: Any) -> str:
    """Cell text, escaped and clipped.

    reportlab's Paragraph markup is a small HTML dialect, so an unescaped `<`
    from a CRM note either breaks the build or injects markup. Clipping matters
    too: one 4,000-character description makes a table cell taller than the page
    and reportlab raises rather than degrading.
    """
    text = _cell(value)
    if len(text) > 80:
        text = text[:77] + "…"
    return _escape(text)


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(
    format: str,
    headers: list[str],
    rows: list[tuple[Any, ...]],
    *,
    title: str,
    subtitle: str = "",
) -> bytes:
    match format:
        case "csv":
            return render_csv(headers, rows)
        case "xlsx":
            return render_xlsx(headers, rows, title)
        case "pdf":
            return render_pdf(headers, rows, title, subtitle=subtitle)
        case _:
            raise ValueError(f"Unsupported export format: {format}")


__all__ = [
    "CONTENT_TYPES",
    "FORMATS",
    "PDF_MAX_ROWS",
    "render",
    "render_csv",
    "render_pdf",
    "render_xlsx",
]
