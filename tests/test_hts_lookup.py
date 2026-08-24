from __future__ import annotations

import unittest

from tools.hts_lookup import (
    build_lookup_result,
    format_hts,
    normalized_units,
    normalize_origin,
    section_232_wood_notes,
    static_additional_hts_details,
)


class HtsLookupTests(unittest.TestCase):
    def test_inherits_rate_and_reads_multiple_required_units(self) -> None:
        records = [
            {
                "htsno": "3924.90",
                "description": "Other household articles, of plastics:",
                "general": None,
            },
            {
                "htsno": "3924.90.56",
                "description": "Other",
                "general": "3.4%",
                "footnotes": [{"value": "See 9903.88.15."}],
            },
            {
                "htsno": "3924.90.56.50",
                "description": "Other",
                "general": "",
                "units": ["No. and kg"],
            },
        ]

        result = build_lookup_result("3924905650", records)

        self.assertEqual(result["code"], "3924.90.56.50")
        self.assertEqual(result["general_rate"], "3.4%")
        self.assertEqual(result["units"], ["NO", "KG"])
        self.assertEqual(result["additional_hts_codes"], ["9903.88.15"])

    def test_formats_hts_and_units(self) -> None:
        self.assertEqual(format_hts("8414519090"), "8414.51.90.90")
        self.assertEqual(normalized_units(["No. and kg"]), ["NO", "KG"])

    def test_adds_section_232_wood_furniture_rule_without_footnote(self) -> None:
        records = [
            {
                "htsno": "9401.61.40",
                "description": "Upholstered",
                "general": "Free",
            },
            {
                "htsno": "9401.61.40.11",
                "description": "Household",
                "general": "",
                "units": ["No."],
                "footnotes": [],
            },
        ]

        result = build_lookup_result("9401614011", records)

        self.assertIn("9903.76.02", result["additional_hts_codes"])
        self.assertIn(
            {
                "code": "9903.76.02",
                "rate": "25%",
                "description": "Section 232 wood products - upholstered wooden furniture products",
                "source": "USITC HTS Chapter 99 U.S. note 37(d); HTS 9903.76.02",
            },
            result["additional_hts_details"],
        )

    def test_adds_section_232_kitchen_cabinet_rule_without_dropping_footnotes(self) -> None:
        records = [
            {
                "htsno": "9403.91",
                "description": "Parts",
                "general": "Free",
                "footnotes": [{"value": "See 9903.90.08."}],
            },
            {
                "htsno": "9403.91.00.80",
                "description": "Other",
                "general": "",
                "units": ["kg"],
            },
        ]

        result = build_lookup_result("9403910080", records)

        self.assertEqual(result["additional_hts_codes"], ["9903.90.08", "9903.76.03"])

    def test_adds_section_232_softwood_rule_for_eight_digit_provisions(self) -> None:
        records = [
            {
                "htsno": "4407.19.00",
                "description": "Other",
                "general": "Free",
                "units": ["m3"],
                "footnotes": [],
            },
        ]

        result = build_lookup_result("44071900", records)

        self.assertIn("9903.76.01", result["additional_hts_codes"])

    def test_adds_section_232_softwood_rule_for_statistical_suffixes(self) -> None:
        records = [
            {
                "htsno": "4407.19.00",
                "description": "Other",
                "general": "Free",
            },
            {
                "htsno": "4407.19.00.10",
                "description": "Other",
                "general": "",
                "units": ["m3"],
                "footnotes": [],
            },
        ]

        result = build_lookup_result("4407190010", records)

        self.assertIn("9903.76.01", result["additional_hts_codes"])

    def test_section_232_wood_current_note_37_covered_lists_are_complete(self) -> None:
        expected = {
            "9903.76.01": (
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
            "9903.76.02": ("9401614011", "9401614031", "9401616011", "9401616031"),
            "9903.76.03": ("9403409060", "9403608093", "9403910080"),
        }

        for chapter_99_code, ordinary_codes in expected.items():
            for ordinary_code in ordinary_codes:
                details = static_additional_hts_details(ordinary_code)
                self.assertIn(chapter_99_code, [detail["code"] for detail in details])

    def test_section_232_wood_country_specific_headings_are_origin_aware(self) -> None:
        self.assertEqual(normalize_origin("Japan"), "JP")
        self.assertEqual(normalize_origin("Germany"), "EU")
        self.assertEqual(normalize_origin("Taiwan"), "TW")
        self.assertEqual(normalize_origin("中国"), "CN")

        japan_details = static_additional_hts_details("9401614011", "Japan")
        taiwan_details = static_additional_hts_details("9403409060", "TW")
        softwood_details = static_additional_hts_details("44071900", "Japan")

        self.assertEqual(japan_details[0]["code"], "9903.76.21")
        self.assertEqual(japan_details[0]["rate"], "15%")
        self.assertEqual(taiwan_details[0]["code"], "9903.76.24")
        self.assertEqual(taiwan_details[0]["rate"], "15%")
        self.assertEqual(softwood_details[0]["code"], "9903.76.01")
        self.assertEqual(softwood_details[0]["rate"], "10%")

    def test_china_section_301_tariffs_apply_to_9401696011(self) -> None:
        records = [
            {
                "htsno": "9401.69.60",
                "description": "Other",
                "general": "Free",
            },
            {
                "htsno": "9401.69.60.11",
                "description": "Other household",
                "general": "",
                "units": ["No."],
                "footnotes": [],
            },
        ]

        result = build_lookup_result("9401696011", records, origin="CN")

        self.assertEqual(result["additional_hts_codes"], ["9903.88.04", "9903.05.31"])
        self.assertEqual(
            [detail["rate"] for detail in result["additional_hts_details"]],
            ["25%", "12.5%"],
        )

    def test_china_section_301_tariffs_do_not_apply_without_china_origin(self) -> None:
        details = static_additional_hts_details("9401696011", "JP")

        self.assertEqual(details, [])

    def test_china_section_301_seating_page_mappings_cover_current_release(self) -> None:
        expected = {
            "9903.88.03": ("94012000", "94016160", "94016980", "94019990"),
            "9903.88.04": ("9401614011", "9401696011", "9401806030"),
            "9903.88.15": ("9401696001", "9401710007", "94019925"),
        }

        for chapter_99_code, ordinary_codes in expected.items():
            for ordinary_code in ordinary_codes:
                details = static_additional_hts_details(ordinary_code, "China")
                self.assertIn(chapter_99_code, [detail["code"] for detail in details])

    def test_section_232_wood_notes_explain_non_upholstered_wooden_seats(self) -> None:
        records = [
            {
                "htsno": "9401.69.60",
                "description": "Other",
                "general": "Free",
            },
            {
                "htsno": "9401.69.60.11",
                "description": "Other household",
                "general": "",
                "units": ["No."],
                "footnotes": [],
            },
        ]

        result = build_lookup_result("9401696011", records)

        self.assertNotIn("9903.76.02", result["additional_hts_codes"])
        self.assertEqual(result["additional_hts_codes"], [])
        self.assertEqual(section_232_wood_notes("9401696011"), result["section_232_wood_notes"])
        self.assertIn("not upholstered", result["section_232_wood_notes"][0]["description"])


if __name__ == "__main__":
    unittest.main()
