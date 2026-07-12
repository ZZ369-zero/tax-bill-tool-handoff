from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

import pandas as pd
from pypdf import PdfReader


MODIFIED_MARKERS = ("副本", "更新")
PDF_EXTENSIONS = {".pdf"}
MONEY_QUANT = Decimal("0.01")
WHOLE_DOLLAR_QUANT = Decimal("1")
MPF_RATE = Decimal("0.003464")
MPF_MIN = Decimal("33.58")
MPF_MAX = Decimal("651.50")
HMF_RATE = Decimal("0.00125")


@dataclass
class TextFragment:
    page: int
    x: float
    y: float
    size: float
    font: str
    text: str


@dataclass
class TaxLine:
    file_role: str
    source_file: str
    pair_key: str
    entry_number: str | None = None
    page: int | None = None
    line_no: str | None = None
    description: str | None = None
    hts: str | None = None
    hts_description: str | None = None
    required_units: str | None = None
    hts_additional_codes: str | None = None
    gross_weight: str | None = None
    gross_unit: str | None = None
    net_quantity: str | None = None
    net_unit: str | None = None
    entered_value: str | None = None
    rate: str | None = None
    duty_amount: str | None = None
    charges: str | None = None
    relationship: str | None = None
    mpf_rate: str | None = None
    mpf_amount: str | None = None
    hmf_rate: str | None = None
    hmf_amount: str | None = None
    chapter_99_codes: str | None = None
    chapter_99_rates: str | None = None
    chapter_99_amounts: str | None = None
    calculated_base_duty: str | None = None
    calculated_chapter_99_duty: str | None = None
    calculated_duty_total: str | None = None
    calculated_mpf_amount: str | None = None
    calculated_hmf_amount: str | None = None
    duty_variance: str | None = None
    mpf_variance: str | None = None
    hmf_variance: str | None = None
    parse_notes: str = ""


@dataclass
class TaxDocument:
    file_role: str
    source_file: str
    pair_key: str
    pages: int
    has_text_layer: bool
    fonts: str
    page_size: str
    entry_number: str | None = None
    entry_type: str | None = None
    summary_date: str | None = None
    port_code: str | None = None
    entry_date: str | None = None
    mode_of_transport: str | None = None
    country_of_origin: str | None = None
    import_date: str | None = None
    bl_or_awb_number: str | None = None
    manufacturer_id: str | None = None
    exporting_country: str | None = None
    export_date: str | None = None
    consignee_number: str | None = None
    importer_number: str | None = None
    ultimate_consignee: str | None = None
    importer_of_record: str | None = None
    duty_total: str | None = None
    tax_total: str | None = None
    other_total: str | None = None
    grand_total: str | None = None
    total_entered_value: str | None = None
    total_other_fees: str | None = None
    mpf_total: str | None = None
    hmf_total: str | None = None
    invoice_number: str | None = None
    invoice_value: str | None = None
    invoice_entered_value: str | None = None
    line_count: int = 0
    calculated_duty_total: str | None = None
    calculated_mpf_total: str | None = None
    calculated_hmf_total: str | None = None
    calculated_other_total: str | None = None
    calculated_grand_total: str | None = None
    duty_variance: str | None = None
    other_variance: str | None = None
    grand_total_variance: str | None = None
    parse_notes: str = ""


@dataclass
class PdfPair:
    pair_key: str
    folder: str
    original_path: str | None = None
    modified_path: str | None = None
    status: str = "unpaired"


@dataclass
class ParsedFile:
    document: TaxDocument
    lines: list[TaxLine] = field(default_factory=list)


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_modified_pdf(path: Path) -> bool:
    return any(marker in path.stem for marker in MODIFIED_MARKERS)


def normalize_pair_stem(stem: str) -> str:
    value = normalize_spaces(stem)
    for marker in MODIFIED_MARKERS:
        value = value.replace(marker, "")
    value = re.sub(r"\s*[-_]\s*$", "", value)
    return normalize_spaces(value)


def pair_key(path: Path) -> str:
    return f"{path.parent}|{normalize_pair_stem(path.stem)}"


def discover_pdfs(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in PDF_EXTENSIONS
    )


def build_pairs(paths: Iterable[Path]) -> list[PdfPair]:
    groups: dict[str, dict[str, list[Path]]] = {}
    for path in paths:
        role = "modified" if is_modified_pdf(path) else "original"
        groups.setdefault(pair_key(path), {"original": [], "modified": []})[role].append(path)

    pairs: list[PdfPair] = []
    for key, group in sorted(groups.items()):
        folder, _ = key.split("|", 1)
        originals = group["original"]
        modified = group["modified"]
        status = "paired" if len(originals) == 1 and len(modified) == 1 else "needs_review"
        pairs.append(
            PdfPair(
                pair_key=key,
                folder=folder,
                original_path=str(originals[0]) if originals else None,
                modified_path=str(modified[0]) if modified else None,
                status=status,
            )
        )
    return pairs


def extract_fragments(reader: PdfReader) -> list[TextFragment]:
    fragments: list[TextFragment] = []
    for page_index, page in enumerate(reader.pages, 1):
        def visitor(text, cm, tm, font_dict, font_size):
            clean = text.replace("\x00", "")
            if not clean.strip():
                return
            font = ""
            if font_dict:
                font = str(font_dict.get("/BaseFont") or "")
            fragments.append(
                TextFragment(
                    page=page_index,
                    x=round(float(tm[4]), 2),
                    y=round(float(tm[5]), 2),
                    size=round(float(font_size), 2),
                    font=font,
                    text=clean,
                )
            )

        page.extract_text(visitor_text=visitor)
    return fragments


def extract_text(reader: PdfReader) -> str:
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def fonts_from_reader(reader: PdfReader) -> list[str]:
    fonts: set[str] = set()
    for page in reader.pages:
        resources = page.get("/Resources") or {}
        page_fonts = resources.get("/Font") or {}
        try:
            page_fonts = page_fonts.get_object()
        except Exception:
            pass
        for key, value in page_fonts.items():
            try:
                obj = value.get_object()
                fonts.add(str(obj.get("/BaseFont") or key))
            except Exception:
                fonts.add(str(key))
    return sorted(fonts)


def page_sizes_from_reader(reader: PdfReader) -> list[str]:
    sizes = []
    for page in reader.pages:
        box = page.mediabox
        sizes.append(f"{float(box.width):.2f}x{float(box.height):.2f}")
    return sizes


def row_text(fragments: list[TextFragment], page: int, y: float, tolerance: float = 1.2) -> str:
    row = [
        fragment
        for fragment in fragments
        if fragment.page == page and abs(fragment.y - y) <= tolerance
    ]
    return normalize_spaces(" ".join(fragment.text.strip() for fragment in sorted(row, key=lambda f: f.x)))


def nearest_value_after_label(text: str, label: str, *, amount: bool = False) -> str | None:
    idx = text.find(label)
    if idx < 0:
        return None
    snippet = text[idx : idx + 180]
    if amount:
        match = re.search(r"\$\s*([0-9,]+(?:\.\d{2})?)", snippet)
    else:
        match = re.search(re.escape(label) + r"\s*\n([^\n]+)", snippet)
    return normalize_spaces(match.group(1)) if match else None


def amount_near_label(fragments: list[TextFragment], labels: tuple[str, ...]) -> str | None:
    for label in labels:
        matches = [f for f in fragments if label in f.text]
        for match in matches:
            candidates = [
                f
                for f in fragments
                if f.page == match.page
                and abs(f.y - (match.y - 11.5)) <= 3.0
                and f.x >= match.x
                and f.x <= match.x + 220
            ]
            if candidates:
                value = "".join(fragment.text for fragment in sorted(candidates, key=lambda f: f.x))
                amount = parse_money(value)
                if amount:
                    return amount
    return None


def parse_money(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"\s+", "", value).replace("$", "")
    match = re.search(r"([0-9,]+(?:\.\d{1,2})?)", compact)
    return match.group(1) if match else None


def money_after_dollar(value: str | None, *, last: bool = False) -> str | None:
    if not value:
        return None
    matches = re.findall(r"\$\s*([0-9,\s.]+)", value)
    if not matches:
        return None
    return parse_money(matches[-1] if last else matches[0])


def parse_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def format_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP):,.2f}"


def money_round(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def cbp_entered_value(value: str | Decimal | None) -> Decimal | None:
    decimal_value = value if isinstance(value, Decimal) else parse_decimal(value)
    if decimal_value is None:
        return None
    return decimal_value.quantize(WHOLE_DOLLAR_QUANT, rounding=ROUND_HALF_UP)


def format_whole_dollars(value: str | Decimal | None) -> str | None:
    decimal_value = cbp_entered_value(value)
    if decimal_value is None:
        return None
    return f"{decimal_value:,.0f}"


def percent_to_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    cleaned = value.strip().upper()
    if cleaned == "FREE":
        return Decimal("0")
    if cleaned.endswith("%"):
        try:
            return Decimal(cleaned[:-1]) / Decimal("100")
        except InvalidOperation:
            return None
    return None


def calculate_duty_for_rate(
    entered_value: Decimal,
    rate_text: str | None,
    *,
    net_quantity: str | None = None,
    net_unit: str | None = None,
) -> Decimal | None:
    rate_decimal = percent_to_decimal(rate_text)
    if rate_decimal is not None:
        return money_round(entered_value * rate_decimal)
    if not rate_text:
        return None

    cleaned = normalize_spaces(rate_text)
    net_quantity_decimal = parse_decimal(net_quantity)

    percent_part = Decimal("0")
    percent_matches = re.findall(r"([0-9.]+%)", cleaned)
    for percent_text in percent_matches:
        percent_rate = percent_to_decimal(percent_text)
        if percent_rate is not None:
            percent_part += money_round(entered_value * percent_rate)

    specific_part = Decimal("0")
    specific_matches = [
        ("$", amount, unit)
        for amount, unit in re.findall(
            r"\$\s*([0-9.]+)\s*(?:per|/)\s*([A-Z.]+)",
            cleaned,
            re.I,
        )
    ]
    specific_matches.extend(
        ("\u00a2", amount, unit)
        for amount, unit in re.findall(
            r"([0-9.]+)\s*\u00a2\s*(?:per|/)\s*([A-Z.]+)",
            cleaned,
            re.I,
        )
    )
    normalized_net_unit = re.sub(r"[^A-Z]", "", (net_unit or "").upper())
    for currency, amount_text, rate_unit in specific_matches:
        normalized_rate_unit = re.sub(r"[^A-Z]", "", rate_unit.upper())
        if (
            net_quantity_decimal is None
            or not normalized_net_unit
            or normalized_rate_unit != normalized_net_unit
        ):
            return None
        try:
            amount = Decimal(amount_text)
            if currency == "\u00a2":
                amount /= Decimal("100")
            specific_part += money_round(amount * net_quantity_decimal)
        except InvalidOperation:
            return None

    has_specific_marker = "$" in cleaned or "\u00a2" in cleaned
    if has_specific_marker and not specific_matches:
        return None
    if percent_matches or specific_matches:
        return money_round(percent_part + specific_part)
    return None


def decimal_difference(actual: str | None, calculated: str | None) -> str | None:
    actual_decimal = parse_decimal(actual)
    calculated_decimal = parse_decimal(calculated)
    if actual_decimal is None or calculated_decimal is None:
        return None
    return format_money(actual_decimal - calculated_decimal)


def field_below(fragments: list[TextFragment], label: str, x_min: float, x_max: float) -> str | None:
    labels = [f for f in fragments if label in f.text]
    for label_fragment in labels:
        candidates = [
            f
            for f in fragments
            if f.page == label_fragment.page
            and x_min <= f.x <= x_max
            and label_fragment.y - 18 <= f.y <= label_fragment.y - 6
            and label not in f.text
        ]
        if candidates:
            return normalize_spaces(" ".join(f.text.strip() for f in sorted(candidates, key=lambda f: f.x)))
    return None


def value_at_box(
    fragments: list[TextFragment],
    *,
    page: int,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> str | None:
    values = [
        f
        for f in fragments
        if f.page == page and x_min <= f.x <= x_max and y_min <= f.y <= y_max
    ]
    if not values:
        return None
    return normalize_spaces(" ".join(f.text.strip() for f in sorted(values, key=lambda f: (-f.y, f.x))))


def money_at_box(
    fragments: list[TextFragment],
    *,
    page: int,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> str | None:
    values = [
        fragment
        for fragment in fragments
        if fragment.page == page and x_min <= fragment.x <= x_max and y_min <= fragment.y <= y_max
    ]
    if not values:
        return None
    text = "".join(fragment.text for fragment in sorted(values, key=lambda f: (round(f.y), f.x)))
    return parse_money(text)


def parse_invoice_totals(text: str) -> tuple[str | None, str | None, str | None]:
    match = re.search(
        r"Totals for Invoice.*?\n([^\n]+?)\s+([0-9,]+\.\d{2})\s+USD\s+1\.00000\s+([0-9,]+\.\d{2})\s+USD",
        text,
        re.S,
    )
    if not match:
        return None, None, None
    return normalize_spaces(match.group(1)), match.group(2), match.group(3)


def parse_total_entered_value_from_text(text: str) -> str | None:
    label_index = text.find("39. Total Entered Value")
    if label_index < 0:
        return None
    snippet = text[label_index : label_index + 120]
    match = re.search(r"\$\s*([0-9,\s]+(?:\.\d{2})?)", snippet)
    return parse_money(match.group(1)) if match else None


def parse_total_other_fees_from_text(text: str) -> str | None:
    label_index = text.find("Total Other Fees")
    if label_index < 0:
        return None
    snippet = text[label_index : label_index + 100]
    match = re.search(r"\$\s*([0-9,\s]+(?:\.\d{2})?)", snippet)
    return parse_money(match.group(1)) if match else None


def fee_summary_section(text: str) -> str:
    start = text.find("Other Fee Summary")
    if start < 0:
        return ""
    end = text.find("40. Declaration", start)
    if end < 0:
        end = start + 500
    return text[start:end]


def parse_fee_summary_amount(text: str, fee_code: str, label: str) -> str | None:
    section = fee_summary_section(text)
    if not section:
        return None
    pattern = re.compile(rf"{re.escape(fee_code)}\s*-\s*{re.escape(label)}[^\n]*")
    match = pattern.search(section)
    if not match:
        return None
    return money_after_dollar(match.group(0))


def parse_header_document(
    path: Path,
    file_role: str,
    key: str,
    reader: PdfReader,
    text: str,
    fragments: list[TextFragment],
) -> TaxDocument:
    fonts = fonts_from_reader(reader)
    page_sizes = page_sizes_from_reader(reader)
    invoice_number, invoice_value, invoice_entered_value = parse_invoice_totals(text)
    coord_total_entered_value = parse_total_entered_value(fragments)
    text_total_entered_value = parse_total_entered_value_from_text(text)
    total_entered_value = prefer_larger_amount(coord_total_entered_value, text_total_entered_value)
    total_other_fees = parse_total_other_fees(fragments) or parse_total_other_fees_from_text(text)
    mpf_total = parse_fee_summary_amount(text, "499", "MPF")
    hmf_total = parse_fee_summary_amount(text, "501", "HMF")

    doc = TaxDocument(
        file_role=file_role,
        source_file=str(path),
        pair_key=key,
        pages=len(reader.pages),
        has_text_layer=len(text.strip()) > 100,
        fonts=", ".join(fonts),
        page_size=", ".join(sorted(set(page_sizes))),
        entry_number=field_below(fragments, "1. Filer Code/Entry Number", 20, 150),
        entry_type=field_below(fragments, "2. Entry Type", 150, 225),
        summary_date=field_below(fragments, "3. Summary Date", 225, 300),
        port_code=field_below(fragments, "6. Port Code", 430, 490),
        entry_date=field_below(fragments, "7. Entry Date", 490, 570),
        mode_of_transport=field_below(fragments, "9. Mode of Transport", 185, 260),
        country_of_origin=field_below(fragments, "10. Country of Origin", 310, 370),
        import_date=field_below(fragments, "11. Import Date", 490, 570),
        bl_or_awb_number=field_below(fragments, "12. B/L or AWB Number", 20, 150),
        manufacturer_id=field_below(fragments, "13. Manufacturer ID", 190, 315),
        exporting_country=field_below(fragments, "14. Exporting Country", 310, 370),
        export_date=field_below(fragments, "15. Export Date", 490, 570),
        consignee_number=field_below(fragments, "26. Consignee Number", 160, 315),
        importer_number=field_below(fragments, "27. Importer Number", 315, 450),
        ultimate_consignee=value_at_box(fragments, page=1, x_min=25, x_max=300, y_min=515, y_max=570),
        importer_of_record=value_at_box(fragments, page=1, x_min=315, x_max=590, y_min=515, y_max=570),
        duty_total=amount_near_label(fragments, ("41. Duty",))
        or money_at_box(fragments, page=1, x_min=530, x_max=590, y_min=235, y_max=248),
        tax_total=amount_near_label(fragments, ("42. Tax",)),
        other_total=amount_near_label(fragments, ("43. Other",))
        or money_at_box(fragments, page=1, x_min=530, x_max=590, y_min=190, y_max=204),
        grand_total=amount_near_label(fragments, ("44. Total",))
        or money_at_box(fragments, page=1, x_min=530, x_max=590, y_min=169, y_max=182),
        total_entered_value=total_entered_value,
        total_other_fees=total_other_fees,
        mpf_total=mpf_total,
        hmf_total=hmf_total,
        invoice_number=invoice_number,
        invoice_value=invoice_value,
        invoice_entered_value=invoice_entered_value,
    )
    notes = []
    if not doc.has_text_layer:
        notes.append("no readable text layer")
    if len(fonts) > 4:
        notes.append("multiple embedded font families")
    if doc.invoice_entered_value and doc.total_entered_value:
        total_value = parse_decimal(doc.total_entered_value)
        invoice_value = parse_decimal(doc.invoice_entered_value)
        if total_value is not None and invoice_value is not None and total_value != invoice_value:
            doc.total_entered_value = doc.invoice_entered_value
            notes.append("total entered value normalized from invoice totals")
    elif doc.invoice_entered_value and not doc.total_entered_value:
        doc.total_entered_value = doc.invoice_entered_value
        notes.append("total entered value filled from invoice totals")
    doc.parse_notes = "; ".join(notes)
    return doc


def prefer_larger_amount(first: str | None, second: str | None) -> str | None:
    if not first:
        return second
    if not second:
        return first
    first_value = parse_decimal(first)
    second_value = parse_decimal(second)
    if first_value is None or second_value is None:
        return first
    return second if second_value > first_value else first


def parse_total_entered_value(fragments: list[TextFragment]) -> str | None:
    labels = [f for f in fragments if "39. Total Entered Value" in f.text]
    for label in labels:
        candidates = [
            f
            for f in fragments
            if f.page == label.page
            and 165 <= f.x <= 270
            and label.y - 30 <= f.y <= label.y - 5
        ]
        if candidates:
            # Use the closest money fragment to the label row. Original files sometimes
            # place the value slightly above the label; adjusted copies often place it below.
            y_groups: dict[float, list[TextFragment]] = {}
            for candidate in candidates:
                y_groups.setdefault(round(candidate.y), []).append(candidate)
            chosen_y = sorted(y_groups, key=lambda y: abs(y - label.y))[0]
            value = "".join(fragment.text for fragment in sorted(y_groups[chosen_y], key=lambda f: f.x))
            return parse_money(value)
    return None


def parse_total_other_fees(fragments: list[TextFragment]) -> str | None:
    labels = [f for f in fragments if "Total Other Fees" in f.text]
    for label in labels:
        candidates = [
            f
            for f in fragments
            if f.page == label.page
            and 165 <= f.x <= 270
            and label.y - 30 <= f.y <= label.y - 5
        ]
        if candidates:
            y_groups: dict[float, list[TextFragment]] = {}
            for candidate in candidates:
                y_groups.setdefault(round(candidate.y), []).append(candidate)
            chosen_y = sorted(y_groups, key=lambda y: abs(y - label.y))[0]
            value = "".join(fragment.text for fragment in sorted(y_groups[chosen_y], key=lambda f: f.x))
            return parse_money(value)
    return None


def page_line_table_top(page: int) -> float:
    return 455 if page == 1 else 655


def parse_lines(
    path: Path,
    file_role: str,
    key: str,
    entry_number: str | None,
    fragments: list[TextFragment],
) -> list[TaxLine]:
    lines: list[TaxLine] = []
    line_starts = [
        f
        for f in fragments
        if 35 <= f.x <= 55
        and re.match(r"^\s*\d{3}(?:\s|$)", f.text.strip())
        and not f.text.strip().startswith("499")
        and f.y < page_line_table_top(f.page)
        and f.y > (280 if f.page == 1 else 40)
    ]
    line_starts = sorted(line_starts, key=lambda f: (f.page, -f.y))

    for index, start in enumerate(line_starts):
        next_start = line_starts[index + 1] if index + 1 < len(line_starts) else None
        y_low = 280.0 if start.page == 1 else 40.0
        if next_start and next_start.page == start.page:
            y_low = next_start.y + 1.0
        rows = rows_for_line(fragments, start.page, start.y, y_low)
        line = parse_line_rows(path, file_role, key, entry_number, start, rows)
        lines.append(line)
    return lines


def rows_for_line(
    fragments: list[TextFragment],
    page: int,
    y_high: float,
    y_low: float,
) -> list[list[TextFragment]]:
    row_map: dict[float, list[TextFragment]] = {}
    for fragment in fragments:
        if fragment.page != page:
            continue
        if not (y_low <= fragment.y <= y_high + 0.5):
            continue
        if fragment.size < 8.5:
            continue
        y_key = round(fragment.y)
        row_map.setdefault(y_key, []).append(fragment)

    rows = []
    for y in sorted(row_map.keys(), reverse=True):
        row = sorted(row_map[y], key=lambda f: f.x)
        row_text_value = normalize_spaces(" ".join(f.text.strip() for f in row))
        if row_text_value.startswith("CBP Form"):
            continue
        if "Totals for Invoice" in row_text_value:
            break
        rows.append(row)
    return rows


def parse_line_rows(
    path: Path,
    file_role: str,
    key: str,
    entry_number: str | None,
    start: TextFragment,
    rows: list[list[TextFragment]],
) -> TaxLine:
    notes: list[str] = []
    line = TaxLine(
        file_role=file_role,
        source_file=str(path),
        pair_key=key,
        entry_number=entry_number,
        page=start.page,
        line_no=start.text.strip()[:3],
    )

    descriptions: list[str] = []
    chapter_codes: list[str] = []
    chapter_rates: list[str] = []
    chapter_amounts: list[str] = []

    for row in rows:
        text = normalize_spaces(" ".join(fragment.text.strip() for fragment in row))
        if not text:
            continue

        if "Merchandise Processing Fee" in text:
            rate_match = re.search(r"([0-9.]+%)", text)
            line.mpf_rate = rate_match.group(1) if rate_match else line.mpf_rate
            line.mpf_amount = money_after_dollar(text, last=True)
            if not line.mpf_amount:
                notes.append("mpf row not parsed")
            continue

        if "Harbor Maintenance Fee" in text:
            rate_match = re.search(r"([0-9.]+%)", text)
            line.hmf_rate = rate_match.group(1) if rate_match else line.hmf_rate
            line.hmf_amount = money_after_dollar(text, last=True)
            if not line.hmf_amount:
                notes.append("hmf row not parsed")
            continue

        hts_match = re.search(r"\b(\d{4}\.\d{2}\.\d{4})\b", text)
        chapter_match = re.search(
            r"\b(99\d{2}\.\d{2}\.\d{2})\b(?:\s+([A-Z0-9.]+%|FREE))?(?:\s+\$?\s*([0-9,]+(?:\.\d{2})?))?",
            text,
        )
        if chapter_match:
            chapter_codes.append(chapter_match.group(1))
            chapter_rates.append(chapter_match.group(2) or "")
            chapter_amounts.append(chapter_match.group(3) or "")
            continue

        if hts_match:
            line.hts = hts_match.group(1)
            parsed = parse_main_hts_row(row, text, hts_match.group(1))
            for field_name, value in parsed.items():
                setattr(line, field_name, value)
            if not line.entered_value:
                notes.append("entered value not parsed")
            if not line.rate:
                notes.append("rate not parsed")
            continue

        charge_match = re.search(r"\bC\s+\$?\s*([0-9,]+(?:\.\d{2})?)", text)
        if charge_match:
            line.charges = charge_match.group(1)
            continue

        if text == "N" or text.endswith(" N"):
            line.relationship = "N"
            continue

        cleaned_description = re.sub(r"^\d{3}\s+", "", text)
        if cleaned_description:
            descriptions.append(cleaned_description)

    line.description = " | ".join(descriptions) or None
    line.chapter_99_codes = "; ".join(chapter_codes) or None
    line.chapter_99_rates = "; ".join(chapter_rates) or None
    line.chapter_99_amounts = "; ".join(chapter_amounts) or None
    line.parse_notes = "; ".join(notes)
    return line


def parse_main_hts_row(
    row: list[TextFragment],
    row_text_value: str,
    hts: str,
) -> dict[str, str | None]:
    result: dict[str, str | None] = {
        "gross_weight": None,
        "gross_unit": None,
        "net_quantity": None,
        "net_unit": None,
        "entered_value": None,
        "rate": None,
        "duty_amount": None,
    }

    def zone_text(x_min: float, x_max: float) -> str:
        zone = [fragment for fragment in row if x_min <= fragment.x <= x_max]
        return normalize_spaces("".join(fragment.text for fragment in sorted(zone, key=lambda f: f.x)))

    net_zone = zone_text(235, 335)
    entered_rate_zone = zone_text(330, 500)
    duty_zone = zone_text(500, 590)
    if net_zone:
        match = re.search(r"([0-9,]+(?:\.\d+)?)\s+([A-Z]+)", net_zone)
        if match:
            result["net_quantity"] = match.group(1)
            result["net_unit"] = match.group(2)
    if entered_rate_zone:
        money = parse_money(entered_rate_zone)
        if money:
            result["entered_value"] = money
        rate = re.search(r"(FREE|[0-9.]+%)", entered_rate_zone)
        if rate:
            result["rate"] = rate.group(1)
    if duty_zone:
        money = parse_money(duty_zone)
        if money:
            result["duty_amount"] = money

    for fragment in row:
        text = normalize_spaces(fragment.text)
        if fragment.x < 150:
            match = re.search(rf"{re.escape(hts)}\s+([0-9,]+(?:\.\d+)?)\s+([A-Z]+)", text)
            if match:
                result["gross_weight"] = match.group(1)
                result["gross_unit"] = match.group(2)
        if 235 <= fragment.x <= 335:
            match = re.search(r"([0-9,]+(?:\.\d+)?)\s+([A-Z]+)", text)
            if match:
                result["net_quantity"] = match.group(1)
                result["net_unit"] = match.group(2)
        if 330 <= fragment.x <= 430:
            money = parse_money(text)
            if money:
                result["entered_value"] = money
        if 380 <= fragment.x <= 500:
            match = re.search(r"(FREE|[0-9.]+%)", text)
            if match:
                result["rate"] = match.group(1)
        if fragment.x >= 500:
            money = parse_money(text)
            if money:
                result["duty_amount"] = money

    if not result["entered_value"] or not result["rate"] or not result["duty_amount"]:
        fallback = re.search(
            rf"{re.escape(hts)}\s+"
            r"([0-9,]+(?:\.\d+)?)\s+([A-Z]+)\s+"
            r"([0-9,]+(?:\.\d+)?)\s+([A-Z]+)\s+"
            r"\$?\s*([0-9,]+(?:\.\d{2})?)\s+"
            r"(.+)",
            row_text_value,
        )
        if fallback:
            rate_and_duty = normalize_spaces(fallback.group(6))
            dollar_matches = list(re.finditer(r"\$\s*([0-9,]+(?:\.\d{1,2})?)", rate_and_duty))
            if dollar_matches:
                duty_match = dollar_matches[-1]
                duty_amount = parse_money(duty_match.group(0))
                rate_text = normalize_spaces(rate_and_duty[: duty_match.start()])
            else:
                parts = rate_and_duty.rsplit(" ", 1)
                rate_text = parts[0] if len(parts) == 2 else rate_and_duty
                duty_amount = parse_money(parts[-1]) if len(parts) == 2 else None
            result["gross_weight"] = result["gross_weight"] or fallback.group(1)
            result["gross_unit"] = result["gross_unit"] or fallback.group(2)
            result["net_quantity"] = result["net_quantity"] or fallback.group(3)
            result["net_unit"] = result["net_unit"] or fallback.group(4)
            result["entered_value"] = result["entered_value"] or fallback.group(5)
            result["rate"] = result["rate"] or rate_text
            result["duty_amount"] = result["duty_amount"] or duty_amount

    return result


def parse_pdf(path: Path, file_role: str, key: str) -> ParsedFile:
    reader = PdfReader(str(path))
    text = extract_text(reader)
    fragments = extract_fragments(reader)
    document = parse_header_document(path, file_role, key, reader, text, fragments)
    lines = parse_lines(path, file_role, key, document.entry_number, fragments)
    document.line_count = len(lines)
    return ParsedFile(document=document, lines=lines)


def calculate_line_amounts(line: TaxLine, *, has_hmf: bool) -> None:
    entered_value = cbp_entered_value(line.entered_value)
    if entered_value is None:
        return

    base_duty = calculate_duty_for_rate(
        entered_value,
        line.rate,
        net_quantity=line.net_quantity,
        net_unit=line.net_unit,
    )
    if base_duty is not None:
        line.calculated_base_duty = format_money(base_duty)
    else:
        base_duty = None

    chapter_duty = Decimal("0")
    chapter_rates = [item.strip() for item in (line.chapter_99_rates or "").split(";") if item.strip()]
    for rate_text in chapter_rates:
        rate = percent_to_decimal(rate_text)
        if rate is not None:
            chapter_duty += money_round(entered_value * rate)
    if chapter_rates:
        line.calculated_chapter_99_duty = format_money(chapter_duty)

    if base_duty is not None or chapter_rates:
        line.calculated_duty_total = format_money((base_duty or Decimal("0")) + chapter_duty)
        extracted_duty = sum_amounts(line.duty_amount, line.chapter_99_amounts)
        line.duty_variance = decimal_difference(extracted_duty, line.calculated_duty_total)

    calculated_mpf = money_round(entered_value * MPF_RATE)
    line.calculated_mpf_amount = format_money(calculated_mpf)
    line.mpf_variance = decimal_difference(line.mpf_amount, line.calculated_mpf_amount)

    if has_hmf:
        calculated_hmf = money_round(entered_value * HMF_RATE)
        line.calculated_hmf_amount = format_money(calculated_hmf)
        line.hmf_variance = decimal_difference(line.hmf_amount, line.calculated_hmf_amount)


def sum_amounts(*values: str | None) -> str | None:
    total = Decimal("0")
    has_value = False
    for value in values:
        if not value:
            continue
        for part in str(value).split(";"):
            decimal_value = parse_decimal(part.strip())
            if decimal_value is not None:
                total += decimal_value
                has_value = True
    return format_money(total) if has_value else None


def clamp_mpf(value: Decimal) -> Decimal:
    if value < MPF_MIN:
        return MPF_MIN
    if value > MPF_MAX:
        return MPF_MAX
    return value


def apply_calculations(parsed_files: list[ParsedFile]) -> None:
    for parsed in parsed_files:
        has_hmf = bool(parsed.document.hmf_total) or any(line.hmf_amount for line in parsed.lines)
        for line in parsed.lines:
            calculate_line_amounts(line, has_hmf=has_hmf)

        duty_total = sum_decimal_field(parsed.lines, "calculated_duty_total")
        hmf_total = sum_decimal_field(parsed.lines, "calculated_hmf_amount")
        entered_values = [cbp_entered_value(line.entered_value) for line in parsed.lines]
        entered_total = sum((value for value in entered_values if value is not None), Decimal("0"))
        if not any(value is not None for value in entered_values):
            entered_total = None

        if entered_total is not None:
            parsed.document.calculated_mpf_total = format_money(clamp_mpf(money_round(entered_total * MPF_RATE)))
        parsed.document.calculated_duty_total = format_money(duty_total) if duty_total is not None else None
        parsed.document.calculated_hmf_total = format_money(hmf_total) if hmf_total is not None else None

        mpf_total = parse_decimal(parsed.document.calculated_mpf_total)
        other_total = Decimal("0")
        has_other = False
        if mpf_total is not None:
            other_total += mpf_total
            has_other = True
        if hmf_total is not None:
            other_total += hmf_total
            has_other = True
        if has_other:
            parsed.document.calculated_other_total = format_money(other_total)

        grand_total = Decimal("0")
        has_grand = False
        if duty_total is not None:
            grand_total += duty_total
            has_grand = True
        if has_other:
            grand_total += other_total
            has_grand = True
        if has_grand:
            parsed.document.calculated_grand_total = format_money(grand_total)

        parsed.document.duty_variance = decimal_difference(
            parsed.document.duty_total,
            parsed.document.calculated_duty_total,
        )
        parsed.document.other_variance = decimal_difference(
            parsed.document.other_total,
            parsed.document.calculated_other_total,
        )
        parsed.document.grand_total_variance = decimal_difference(
            parsed.document.grand_total,
            parsed.document.calculated_grand_total,
        )


def sum_decimal_field(lines: list[TaxLine], field_name: str) -> Decimal | None:
    total = Decimal("0")
    has_value = False
    for line in lines:
        value = parse_decimal(getattr(line, field_name))
        if value is not None:
            total += value
            has_value = True
    return total if has_value else None


def export_results(
    output_dir: Path,
    pairs: list[PdfPair],
    parsed_files: list[ParsedFile],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    pair_rows = [asdict(pair) for pair in pairs]
    doc_rows = [asdict(parsed.document) for parsed in parsed_files]
    line_rows = [asdict(line) for parsed in parsed_files for line in parsed.lines]

    write_excel(output_dir / "7501_pairs.xlsx", pair_rows, "pairs")
    write_excel(output_dir / "7501_documents.xlsx", doc_rows, "documents")
    write_excel(output_dir / "7501_line_items.xlsx", line_rows, "line_items")
    write_pair_summary(output_dir / "7501_pair_summary.xlsx", doc_rows)
    write_line_compare(output_dir / "7501_line_compare.xlsx", line_rows)

    payload = {
        "pairs": pair_rows,
        "documents": doc_rows,
        "line_items": line_rows,
    }
    (output_dir / "7501_extraction.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_excel(path: Path, rows: list[dict], sheet_name: str) -> None:
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        workbook = writer.book
        worksheet = writer.sheets[sheet_name]
        header_format = workbook.add_format(
            {
                "bold": True,
                "bg_color": "#1f4e78",
                "font_color": "white",
                "border": 1,
                "valign": "vcenter",
            }
        )
        for column_index, column_name in enumerate(df.columns):
            worksheet.write(0, column_index, column_name, header_format)
            width = min(max(len(str(column_name)) + 4, 12), 60)
            if not df.empty:
                quantile = df[column_name].dropna().astype(str).str.len().quantile(0.9)
                if pd.notna(quantile):
                    sample_width = min(max(quantile + 2, width), 70)
                    width = int(sample_width)
            worksheet.set_column(column_index, column_index, width)
        worksheet.autofilter(0, 0, max(len(df), 1), max(len(df.columns) - 1, 0))
        worksheet.freeze_panes(1, 0)


def numeric_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False), errors="coerce")


def write_pair_summary(path: Path, doc_rows: list[dict]) -> None:
    if not doc_rows:
        write_excel(path, [], "pair_summary")
        return
    df = pd.DataFrame(doc_rows)
    original = df[df["file_role"] == "original"].copy()
    modified = df[df["file_role"] == "modified"].copy()
    keep = [
        "pair_key",
        "source_file",
        "entry_number",
        "mode_of_transport",
        "total_entered_value",
        "duty_total",
        "other_total",
        "grand_total",
        "line_count",
        "mpf_total",
        "hmf_total",
        "calculated_duty_total",
        "calculated_mpf_total",
        "calculated_hmf_total",
        "calculated_other_total",
        "calculated_grand_total",
        "duty_variance",
        "other_variance",
        "grand_total_variance",
        "parse_notes",
    ]
    original = original[keep].add_prefix("original_").rename(columns={"original_pair_key": "pair_key"})
    modified = modified[keep].add_prefix("modified_").rename(columns={"modified_pair_key": "pair_key"})
    merged = original.merge(modified, on="pair_key", how="outer")
    for field in ["total_entered_value", "duty_total", "other_total", "grand_total"]:
        original_col = f"original_{field}"
        modified_col = f"modified_{field}"
        delta_col = f"delta_{field}"
        merged[delta_col] = numeric_series(merged[modified_col]) - numeric_series(merged[original_col])
    write_excel(path, merged.to_dict("records"), "pair_summary")


def write_line_compare(path: Path, line_rows: list[dict]) -> None:
    if not line_rows:
        write_excel(path, [], "line_compare")
        return
    df = pd.DataFrame(line_rows)
    original = df[df["file_role"] == "original"].copy()
    modified = df[df["file_role"] == "modified"].copy()
    keep = [
        "pair_key",
        "line_no",
        "description",
        "hts",
        "entered_value",
        "rate",
        "duty_amount",
        "mpf_amount",
        "hmf_amount",
        "chapter_99_codes",
        "chapter_99_amounts",
        "calculated_base_duty",
        "calculated_chapter_99_duty",
        "calculated_duty_total",
        "calculated_mpf_amount",
        "calculated_hmf_amount",
        "duty_variance",
        "mpf_variance",
        "hmf_variance",
        "parse_notes",
    ]
    original = original[keep].add_prefix("original_").rename(
        columns={"original_pair_key": "pair_key", "original_line_no": "line_no"}
    )
    modified = modified[keep].add_prefix("modified_").rename(
        columns={"modified_pair_key": "pair_key", "modified_line_no": "line_no"}
    )
    merged = original.merge(modified, on=["pair_key", "line_no"], how="outer")
    for field in ["entered_value", "duty_amount", "mpf_amount", "hmf_amount"]:
        original_col = f"original_{field}"
        modified_col = f"modified_{field}"
        delta_col = f"delta_{field}"
        merged[delta_col] = numeric_series(merged[modified_col]) - numeric_series(merged[original_col])
    write_excel(path, merged.to_dict("records"), "line_compare")


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse CBP 7501 PDFs into review tables.")
    parser.add_argument("--input", required=True, help="Root folder containing 7501 PDFs.")
    parser.add_argument("--output", default="output", help="Output folder for reports.")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional maximum number of PDFs to parse. 0 means all.",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    if not input_dir.exists():
        raise SystemExit(f"Input folder does not exist: {input_dir}")

    paths = discover_pdfs(input_dir)
    pairs = build_pairs(paths)

    parse_jobs: list[tuple[Path, str, str]] = []
    for pair in pairs:
        if pair.original_path:
            parse_jobs.append((Path(pair.original_path), "original", pair.pair_key))
        if pair.modified_path:
            parse_jobs.append((Path(pair.modified_path), "modified", pair.pair_key))
    if args.limit:
        parse_jobs = parse_jobs[: args.limit]

    parsed_files: list[ParsedFile] = []
    errors: list[dict[str, str]] = []
    for path, role, key in parse_jobs:
        try:
            parsed_files.append(parse_pdf(path, role, key))
        except Exception as exc:
            errors.append({"source_file": str(path), "file_role": role, "error": str(exc)})

    apply_calculations(parsed_files)
    export_results(output_dir, pairs, parsed_files)
    if errors:
        write_excel(output_dir / "7501_errors.xlsx", errors, "errors")

    print(f"PDF files found: {len(paths)}")
    print(f"Pairs found: {len(pairs)}")
    print(f"Parsed files: {len(parsed_files)}")
    print(f"Errors: {len(errors)}")
    print(f"Output folder: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
