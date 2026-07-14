from __future__ import annotations

from decimal import Decimal
import unittest

from web_app.app import line_field_key, line_validation_errors, parser, recalculate


def tax_line(
    line_no: str,
    entered_value: str,
    rate: str,
    chapter_rates: str,
    *,
    net_quantity: str,
    net_unit: str,
) -> object:
    return parser.TaxLine(
        file_role="test",
        source_file="sample.pdf",
        pair_key="sample",
        page=1,
        line_no=line_no,
        entered_value=entered_value,
        rate=rate,
        chapter_99_rates=chapter_rates,
        net_quantity=net_quantity,
        net_unit=net_unit,
    )


def tax_document() -> object:
    return parser.TaxDocument(
        file_role="test",
        source_file="sample.pdf",
        pair_key="sample",
        pages=2,
        has_text_layer=True,
        fonts="/Helvetica",
        page_size="612.00x792.00",
        invoice_value="4,164.00",
        invoice_entered_value="4,164.00",
    )


class CbpCalculationTests(unittest.TestCase):
    def test_parses_reporting_unit_with_digit_from_hts_row(self) -> None:
        text = "7007.19.0000 157 KG 39.44 M2 $3,200 5% $160.00"
        row = [
            parser.TextFragment(
                page=2,
                x=67.0,
                y=600.0,
                size=9.0,
                font="/Helvetica",
                text=text,
            )
        ]

        parsed = parser.parse_main_hts_row(row, text, "7007.19.0000")

        self.assertEqual(parsed["gross_weight"], "157")
        self.assertEqual(parsed["gross_unit"], "KG")
        self.assertEqual(parsed["net_quantity"], "39.44")
        self.assertEqual(parsed["net_unit"], "M2")
        self.assertEqual(parsed["entered_value"], "3,200")
        self.assertEqual(parsed["rate"], "5%")
        self.assertEqual(parsed["duty_amount"], "160.00")

    def test_131_80755312_uses_whole_dollar_line_values(self) -> None:
        lines = [
            tax_line("001", "1992", "4.7%", "10%", net_quantity="400", net_unit="NO"),
            tax_line("002", "243", "FREE", "25%; 10%", net_quantity="10", net_unit="NO"),
            tax_line("003", "704", "3.4%", "7.5%; 10%", net_quantity="880", net_unit="NO"),
            tax_line("004", "2637.84", "FREE", "10%", net_quantity="536", net_unit="KG"),
        ]
        document = tax_document()

        recalculate(document, lines, include_hmf=False)

        self.assertEqual(lines[3].entered_value, "2,638")
        self.assertEqual(lines[3].calculated_chapter_99_duty, "263.80")
        self.assertEqual(lines[3].calculated_mpf_amount, "9.14")
        self.assertEqual(document.total_entered_value, "5,577.00")
        self.assertEqual(document.calculated_duty_total, "788.81")
        self.assertEqual(document.calculated_other_total, "33.58")
        self.assertEqual(document.calculated_grand_total, "822.39")

    def test_rejects_net_kg_above_gross_kg_for_modified_line(self) -> None:
        line = tax_line("004", "2637.84", "FREE", "10%", net_quantity="696", net_unit="KG")
        line.gross_weight = "650"
        line.gross_unit = "KG"

        errors = line_validation_errors([line], {line_field_key(line, "net_quantity")})

        self.assertEqual(
            errors,
            ["Line 004: net quantity 696 KG exceeds gross weight 650 KG"],
        )

    def test_compound_percent_and_specific_rate_uses_matching_unit(self) -> None:
        duty = parser.calculate_duty_for_rate(
            Decimal("1000"),
            "5.7% + 1.7\u00a2/kg",
            net_quantity="100",
            net_unit="KG",
        )
        mismatched = parser.calculate_duty_for_rate(
            Decimal("1000"),
            "5.7% + 1.7\u00a2/kg",
            net_quantity="100",
            net_unit="NO",
        )

        self.assertEqual(duty, Decimal("58.70"))
        self.assertIsNone(mismatched)


if __name__ == "__main__":
    unittest.main()
