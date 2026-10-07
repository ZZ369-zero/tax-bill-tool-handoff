from __future__ import annotations

import base64
import importlib.util
from io import BytesIO
import math
import os
import secrets
import shutil
import sys
import time
from dataclasses import asdict, dataclass, fields, is_dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import ArrayObject, ContentStream, FloatObject, StreamObject, TextStringObject
from pydantic import BaseModel, Field
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from tools.excel_adjustment import apply_second_sheet
from tools.hts_lookup import lookup_hts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.getenv("TAX_TOOL_DATA_DIR", PROJECT_ROOT))
UPLOAD_DIR = DATA_ROOT / "uploads"
STATIC_DIR = Path(__file__).resolve().parent / "static"
PARSER_PATH = PROJECT_ROOT / "tools" / "7501_parser.py"
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
UPLOAD_RETENTION_SECONDS = int(os.getenv("UPLOAD_RETENTION_SECONDS", str(24 * 60 * 60)))
APP_USERNAME = os.getenv("APP_USERNAME")
APP_PASSWORD = os.getenv("APP_PASSWORD")
TEMP_UPLOAD_SUFFIXES = {".pdf", ".xlsx"}
PDF_COORDINATE_TOLERANCE = 0.5
PDF_OVERLAY_BORDER_SAFE_GAP = 0.8
TRANSPORT_MODES = {"auto", "air", "ocean"}
APP_VERSION = "0.1.26"
WEIGHT_UNITS = {"KG", "KGS", "LB", "LBS", "G"}
LINE_CALCULATION_FIELDS = ("hts", "net_quantity", "entered_value", "rate")


def load_parser_module():
    spec = importlib.util.spec_from_file_location("tax7501_parser", PARSER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load parser module: {PARSER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parser = load_parser_module()
app = FastAPI(title="7501 Tax Bill Tool", version=APP_VERSION)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def optional_basic_auth(request: Request, call_next):
    if not APP_USERNAME or not APP_PASSWORD or request.url.path == "/api/health":
        return await call_next(request)
    authorization = request.headers.get("Authorization", "")
    authenticated = False
    if authorization.startswith("Basic "):
        try:
            decoded = base64.b64decode(authorization[6:]).decode("utf-8")
            username, password = decoded.split(":", 1)
            authenticated = secrets.compare_digest(username, APP_USERNAME) and secrets.compare_digest(
                password,
                APP_PASSWORD,
            )
        except (ValueError, UnicodeDecodeError):
            authenticated = False
    if not authenticated:
        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="7501 Tax Bill Tool"'},
        )
    return await call_next(request)


class RecalculateRequest(BaseModel):
    document: dict[str, Any]
    lines: list[dict[str, Any]]
    include_hmf: bool = False
    upload_id: str | None = None
    transport_mode: str = "auto"
    modified_fields: list[str] = Field(default_factory=list)


class GeneratePdfRequest(RecalculateRequest):
    pass


@dataclass(frozen=True)
class PdfTextReplacement:
    page: int
    field: str
    old_text: str
    new_text: str
    x_min: float
    x_max: float
    y: float | None = None
    alignment: str = "right"
    y_tolerance: float = 0.8
    font_name: str = "Helvetica"
    font_size: float = 8.0
    erase_x_min: float | None = None
    erase_x_max: float | None = None


@dataclass(frozen=True)
class PdfRuleSegment:
    orientation: str
    position: float
    start: float
    end: float
    line_width: float = 1.0


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
        value = parser.cbp_entered_value(line.entered_value)
        if value is not None:
            total += value
            has_value = True
    return total if has_value else None


def recalculate(document: Any, lines: list[Any], *, include_hmf: bool) -> None:
    reset_document_calculated_fields(document)
    for line in lines:
        reset_calculated_fields(line)
        raw_entered_value = line.entered_value
        normalized_value = parser.format_whole_dollars(line.entered_value)
        if normalized_value is not None:
            line.entered_value = normalized_value
        notes = [
            note.strip()
            for note in display(line.parse_notes).split(";")
            if note.strip() and not note.strip().startswith("entered value rounded")
        ]
        raw_decimal = parser.parse_decimal(raw_entered_value)
        normalized_decimal = parser.parse_decimal(normalized_value)
        if (
            raw_decimal is not None
            and normalized_decimal is not None
            and raw_decimal != normalized_decimal
        ):
            notes.append(f"entered value rounded from {raw_entered_value} to {normalized_value} USD")
        line.parse_notes = "; ".join(notes)
        if not line.required_units and line.net_unit:
            line.required_units = line.net_unit

    entered_total = sum_entered_value(lines)
    if entered_total is not None:
        document.total_entered_value = parser.format_money(entered_total)
        invoice_total = parser.format_money(entered_total)
        if document.invoice_value is not None:
            document.invoice_value = invoice_total
        if document.invoice_entered_value is not None:
            document.invoice_entered_value = invoice_total

    for line in lines:
        parser.calculate_line_amounts(line, has_hmf=include_hmf)

    duty_total = parser.sum_decimal_field(lines, "calculated_duty_total")
    mpf_line_total = parser.sum_decimal_field(lines, "calculated_mpf_amount")
    hmf_total = parser.sum_decimal_field(lines, "calculated_hmf_amount") if include_hmf else None

    if mpf_line_total is not None:
        document.calculated_mpf_total = parser.format_money(
            parser.clamp_mpf(mpf_line_total, document)
        )
    elif entered_total is not None:
        document.calculated_mpf_total = parser.format_money(
            parser.clamp_mpf(parser.money_round(entered_total * parser.MPF_RATE), document)
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


def normalize_transport_mode(value: str | None) -> str:
    mode = (value or "auto").strip().lower()
    if mode not in TRANSPORT_MODES:
        raise HTTPException(
            status_code=400,
            detail="transport_mode must be one of: auto, air, ocean.",
        )
    return mode


def parsed_has_hmf(document: Any, lines: list[Any]) -> bool:
    return bool(document.hmf_total) or any(line.hmf_amount for line in lines)


def include_hmf_for_transport(document: Any, lines: list[Any], transport_mode: str | None) -> bool:
    mode = normalize_transport_mode(transport_mode)
    if mode == "ocean":
        return True
    if mode == "air":
        return False
    return parsed_has_hmf(document, lines)


def validate_hmf_pdf_layout(*, original_has_hmf: bool, include_hmf: bool, transport_mode: str) -> None:
    if transport_mode == "auto":
        return
    if include_hmf and not original_has_hmf:
        raise ValueError(
            "海运模式需要生成 501-HMF，但原始 7501 PDF 没有可替换的 501-HMF 栏位；"
            "请确认原单是否为海运税单模板。"
        )
    if not include_hmf and original_has_hmf:
        raise ValueError(
            "空运模式不应包含 501-HMF，但原始 7501 PDF 已带有 501-HMF 栏位；"
            "请确认原单是否选错运输方式。"
        )


def response_payload(
    document: Any,
    lines: list[Any],
    *,
    include_hmf: bool,
    upload_id: str | None = None,
    transport_mode: str = "auto",
    modified_fields: list[str] | None = None,
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "document": asdict(document),
        "lines": [asdict(line) for line in lines],
        "include_hmf": include_hmf,
        "upload_id": upload_id,
        "transport_mode": transport_mode,
        "modified_fields": modified_fields or [],
        "validation_errors": validation_errors or [],
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


def cleanup_old_uploads(*, now: float | None = None) -> int:
    if UPLOAD_RETENTION_SECONDS <= 0 or not UPLOAD_DIR.exists():
        return 0
    cutoff = (time.time() if now is None else now) - UPLOAD_RETENTION_SECONDS
    removed = 0
    for path in UPLOAD_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in TEMP_UPLOAD_SUFFIXES:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


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


def format_pdf_number(value: Any, *, keep_cents: bool = True) -> str:
    decimal_value = parser.parse_decimal(value)
    if decimal_value is None:
        return display(value)
    if keep_cents or decimal_value != decimal_value.to_integral_value():
        return f"{decimal_value:,.2f}"
    return f"{decimal_value:,.0f}"


def format_pdf_money(value: Any, *, keep_cents: bool = True) -> str:
    number = format_pdf_number(value, keep_cents=keep_cents)
    return f"${number}" if number else ""


def format_pdf_money_like_original(
    value: Any,
    original_text: Any,
    *,
    keep_cents: bool = True,
) -> str:
    number = format_pdf_number(value, keep_cents=keep_cents)
    if not number:
        return ""
    return f"${number}" if "$" in display(original_text) else number


def format_pdf_number_like_original(
    value: Any,
    original_text: Any,
    *,
    default_decimals: int = 2,
) -> str:
    decimal_value = parser.parse_decimal(value)
    if decimal_value is None:
        return display(value)
    tokens = list(parser.re.finditer(r"[0-9][0-9,]*(?:\.\d+)?", display(original_text)))
    original_number = tokens[-1].group(0) if tokens else ""
    decimals = (
        len(original_number.rsplit(".", 1)[1])
        if "." in original_number
        else default_decimals
    )
    grouped = "," in original_number
    format_spec = f",.{decimals}f" if grouped else f".{decimals}f"
    return format(decimal_value, format_spec)


def values_equal(left: Any, right: Any) -> bool:
    left_decimal = parser.parse_decimal(left)
    right_decimal = parser.parse_decimal(right)
    if left_decimal is not None and right_decimal is not None:
        return left_decimal == right_decimal
    return parser.normalize_spaces(display(left)) == parser.normalize_spaces(display(right))


def decimal_places(value: Any, *, trim_trailing_zeros: bool = False) -> int:
    text = display(value).replace(",", "").strip()
    if "." not in text:
        return 0
    fraction = text.rsplit(".", 1)[1]
    if trim_trailing_zeros:
        fraction = fraction.rstrip("0")
    return len(fraction)


def quantity_decimal_limit(unit: Any) -> int | None:
    normalized = parser.re.sub(r"[^A-Z]", "", display(unit).upper())
    if normalized in WEIGHT_UNITS:
        return 2
    return None


def quantity_text(value: Any, unit: Any, original_value: Any) -> str:
    decimal_value = parser.parse_decimal(value)
    original_decimals = decimal_places(original_value)
    value_decimals = decimal_places(value, trim_trailing_zeros=True)
    decimals = max(original_decimals, value_decimals)
    decimal_limit = quantity_decimal_limit(unit)
    if decimal_limit is not None:
        decimals = min(decimals, decimal_limit)
    if decimal_value is None:
        number = display(value)
    elif decimals:
        quantum = Decimal("1").scaleb(-decimals)
        rounded = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
        number = f"{rounded:,.{decimals}f}"
    else:
        rounded = decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        number = f"{rounded:,.0f}"
    return parser.normalize_spaces(f"{number} {display(unit)}")


def format_hts_like_original(value: Any, original_value: Any) -> str:
    digits = parser.re.sub(r"\D", "", display(value))
    original = display(original_value)
    groups = [part for part in parser.re.split(r"\D+", original) if part]
    if not groups or sum(len(part) for part in groups) != len(digits):
        return display(value)
    parts: list[str] = []
    offset = 0
    for group in groups:
        parts.append(digits[offset : offset + len(group)])
        offset += len(group)
    return ".".join(parts)


def money_values(value: Any) -> list[str]:
    return [item.strip() for item in display(value).split(";") if item.strip()]


def add_replacement(
    replacements: list[PdfTextReplacement],
    *,
    page: int,
    field: str,
    old_value: Any,
    new_value: Any,
    old_text: str,
    new_text: str,
    x_min: float,
    x_max: float,
    y: float | None,
    alignment: str = "right",
    font_name: str | None = None,
    font_size: float | None = None,
    erase_x_min: float | None = None,
    erase_x_max: float | None = None,
) -> None:
    if values_equal(old_value, new_value):
        return
    replacements.append(
        PdfTextReplacement(
            page=page,
            field=field,
            old_text=old_text,
            new_text=new_text,
            x_min=x_min,
            x_max=x_max,
            y=y,
            alignment=alignment,
            font_name=font_name or "Helvetica",
            font_size=float(font_size or 8.0),
            erase_x_min=erase_x_min,
            erase_x_max=erase_x_max,
        )
    )


def row_text(row: list[Any]) -> str:
    return parser.normalize_spaces(" ".join(fragment.text.strip() for fragment in row))


def reportlab_overlay_font_name(pdf_font_name: Any) -> str:
    base_name = display(pdf_font_name).lstrip("/")
    if "+" in base_name:
        base_name = base_name.split("+", 1)[1]
    aliases = {
        "Arial": "Helvetica",
        "ArialMT": "Helvetica",
        "Arial-BoldMT": "Helvetica-Bold",
        "Arial-ItalicMT": "Helvetica-Oblique",
        "Arial-BoldItalicMT": "Helvetica-BoldOblique",
        "CourierNewPSMT": "Courier",
        "CourierNewPS-BoldMT": "Courier-Bold",
        "CourierNewPS-ItalicMT": "Courier-Oblique",
        "CourierNewPS-BoldItalicMT": "Courier-BoldOblique",
    }
    font_name = aliases.get(base_name, base_name)
    try:
        pdfmetrics.getFont(font_name)
    except KeyError:
        return "Helvetica"
    return font_name


def fragment_text_style(fragment: Any | None) -> dict[str, Any]:
    if fragment is None:
        return {}
    try:
        font_size = float(fragment.size)
    except (TypeError, ValueError):
        font_size = 8.0
    return {
        "font_name": reportlab_overlay_font_name(getattr(fragment, "font", "")),
        "font_size": font_size or 8.0,
    }


def row_text_style(row: list[Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    candidates = [
        fragment
        for fragment in row
        if display(fragment.text).strip()
        and getattr(fragment, "size", 0) >= 8
    ]
    if not candidates:
        return {}
    return fragment_text_style(candidates[0])


def replacement_style(
    target: dict[str, Any] | None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    style = dict(fallback or {})
    if target:
        if target.get("font_name"):
            style["font_name"] = target["font_name"]
        if target.get("font_size"):
            style["font_size"] = target["font_size"]
    return style


def page_line_starts(fragments: list[Any]) -> list[Any]:
    page_bottoms = {
        page: parser.page_line_table_bottom(fragments, page)
        for page in {fragment.page for fragment in fragments}
    }
    starts = [
        fragment
        for fragment in fragments
        if 20 <= fragment.x <= 55
        and parser.line_number_from_text(fragment.text)
        and not fragment.text.strip().startswith("499")
        and fragment.y < parser.page_line_table_top(fragment.page)
        and fragment.y > page_bottoms.get(fragment.page, 40.0)
    ]
    return sorted(starts, key=lambda fragment: (fragment.page, -fragment.y))


def zone_text(row: list[Any], x_min: float, x_max: float) -> str | None:
    fragments = [fragment for fragment in row if x_min <= fragment.x <= x_max]
    if not fragments:
        return None
    return parser.normalize_spaces("".join(fragment.text for fragment in sorted(fragments, key=lambda f: f.x)))


def amount_target_for_fragment(fragment: Any, amount: str) -> dict[str, Any]:
    text = display(fragment.text)
    styled_amount = f"${amount}" if "$" in text else amount
    if styled_amount not in text:
        styled_amount = amount
    return inline_amount_target(fragment, styled_amount)


def matching_amount_token(text: Any, value: Any) -> str | None:
    expected = parser.parse_decimal(value)
    if expected is None:
        return None
    matches = [
        match.group(0)
        for match in parser.re.finditer(
            r"(?<![0-9.])[0-9][0-9,]*(?:\.\d+)?(?![0-9.])",
            display(text),
        )
        if parser.parse_decimal(match.group(0)) == expected
    ]
    return matches[-1] if matches else None


def zone_amount_target(row: list[Any], x_min: float, x_max: float) -> dict[str, Any] | None:
    fragments = [fragment for fragment in row if x_min <= fragment.x <= x_max]
    for fragment in sorted(fragments, key=lambda f: f.x, reverse=True):
        text = display(fragment.text)
        amount = parser.money_after_dollar(text, last=True) or parser.parse_last_money(text)
        if amount:
            return amount_target_for_fragment(fragment, amount)
    text = zone_text(row, x_min, x_max)
    if not text:
        return None
    amount = parser.money_after_dollar(text, last=True) or parser.parse_last_money(text)
    if not amount:
        return None
    return {
        "text": f"${amount}" if "$" in text else amount,
        "x_min": x_min,
        "x_max": x_max,
        **row_text_style(row),
    }


def zone_amount_text(row: list[Any], x_min: float, x_max: float) -> str | None:
    target = zone_amount_target(row, x_min, x_max)
    return target.get("text") if target else None


def amount_target_at_box(
    fragments: list[Any],
    *,
    page: int = 1,
    value: Any,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> dict[str, Any] | None:
    expected = parser.parse_decimal(value)
    if expected is None:
        return None
    candidates: list[dict[str, Any]] = []
    for fragment in fragments:
        if fragment.page != page or not (x_min <= fragment.x <= x_max) or not (y_min <= fragment.y <= y_max):
            continue
        text = display(fragment.text)
        amount = matching_amount_token(text, value)
        if amount and parser.parse_decimal(amount) == expected:
            target = amount_target_for_fragment(fragment, amount)
            target["y"] = fragment.y
            candidates.append(target)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item["y"], reverse=True)[0]


def inline_amount_target(
    fragment: Any,
    amount: str,
    offset: int | None = None,
    *,
    left_padding: float = 30,
) -> dict[str, Any]:
    text = display(fragment.text)
    offset = text.find(amount) if offset is None else offset
    style = fragment_text_style(fragment)
    if offset < 0:
        return {"y": fragment.y, "text": amount, **style}
    char_width = max(float(fragment.size or 10) * 0.6, 4.5)
    x_min = float(fragment.x) + offset * char_width
    x_max = x_min + len(amount) * char_width
    return {
        "y": fragment.y,
        "text": amount,
        "x_min": max(0, x_min - left_padding),
        "x_max": x_max,
        "alignment": "right",
        **style,
    }


def fee_summary_target(
    fragments: list[Any],
    *,
    fee_code: str,
    label: str,
    value: Any,
) -> dict[str, Any] | None:
    expected = parser.parse_decimal(value)
    if expected is None:
        return None
    dotted_label = parser.re.compile(r"\.?".join(parser.re.escape(char) for char in label) + r"\.?", parser.re.I)
    for fragment in fragments:
        text = display(fragment.text)
        if fragment.page != 1 or fee_code not in text or not dotted_label.search(text):
            continue
        amount = matching_amount_token(text, value)
        if amount and parser.parse_decimal(amount) == expected:
            target = amount_target_for_fragment(fragment, amount)
            target["y"] = fragment.y
            return target
    return None


def document_replacement_targets(original_path: Path, document: Any) -> dict[str, dict[str, Any]]:
    if not original_path.exists():
        return {}
    reader = PdfReader(str(original_path))
    fragments = parser.extract_fragments(reader, original_path)
    targets: dict[str, dict[str, Any]] = {}
    target = amount_target_at_box(
        fragments,
        value=document.total_entered_value,
        x_min=165,
        x_max=270,
        y_min=210,
        y_max=275,
    )
    if target:
        targets["total_entered_value"] = target
    target = fee_summary_target(fragments, fee_code="499", label="MPF", value=document.mpf_total)
    if target:
        targets["mpf_summary"] = target
    target = fee_summary_target(fragments, fee_code="501", label="HMF", value=document.hmf_total)
    if target:
        targets["hmf_summary"] = target
    target = amount_target_at_box(
        fragments,
        value=document.total_other_fees or document.other_total,
        x_min=165,
        x_max=270,
        y_min=190,
        y_max=245,
    )
    if target:
        targets["block_39_other_fees"] = target
    target = amount_target_at_box(
        fragments,
        value=document.duty_total,
        x_min=520,
        x_max=595,
        y_min=225,
        y_max=285,
    )
    if target:
        targets["duty_total"] = target
    target = amount_target_at_box(
        fragments,
        value=document.other_total,
        x_min=520,
        x_max=595,
        y_min=185,
        y_max=235,
    )
    if target:
        targets["other_total"] = target
    target = amount_target_at_box(
        fragments,
        value=document.grand_total,
        x_min=520,
        x_max=595,
        y_min=165,
        y_max=210,
    )
    if target:
        targets["grand_total"] = target
    invoice_page = document.pages
    invoice_value = parser.parse_decimal(document.invoice_value)
    invoice_entered_value = parser.parse_decimal(document.invoice_entered_value)
    total_entered_value = parser.parse_decimal(document.total_entered_value)
    for fragment in fragments:
        if fragment.page != invoice_page:
            continue
        text = display(fragment.text)
        iv_match = parser.re.search(r"\bI\.V\.\s+([0-9,]+\.\d{2})", text, parser.re.I)
        if iv_match and invoice_value is not None and parser.parse_decimal(iv_match.group(1)) == invoice_value:
            targets["invoice_value"] = inline_amount_target(
                fragment,
                iv_match.group(1),
                iv_match.start(1),
            )
        ev_match = parser.re.search(r"\bE\.V\.\s+([0-9,]+\.\d{2})", text, parser.re.I)
        if (
            ev_match
            and invoice_entered_value is not None
            and parser.parse_decimal(ev_match.group(1)) == invoice_entered_value
        ):
            targets["invoice_entered_value"] = inline_amount_target(
                fragment,
                ev_match.group(1),
                ev_match.start(1),
            )
        as_match = parser.re.search(r"\bAS\s+([0-9,]+)\b", text, parser.re.I)
        if (
            as_match
            and total_entered_value is not None
            and parser.parse_decimal(as_match.group(1)) == total_entered_value
        ):
            targets["invoice_entered_value_as"] = inline_amount_target(
                fragment,
                as_match.group(1),
                as_match.start(1),
                left_padding=8,
            )
    return targets


def original_line_targets(original_path: Path, parsed: Any) -> dict[tuple[int, str], dict[str, Any]]:
    reader = PdfReader(str(original_path))
    fragments = parser.extract_fragments(reader, original_path)
    starts = page_line_starts(fragments)
    targets: dict[tuple[int, str], dict[str, Any]] = {}

    for index, start in enumerate(starts):
        line_no = parser.line_number_from_text(start.text) or start.text.strip()[:3]
        original_line = next(
            (line for line in parsed.lines if line.page == start.page and line.line_no == line_no),
            None,
        )
        if original_line is None:
            continue
        next_start = starts[index + 1] if index + 1 < len(starts) else None
        y_low = parser.page_line_table_bottom(fragments, start.page)
        if next_start and next_start.page == start.page:
            y_low = next_start.y + 1.0
        rows = parser.rows_for_line(fragments, start.page, start.y, y_low)
        hts_y = None
        hts_row: list[Any] | None = None
        chapter_ys: list[float] = []
        chapter_targets: list[dict[str, Any] | None] = []
        mpf_y = None
        mpf_target = None
        hmf_y = None
        hmf_target = None
        for row in rows:
            text = row_text(row)
            if original_line.hts and original_line.hts in text:
                hts_y = row[0].y
                hts_row = row
            if parser.re.search(r"Merchandise\s+Process(?:ing|\.)?\s*Fee", text, parser.re.I):
                mpf_y = row[0].y
                mpf_target = zone_amount_target(row, 500, 590)
            if "Harbor Maintenance Fee" in text:
                hmf_y = row[0].y
                hmf_target = zone_amount_target(row, 500, 590)
            chapter_codes = [
                item.strip()
                for item in (original_line.chapter_99_codes or "").split(";")
                if item.strip()
            ]
            if any(code in text for code in chapter_codes):
                chapter_ys.append(row[0].y)
                chapter_targets.append(zone_amount_target(row, 500, 590))
        entered_value_target = zone_amount_target(hts_row, 320, 398) if hts_row else None
        base_duty_target = zone_amount_target(hts_row, 500, 590) if hts_row else None
        line_style = row_text_style(hts_row) or fragment_text_style(start)
        targets[(start.page, line_no)] = {
            "original": original_line,
            "line_style": line_style,
            "hts_y": hts_y,
            "entered_value_target": entered_value_target,
            "entered_value_text": entered_value_target.get("text") if entered_value_target else None,
            "base_duty_target": base_duty_target,
            "base_duty_text": base_duty_target.get("text") if base_duty_target else None,
            "chapter_ys": chapter_ys,
            "chapter_targets": chapter_targets,
            "chapter_texts": [item.get("text") if item else None for item in chapter_targets],
            "mpf_y": mpf_y,
            "mpf_target": mpf_target,
            "mpf_text": mpf_target.get("text") if mpf_target else None,
            "hmf_y": hmf_y,
            "hmf_target": hmf_target,
            "hmf_text": hmf_target.get("text") if hmf_target else None,
        }

    return targets


def calculated_chapter_amounts(line: Any) -> list[str]:
    entered_value = parser.cbp_entered_value(line.entered_value)
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


def line_field_key(line: Any, field_name: str) -> str:
    return f"line:{line.page}:{line.line_no}:{field_name}"


def line_has_calculation_change(line: Any, modified_fields: list[str] | set[str]) -> bool:
    modified = set(modified_fields)
    return any(line_field_key(line, field_name) in modified for field_name in LINE_CALCULATION_FIELDS)


def suppress_expected_modified_variances(
    document: Any,
    lines: list[Any],
    modified_fields: list[str] | set[str],
) -> None:
    if not modified_fields:
        return

    has_calculation_change = False
    for line in lines:
        if not line_has_calculation_change(line, modified_fields):
            continue
        has_calculation_change = True
        line.duty_variance = None
        line.mpf_variance = None
        line.hmf_variance = None

    if has_calculation_change or "document:transport_mode" in set(modified_fields):
        document.duty_variance = None
        document.other_variance = None
        document.grand_total_variance = None


def line_validation_errors(lines: list[Any], modified_fields: list[str] | set[str]) -> list[str]:
    modified = set(modified_fields)
    errors: list[str] = []
    for line in lines:
        calculation_modified = line_has_calculation_change(line, modified)
        if calculation_modified and line.rate:
            calculated_duty = parser.calculate_duty_for_rate(
                parser.cbp_entered_value(line.entered_value),
                line.rate,
                net_quantity=line.net_quantity,
                net_unit=line.net_unit,
            )
            if calculated_duty is None:
                errors.append(
                    f"Line {line.line_no}: unsupported or unit-mismatched duty rate {line.rate}"
                )
        if calculation_modified:
            for chapter_rate in [
                item.strip()
                for item in display(line.chapter_99_rates).split(";")
                if item.strip()
            ]:
                if parser.percent_to_decimal(chapter_rate) is None:
                    errors.append(
                        f"Line {line.line_no}: unsupported Chapter 99 rate {chapter_rate}"
                    )
        net_quantity_modified = line_field_key(line, "net_quantity") in modified
        gross_weight_modified = line_field_key(line, "gross_weight") in modified
        if not net_quantity_modified and not gross_weight_modified:
            continue
        net_quantity = parser.parse_decimal(line.net_quantity)
        gross_weight = parser.parse_decimal(line.gross_weight)
        net_unit = display(line.net_unit).upper()
        gross_unit = display(line.gross_unit).upper()
        if net_quantity_modified and net_quantity is not None and net_quantity < 0:
            errors.append(f"Line {line.line_no}: net quantity cannot be negative")
        if gross_weight_modified and gross_weight is not None and gross_weight < 0:
            errors.append(f"Line {line.line_no}: gross weight cannot be negative")
        if (
            net_unit == "KG"
            and gross_unit == "KG"
            and net_quantity is not None
            and gross_weight is not None
            and net_quantity > gross_weight
        ):
            errors.append(
                f"Line {line.line_no}: net quantity {line.net_quantity} KG exceeds "
                f"gross weight {line.gross_weight} KG"
            )
    return errors


def build_pdf_text_replacements(
    original_path: Path,
    document: Any,
    lines: list[Any],
    modified_fields: list[str] | set[str],
) -> list[PdfTextReplacement]:
    modified = set(modified_fields)
    if not modified:
        return []

    parsed = parser.parse_pdf(original_path, "original", f"upload|{original_path.stem}")
    original_document = parsed.document
    targets = original_line_targets(original_path, parsed)
    document_targets = document_replacement_targets(original_path, original_document)
    replacements: list[PdfTextReplacement] = []
    transport_changed = "document:transport_mode" in modified
    entered_changed_any = False
    duty_changed_any = False
    other_changed_any = transport_changed

    for line in lines:
        if not line.page or not line.line_no:
            continue
        gross_weight_changed = line_field_key(line, "gross_weight") in modified
        net_quantity_changed = line_field_key(line, "net_quantity") in modified
        entered_value_changed = line_field_key(line, "entered_value") in modified
        rate_changed = line_field_key(line, "rate") in modified
        hts_changed = line_field_key(line, "hts") in modified
        line_duty_changed = net_quantity_changed or entered_value_changed or rate_changed
        target = targets.get((line.page, line.line_no))
        if target is None:
            raise ValueError(f"Unable to locate line {line.line_no} on page {line.page} in the original PDF")
        original_line = target["original"]
        mpf_changed = (
            original_line.mpf_amount is not None
            and line.calculated_mpf_amount is not None
            and not values_equal(original_line.mpf_amount, line.calculated_mpf_amount)
        )
        if (
            not line_duty_changed
            and not gross_weight_changed
            and not hts_changed
            and not transport_changed
            and not mpf_changed
        ):
            continue

        hts_y = target.get("hts_y")
        line_style = target.get("line_style", {})
        entered_changed_any = entered_changed_any or entered_value_changed
        duty_changed_any = duty_changed_any or line_duty_changed
        other_changed_any = other_changed_any or entered_value_changed or mpf_changed

        if hts_changed:
            add_replacement(
                replacements,
                page=line.page,
                field=f"line {line.line_no} HTS",
                old_value=parser.re.sub(r"\D", "", display(original_line.hts)),
                new_value=parser.re.sub(r"\D", "", display(line.hts)),
                old_text=display(original_line.hts),
                new_text=format_hts_like_original(line.hts, original_line.hts),
                x_min=60,
                x_max=190,
                y=hts_y,
                alignment="left",
                **replacement_style(None, line_style),
            )

        if gross_weight_changed:
            old_gross_text = quantity_text(
                original_line.gross_weight,
                original_line.gross_unit,
                original_line.gross_weight,
            )
            new_gross_text = quantity_text(
                line.gross_weight,
                line.gross_unit or original_line.gross_unit,
                original_line.gross_weight,
            )
            add_replacement(
                replacements,
                page=line.page,
                field=f"line {line.line_no} gross weight",
                old_value=old_gross_text,
                new_value=new_gross_text,
                old_text=old_gross_text,
                new_text=new_gross_text,
                x_min=185,
                x_max=235,
                y=hts_y,
                **replacement_style(None, line_style),
            )

        if net_quantity_changed:
            old_quantity_text = quantity_text(
                original_line.net_quantity,
                original_line.net_unit,
                original_line.net_quantity,
            )
            new_quantity_text = quantity_text(
                line.net_quantity,
                line.net_unit or original_line.net_unit,
                original_line.net_quantity,
            )
            add_replacement(
                replacements,
                page=line.page,
                field=f"line {line.line_no} net quantity",
                old_value=old_quantity_text,
                new_value=new_quantity_text,
                old_text=old_quantity_text,
                new_text=new_quantity_text,
                x_min=230,
                x_max=315,
                y=hts_y,
                **replacement_style(None, line_style),
            )
        if entered_value_changed:
            entered_value_target = target.get("entered_value_target") or {}
            old_entered_text = entered_value_target.get("text") or target.get("entered_value_text") or format_pdf_money(
                original_line.entered_value,
                keep_cents=False,
            )
            entered_value_style = replacement_style(entered_value_target, line_style)
            entered_value_alignment = entered_value_target.get("alignment", "right")
            entered_value_x_min = entered_value_target.get("x_min", 350)
            entered_value_x_max = entered_value_target.get("x_max", 398)
            entered_value_erase_x_min = None
            if entered_value_target:
                char_width = max(float(entered_value_style.get("font_size", 8.0)) * 0.6, 4.5)
                original_amount_start = float(entered_value_x_max) - len(old_entered_text) * char_width
                # Draft 7501 templates place Block 36 values just to the right of
                # the 35/36 divider. The generic right-aligned amount target can
                # pull longer replacement text across that divider, so keep the
                # original left edge and start erasing only after the divider.
                if float(entered_value_x_min) < 320 <= float(entered_value_x_max):
                    entered_value_alignment = "left"
                    entered_value_x_min = max(320.5, original_amount_start)
                    entered_value_erase_x_min = 320.0
            add_replacement(
                replacements,
                page=line.page,
                field=f"line {line.line_no} entered value",
                old_value=original_line.entered_value,
                new_value=line.entered_value,
                old_text=old_entered_text,
                new_text=format_pdf_money_like_original(
                    line.entered_value,
                    old_entered_text,
                    keep_cents=False,
                ),
                x_min=entered_value_x_min,
                x_max=entered_value_x_max,
                y=entered_value_target.get("y", hts_y),
                alignment=entered_value_alignment,
                erase_x_min=entered_value_erase_x_min,
                **entered_value_style,
            )
        if rate_changed:
            add_replacement(
                replacements,
                page=line.page,
                field=f"line {line.line_no} rate",
                old_value=original_line.rate,
                new_value=line.rate,
                old_text=display(original_line.rate),
                new_text=display(line.rate),
                x_min=395,
                x_max=535,
                y=hts_y,
                alignment="left",
                **replacement_style(None, line_style),
            )
        if line_duty_changed:
            base_duty_target = target.get("base_duty_target") or {}
            old_base_duty_text = base_duty_target.get("text") or target.get("base_duty_text") or format_pdf_money(
                original_line.duty_amount
            )
            add_replacement(
                replacements,
                page=line.page,
                field=f"line {line.line_no} base duty",
                old_value=original_line.duty_amount,
                new_value=line.calculated_base_duty,
                old_text=old_base_duty_text,
                new_text=format_pdf_money_like_original(line.calculated_base_duty, old_base_duty_text),
                x_min=base_duty_target.get("x_min", 530),
                x_max=base_duty_target.get("x_max", 590),
                y=base_duty_target.get("y", hts_y),
                **replacement_style(base_duty_target, line_style),
            )

        if entered_value_changed or mpf_changed:
            old_chapter_amounts = money_values(original_line.chapter_99_amounts)
            new_chapter_amounts = calculated_chapter_amounts(line)
            chapter_ys = target.get("chapter_ys") or []
            chapter_texts = target.get("chapter_texts") or []
            chapter_targets = target.get("chapter_targets") or []
            if entered_value_changed:
                for index, (old_amount, new_amount) in enumerate(zip(old_chapter_amounts, new_chapter_amounts)):
                    chapter_target = (
                        chapter_targets[index]
                        if index < len(chapter_targets) and chapter_targets[index]
                        else {}
                    )
                    old_chapter_text = chapter_target.get("text")
                    if not old_chapter_text:
                        old_chapter_text = (
                            chapter_texts[index]
                            if index < len(chapter_texts) and chapter_texts[index]
                            else format_pdf_money(old_amount)
                        )
                    add_replacement(
                        replacements,
                        page=line.page,
                        field=f"line {line.line_no} chapter 99 duty {index + 1}",
                        old_value=old_amount,
                        new_value=new_amount,
                        old_text=old_chapter_text,
                        new_text=format_pdf_money_like_original(new_amount, old_chapter_text),
                        x_min=chapter_target.get("x_min", 530),
                        x_max=chapter_target.get("x_max", 590),
                        y=chapter_target.get(
                            "y",
                            chapter_ys[index] if index < len(chapter_ys) else None,
                        ),
                        **replacement_style(chapter_target, line_style),
                    )
            mpf_target = target.get("mpf_target") or {}
            mpf_y = mpf_target.get("y", target.get("mpf_y"))
            if mpf_target or target.get("mpf_text") or original_line.mpf_amount is not None or mpf_y is not None:
                old_mpf_text = mpf_target.get("text") or target.get("mpf_text") or format_pdf_money(
                    original_line.mpf_amount
                )
                add_replacement(
                    replacements,
                    page=line.page,
                    field=f"line {line.line_no} MPF",
                    old_value=original_line.mpf_amount,
                    new_value=line.calculated_mpf_amount,
                    old_text=old_mpf_text,
                    new_text=format_pdf_money_like_original(line.calculated_mpf_amount, old_mpf_text),
                    x_min=mpf_target.get("x_min", 530),
                    x_max=mpf_target.get("x_max", 590),
                    y=mpf_y,
                    **replacement_style(mpf_target, line_style),
                )

        if (entered_value_changed or transport_changed) and (
            original_line.hmf_amount is not None or line.calculated_hmf_amount is not None
        ):
            hmf_target = target.get("hmf_target") or {}
            hmf_y = hmf_target.get("y", target.get("hmf_y"))
            if hmf_target or target.get("hmf_text") or original_line.hmf_amount is not None or hmf_y is not None:
                old_hmf_text = hmf_target.get("text") or target.get("hmf_text") or format_pdf_money(
                    original_line.hmf_amount
                )
                add_replacement(
                    replacements,
                    page=line.page,
                    field=f"line {line.line_no} HMF",
                    old_value=original_line.hmf_amount,
                    new_value=line.calculated_hmf_amount,
                    old_text=old_hmf_text,
                    new_text=format_pdf_money_like_original(line.calculated_hmf_amount, old_hmf_text),
                    x_min=hmf_target.get("x_min", 530),
                    x_max=hmf_target.get("x_max", 590),
                    y=hmf_y,
                    **replacement_style(hmf_target, line_style),
                )

    if entered_changed_any:
        target = document_targets.get("total_entered_value", {})
        add_replacement(
            replacements,
            page=1,
            field="total entered value",
            old_value=original_document.total_entered_value,
            new_value=document.total_entered_value,
            old_text=target.get(
                "text",
                format_pdf_number(original_document.total_entered_value, keep_cents=False),
            ),
            new_text=format_pdf_number(document.total_entered_value, keep_cents=False),
            x_min=target.get("x_min", 175),
            x_max=target.get("x_max", 260),
            y=target.get("y", 248),
            alignment="left",
            erase_x_min=176.0,
            erase_x_max=317.0,
            **replacement_style(target),
        )
        target = document_targets.get("mpf_summary", {})
        old_mpf_summary_text = target.get("text", format_pdf_money(original_document.mpf_total))
        add_replacement(
            replacements,
            page=1,
            field="MPF summary",
            old_value=original_document.mpf_total,
            new_value=document.calculated_mpf_total,
            old_text=old_mpf_summary_text,
            new_text=format_pdf_money_like_original(document.calculated_mpf_total, old_mpf_summary_text),
            x_min=target.get("x_min", 120),
            x_max=target.get("x_max", 175),
            y=target.get("y", 258),
            **replacement_style(target),
        )
    if duty_changed_any:
        target = document_targets.get("duty_total", {})
        old_duty_total_text = target.get("text", format_pdf_money(original_document.duty_total))
        add_replacement(
            replacements,
            page=1,
            field="duty total",
            old_value=original_document.duty_total,
            new_value=document.calculated_duty_total,
            old_text=old_duty_total_text,
            new_text=format_pdf_money_like_original(document.calculated_duty_total, old_duty_total_text),
            x_min=target.get("x_min", 530),
            x_max=target.get("x_max", 590),
            y=target.get("y", 241.5),
            erase_x_max=593.5,
            **replacement_style(target),
        )
    if other_changed_any:
        if original_document.hmf_total is not None or document.calculated_hmf_total is not None:
            target = document_targets.get("hmf_summary", {})
            old_hmf_summary_text = target.get("text", format_pdf_money(original_document.hmf_total))
            add_replacement(
                replacements,
                page=1,
                field="HMF summary",
                old_value=original_document.hmf_total,
                new_value=document.calculated_hmf_total,
                old_text=old_hmf_summary_text,
                new_text=format_pdf_money_like_original(document.calculated_hmf_total, old_hmf_summary_text),
                x_min=target.get("x_min", 120),
                x_max=target.get("x_max", 175),
                y=target.get("y", 249),
                **replacement_style(target),
            )
        target = document_targets.get("block_39_other_fees", {})
        add_replacement(
            replacements,
            page=1,
            field="block 39 other fees",
            old_value=original_document.total_other_fees or original_document.other_total,
            new_value=document.calculated_other_total,
            old_text=target.get(
                "text",
                format_pdf_number(original_document.total_other_fees or original_document.other_total),
            ),
            new_text=format_pdf_number_like_original(
                document.calculated_other_total,
                target.get(
                    "text",
                    format_pdf_number(original_document.total_other_fees or original_document.other_total),
                ),
            ),
            x_min=target.get("x_min", 175),
            x_max=target.get("x_max", 260),
            y=target.get("y", 218),
            alignment="left",
            erase_x_min=176.0,
            erase_x_max=317.0,
            **replacement_style(target),
        )
        target = document_targets.get("other_total", {})
        old_other_total_text = target.get("text", format_pdf_money(original_document.other_total))
        add_replacement(
            replacements,
            page=1,
            field="other total",
            old_value=original_document.other_total,
            new_value=document.calculated_other_total,
            old_text=old_other_total_text,
            new_text=format_pdf_money_like_original(document.calculated_other_total, old_other_total_text),
            x_min=target.get("x_min", 530),
            x_max=target.get("x_max", 590),
            y=target.get("y", 197.5),
            erase_x_max=593.5,
            **replacement_style(target),
        )
    if duty_changed_any or other_changed_any:
        target = document_targets.get("grand_total", {})
        old_grand_total_text = target.get("text", format_pdf_money(original_document.grand_total))
        add_replacement(
            replacements,
            page=1,
            field="grand total",
            old_value=original_document.grand_total,
            new_value=document.calculated_grand_total,
            old_text=old_grand_total_text,
            new_text=format_pdf_money_like_original(document.calculated_grand_total, old_grand_total_text),
            x_min=target.get("x_min", 530),
            x_max=target.get("x_max", 590),
            y=target.get("y", 175.5),
            erase_x_max=593.5,
            **replacement_style(target),
        )

    if entered_changed_any:
        invoice_page = original_document.pages
        invoice_value_target = document_targets.get("invoice_value", {})
        invoice_value_old_text = invoice_value_target.get(
            "text",
            f"{format_pdf_number(original_document.invoice_value)} USD",
        )
        invoice_value_new_text = (
            format_pdf_number(document.invoice_value)
            if invoice_value_target
            else f"{format_pdf_number(document.invoice_value)} USD"
        )
        add_replacement(
            replacements,
            page=invoice_page,
            field="invoice value",
            old_value=original_document.invoice_value,
            new_value=document.invoice_value,
            old_text=invoice_value_old_text,
            new_text=invoice_value_new_text,
            x_min=invoice_value_target.get("x_min", 200),
            x_max=invoice_value_target.get("x_max", 390),
            y=invoice_value_target.get("y"),
            **replacement_style(invoice_value_target),
        )
        invoice_entered_target = document_targets.get("invoice_entered_value", {})
        invoice_entered_old_text = invoice_entered_target.get(
            "text",
            f"{format_pdf_number(original_document.invoice_entered_value)} USD",
        )
        invoice_entered_new_text = (
            format_pdf_number(document.invoice_entered_value)
            if invoice_entered_target
            else f"{format_pdf_number(document.invoice_entered_value)} USD"
        )
        add_replacement(
            replacements,
            page=invoice_page,
            field="invoice entered value",
            old_value=original_document.invoice_entered_value,
            new_value=document.invoice_entered_value,
            old_text=invoice_entered_old_text,
            new_text=invoice_entered_new_text,
            x_min=invoice_entered_target.get("x_min", 480),
            x_max=invoice_entered_target.get("x_max", 590),
            y=invoice_entered_target.get("y"),
            **replacement_style(invoice_entered_target),
        )
        invoice_entered_as_target = document_targets.get("invoice_entered_value_as")
        if invoice_entered_as_target:
            add_replacement(
                replacements,
                page=invoice_page,
                field="invoice entered value AS",
                old_value=original_document.total_entered_value,
                new_value=document.total_entered_value,
                old_text=invoice_entered_as_target["text"],
                new_text=format_pdf_number(document.total_entered_value, keep_cents=False),
                x_min=invoice_entered_as_target.get("x_min", 480),
                x_max=invoice_entered_as_target.get("x_max", 590),
                y=invoice_entered_as_target.get("y"),
                **replacement_style(invoice_entered_as_target),
            )
    return replacements


def page_font_name(resource_owner: Any, resource_name: Any) -> str:
    resources = resource_owner.get("/Resources") or resource_owner
    try:
        resources = resources.get_object()
    except AttributeError:
        pass
    fonts = resources.get("/Font") or {}
    try:
        fonts = fonts.get_object()
    except AttributeError:
        pass
    font = fonts.get(resource_name)
    if font is None:
        raise ValueError(f"Unable to resolve PDF font {resource_name}")
    font = font.get_object()
    base_name = str(font.get("/BaseFont") or resource_name).lstrip("/")
    if "+" in base_name:
        base_name = base_name.split("+", 1)[1]
    aliases = {
        "Arial": "Helvetica",
        "ArialMT": "Helvetica",
        "Arial-BoldMT": "Helvetica-Bold",
        "Arial-ItalicMT": "Helvetica-Oblique",
    }
    font_name = aliases.get(base_name, base_name)
    try:
        pdfmetrics.getFont(font_name)
    except KeyError as exc:
        raise ValueError(f"Unsupported PDF font for exact replacement: {base_name}") from exc
    return font_name


def text_from_pdf_text_operands(operands: list[Any], operator: bytes) -> str:
    if not operands:
        return ""
    if operator == b"TJ":
        return "".join(str(item) for item in operands[0] if isinstance(item, str))
    return str(operands[0])


def set_pdf_text_operands(operands: list[Any], operator: bytes, value: str) -> None:
    if operator == b"TJ":
        operands[0] = ArrayObject([TextStringObject(value)])
    else:
        operands[0] = TextStringObject(value)


def apply_page_replacements(
    page: Any,
    writer: PdfWriter,
    replacements: list[PdfTextReplacement],
) -> list[PdfTextReplacement]:
    pending = list(replacements)
    applied: list[PdfTextReplacement] = []

    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def multiply_matrix(
        left: tuple[float, float, float, float, float, float],
        right: tuple[float, float, float, float, float, float],
    ) -> tuple[float, float, float, float, float, float]:
        a1, b1, c1, d1, e1, f1 = left
        a2, b2, c2, d2, e2, f2 = right
        return (
            a1 * a2 + c1 * b2,
            b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2,
            b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1,
            b1 * e2 + d1 * f2 + f1,
        )

    def transform_point(
        matrix: tuple[float, float, float, float, float, float],
        x: float,
        y: float,
    ) -> tuple[float, float]:
        a, b, c, d, e, f = matrix
        return a * x + c * y + e, b * x + d * y + f

    def object_resources(container: Any, inherited: Any | None) -> Any:
        resources = container.get("/Resources") or inherited or {}
        try:
            return resources.get_object()
        except AttributeError:
            return resources

    def object_key(reference: Any, value: Any) -> tuple[Any, ...]:
        candidate = reference if hasattr(reference, "idnum") else getattr(value, "indirect_reference", None)
        if candidate is not None and hasattr(candidate, "idnum"):
            return ("indirect", int(candidate.idnum), int(candidate.generation))
        return ("direct", id(value))

    def content_for(container: Any, *, is_page: bool) -> ContentStream:
        source = container.get_contents() if is_page else container
        return ContentStream(source, writer)

    form_usage_counts: dict[tuple[Any, ...], int] = {}

    def collect_form_usage(
        container: Any,
        inherited_resources: Any | None,
        *,
        is_page: bool,
        path: frozenset[tuple[Any, ...]],
    ) -> None:
        try:
            content = content_for(container, is_page=is_page)
        except Exception:
            return
        resources = object_resources(container, inherited_resources)
        xobjects = resources.get("/XObject") or {}
        try:
            xobjects = xobjects.get_object()
        except AttributeError:
            pass
        for operands, operator in content.operations:
            if operator != b"Do" or not operands:
                continue
            reference = xobjects.get(operands[0])
            if reference is None:
                continue
            value = reference.get_object()
            if value.get("/Subtype") != "/Form":
                continue
            key = object_key(reference, value)
            form_usage_counts[key] = form_usage_counts.get(key, 0) + 1
            if key in path:
                continue
            collect_form_usage(
                value,
                resources,
                is_page=False,
                path=path | {key},
            )

    for candidate_page in writer.pages:
        collect_form_usage(
            candidate_page,
            None,
            is_page=True,
            path=frozenset(),
        )

    def expand_form_bbox(
        container: Any,
        content: ContentStream,
        text_matrix: list[Any],
        text_width: float,
    ) -> None:
        bbox = container.get("/BBox")
        if bbox is None:
            return
        try:
            bbox = bbox.get_object()
        except AttributeError:
            pass
        if len(bbox) < 4:
            return
        origin_x = float(text_matrix[4])
        end_x = origin_x + float(text_matrix[0]) * text_width
        required_left = min(origin_x, end_x) - 0.5
        required_right = max(origin_x, end_x) + 0.5
        old_left = float(bbox[0])
        old_bottom = float(bbox[1])
        old_right = float(bbox[2])
        old_top = float(bbox[3])
        new_left = min(old_left, required_left)
        new_right = max(old_right, required_right)
        if new_left == old_left and new_right == old_right:
            return

        # These templates place an explicit clipping rectangle inside each
        # Form XObject in addition to /BBox. Expand both boundaries together;
        # otherwise a longer in-place value would have its leading glyphs
        # clipped even though the Form /BBox itself was widened.
        for operands, operator in content.operations:
            if operator != b"re" or len(operands) < 4:
                continue
            clip_left = float(operands[0])
            clip_bottom = float(operands[1])
            clip_right = clip_left + float(operands[2])
            clip_top = clip_bottom + float(operands[3])
            if (
                abs(clip_left - old_left) <= 0.01
                and abs(clip_bottom - old_bottom) <= 0.01
                and abs(clip_right - old_right) <= 0.01
                and abs(clip_top - old_top) <= 0.01
            ):
                operands[0] = FloatObject(new_left)
                operands[2] = FloatObject(new_right - new_left)
        bbox[0] = FloatObject(new_left)
        bbox[2] = FloatObject(new_right)

    def inline_span_matches(
        current_text: str,
        start: int,
        end: int,
        current_tm: list[Any],
        current_ctm: tuple[float, float, float, float, float, float],
        font_name: str,
        font_size: float,
        replacement: PdfTextReplacement,
    ) -> bool:
        prefix_width = pdfmetrics.stringWidth(current_text[:start], font_name, font_size)
        value_width = pdfmetrics.stringWidth(current_text[start:end], font_name, font_size)
        local_x = float(current_tm[4])
        local_y = float(current_tm[5])
        start_point = transform_point(
            current_ctm,
            local_x + float(current_tm[0]) * prefix_width,
            local_y + float(current_tm[1]) * prefix_width,
        )
        end_point = transform_point(
            current_ctm,
            local_x + float(current_tm[0]) * (prefix_width + value_width),
            local_y + float(current_tm[1]) * (prefix_width + value_width),
        )
        left = min(start_point[0], end_point[0])
        right = max(start_point[0], end_point[0])
        return (
            right >= replacement.x_min - PDF_COORDINATE_TOLERANCE
            and left <= replacement.x_max + PDF_COORDINATE_TOLERANCE
        )

    def replace_inline_range(
        current_text: str,
        start: int,
        end: int,
        new_text: str,
        replacement: PdfTextReplacement,
        font_name: str,
        font_size: float,
    ) -> str:
        prefix = current_text[:start]
        suffix = current_text[end:]
        if replacement.alignment == "right":
            old_width = pdfmetrics.stringWidth(current_text[start:end], font_name, font_size)
            new_width = pdfmetrics.stringWidth(new_text, font_name, font_size)
            space_width = pdfmetrics.stringWidth(" ", font_name, font_size)
            whitespace = parser.re.search(r" +$", prefix)
            if whitespace and space_width > 0:
                space_delta = int(round((new_width - old_width) / space_width))
                width_error = abs((new_width - old_width) - space_delta * space_width)
                available_spaces = len(whitespace.group(0))
                if width_error <= space_width * 0.2:
                    if 0 < space_delta <= available_spaces:
                        prefix = prefix[:-space_delta]
                    elif space_delta < 0:
                        prefix += " " * (-space_delta)
        return prefix + new_text + suffix

    def replace_exact_inline_text(
        current_text: str,
        replacement: PdfTextReplacement,
        current_tm: list[Any],
        current_ctm: tuple[float, float, float, float, float, float],
        font_name: str,
        font_size: float,
    ) -> str | None:
        start = current_text.find(replacement.old_text)
        while start >= 0:
            end = start + len(replacement.old_text)
            line_row_match = (
                replacement.field.startswith("line ")
                and parser.re.match(r"^\s*\d{4}\.\d{2}\.\d{4}", current_text)
            )
            if line_row_match or inline_span_matches(
                current_text,
                start,
                end,
                current_tm,
                current_ctm,
                font_name,
                font_size,
                replacement,
            ):
                return replace_inline_range(
                    current_text,
                    start,
                    end,
                    replacement.new_text,
                    replacement,
                    font_name,
                    font_size,
                )
            start = current_text.find(replacement.old_text, start + 1)
        return None

    def replace_compact_inline_quantity(
        current_text: str,
        replacement: PdfTextReplacement,
        current_tm: list[Any],
        current_ctm: tuple[float, float, float, float, float, float],
        font_name: str,
        font_size: float,
    ) -> str | None:
        if not replacement.field.endswith((" gross weight", " net quantity")):
            return None
        old_match = parser.re.fullmatch(
            r"\s*([0-9][0-9,]*(?:\.\d+)?)\s+([A-Z][A-Z0-9]*)\s*",
            replacement.old_text,
            parser.re.I,
        )
        new_match = parser.re.fullmatch(
            r"\s*([0-9][0-9,]*(?:\.\d+)?)\s+([A-Z][A-Z0-9]*)\s*",
            replacement.new_text,
            parser.re.I,
        )
        if not old_match or not new_match:
            return None
        old_value = parser.parse_decimal(old_match.group(1))
        unit = old_match.group(2)
        pattern = parser.re.compile(
            rf"(?<![0-9.])([0-9][0-9,]*(?:\.\d+)?)(\s*)({parser.re.escape(unit)})(?![A-Z0-9])",
            parser.re.I,
        )
        for match in pattern.finditer(current_text):
            if parser.parse_decimal(match.group(1)) != old_value:
                continue
            if not inline_span_matches(
                current_text,
                match.start(),
                match.end(),
                current_tm,
                current_ctm,
                font_name,
                font_size,
                replacement,
            ):
                continue
            new_number = new_match.group(1)
            if "," not in match.group(1):
                new_number = new_number.replace(",", "")
            new_quantity = new_number + match.group(2) + new_match.group(2)
            return replace_inline_range(
                current_text,
                match.start(),
                match.end(),
                new_quantity,
                replacement,
                font_name,
                font_size,
            )
        return None

    def process_container(
        container: Any,
        inherited_resources: Any | None,
        outer_ctm: tuple[float, float, float, float, float, float],
        *,
        is_page: bool,
        allow_replacements: bool,
    ) -> None:
        if not pending:
            return
        content = content_for(container, is_page=is_page)
        resources = object_resources(container, inherited_resources)
        xobjects = resources.get("/XObject") or {}
        try:
            xobjects = xobjects.get_object()
        except AttributeError:
            pass
        current_ctm = outer_ctm
        ctm_stack: list[tuple[float, float, float, float, float, float]] = []
        current_tm: list[Any] | None = None
        current_font: Any = None
        current_size = 0.0
        stream_changed = False

        for operands, operator in content.operations:
            if operator == b"q":
                ctm_stack.append(current_ctm)
                continue
            if operator == b"Q":
                current_ctm = ctm_stack.pop() if ctm_stack else outer_ctm
                continue
            if operator == b"cm" and len(operands) >= 6:
                matrix = tuple(float(item) for item in operands[:6])
                current_ctm = multiply_matrix(current_ctm, matrix)
                continue
            if operator == b"Do" and operands:
                reference = xobjects.get(operands[0])
                if reference is None:
                    continue
                value = reference.get_object()
                if value.get("/Subtype") != "/Form":
                    continue
                matrix_value = value.get("/Matrix") or identity
                form_matrix = tuple(float(item) for item in matrix_value[:6])
                key = object_key(reference, value)
                process_container(
                    value,
                    resources,
                    multiply_matrix(current_ctm, form_matrix),
                    is_page=False,
                    allow_replacements=(
                        allow_replacements and form_usage_counts.get(key, 0) == 1
                    ),
                )
                continue
            if operator == b"Tf":
                current_font = operands[0]
                current_size = float(operands[1])
                continue
            if operator == b"Tm":
                current_tm = operands
                continue
            if operator == b"BT":
                current_tm = None
                continue
            if operator == b"ET":
                current_tm = None
                continue
            if (
                not allow_replacements
                or operator not in (b"Tj", b"TJ")
                or current_tm is None
                or not operands
            ):
                continue

            x, baseline_y = transform_point(
                current_ctm,
                float(current_tm[4]),
                float(current_tm[5]),
            )
            current_text = text_from_pdf_text_operands(operands, operator)
            y_tolerance = max(PDF_COORDINATE_TOLERANCE, current_size * 0.55)
            for replacement in list(pending):
                y_matches = replacement.y is None or abs(baseline_y - replacement.y) <= max(
                    replacement.y_tolerance,
                    y_tolerance,
                )
                if (
                    current_text != replacement.old_text
                    or not replacement.x_min - PDF_COORDINATE_TOLERANCE
                    <= x
                    <= replacement.x_max + PDF_COORDINATE_TOLERANCE
                    or not y_matches
                ):
                    continue
                font_name = page_font_name(resources, current_font)
                if replacement.alignment == "right":
                    old_width = pdfmetrics.stringWidth(current_text, font_name, current_size)
                    new_width = pdfmetrics.stringWidth(replacement.new_text, font_name, current_size)
                    width_delta = old_width - new_width
                    current_tm[4] = FloatObject(
                        float(current_tm[4]) + float(current_tm[0]) * width_delta
                    )
                    current_tm[5] = FloatObject(
                        float(current_tm[5]) + float(current_tm[1]) * width_delta
                    )
                set_pdf_text_operands(operands, operator, replacement.new_text)
                if not is_page:
                    new_width = pdfmetrics.stringWidth(
                        replacement.new_text,
                        font_name,
                        current_size,
                    )
                    expand_form_bbox(container, content, current_tm, new_width)
                pending.remove(replacement)
                applied.append(replacement)
                stream_changed = True
                break
            else:
                row_applied: list[PdfTextReplacement] = []
                inline_font_name: str | None = None
                for replacement in list(pending):
                    y_matches = replacement.y is not None and abs(
                        baseline_y - replacement.y
                    ) <= max(replacement.y_tolerance, y_tolerance)
                    if not y_matches:
                        continue
                    if inline_font_name is None:
                        try:
                            inline_font_name = page_font_name(resources, current_font)
                        except ValueError:
                            inline_font_name = replacement.font_name
                    updated_text = replace_exact_inline_text(
                        current_text,
                        replacement,
                        current_tm,
                        current_ctm,
                        inline_font_name,
                        current_size,
                    )
                    if updated_text is None:
                        updated_text = replace_compact_inline_quantity(
                            current_text,
                            replacement,
                            current_tm,
                            current_ctm,
                            inline_font_name,
                            current_size,
                        )
                    if updated_text is None:
                        continue
                    current_text = updated_text
                    pending.remove(replacement)
                    row_applied.append(replacement)
                if row_applied:
                    set_pdf_text_operands(operands, operator, current_text)
                    if not is_page:
                        expand_form_bbox(
                            container,
                            content,
                            current_tm,
                            pdfmetrics.stringWidth(
                                current_text,
                                inline_font_name or "Helvetica",
                                current_size,
                            ),
                        )
                    applied.extend(row_applied)
                    stream_changed = True

        if not stream_changed:
            return
        if is_page:
            container.replace_contents(content)
        else:
            updated_data = content.get_data()
            try:
                container.set_data(updated_data)
            except PdfReadError:
                # Some generators combine ASCII85 and Flate filters. pypdf
                # cannot re-encode that filter chain via set_data(), so store
                # the already-decoded replacement stream without filters.
                container.pop("/Filter", None)
                container.pop("/DecodeParms", None)
                if hasattr(container, "decoded_self"):
                    container.decoded_self = None
                StreamObject.set_data(container, updated_data)

    process_container(
        page,
        None,
        identity,
        is_page=True,
        allow_replacements=True,
    )
    return applied


def page_rule_segments(page: Any, pdf_context: Any) -> list[PdfRuleSegment]:
    content = ContentStream(page.get_contents(), pdf_context)
    segments: list[PdfRuleSegment] = []
    pending_segments: list[PdfRuleSegment] = []
    current: tuple[float, float, float, float] | None = None
    subpath_start: tuple[float, float, float, float] | None = None
    ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    line_width = 1.0
    graphics_stack: list[
        tuple[tuple[float, float, float, float, float, float], float]
    ] = []

    def multiply_matrix(
        left: tuple[float, float, float, float, float, float],
        right: tuple[float, float, float, float, float, float],
    ) -> tuple[float, float, float, float, float, float]:
        a1, b1, c1, d1, e1, f1 = left
        a2, b2, c2, d2, e2, f2 = right
        return (
            a1 * a2 + c1 * b2,
            b1 * a2 + d1 * b2,
            a1 * c2 + c1 * d2,
            b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1,
            b1 * e2 + d1 * f2 + f1,
        )

    def transform_point(x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = ctm
        return a * x + c * y + e, b * x + d * y + f

    def transformed_line_width(x1: float, y1: float, x2: float, y2: float) -> float:
        dx = x2 - x1
        dy = y2 - y1
        source_length = math.hypot(dx, dy)
        if source_length <= 0.000001:
            return abs(line_width)
        tangent_x = dx / source_length
        tangent_y = dy / source_length
        a, b, c, d, _, _ = ctm
        transformed_tangent_length = math.hypot(
            a * tangent_x + c * tangent_y,
            b * tangent_x + d * tangent_y,
        )
        if transformed_tangent_length <= 0.000001:
            return abs(line_width)
        area_scale = abs(a * d - b * c)
        return abs(line_width) * area_scale / transformed_tangent_length

    def add_segment(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        segment_line_width: float,
    ) -> None:
        if abs(x1 - x2) <= 0.01 and abs(y1 - y2) >= 2:
            pending_segments.append(
                PdfRuleSegment(
                    "vertical",
                    x1,
                    min(y1, y2),
                    max(y1, y2),
                    segment_line_width,
                )
            )
        elif abs(y1 - y2) <= 0.01 and abs(x1 - x2) >= 2:
            pending_segments.append(
                PdfRuleSegment(
                    "horizontal",
                    y1,
                    min(x1, x2),
                    max(x1, x2),
                    segment_line_width,
                )
            )

    for operands, operator in content.operations:
        if operator == b"q":
            graphics_stack.append((ctm, line_width))
            continue
        if operator == b"Q":
            if graphics_stack:
                ctm, line_width = graphics_stack.pop()
            else:
                ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
                line_width = 1.0
            current = None
            continue
        if operator == b"cm" and len(operands) >= 6:
            matrix = tuple(float(item) for item in operands[:6])
            ctm = multiply_matrix(ctm, matrix)
            current = None
            continue
        if operator == b"w" and operands:
            line_width = float(operands[0])
            continue
        if operator == b"m" and len(operands) >= 2:
            source_x = float(operands[0])
            source_y = float(operands[1])
            transformed_x, transformed_y = transform_point(source_x, source_y)
            current = (source_x, source_y, transformed_x, transformed_y)
            subpath_start = current
            continue
        if operator == b"l" and current is not None and len(operands) >= 2:
            end_source_x = float(operands[0])
            end_source_y = float(operands[1])
            end_x, end_y = transform_point(end_source_x, end_source_y)
            segment_line_width = transformed_line_width(
                current[0],
                current[1],
                end_source_x,
                end_source_y,
            )
            add_segment(current[2], current[3], end_x, end_y, segment_line_width)
            current = (end_source_x, end_source_y, end_x, end_y)
            continue
        if operator == b"h" and current is not None and subpath_start is not None:
            segment_line_width = transformed_line_width(
                current[0],
                current[1],
                subpath_start[0],
                subpath_start[1],
            )
            add_segment(
                current[2],
                current[3],
                subpath_start[2],
                subpath_start[3],
                segment_line_width,
            )
            current = subpath_start
            continue
        if operator == b"re" and len(operands) >= 4:
            x, y, width, height = (float(item) for item in operands[:4])
            bottom_left = transform_point(x, y)
            bottom_right = transform_point(x + width, y)
            top_right = transform_point(x + width, y + height)
            top_left = transform_point(x, y + height)
            horizontal_width = transformed_line_width(x, y, x + width, y)
            vertical_width = transformed_line_width(x + width, y, x + width, y + height)
            add_segment(*bottom_left, *bottom_right, horizontal_width)
            add_segment(*bottom_right, *top_right, vertical_width)
            add_segment(*top_right, *top_left, horizontal_width)
            add_segment(*top_left, *bottom_left, vertical_width)
            continue
        if operator in {b"s", b"b", b"b*"} and current is not None and subpath_start is not None:
            segment_line_width = transformed_line_width(
                current[0],
                current[1],
                subpath_start[0],
                subpath_start[1],
            )
            add_segment(
                current[2],
                current[3],
                subpath_start[2],
                subpath_start[3],
                segment_line_width,
            )
        if operator in {b"S", b"s", b"B", b"B*", b"b", b"b*"}:
            segments.extend(pending_segments)
            pending_segments.clear()
            current = None
            subpath_start = None
            continue
        if operator in {b"f", b"F", b"f*", b"n"}:
            pending_segments.clear()
            current = None
            subpath_start = None

    return segments


def protected_erase_rectangles(
    rectangle: tuple[float, float, float, float],
    rule_segments: list[PdfRuleSegment],
) -> list[tuple[float, float, float, float]]:
    rectangles = [rectangle]
    rule_gap = PDF_OVERLAY_BORDER_SAFE_GAP

    for segment in rule_segments:
        next_rectangles: list[tuple[float, float, float, float]] = []
        for x, y, width, height in rectangles:
            x2 = x + width
            y2 = y + height
            if segment.orientation == "vertical":
                crosses_x = x < segment.position < x2
                overlaps_y = y < segment.end and y2 > segment.start
                if not crosses_x or not overlaps_y:
                    next_rectangles.append((x, y, width, height))
                    continue
                left_end = max(x, segment.position - rule_gap)
                right_start = min(x2, segment.position + rule_gap)
                if left_end - x > 0.1:
                    next_rectangles.append((x, y, left_end - x, height))
                if x2 - right_start > 0.1:
                    next_rectangles.append((right_start, y, x2 - right_start, height))
                continue
            if segment.orientation == "horizontal":
                crosses_y = y < segment.position < y2
                overlaps_x = x < segment.end and x2 > segment.start
                if not crosses_y or not overlaps_x:
                    next_rectangles.append((x, y, width, height))
                    continue
                lower_end = max(y, segment.position - rule_gap)
                upper_start = min(y2, segment.position + rule_gap)
                if lower_end - y > 0.1:
                    next_rectangles.append((x, y, width, lower_end - y))
                if y2 - upper_start > 0.1:
                    next_rectangles.append((x, upper_start, width, y2 - upper_start))
                continue
            next_rectangles.append((x, y, width, height))
        rectangles = next_rectangles
    return rectangles


def rule_segments_inside_rectangle(
    rectangle: tuple[float, float, float, float],
    rule_segments: list[PdfRuleSegment],
) -> set[tuple[str, float, float, float, float]]:
    x, y, width, height = rectangle
    x2 = x + width
    y2 = y + height
    segments_by_geometry: dict[tuple[str, float, float, float], float] = {}

    def remember_segment(
        orientation: str,
        position: float,
        start: float,
        end: float,
        line_width: float,
    ) -> None:
        geometry = (
            orientation,
            round(position, 3),
            round(start, 3),
            round(end, 3),
        )
        rounded_width = round(line_width, 3)
        segments_by_geometry[geometry] = max(
            rounded_width,
            segments_by_geometry.get(geometry, 0.0),
        )

    for segment in rule_segments:
        if segment.orientation == "vertical":
            crosses_x = x <= segment.position <= x2
            overlaps_y = y < segment.end and y2 > segment.start
            if not crosses_x or not overlaps_y:
                continue
            start = max(y, segment.start)
            end = min(y2, segment.end)
            if end - start > 0.1:
                remember_segment(
                    "vertical",
                    segment.position,
                    start,
                    end,
                    segment.line_width,
                )
            continue
        if segment.orientation == "horizontal":
            crosses_y = y <= segment.position <= y2
            overlaps_x = x < segment.end and x2 > segment.start
            if not crosses_y or not overlaps_x:
                continue
            start = max(x, segment.start)
            end = min(x2, segment.end)
            if end - start > 0.1:
                remember_segment(
                    "horizontal",
                    segment.position,
                    start,
                    end,
                    segment.line_width,
                )

    return {
        (*geometry, line_width)
        for geometry, line_width in segments_by_geometry.items()
    }


def overlay_page_replacements(
    page: Any,
    replacements: list[PdfTextReplacement],
    pdf_context: Any | None = None,
) -> list[PdfTextReplacement]:
    drawable = [replacement for replacement in replacements if replacement.y is not None]
    if not drawable:
        return []

    rule_segments = page_rule_segments(page, pdf_context) if pdf_context is not None else []
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    packet = BytesIO()
    overlay = canvas.Canvas(packet, pagesize=(width, height))
    drawable = sorted(drawable, key=lambda item: item.x_min, reverse=True)
    restore_rule_segments: set[tuple[str, float, float, float, float]] = set()
    erase_rectangles: list[tuple[float, float, float, float]] = []
    for replacement in drawable:
        erase_rectangle = overlay_erase_rectangle(replacement)
        erase_rectangles.append(erase_rectangle)
        restore_rule_segments.update(rule_segments_inside_rectangle(erase_rectangle, rule_segments))

    overlay.setFillColorRGB(1, 1, 1)
    for erase_rectangle in erase_rectangles:
        overlay.rect(*erase_rectangle, stroke=0, fill=1)

    if restore_rule_segments:
        overlay.setStrokeColorRGB(0, 0, 0)
        for orientation, position, start, end, source_line_width in sorted(restore_rule_segments):
            overlay.setLineWidth(source_line_width)
            if orientation == "vertical":
                overlay.line(position, start, position, end)
            else:
                overlay.line(start, position, end, position)

    for replacement in drawable:
        y = float(replacement.y or 0)
        x_min = float(replacement.x_min)
        x_max = float(replacement.x_max)
        font_name = replacement.font_name or "Helvetica"
        font_size = float(replacement.font_size or 8.0)
        overlay.setFillColorRGB(0, 0, 0)
        overlay.setFont(font_name, font_size)
        if replacement.alignment == "right":
            overlay.drawRightString(x_max, y, replacement.new_text)
        else:
            overlay.drawString(x_min, y, replacement.new_text)
    overlay.save()
    packet.seek(0)
    overlay_page = PdfReader(packet).pages[0]
    page.merge_page(overlay_page)
    return drawable


def overlay_erase_rectangle(replacement: PdfTextReplacement) -> tuple[float, float, float, float]:
    font_name = replacement.font_name or "Helvetica"
    font_size = float(replacement.font_size or 8.0)
    old_width = pdfmetrics.stringWidth(replacement.old_text, font_name, font_size)
    new_width = pdfmetrics.stringWidth(replacement.new_text, font_name, font_size)
    y = float(replacement.y or 0)
    erase_y = y - max(font_size * 0.35, 2.0)
    erase_height = max(font_size * 1.65, font_size + 4.0)

    if replacement.alignment == "right":
        right_edge = float(replacement.x_max)
        widest_text = max(old_width, new_width)
        erase_x = max(
            0.0,
            replacement.erase_x_min
            if replacement.erase_x_min is not None
            else min(float(replacement.x_min), right_edge - widest_text - 0.8),
        )
        erase_right = (
            replacement.erase_x_max
            if replacement.erase_x_max is not None
            else max(erase_x + 1.0, right_edge + max(1.2, font_size * 0.2))
        )
        return erase_x, erase_y, erase_right - erase_x, erase_height

    left_edge = float(replacement.x_min)
    erase_x = max(0.0, replacement.erase_x_min if replacement.erase_x_min is not None else left_edge - 0.6)
    erase_right = (
        replacement.erase_x_max
        if replacement.erase_x_max is not None
        else left_edge + max(old_width, new_width) + 1.2
    )
    return erase_x, erase_y, erase_right - erase_x, erase_height


def template_preserving_pdf(
    original_path: Path,
    document: Any,
    lines: list[Any],
    modified_fields: list[str] | set[str],
) -> bytes:
    replacements = build_pdf_text_replacements(original_path, document, lines, modified_fields)
    if not replacements:
        return original_path.read_bytes()

    writer = PdfWriter(clone_from=str(original_path))
    applied: list[PdfTextReplacement] = []
    for page_number, page in enumerate(writer.pages, start=1):
        page_replacements = [item for item in replacements if item.page == page_number]
        if page_replacements:
            page_applied = apply_page_replacements(page, writer, page_replacements)
            applied.extend(page_applied)

    missing = [item.field for item in replacements if item not in applied]
    if missing:
        fields_text = ", ".join(missing)
        raise ValueError(
            "The original PDF text layout could not be matched exactly for: "
            f"{fields_text}. No adjusted PDF was generated."
        )

    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def generate_adjusted_pdf(
    original_path: Path,
    document: Any,
    lines: list[Any],
    modified_fields: list[str] | set[str],
) -> bytes:
    return template_preserving_pdf(original_path, document, lines, modified_fields)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "mpf_rounding": "line-sum",
        "mpf_limits": "date-aware-fy2026-33.58-651.50-fy2027-34.58-670.86",
        "worksheet_matching": "best-hts-match",
        "kg_quantity": "item-size-aware",
        "dpr_quantity": "pairs-divided-by-12",
        "invoice_footer_alignment": "right-edge",
        "quantity_decimal_format": "trim-input-trailing-zeros-weight-max-2",
        "hts_mismatch_strategy": "row-order-when-counts-match",
        "entered_value_parsing": "split-entered-value-and-rate-columns",
        "bl_awb_normalization": "carrier-prefix-space-removed",
        "new_template_parsing": "readable-text-with-coordinate-repair",
        "new_template_pdf_generation": "dynamic-money-targets",
        "overlay_font_matching": "original-fragment-font-and-size",
        "pdf_text_fallback": "pymupdf",
        "overlay_right_edge": "original-text-edge",
        "line_fee_missing_target": "skip-line-fee-use-document-summary",
        "overlay_border_safety": "split-erase-around-original-rule-lines",
        "variance_warnings": "parse-only-for-unmodified-fields",
        "short_line_template": "one-to-three-digit-lines-and-compact-quantity-units",
        "draft_template_fragment_source": "prefer-source-with-most-line-items",
        "totals_overlay_erase": "cell-boundary-clear-with-text-position-preserved",
        "draft_line_item_overlay": "protect-35-36-divider-and-block39-separator",
        "overlay_rule_transform": "apply-cm-ctm-before-restoring-erased-lines",
        "overlay_rule_style": "preserve-original-transformed-line-width",
        "pdf_text_update": "in-place-page-and-form-xobject-only",
        "pdf_overlay_fallback": "disabled-for-generation",
        "combined_text_replacement": "coordinate-scoped-inline-and-compact-quantity",
        "fee_summary_matching": "value-aware-flexible-decimals",
    }


def friendly_error_detail(action: str, exc: Exception) -> str:
    message = str(exc)
    translations = (
        (
            "The Excel workbook must contain at least two worksheets.",
            "Excel 文件至少需要包含两个工作表；请确认第二个工作表是已更新后的明细表。",
        ),
        (
            "Unable to locate the item table in the second Excel worksheet.",
            "无法在 Excel 第二个工作表中找到商品明细表；请检查 HTS/HS 编码、数量、FOB 总价等表头是否存在。",
        ),
        (
            "No HTS item rows were found in the second Excel worksheet.",
            "Excel 第二个工作表中没有找到有效 HTS 商品行。",
        ),
        (
            "The second Excel worksheet does not contain any changes from the original PDF.",
            "Excel 第二个工作表的数据与原始 PDF 已解析数据一致，因此没有可生成的修改。",
        ),
        (
            "Unable to match Excel rows for",
            "Excel 行无法按 HTS 与税单行匹配；请确认 HTS 编码和行项目数量是否一致。",
        ),
        (
            "The original PDF text layout could not be matched exactly for",
            "无法在原始 PDF 中安全匹配需要替换的文本位置；为避免生成错位税单，已停止生成。",
        ),
        (
            "net quantity",
            "净数量校验失败；请确认 KG 净重没有超过 KG 毛重，且数量不是负数。",
        ),
        (
            "unsupported",
            "存在当前程序暂不支持的税率或单位组合；请检查税率格式和数量单位。",
        ),
    )
    for needle, friendly in translations:
        if needle in message:
            return f"{action}: {friendly} 原始信息：{message}"
    return f"{action}: {message}"


@app.get("/api/hts-lookup")
def hts_lookup(code: str) -> dict[str, Any]:
    try:
        return lookup_hts(code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to query current USITC HTS data: {exc}") from exc


@app.post("/api/parse")
async def parse_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_uploads()
    saved_path = UPLOAD_DIR / safe_upload_name(file.filename)
    try:
        with saved_path.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        if saved_path.stat().st_size > MAX_UPLOAD_BYTES:
            saved_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=413,
                detail=f"PDF exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.",
            )
        key = f"upload|{Path(file.filename).stem}"
        parsed = parser.parse_pdf(saved_path, "original", key)
        include_hmf = parsed_has_hmf(parsed.document, parsed.lines)
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
        raise HTTPException(status_code=422, detail=friendly_error_detail("无法解析 PDF", exc)) from exc


@app.post("/api/recalculate")
def recalculate_payload(payload: RecalculateRequest) -> dict[str, Any]:
    try:
        document = dataclass_from_dict(parser.TaxDocument, payload.document)
        lines = [dataclass_from_dict(parser.TaxLine, line) for line in payload.lines]
        document.line_count = len(lines)
        recalculate(document, lines, include_hmf=payload.include_hmf)
        validation_errors = line_validation_errors(lines, payload.modified_fields)
        suppress_expected_modified_variances(document, lines, payload.modified_fields)
        return response_payload(
            document,
            lines,
            include_hmf=payload.include_hmf,
            upload_id=payload.upload_id,
            transport_mode=payload.transport_mode,
            modified_fields=payload.modified_fields,
            validation_errors=validation_errors,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=friendly_error_detail("无法重新计算", exc)) from exc


@app.post("/api/generate-from-excel")
async def generate_from_excel(
    pdf_file: UploadFile = File(...),
    excel_file: UploadFile = File(...),
    transport_mode: str = Form("auto"),
) -> StreamingResponse:
    if not pdf_file.filename or Path(pdf_file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="A source PDF file is required.")
    if not excel_file.filename or Path(excel_file.filename).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=400, detail="A two-sheet .xlsx workbook is required.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_uploads()
    pdf_path = UPLOAD_DIR / f"{uuid4().hex}.pdf"
    excel_path = UPLOAD_DIR / f"{uuid4().hex}.xlsx"
    try:
        for upload, saved_path, label in (
            (pdf_file, pdf_path, "PDF"),
            (excel_file, excel_path, "Excel"),
        ):
            with saved_path.open("wb") as output:
                shutil.copyfileobj(upload.file, output)
            if saved_path.stat().st_size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"{label} exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.",
                )

        key = f"excel-upload|{Path(pdf_file.filename).stem}"
        parsed = parser.parse_pdf(pdf_path, "original", key)
        adjustment = apply_second_sheet(excel_path, parsed.lines)
        normalized_transport_mode = normalize_transport_mode(transport_mode)
        original_has_hmf = parsed_has_hmf(parsed.document, parsed.lines)
        include_hmf = include_hmf_for_transport(
            parsed.document,
            parsed.lines,
            normalized_transport_mode,
        )
        validate_hmf_pdf_layout(
            original_has_hmf=original_has_hmf,
            include_hmf=include_hmf,
            transport_mode=normalized_transport_mode,
        )
        recalculate(parsed.document, parsed.lines, include_hmf=include_hmf)
        validation_errors = line_validation_errors(parsed.lines, adjustment.modified_fields)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        modified_fields = list(adjustment.modified_fields)
        if include_hmf != original_has_hmf:
            modified_fields.append("document:transport_mode")
        pdf_bytes = generate_adjusted_pdf(
            pdf_path,
            parsed.document,
            parsed.lines,
            modified_fields,
        )
        filename = f"{clean_filename(pdf_file.filename)}-excel-adjusted.pdf"
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Excel-Sheet": adjustment.sheet_name.encode("ascii", errors="replace").decode("ascii"),
                "X-Matched-Lines": str(adjustment.matched_lines),
                "X-Matching-Strategy": adjustment.matching_strategy,
                "X-Modified-Fields": str(len(modified_fields)),
                "X-Transport-Mode": normalized_transport_mode,
                "X-Include-HMF": str(include_hmf).lower(),
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=friendly_error_detail("无法从 Excel 生成 PDF", exc)) from exc
    finally:
        pdf_path.unlink(missing_ok=True)
        excel_path.unlink(missing_ok=True)


@app.post("/api/generate-pdf")
def generate_pdf(payload: GeneratePdfRequest) -> StreamingResponse:
    try:
        document = dataclass_from_dict(parser.TaxDocument, payload.document)
        lines = [dataclass_from_dict(parser.TaxLine, line) for line in payload.lines]
        document.line_count = len(lines)
        recalculate(document, lines, include_hmf=payload.include_hmf)
        validation_errors = line_validation_errors(lines, payload.modified_fields)
        if validation_errors:
            raise ValueError("; ".join(validation_errors))
        original_path = upload_path(payload.upload_id)
        pdf_bytes = generate_adjusted_pdf(original_path, document, lines, payload.modified_fields)
        filename = f"{clean_filename(document.source_file)}-adjusted-7501.pdf"
        return StreamingResponse(
            BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=friendly_error_detail("无法生成 PDF", exc)) from exc
