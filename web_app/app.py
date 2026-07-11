from __future__ import annotations

import importlib.util
from io import BytesIO
import shutil
import sys
from dataclasses import asdict, fields, is_dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = PROJECT_ROOT / "uploads"
STATIC_DIR = Path(__file__).resolve().parent / "static"
PARSER_PATH = PROJECT_ROOT / "tools" / "7501_parser.py"


def load_parser_module():
    spec = importlib.util.spec_from_file_location("tax7501_parser", PARSER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load parser module: {PARSER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parser = load_parser_module()
app = FastAPI(title="7501 Tax Bill Tool", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class RecalculateRequest(BaseModel):
    document: dict[str, Any]
    lines: list[dict[str, Any]]
    include_hmf: bool = False


class GeneratePdfRequest(RecalculateRequest):
    pass


def dataclass_from_dict(cls, payload: dict[str, Any]):
    if not is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")
    allowed = {item.name for item in fields(cls)}
    values = {key: value for key, value in payload.items() if key in allowed}
    return cls(**values)


def reset_calculated_fields(line: Any) -> None:
    for field_name in (
        "calculated_base_duty",
        "calculated_chapter_99_duty",
        "calculated_duty_total",
        "calculated_mpf_amount",
        "calculated_hmf_amount",
        "duty_variance",
        "mpf_variance",
        "hmf_variance",
    ):
        setattr(line, field_name, None)


def reset_document_calculated_fields(document: Any) -> None:
    for field_name in (
        "calculated_duty_total",
        "calculated_mpf_total",
        "calculated_hmf_total",
        "calculated_other_total",
        "calculated_grand_total",
        "duty_variance",
        "other_variance",
        "grand_total_variance",
    ):
        setattr(document, field_name, None)


def sum_entered_value(lines: list[Any]) -> Decimal | None:
    total = Decimal("0")
    has_value = False
    for line in lines:
        value = parser.parse_decimal(line.entered_value)
        if value is not None:
            total += value
            has_value = True
    return total if has_value else None


def recalculate(document: Any, lines: list[Any], *, include_hmf: bool) -> None:
    reset_document_calculated_fields(document)
    for line in lines:
        reset_calculated_fields(line)

    entered_total = sum_entered_value(lines)
    if entered_total is not None:
        document.total_entered_value = parser.format_money(entered_total)

    for line in lines:
        parser.calculate_line_amounts(line, has_hmf=include_hmf)

    duty_total = parser.sum_decimal_field(lines, "calculated_duty_total")
    hmf_total = parser.sum_decimal_field(lines, "calculated_hmf_amount") if include_hmf else None

    if entered_total is not None:
        document.calculated_mpf_total = parser.format_money(
            parser.clamp_mpf(parser.money_round(entered_total * parser.MPF_RATE))
        )
    document.calculated_duty_total = parser.format_money(duty_total) if duty_total is not None else None
    document.calculated_hmf_total = parser.format_money(hmf_total) if hmf_total is not None else None

    mpf_total = parser.parse_decimal(document.calculated_mpf_total)
    other_total = Decimal("0")
    has_other = False
    if mpf_total is not None:
        other_total += mpf_total
        has_other = True
    if hmf_total is not None:
        other_total += hmf_total
        has_other = True
    if has_other:
        document.calculated_other_total = parser.format_money(other_total)

    grand_total = Decimal("0")
    has_grand = False
    if duty_total is not None:
        grand_total += duty_total
        has_grand = True
    if has_other:
        grand_total += other_total
        has_grand = True
    if has_grand:
        document.calculated_grand_total = parser.format_money(grand_total)

    document.duty_variance = parser.decimal_difference(
        document.duty_total,
        document.calculated_duty_total,
    )
    document.other_variance = parser.decimal_difference(
        document.other_total,
        document.calculated_other_total,
    )
    document.grand_total_variance = parser.decimal_difference(
        document.grand_total,
        document.calculated_grand_total,
    )


def response_payload(document: Any, lines: list[Any], *, include_hmf: bool) -> dict[str, Any]:
    return {
        "document": asdict(document),
        "lines": [asdict(line) for line in lines],
        "include_hmf": include_hmf,
        "summary": {
            "line_count": len(lines),
            "has_text_layer": document.has_text_layer,
            "parse_notes": document.parse_notes,
        },
    }


def safe_upload_name(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    return f"{uuid4().hex}{suffix}"


def display(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def clean_filename(value: str | None) -> str:
    stem = Path(value or "7501-adjusted").stem
    safe = "".join(char if char.isascii() and (char.isalnum() or char in ("-", "_")) else "-" for char in stem)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or "7501-adjusted"


def trim_text(value: Any, max_chars: int) -> str:
    text = display(value).replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "*"


def split_text(value: Any, max_chars: int, max_lines: int) -> list[str]:
    words = display(value).replace("\n", " ").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word[:max_chars]
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = trim_text(lines[-1], max_chars)
    return lines or [""]


def money_value(*values: Any) -> str:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""


def draw_box(c: canvas.Canvas, x: float, y: float, w: float, h: float, label: str, value: Any) -> None:
    c.setStrokeColor(colors.black)
    c.rect(x, y, w, h, stroke=1, fill=0)
    c.setFont("Helvetica", 6)
    c.drawString(x + 3, y + h - 8, label)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(x + 3, y + 5, trim_text(value, max(8, int(w / 4.4))))


def draw_right(c: canvas.Canvas, text: Any, x: float, y: float, w: float) -> None:
    c.drawRightString(x + w - 3, y, trim_text(text, max(8, int(w / 4.8))))


def draw_pdf_header(c: canvas.Canvas, document: Any, page_no: int, page_count: int) -> None:
    c.setTitle("7501 Internal Adjustment Copy")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(24, 762, "CBP Form 7501 - Internal Adjustment Copy")
    c.setFont("Helvetica", 7)
    c.drawRightString(588, 764, f"Generated {date.today().isoformat()}  Page {page_no} of {page_count}")
    c.drawString(24, 750, "For internal reconciliation only. Review before any official filing or customer release.")

    top = 714
    row_h = 28
    col_w = 94
    fields_row_1 = [
        ("Entry Number", document.entry_number),
        ("Entry Type", document.entry_type),
        ("Summary Date", document.summary_date),
        ("Port Code", document.port_code),
        ("Entry Date", document.entry_date),
        ("Import Date", document.import_date),
    ]
    for index, (label, value) in enumerate(fields_row_1):
        draw_box(c, 24 + index * col_w, top, col_w, row_h, label, value)

    fields_row_2 = [
        ("Mode", document.mode_of_transport),
        ("Origin", document.country_of_origin),
        ("B/L or AWB", document.bl_or_awb_number),
        ("Manufacturer ID", document.manufacturer_id),
        ("Exporting Country", document.exporting_country),
        ("Invoice", document.invoice_number),
    ]
    for index, (label, value) in enumerate(fields_row_2):
        draw_box(c, 24 + index * col_w, top - row_h, col_w, row_h, label, value)


def draw_table_header(c: canvas.Canvas, y: float) -> None:
    headers = [
        ("Line", 24, 32),
        ("Description", 56, 142),
        ("HTS", 198, 76),
        ("Entered", 274, 66),
        ("Rate", 340, 80),
        ("Duty", 420, 54),
        ("MPF", 474, 52),
        ("HMF", 526, 52),
    ]
    c.setFillColor(colors.HexColor("#eef2f6"))
    c.rect(24, y - 14, 554, 18, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setStrokeColor(colors.black)
    c.line(24, y - 14, 578, y - 14)
    c.setFont("Helvetica-Bold", 7)
    for label, x, _ in headers:
        c.drawString(x + 3, y - 8, label)


def draw_line_row(c: canvas.Canvas, line: Any, y: float) -> None:
    c.setFont("Helvetica", 7)
    c.drawString(27, y, trim_text(line.line_no, 5))
    desc_lines = split_text(line.description, 34, 2)
    c.drawString(59, y, desc_lines[0])
    if len(desc_lines) > 1:
        c.drawString(59, y - 9, desc_lines[1])
    c.drawString(201, y, trim_text(line.hts, 17))
    c.drawRightString(337, y, money_value(line.entered_value))
    c.drawString(343, y, trim_text(line.rate, 18))
    draw_right(c, money_value(line.calculated_duty_total, line.duty_amount), 420, y, 54)
    draw_right(c, money_value(line.calculated_mpf_amount, line.mpf_amount), 474, y, 52)
    draw_right(c, money_value(line.calculated_hmf_amount, line.hmf_amount), 526, y, 52)
    c.setStrokeColor(colors.HexColor("#d9e1ea"))
    c.line(24, y - 13, 578, y - 13)


def draw_totals(c: canvas.Canvas, document: Any, y: float) -> None:
    c.setStrokeColor(colors.black)
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(438, y, "Total Entered Value")
    c.drawRightString(578, y, money_value(document.total_entered_value))
    c.drawRightString(438, y - 15, "Duty")
    c.drawRightString(578, y - 15, money_value(document.calculated_duty_total, document.duty_total))
    c.drawRightString(438, y - 30, "Other Fees")
    c.drawRightString(578, y - 30, money_value(document.calculated_other_total, document.other_total))
    c.drawRightString(438, y - 45, "Grand Total")
    c.drawRightString(578, y - 45, money_value(document.calculated_grand_total, document.grand_total))
    c.line(450, y - 36, 578, y - 36)


def generate_adjusted_pdf(document: Any, lines: list[Any]) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    rows_per_page = 28
    line_pages = max(1, (len(lines) + rows_per_page - 1) // rows_per_page)
    page_count = line_pages

    for page_index in range(page_count):
        draw_pdf_header(c, document, page_index + 1, page_count)
        table_y = 646
        draw_table_header(c, table_y)
        start = page_index * rows_per_page
        page_lines = lines[start : start + rows_per_page]
        y = table_y - 31
        for line in page_lines:
            draw_line_row(c, line, y)
            y -= 18
        if page_index == page_count - 1:
            draw_totals(c, document, 104)
        c.showPage()

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/parse")
async def parse_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    saved_path = UPLOAD_DIR / safe_upload_name(file.filename)
    try:
        with saved_path.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        key = f"upload|{Path(file.filename).stem}"
        parsed = parser.parse_pdf(saved_path, "original", key)
        include_hmf = bool(parsed.document.hmf_total) or any(line.hmf_amount for line in parsed.lines)
        recalculate(parsed.document, parsed.lines, include_hmf=include_hmf)
        parsed.document.source_file = file.filename
        for line in parsed.lines:
            line.source_file = file.filename
        return response_payload(parsed.document, parsed.lines, include_hmf=include_hmf)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to parse PDF: {exc}") from exc


@app.post("/api/recalculate")
def recalculate_payload(payload: RecalculateRequest) -> dict[str, Any]:
    try:
        document = dataclass_from_dict(parser.TaxDocument, payload.document)
        lines = [dataclass_from_dict(parser.TaxLine, line) for line in payload.lines]
        document.line_count = len(lines)
        recalculate(document, lines, include_hmf=payload.include_hmf)
        return response_payload(document, lines, include_hmf=payload.include_hmf)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to recalculate: {exc}") from exc


@app.post("/api/generate-pdf")
def generate_pdf(payload: GeneratePdfRequest) -> StreamingResponse:
    try:
        document = dataclass_from_dict(parser.TaxDocument, payload.document)
        lines = [dataclass_from_dict(parser.TaxLine, line) for line in payload.lines]
        document.line_count = len(lines)
        recalculate(document, lines, include_hmf=payload.include_hmf)
        pdf_bytes = generate_adjusted_pdf(document, lines)
        filename = f"{clean_filename(document.source_file)}-adjusted-7501.pdf"
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to generate PDF: {exc}") from exc
