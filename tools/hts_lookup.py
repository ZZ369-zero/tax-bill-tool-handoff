from __future__ import annotations

from functools import lru_cache
import json
import re
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


HTS_SEARCH_URL = "https://hts.usitc.gov/reststop/search"
ADDITIONAL_HTS_PATTERN = re.compile(r"\b(99\d{2}\.\d{2}\.\d{2})\b")


def hts_digits(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) not in (8, 10):
        raise ValueError("HTS code must contain 8 or 10 digits")
    return digits


def format_hts(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 4:
        return digits
    if len(digits) == 6:
        return f"{digits[:4]}.{digits[4:6]}"
    if len(digits) == 8:
        return f"{digits[:4]}.{digits[4:6]}.{digits[6:8]}"
    if len(digits) == 10:
        return f"{digits[:4]}.{digits[4:6]}.{digits[6:8]}.{digits[8:10]}"
    raise ValueError("HTS code must contain 4, 6, 8, or 10 digits")


def record_digits(record: dict[str, Any]) -> str:
    return re.sub(r"\D", "", str(record.get("htsno") or ""))


def as_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


@lru_cache(maxsize=256)
def search_hts(keyword: str) -> tuple[dict[str, Any], ...]:
    url = f"{HTS_SEARCH_URL}?{urlencode({'keyword': keyword})}"
    request = Request(url, headers={"User-Agent": "tax-bill-tool/1.0"})
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    return tuple(as_records(payload))


def normalized_units(raw_units: Any) -> list[str]:
    if not raw_units:
        return []
    values = raw_units if isinstance(raw_units, list) else [raw_units]
    units: list[str] = []
    aliases = {
        "no.": "NO",
        "no": "NO",
        "kg": "KG",
        "g": "G",
        "doz.": "DOZ",
        "doz": "DOZ",
        "liters": "L",
        "liter": "L",
    }
    for value in values:
        for part in re.split(r"\s+(?:and|or)\s+|[,/]", str(value), flags=re.I):
            cleaned = part.strip()
            if not cleaned:
                continue
            unit = aliases.get(cleaned.lower(), cleaned.upper().rstrip("."))
            if unit not in units:
                units.append(unit)
    return units


def build_lookup_result(code: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    digits = hts_digits(code)
    relevant = {
        str(record.get("htsno")): record
        for record in records
        if record_digits(record) and digits.startswith(record_digits(record))
    }
    exact = next((record for record in records if record_digits(record) == digits), None)
    if exact is None:
        raise LookupError(f"HTS code {format_hts(digits)} was not found in the current USITC data")

    path = sorted(relevant.values(), key=lambda record: len(record_digits(record)))
    descriptions: list[str] = []
    for record in path:
        description = re.sub(r"\s+", " ", str(record.get("description") or "")).strip().rstrip(":")
        if not description or description.lower() == "other":
            continue
        if description not in descriptions:
            descriptions.append(description)
    exact_description = re.sub(r"\s+", " ", str(exact.get("description") or "")).strip().rstrip(":")
    if exact_description and exact_description.lower() != "other" and exact_description not in descriptions:
        descriptions.append(exact_description)
    description = " / ".join(descriptions) or exact_description or "Other"

    rate_candidates = [
        record
        for record in path
        if str(record.get("general") or "").strip()
    ]
    rate_record = max(rate_candidates, key=lambda record: len(record_digits(record)), default=exact)
    units = normalized_units(exact.get("units"))

    additional_codes: list[str] = []
    for record in path:
        for footnote in record.get("footnotes") or []:
            for match in ADDITIONAL_HTS_PATTERN.findall(str(footnote.get("value") or "")):
                if match not in additional_codes:
                    additional_codes.append(match)

    return {
        "code": format_hts(digits),
        "description": description,
        "leaf_description": exact_description or None,
        "units": units,
        "required_units": " + ".join(units) or None,
        "general_rate": str(rate_record.get("general") or "").strip() or None,
        "special_rate": str(rate_record.get("special") or "").strip() or None,
        "column_2_rate": str(rate_record.get("other") or "").strip() or None,
        "additional_hts_codes": additional_codes,
        "source": "USITC HTS REST API",
    }


def lookup_hts(code: str) -> dict[str, Any]:
    digits = hts_digits(code)
    keywords = [format_hts(digits[:length]) for length in (4, 6, 8, len(digits))]
    records: dict[str, dict[str, Any]] = {}
    for keyword in dict.fromkeys(keywords):
        for record in search_hts(keyword):
            key = str(record.get("htsno") or "")
            if key:
                records[key] = dict(record)
    return build_lookup_result(digits, list(records.values()))
