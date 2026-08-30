from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from web_app.app import (
    include_hmf_for_transport,
    line_field_key,
    line_validation_errors,
    parsed_has_hmf,
    parser,
    recalculate,
    suppress_expected_modified_variances,
)


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
    def test_repairs_readable_fragments_with_content_stream_coordinates(self) -> None:
        readable = [
            parser.TextFragment(page=1, x=0, y=0, size=10, font="/Courier", text="NXG   0002503-4"),
            parser.TextFragment(page=1, x=0, y=0, size=10, font="/Courier", text="12.50%"),
        ]
        coordinates = [
            parser.TextFragment(page=1, x=24, y=707, size=10, font="/F5", text="1;*   0002503-4"),
            parser.TextFragment(page=1, x=429, y=349, size=10, font="/F5", text="\x14\x15\x11\x18\x13\x08"),
        ]

        repaired = parser.repair_fragment_coordinates(readable, coordinates)

        self.assertEqual(repaired[0].text, "NXG   0002503-4")
        self.assertEqual((repaired[0].x, repaired[0].y), (24, 707))
        self.assertEqual((repaired[1].x, repaired[1].y), (429, 349))

    def test_parses_new_template_line_without_currency_symbols(self) -> None:
        rows = [
            [
                parser.TextFragment(page=1, x=24, y=361.34, size=10, font="/Courier", text="001"),
                parser.TextFragment(page=1, x=61, y=361.34, size=10, font="/Courier", text=" PRDTS OF CHINA, NOTE 52"),
                parser.TextFragment(page=1, x=324, y=361.34, size=10, font="/Courier", text=" NOT-RELATED"),
            ],
            [
                parser.TextFragment(page=1, x=61, y=349.34, size=10, font="/Courier", text="9903.05.31"),
                parser.TextFragment(page=1, x=339, y=349.34, size=10, font="/Courier", text="      0"),
                parser.TextFragment(page=1, x=429, y=349.34, size=10, font="/Courier", text=" 12.50%"),
                parser.TextFragment(page=1, x=561, y=349.34, size=10, font="/Courier", text=" 46.88"),
            ],
            [
                parser.TextFragment(page=1, x=61, y=337.34, size=10, font="/Courier", text="PLAS,STATUETTES/OTHER ORNAMENT"),
            ],
            [
                parser.TextFragment(page=1, x=61, y=325.34, size=10, font="/Courier", text="3926.40.0090"),
                parser.TextFragment(page=1, x=207, y=325.34, size=10, font="/Courier", text=" 300"),
                parser.TextFragment(page=1, x=209.5, y=325.34, size=10, font="/Courier", text="          950 NO"),
                parser.TextFragment(page=1, x=339, y=325.34, size=10, font="/Courier", text="    375"),
                parser.TextFragment(page=1, x=432, y=325.34, size=10, font="/Courier", text=" 5.30%"),
                parser.TextFragment(page=1, x=561, y=325.34, size=10, font="/Courier", text=" 19.88"),
            ],
            [
                parser.TextFragment(page=1, x=339, y=313.34, size=10, font="/Courier", text="   C 0"),
            ],
            [
                parser.TextFragment(page=1, x=61, y=301.34, size=10, font="/Courier", text="Merchandise Processing Fee"),
                parser.TextFragment(page=1, x=429, y=301.34, size=10, font="/Courier", text=" 0.3464%"),
                parser.TextFragment(page=1, x=567, y=301.34, size=10, font="/Courier", text=" 1.30"),
            ],
        ]
        start = rows[0][0]

        line = parser.parse_line_rows(Path("new-template.pdf"), "original", "case", "NXG 0002503-4", start, rows)

        self.assertEqual(line.line_no, "001")
        self.assertEqual(line.hts, "3926.40.0090")
        self.assertEqual(line.gross_weight, "300")
        self.assertIsNone(line.gross_unit)
        self.assertEqual(line.net_quantity, "950")
        self.assertEqual(line.net_unit, "NO")
        self.assertEqual(line.entered_value, "375")
        self.assertEqual(line.rate, "5.30%")
        self.assertEqual(line.duty_amount, "19.88")
        self.assertEqual(line.chapter_99_codes, "9903.05.31")
        self.assertEqual(line.chapter_99_rates, "12.50%")
        self.assertEqual(line.chapter_99_amounts, "46.88")
        self.assertEqual(line.mpf_amount, "1.30")
        self.assertEqual(line.relationship, "N")

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

    def test_parses_entered_value_separately_from_adjacent_percent_rate(self) -> None:
        row = [
            parser.TextFragment(
                page=1,
                x=67.0,
                y=398.0,
                size=9.0,
                font="/Helvetica",
                text="3921.90.5050",
            ),
            parser.TextFragment(
                page=1,
                x=179.97,
                y=398.0,
                size=9.0,
                font="/Helvetica",
                text="35,573 KG",
            ),
            parser.TextFragment(
                page=1,
                x=260.97,
                y=398.0,
                size=9.0,
                font="/Helvetica",
                text="2,787.00 M2",
            ),
            parser.TextFragment(
                page=1,
                x=363.48,
                y=398.0,
                size=9.0,
                font="/Helvetica",
                text="$8,400",
            ),
            parser.TextFragment(
                page=1,
                x=399.0,
                y=398.0,
                size=9.0,
                font="/Helvetica",
                text="4.8%",
            ),
            parser.TextFragment(
                page=1,
                x=548.47,
                y=398.0,
                size=9.0,
                font="/Helvetica",
                text="$403.20",
            ),
        ]
        text = "3921.90.5050 35,573 KG 2,787.00 M2 $8,400 4.8% $403.20"

        parsed = parser.parse_main_hts_row(row, text, "3921.90.5050")

        self.assertEqual(parsed["entered_value"], "8,400")
        self.assertEqual(parsed["rate"], "4.8%")
        self.assertEqual(parsed["duty_amount"], "403.20")

    def test_normalizes_bl_or_awb_carrier_prefix_spacing(self) -> None:
        self.assertEqual(
            parser.normalize_bl_or_awb_number("COSU 6504318320, YSSZ26060511"),
            "COSU6504318320, YSSZ26060511",
        )

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

    def test_transport_mode_controls_hmf_calculation(self) -> None:
        document = tax_document()
        line = tax_line("001", "9699", "FREE", "", net_quantity="10", net_unit="K")

        self.assertFalse(parsed_has_hmf(document, [line]))
        self.assertFalse(include_hmf_for_transport(document, [line], "auto"))
        self.assertFalse(include_hmf_for_transport(document, [line], "air"))
        self.assertTrue(include_hmf_for_transport(document, [line], "ocean"))

        recalculate(document, [line], include_hmf=True)

        self.assertEqual(line.calculated_hmf_amount, "12.12")
        self.assertEqual(document.calculated_hmf_total, "12.12")

    def test_auto_transport_keeps_original_hmf_state(self) -> None:
        document = tax_document()
        document.hmf_total = "8.26"
        line = tax_line("001", "9699", "FREE", "", net_quantity="10", net_unit="K")

        self.assertTrue(parsed_has_hmf(document, [line]))
        self.assertTrue(include_hmf_for_transport(document, [line], "auto"))

    def test_document_mpf_uses_sum_of_rounded_line_item_mpf_records(self) -> None:
        lines = [
            tax_line("001", "1452", "FREE", "", net_quantity="726", net_unit="NO"),
            tax_line("002", "683", "FREE", "", net_quantity="650", net_unit="NO"),
            tax_line("003", "583", "FREE", "", net_quantity="15", net_unit="NO"),
            tax_line("004", "4200", "FREE", "", net_quantity="60", net_unit="NO"),
            tax_line("005", "1102", "FREE", "", net_quantity="7344", net_unit="NO"),
            tax_line("006", "548", "FREE", "", net_quantity="876.48", net_unit="KG"),
            tax_line("007", "3153", "FREE", "", net_quantity="31533", net_unit="NO"),
        ]
        document = tax_document()

        recalculate(document, lines, include_hmf=True)
        line_mpf_total = sum(
            parser.parse_decimal(line.calculated_mpf_amount) or Decimal("0")
            for line in lines
        )

        self.assertEqual(document.calculated_mpf_total, "40.61")
        self.assertEqual(line_mpf_total, Decimal("40.61"))
        self.assertEqual(lines[1].calculated_mpf_amount, "2.37")

    def test_modified_line_variance_is_not_reported_against_original_pdf(self) -> None:
        document = tax_document()
        document.duty_total = "2,006.95"
        document.other_total = "33.58"
        document.grand_total = "2,040.53"
        line = tax_line("001", "2850", "FREE", "25%; 12.50%", net_quantity="1500", net_unit="NO")
        line.duty_amount = "956.25"
        unchanged_line = tax_line("002", "2660", "2%", "25%; 12.50%", net_quantity="1400", net_unit="NO")
        unchanged_line.duty_amount = "1,050.70"

        lines = [line, unchanged_line]

        recalculate(document, lines, include_hmf=False)
        self.assertEqual(line.calculated_duty_total, "1,068.75")
        self.assertEqual(unchanged_line.calculated_duty_total, "1,050.70")
        self.assertEqual(line.duty_variance, "-112.50")
        self.assertEqual(unchanged_line.duty_variance, "0.00")
        self.assertEqual(document.grand_total_variance, "-112.50")

        suppress_expected_modified_variances(
            document,
            lines,
            {line_field_key(line, "entered_value")},
        )

        self.assertIsNone(line.duty_variance)
        self.assertIsNone(line.mpf_variance)
        self.assertEqual(unchanged_line.duty_variance, "0.00")
        self.assertIsNone(document.duty_variance)
        self.assertIsNone(document.grand_total_variance)


if __name__ == "__main__":
    unittest.main()
