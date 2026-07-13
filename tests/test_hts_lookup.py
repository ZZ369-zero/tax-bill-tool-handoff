from __future__ import annotations

import unittest

from tools.hts_lookup import build_lookup_result, format_hts, normalized_units


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


if __name__ == "__main__":
    unittest.main()
