from __future__ import annotations

from functools import lru_cache
import json
import re
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


HTS_SEARCH_URL = "https://hts.usitc.gov/reststop/search"
ADDITIONAL_HTS_PATTERN = re.compile(r"\b(99\d{2}\.\d{2}\.\d{2})\b")

EU_ORIGIN_KEYS = {
    "AT",
    "AUSTRIA",
    "BE",
    "BELGIUM",
    "BG",
    "BULGARIA",
    "HR",
    "CROATIA",
    "CY",
    "CYPRUS",
    "CZ",
    "CZECHIA",
    "CZECHREPUBLIC",
    "DK",
    "DENMARK",
    "EE",
    "ESTONIA",
    "FI",
    "FINLAND",
    "FR",
    "FRANCE",
    "DE",
    "GERMANY",
    "GR",
    "GREECE",
    "HU",
    "HUNGARY",
    "IE",
    "IRELAND",
    "IT",
    "ITALY",
    "LV",
    "LATVIA",
    "LT",
    "LITHUANIA",
    "LU",
    "LUXEMBOURG",
    "MT",
    "MALTA",
    "NL",
    "NETHERLANDS",
    "PL",
    "POLAND",
    "PT",
    "PORTUGAL",
    "RO",
    "ROMANIA",
    "SK",
    "SLOVAKIA",
    "SI",
    "SLOVENIA",
    "ES",
    "SPAIN",
    "SE",
    "SWEDEN",
}

ORIGIN_ALIASES = {
    "CN": "CN",
    "CHINA": "CN",
    "PRC": "CN",
    "PEOPLESREPUBLICOFCHINA": "CN",
    "GB": "GB",
    "UK": "GB",
    "UNITEDKINGDOM": "GB",
    "GREATBRITAIN": "GB",
    "JP": "JP",
    "JAPAN": "JP",
    "KR": "KR",
    "KOREA": "KR",
    "SOUTHKOREA": "KR",
    "REPUBLICOFKOREA": "KR",
    "TW": "TW",
    "TAIWAN": "TW",
}

SECTION_232_WOOD_ORIGIN_OVERRIDES = {
    "GB": {
        "code": "9903.76.20",
        "rate": "10%",
        "description": "Section 232 wood products - United Kingdom origin",
        "source": "USITC HTS Chapter 99 U.S. note 37(h); HTS 9903.76.20",
    },
    "JP": {
        "code": "9903.76.21",
        "rate": "15%",
        "description": "Section 232 wood products - Japan origin",
        "source": "USITC HTS Chapter 99 U.S. note 37(i); HTS 9903.76.21",
    },
    "EU": {
        "code": "9903.76.22",
        "rate": "15%",
        "description": "Section 232 wood products - European Union origin",
        "source": "USITC HTS Chapter 99 U.S. note 37(j); HTS 9903.76.22",
    },
    "KR": {
        "code": "9903.76.23",
        "rate": "15%",
        "description": "Section 232 wood products - South Korea origin",
        "source": "USITC HTS Chapter 99 U.S. note 37(l); HTS 9903.76.23",
    },
    "TW": {
        "code": "9903.76.24",
        "rate": "15%",
        "description": "Section 232 wood products - Taiwan origin",
        "source": "USITC HTS Chapter 99 U.S. note 37(m); HTS 9903.76.24",
    },
}

# Some Chapter 99 provisions are not exposed as footnotes on every ordinary HTS
# line in the USITC search result. USITC Chapter 99 U.S. note 37 lists the
# affected ordinary HTS subheadings and matching Chapter 99 reporting number.
# Keep these explicit reverse mappings here so the UI/batch workflow does not
# depend solely on ordinary-line footnotes.
STATIC_ADDITIONAL_HTS_RULES: tuple[dict[str, Any], ...] = (
    {
        "code": "9903.88.03",
        "rate": "25%",
        "description": "Section 301 China Tariffs - seating products listed in U.S. note 20(f)",
        "applies_to": (
            "94012000",
            "94013100",
            "94013900",
            "94014100",
            "94014900",
            "94015200",
            "94015300",
            "94015900",
            "94016120",
            "94016160",
            "94016920",
            "94016940",
            "94016980",
            "94019190",
            "94019935",
            "94019990",
        ),
        "origin_keys": ("CN",),
        "source": "USITC China Tariffs current release; HTS 9903.88.03",
    },
    {
        "code": "9903.88.04",
        "rate": "25%",
        "description": "Section 301 China Tariffs - seating products listed in U.S. note 20(g)",
        "applies_to": (
            "9401614011",
            "9401614031",
            "9401696011",
            "9401696031",
            "9401710008",
            "9401710011",
            "9401710031",
            "9401790006",
            "9401790011",
            "9401790015",
            "9401790025",
            "9401790035",
            "9401790046",
            "9401790050",
            "9401802005",
            "9401802011",
            "9401802031",
            "9401804004",
            "9401804006",
            "9401804015",
            "9401804026",
            "9401804035",
            "9401804046",
            "9401806024",
            "9401806025",
            "9401806028",
            "9401806030",
        ),
        "origin_keys": ("CN",),
        "source": "USITC China Tariffs current release; HTS 9903.88.04",
    },
    {
        "code": "9903.88.15",
        "rate": "7.5%",
        "description": "Section 301 China Tariffs - seating products listed in U.S. note 20(s)",
        "applies_to": ("9401696001", "9401710007", "94019115", "94019120", "94019910", "94019925"),
        "origin_keys": ("CN",),
        "source": "USITC China Tariffs current release; HTS 9903.88.15",
    },
    {
        "code": "9903.76.01",
        "rate": "10%",
        "description": "Section 232 wood products - softwood timber and lumber products",
        "applies_to": (
            "44031100",
            "44032101",
            "44032201",
            "44032301",
            "44032401",
            "44032501",
            "44032601",
            "44039901",
            "44061100",
            "44069100",
            "44071100",
            "44071200",
            "44071300",
            "44071400",
            "44071900",
        ),
        "source": "USITC HTS Chapter 99 U.S. note 37(b); HTS 9903.76.01",
    },
    {
        "code": "9903.76.02",
        "rate": "25%",
        "description": "Section 232 wood products - upholstered wooden furniture products",
        "applies_to": ("9401614011", "9401614031", "9401616011", "9401616031"),
        "source": "USITC HTS Chapter 99 U.S. note 37(d); HTS 9903.76.02",
        "origin_sensitive": True,
    },
    {
        "code": "9903.76.03",
        "rate": "25%",
        "description": "Section 232 wood products - kitchen cabinets, vanities, and parts",
        "applies_to": ("9403409060", "9403608093", "9403910080"),
        "source": "USITC HTS Chapter 99 U.S. note 37(f); HTS 9903.76.03",
        "origin_sensitive": True,
    },
    {
        "code": "9903.05.31",
        "rate": "12.5%",
        "description": "New Section 301 / U.S. note 52 - articles the product of China",
        "applies_to_all": True,
        "origin_keys": ("CN",),
        "source": "USITC HTS Chapter 99 U.S. note 52; HTS 9903.05.31",
    },
)

SECTION_232_WOOD_NON_COVERED_NOTES: tuple[dict[str, Any], ...] = (
    {
        "applies_to": ("9401696011", "9401696031"),
        "status": "not_covered",
        "description": (
            "Section 232 wood products: HTS 9401.69.60.11/31 are other seats with wooden "
            "frames, not upholstered. Current USITC Chapter 99 U.S. note 37(d) lists "
            "upholstered wooden furniture under HTS 9401.61.40.11/31 and 9401.61.60.11/31; "
            "note 37(f) lists kitchen cabinets, vanities, and parts under HTS 9403.40.90.60, "
            "9403.60.80.93, and 9403.91.00.80."
        ),
        "source": "USITC HTS Chapter 99 U.S. note 37(d) and 37(f)",
    },
)


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


def normalize_origin(value: Any) -> str | None:
    raw = str(value or "").upper()
    if "中国" in raw or "中國" in raw:
        return "CN"
    cleaned = re.sub(r"[^A-Z]", "", raw)
    if not cleaned:
        return None
    if cleaned in ORIGIN_ALIASES:
        return ORIGIN_ALIASES[cleaned]
    if cleaned in EU_ORIGIN_KEYS:
        return "EU"
    return cleaned[:2] if len(cleaned) == 2 else cleaned


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


def is_explicit_origin_key(origin_key: str | None) -> bool:
    return origin_key == "EU" or bool(origin_key and len(origin_key) == 2)


def static_additional_hts_details(
    digits: str,
    origin: Any = None,
    *,
    include_origin_conditions: bool = False,
) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    origin_key = normalize_origin(origin)
    for rule in STATIC_ADDITIONAL_HTS_RULES:
        origin_keys = rule.get("origin_keys")
        conditional_origin_keys: tuple[str, ...] = ()
        if origin_keys and origin_key not in origin_keys:
            if include_origin_conditions and not is_explicit_origin_key(origin_key) and not rule.get("applies_to_all"):
                conditional_origin_keys = tuple(str(key) for key in origin_keys)
            else:
                continue
        applies_to = rule.get("applies_to", ())
        matches_hts = bool(rule.get("applies_to_all")) or any(
            digits == target or (len(target) == 8 and digits.startswith(target))
            for target in applies_to
        )
        if matches_hts:
            detail = dict(
                SECTION_232_WOOD_ORIGIN_OVERRIDES.get(origin_key, rule)
                if rule.get("origin_sensitive")
                else rule
            )
            item = {
                "code": str(detail["code"]),
                "rate": str(detail["rate"]),
                "description": str(detail["description"]),
                "source": str(detail["source"]),
            }
            if conditional_origin_keys:
                item["origin_condition"] = ", ".join(conditional_origin_keys)
                item["condition"] = "Applies only when the country of origin is China"
            details.append(item)
    return details


def section_232_wood_notes(digits: str) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    for note in SECTION_232_WOOD_NON_COVERED_NOTES:
        if digits in note["applies_to"]:
            notes.append(
                {
                    "status": str(note["status"]),
                    "description": str(note["description"]),
                    "source": str(note["source"]),
                },
            )
    return notes


def build_lookup_result(code: str, records: list[dict[str, Any]], *, origin: Any = None) -> dict[str, Any]:
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

    additional_details: list[dict[str, str | None]] = []
    for record in path:
        for footnote in record.get("footnotes") or []:
            for match in ADDITIONAL_HTS_PATTERN.findall(str(footnote.get("value") or "")):
                if not any(item["code"] == match for item in additional_details):
                    additional_details.append(
                        {
                            "code": match,
                            "rate": None,
                            "description": "USITC footnote",
                            "source": "USITC HTS footnote",
                        },
                    )

    for detail in static_additional_hts_details(digits, origin, include_origin_conditions=True):
        if not any(item["code"] == detail["code"] for item in additional_details):
            additional_details.append(detail)

    return {
        "code": format_hts(digits),
        "description": description,
        "leaf_description": exact_description or None,
        "units": units,
        "required_units": " + ".join(units) or None,
        "general_rate": str(rate_record.get("general") or "").strip() or None,
        "special_rate": str(rate_record.get("special") or "").strip() or None,
        "column_2_rate": str(rate_record.get("other") or "").strip() or None,
        "additional_hts_codes": [str(item["code"]) for item in additional_details],
        "additional_hts_details": additional_details,
        "section_232_wood_notes": section_232_wood_notes(digits),
        "origin_key": normalize_origin(origin),
        "source": "USITC HTS REST API",
    }


def lookup_hts(code: str, *, origin: Any = None) -> dict[str, Any]:
    digits = hts_digits(code)
    keywords = [format_hts(digits[:length]) for length in (4, 6, 8, len(digits))]
    records: dict[str, dict[str, Any]] = {}
    for keyword in dict.fromkeys(keywords):
        for record in search_hts(keyword):
            key = str(record.get("htsno") or "")
            if key:
                records[key] = dict(record)
    return build_lookup_result(digits, list(records.values()), origin=origin)
