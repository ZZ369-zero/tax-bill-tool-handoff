from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from tools.excel_adjustment import apply_second_sheet, read_second_sheet
from tools.excel_workflow import unique_output_path
from web_app.app import line_validation_errors, parser, recalculate


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class ExcelAdjustmentTests(unittest.TestCase):
    def test_output_path_never_overwrites_existing_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "tax-bill - 自动修改.pdf"
            output.write_bytes(b"existing")

            chosen = unique_output_path(output)

        self.assertEqual(chosen.name, "tax-bill - 自动修改 (2).pdf")

    def make_workbook(self, path: Path) -> None:
        workbook = Workbook()
        workbook.active.title = "Original"
        sheet = workbook.create_sheet("Adjusted")
        headers = [
            "序号",
            "HS 产品编码",
            "No. of Items 数量",
            "FOB Total Value(USD) 总价",
            "毛重（KG)",
            "净重（KG)",
            "毛重",
            "净重",
        ]
        for column, value in enumerate(headers, start=1):
            sheet.cell(row=4, column=column, value=value)
        sheet.append([None] * len(headers))
        sheet.append([1, "2222222222", 999, 110, None, None, 70, 55, 7])
        sheet.append([2, "1111111111", 12, 24, None, None, 110, 90, 11])
        workbook.save(path)

    def make_workbook_from_rows(self, path: Path, rows: list[dict[str, object]]) -> None:
        workbook = Workbook()
        workbook.active.title = "Original"
        sheet = workbook.create_sheet("Adjusted")
        headers = [
            "序号",
            "HS 产品编码",
            "No. of Items 数量",
            "FOB Total Value(USD) 总价",
            "毛重（KG)",
            "净重（KG)",
        ]
        for column, value in enumerate(headers, start=1):
            sheet.cell(row=4, column=column, value=value)
        sheet.append([None] * len(headers))
        for row in rows:
            sheet.append(
                [
                    row["sequence"],
                    row["hts"],
                    row["quantity"],
                    row["entered_value"],
                    row["gross_weight"],
                    row["net_weight"],
                ]
            )
        workbook.save(path)

    def load_fixture_json(self, case_name: str, file_name: str) -> object:
        path = FIXTURES_DIR / case_name / file_name
        return json.loads(path.read_text(encoding="utf-8"))

    def line_from_fixture(self, payload: dict[str, object]) -> object:
        return parser.TaxLine(
            file_role="fixture",
            source_file="fixture.pdf",
            pair_key="case_001_excel_adjustment",
            page=1,
            line_no=str(payload["line_no"]),
            hts=str(payload["hts"]),
            gross_weight=str(payload["gross_weight"]),
            gross_unit=str(payload["gross_unit"]),
            net_quantity=str(payload["net_quantity"]),
            net_unit=str(payload["net_unit"]),
            entered_value=str(payload["entered_value"]),
            rate=str(payload["rate"]),
            chapter_99_rates=str(payload["chapter_99_rates"]),
        )

    def fixture_document(self) -> object:
        return parser.TaxDocument(
            file_role="fixture",
            source_file="fixture.pdf",
            pair_key="case_001_excel_adjustment",
            pages=1,
            has_text_layer=True,
            fonts="/Helvetica",
            page_size="612.00x792.00",
        )

    def test_reads_second_sheet_and_selects_populated_weight_columns(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.xlsx"
            self.make_workbook(path)

            sheet_name, records = read_second_sheet(path)

        self.assertEqual(sheet_name, "Adjusted")
        self.assertEqual(records[0].gross_weight, "70")
        self.assertEqual(records[0].net_weight, "55")
        self.assertEqual(records[1].quantity, "12")
        self.assertEqual(records[1].entered_value, "24")

    def test_applies_values_by_hts_and_uses_net_weight_for_kg_lines(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.xlsx"
            self.make_workbook(path)
            lines = [
                parser.TaxLine(
                    file_role="test",
                    source_file="source.pdf",
                    pair_key="test",
                    page=1,
                    line_no="001",
                    hts="1111.11.1111",
                    gross_weight="100",
                    gross_unit="KG",
                    net_quantity="10",
                    net_unit="NO",
                    entered_value="20",
                ),
                parser.TaxLine(
                    file_role="test",
                    source_file="source.pdf",
                    pair_key="test",
                    page=1,
                    line_no="002",
                    hts="2222.22.2222",
                    gross_weight="60",
                    gross_unit="KG",
                    net_quantity="50",
                    net_unit="KG",
                    entered_value="100",
                ),
            ]

            result = apply_second_sheet(path, lines)

        self.assertEqual(lines[0].gross_weight, "110")
        self.assertEqual(lines[0].net_quantity, "12")
        self.assertEqual(lines[0].entered_value, "24")
        self.assertEqual(lines[1].gross_weight, "70")
        self.assertEqual(lines[1].net_quantity, "55")
        self.assertEqual(lines[1].entered_value, "110")
        self.assertEqual(result.matched_lines, 2)
        self.assertEqual(len(result.modified_fields), 6)

    def test_fixture_excel_adjustment_recalculates_expected_totals(self) -> None:
        case_name = "case_001_excel_adjustment"
        line_payloads = self.load_fixture_json(case_name, "input_lines.json")
        worksheet_rows = self.load_fixture_json(case_name, "worksheet2_rows.json")
        expected = self.load_fixture_json(case_name, "expected.json")
        lines = [self.line_from_fixture(payload) for payload in line_payloads]
        document = self.fixture_document()

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invoice.xlsx"
            self.make_workbook_from_rows(path, worksheet_rows)

            result = apply_second_sheet(path, lines)
            recalculate(document, lines, include_hmf=False)
            validation_errors = line_validation_errors(lines, result.modified_fields)

        self.assertEqual(validation_errors, [])
        self.assertEqual(result.sheet_name, expected["sheet_name"])
        self.assertEqual(result.matched_lines, expected["matched_lines"])
        self.assertEqual(len(result.modified_fields), expected["modified_field_count"])
        self.assertEqual(
            {
                "total_entered_value": document.total_entered_value,
                "calculated_duty_total": document.calculated_duty_total,
                "calculated_mpf_total": document.calculated_mpf_total,
                "calculated_other_total": document.calculated_other_total,
                "calculated_grand_total": document.calculated_grand_total,
            },
            expected["document"],
        )
        actual_lines = [
            {
                "line_no": line.line_no,
                "gross_weight": line.gross_weight,
                "net_quantity": line.net_quantity,
                "entered_value": line.entered_value,
                "calculated_duty_total": line.calculated_duty_total,
                "calculated_mpf_amount": line.calculated_mpf_amount,
            }
            for line in lines
        ]
        self.assertEqual(actual_lines, expected["lines"])


if __name__ == "__main__":
    unittest.main()
