from __future__ import annotations

import importlib.util
from io import BytesIO
import shutil
import sys
from dataclasses import asdict, fields, is_dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader, PdfWriter
from pydantic import BaseModel
from reportlab.lib import colors
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
    upload_id: str | None = None
    transport_mode: str = "auto"


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


def response_payload(
    document: Any,
    lines: list[Any],
    *,
    include_hmf: bool,
    upload_id: str | None = None,
    transport_mode: str = "auto",
) -> dict[str, Any]:
    return {
        "document": asdict(document),
        "lines": [asdict(line) for line in lines],
        "include_hmf": include_hmf,
        "upload_id": upload_id,
        "transport_mode": transport_mode,
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


def upload_path(upload_id: str | None) -> Path:
    if not upload_id:
        raise HTTPException(status_code=400, detail="Please upload and parse the original PDF again.")
    path = UPLOAD_DIR / Path(upload_id).name
    try:
        resolved = path.resolve()
        upload_root = UPLOAD_DIR.resolve()
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="Original uploaded PDF was not found.") from None
    if upload_root not in resolved.parents and resolved != upload_root:
        raise HTTPException(status_code=400, detail="Invalid upload reference.")
    if not resolved.exists():
        raise HTTPException(status_code=400, detail="Original uploaded PDF was not found.")
    return resolved


def display(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def clean_filename(value: str | None) -> str:
    stem = Path(value or "7501-adjusted").stem
    safe = "".join(char if char.isascii() and (char.isalnum() or char in ("-", "_")) else "-" for char in stem)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or "7501-adjusted"


def format_pdf_money(value: Any, *, keep_cents: bool = True) -> str:
    decimal_value = parser.parse_decimal(value)
    if decimal_value is None:
        return display(value)
    if keep_cents or decimal_value != decimal_value.to_integral_value():
        return f"${decimal_value:,.2f}"
    return f"${decimal_value:,.0f}"


def draw_replacement(c: canvas.Canvas, x: float, y: float, w: float, h: float, value: Any) -> None:
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.white)
    c.rect(x, y - 2, w, h, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    c.drawRightString(x + w - 1, y, display(value))


def group_rows(fragments: list[Any], page: int) -> dict[int, list[Any]]:
    rows: dict[int, list[Any]] = {}
    for fragment in fragments:
        if fragment.page != page or fragment.size < 8.0:
            continue
        rows.setdefault(round(fragment.y), []).append(fragment)
    return {key: sorted(value, key=lambda item: item.x) for key, value in rows.items()}


def row_text(row: list[Any]) -> str:
    return parser.normalize_spaces(" ".join(fragment.text.strip() for fragment in row))


def page_line_starts(fragments: list[Any]) -> list[Any]:
    starts = [
        fragment
        for fragment in fragments
        if 35 <= fragment.x <= 55
        and parser.re.match(r"^\s*\d{3}(?:\s|$)", fragment.text.strip())
        and not fragment.text.strip().startswith("499")
        and fragment.y < parser.page_line_table_top(fragment.page)
        and fragment.y > (280 if fragment.page == 1 else 40)
    ]
    return sorted(starts, key=lambda fragment: (fragment.page, -fragment.y))


def original_line_targets(original_path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    reader = PdfReader(str(original_path))
    fragments = parser.extract_fragments(reader)
    parsed = parser.parse_pdf(original_path, "original", f"upload|{original_path.stem}")
    starts = page_line_starts(fragments)
    targets: dict[tuple[int, str], dict[str, Any]] = {}

    for index, start in enumerate(starts):
        line_no = start.text.strip()[:3]
        original_line = next(
            (line for line in parsed.lines if line.page == start.page and line.line_no == line_no),
            None,
        )
        if original_line is None:
            continue
        next_start = starts[index + 1] if index + 1 < len(starts) else None
        y_low = 280.0 if start.page == 1 else 40.0
        if next_start and next_start.page == start.page:
            y_low = next_start.y + 1.0
        rows = parser.rows_for_line(fragments, start.page, start.y, y_low)
        hts_y = None
        chapter_ys: list[float] = []
        mpf_y = None
        hmf_y = None
        for row in rows:
            text = row_text(row)
            if original_line.hts and original_line.hts in text:
                hts_y = row[0].y
            if "Merchandise Processing Fee" in text:
                mpf_y = row[0].y
            if "Harbor Maintenance Fee" in text:
                hmf_y = row[0].y
            chapter_codes = [item.strip() for item in (original_line.chapter_99_codes or "").split(";") if item.strip()]
            if any(code in text for code in chapter_codes):
                chapter_ys.append(row[0].y)
        targets[(start.page, line_no)] = {
            "original": original_line,
            "hts_y": hts_y,
            "chapter_ys": chapter_ys,
            "mpf_y": mpf_y,
            "hmf_y": hmf_y,
        }

    return targets


def calculated_chapter_amounts(line: Any) -> list[str]:
    entered_value = parser.parse_decimal(line.entered_value)
    if entered_value is None:
        return []
    amounts: list[str] = []
    chapter_rates = [item.strip() for item in (line.chapter_99_rates or "").split(";") if item.strip()]
    for rate_text in chapter_rates:
        rate = parser.percent_to_decimal(rate_text)
        if rate is None:
            amounts.append("")
            continue
        amounts.append(parser.format_money(parser.money_round(entered_value * rate)) or "")
    return amounts


def draw_page_overlay(c: canvas.Canvas, document: Any, lines: list[Any], targets: dict[tuple[int, str], dict[str, Any]], page_number: int) -> None:
    for line in lines:
        if line.page != page_number or not line.line_no:
            continue
        target = targets.get((page_number, line.line_no))
        if not target:
            continue
        hts_y = target.get("hts_y")
        if hts_y:
            draw_replacement(c, 330, hts_y, 64, 11, format_pdf_money(line.entered_value, keep_cents=False))
            if line.calculated_base_duty:
                draw_replacement(c, 536, hts_y, 43, 11, format_pdf_money(line.calculated_base_duty, keep_cents=True))
        for amount, y in zip(calculated_chapter_amounts(line), target.get("chapter_ys") or []):
            if amount:
                draw_replacement(c, 536, y, 43, 11, format_pdf_money(amount, keep_cents=True))
        if target.get("mpf_y") and line.calculated_mpf_amount:
            draw_replacement(c, 536, target["mpf_y"], 43, 11, format_pdf_money(line.calculated_mpf_amount, keep_cents=True))
        if target.get("hmf_y") and line.calculated_hmf_amount:
            draw_replacement(c, 536, target["hmf_y"], 43, 11, format_pdf_money(line.calculated_hmf_amount, keep_cents=True))

    if page_number == 1:
        if document.total_entered_value:
            draw_replacement(c, 175, 248, 78, 11, format_pdf_money(document.total_entered_value, keep_cents=False))
        if document.calculated_duty_total:
            draw_replacement(c, 536, 241.5, 43, 11, format_pdf_money(document.calculated_duty_total, keep_cents=True))
        if document.calculated_other_total:
            draw_replacement(c, 536, 197.5, 43, 11, format_pdf_money(document.calculated_other_total, keep_cents=True))
        if document.calculated_grand_total:
            draw_replacement(c, 536, 175.5, 43, 11, format_pdf_money(document.calculated_grand_total, keep_cents=True))


def template_preserving_pdf(original_path: Path, document: Any, lines: list[Any]) -> bytes:
    reader = PdfReader(str(original_path))
    writer = PdfWriter()
    targets = original_line_targets(original_path)

    for page_index, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=(width, height))
        draw_page_overlay(c, document, lines, targets, page_index)
        c.save()
        buffer.seek(0)
        overlay_reader = PdfReader(buffer)
        page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    output = BytesIO()
    writer.write(output)
    output.seek(0)
    return output.getvalue()


def generate_adjusted_pdf(original_path: Path, document: Any, lines: list[Any]) -> bytes:
    return template_preserving_pdf(original_path, document, lines)


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
        transport_mode = "ocean" if include_hmf else "auto"
        return response_payload(
            parsed.document,
            parsed.lines,
            include_hmf=include_hmf,
            upload_id=saved_path.name,
            transport_mode=transport_mode,
        )
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
        return response_payload(
            document,
            lines,
            include_hmf=payload.include_hmf,
            upload_id=payload.upload_id,
            transport_mode=payload.transport_mode,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to recalculate: {exc}") from exc


@app.post("/api/generate-pdf")
def generate_pdf(payload: GeneratePdfRequest) -> StreamingResponse:
    try:
        document = dataclass_from_dict(parser.TaxDocument, payload.document)
        lines = [dataclass_from_dict(parser.TaxLine, line) for line in payload.lines]
        document.line_count = len(lines)
        recalculate(document, lines, include_hmf=payload.include_hmf)
        original_path = upload_path(payload.upload_id)
        pdf_bytes = generate_adjusted_pdf(original_path, document, lines)
        filename = f"{clean_filename(document.source_file)}-adjusted-7501.pdf"
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to generate PDF: {exc}") from exc
