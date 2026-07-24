from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream, DecodedStreamObject, NameObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from web_app.app import (
    PdfTextReplacement,
    apply_page_replacements,
    build_pdf_text_replacements,
    line_field_key,
    quantity_text,
    values_equal,
)


class PdfTextReplacementTests(unittest.TestCase):
    def make_pdf(self, path: Path) -> None:
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(612, 792))
        pdf.setFont("Helvetica", 9)
        pdf.drawString(520, 500, "$100.00")
        pdf.drawString(72, 480, "UNCHANGED")
        pdf.save()
        path.write_bytes(buffer.getvalue())

    def make_combined_line_pdf(self, path: Path) -> None:
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(612, 792))
        pdf.setFont("Helvetica", 9)
        pdf.drawString(67, 398, "3924.90.5650 2,048 KG 3,021.00 NO $1,511 3.4% $51.37")
        pdf.save()
        path.write_bytes(buffer.getvalue())

    def make_near_boundary_pdf(self, path: Path) -> None:
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(612, 792))
        pdf.setFont("Helvetica", 9)
        pdf.drawString(184.98, 398, "2,048 KG")
        pdf.save()
        path.write_bytes(buffer.getvalue())

    def make_tj_pdf(self, path: Path) -> None:
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(612, 792))
        pdf.setFont("Helvetica", 9)
        pdf.drawString(72, 480, "ANCHOR")
        pdf.save()
        source = path.parent / "base.pdf"
        source.write_bytes(buffer.getvalue())

        writer = PdfWriter(clone_from=str(source))
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 9 Tf 1 0 0 1 272.98 398 Tm [(15.45 KG)] TJ ET\n")
        writer.pages[0][NameObject("/Contents")] = writer._add_object(stream)
        with path.open("wb") as output:
            writer.write(output)

    def make_invoice_footer_pdf(self, path: Path) -> None:
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(612, 792))
        pdf.setFont("Helvetica", 9)
        pdf.drawString(253.47, 436.33, "9,334.00 USD")
        pdf.save()
        path.write_bytes(buffer.getvalue())

    def text_position(self, path: Path, target: str) -> tuple[float, float]:
        reader = PdfReader(str(path))
        content = ContentStream(reader.pages[0].get_contents(), reader)
        current_tm = None
        for operands, operator in content.operations:
            if operator == b"Tm":
                current_tm = operands
                continue
            if operator not in (b"Tj", b"TJ") or current_tm is None or not operands:
                continue
            if operator == b"TJ":
                text = "".join(str(item) for item in operands[0] if isinstance(item, str))
            else:
                text = str(operands[0])
            if text == target:
                return float(current_tm[4]), float(current_tm[5])
        raise AssertionError(f"Text not found: {target}")

    def test_replaces_only_targeted_text_object(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            output = Path(temp_dir) / "output.pdf"
            self.make_pdf(source)

            writer = PdfWriter(clone_from=str(source))
            replacement = PdfTextReplacement(
                page=1,
                field="duty total",
                old_text="$100.00",
                new_text="$125.00",
                x_min=500,
                x_max=590,
                y=500,
            )
            applied = apply_page_replacements(writer.pages[0], writer, [replacement])
            with output.open("wb") as stream:
                writer.write(stream)

            text = PdfReader(str(output)).pages[0].extract_text()
            self.assertEqual(applied, [replacement])
            self.assertIn("$125.00", text)
            self.assertNotIn("$100.00", text)
            self.assertIn("UNCHANGED", text)

    def test_quantity_keeps_original_precision_and_unit(self) -> None:
        self.assertEqual(quantity_text("840", "NO", "588.00"), "840.00 NO")
        self.assertEqual(quantity_text("1200.5", "KG", "500.00"), "1,200.50 KG")

    def test_numeric_comparison_ignores_money_formatting(self) -> None:
        self.assertTrue(values_equal("2,533", "2533.00"))
        self.assertFalse(values_equal("2,533", "2,785"))

    def test_no_modified_fields_returns_without_reading_pdf(self) -> None:
        replacements = build_pdf_text_replacements(
            Path("missing-original.pdf"),
            document=None,
            lines=[],
            modified_fields=[],
        )
        self.assertEqual(replacements, [])

    def test_replaces_field_inside_combined_line_text_object(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            output = Path(temp_dir) / "output.pdf"
            self.make_combined_line_pdf(source)

            writer = PdfWriter(clone_from=str(source))
            replacements = [
                PdfTextReplacement(
                    page=1,
                    field="line 001 gross weight",
                    old_text="2,048 KG",
                    new_text="2,087 KG",
                    x_min=185,
                    x_max=235,
                    y=398,
                ),
                PdfTextReplacement(
                    page=1,
                    field="line 001 entered value",
                    old_text="$1,511",
                    new_text="$3,414",
                    x_min=350,
                    x_max=398,
                    y=398,
                ),
            ]

            applied = apply_page_replacements(writer.pages[0], writer, replacements)
            with output.open("wb") as stream:
                writer.write(stream)

            text = PdfReader(str(output)).pages[0].extract_text()
            self.assertEqual(applied, replacements)
            self.assertIn("2,087 KG", text)
            self.assertIn("$3,414", text)
            self.assertNotIn("2,048 KG", text)
            self.assertNotIn("$1,511", text)

    def test_replaces_text_slightly_outside_coordinate_boundary(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            output = Path(temp_dir) / "output.pdf"
            self.make_near_boundary_pdf(source)

            writer = PdfWriter(clone_from=str(source))
            replacement = PdfTextReplacement(
                page=1,
                field="line 001 gross weight",
                old_text="2,048 KG",
                new_text="2,087 KG",
                x_min=185,
                x_max=235,
                y=398,
            )

            applied = apply_page_replacements(writer.pages[0], writer, [replacement])
            with output.open("wb") as stream:
                writer.write(stream)

            text = PdfReader(str(output)).pages[0].extract_text()
            self.assertEqual(applied, [replacement])
            self.assertIn("2,087 KG", text)
            self.assertNotIn("2,048 KG", text)

    def test_replaces_tj_text_array_without_overlay(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            output = Path(temp_dir) / "output.pdf"
            self.make_tj_pdf(source)

            writer = PdfWriter(clone_from=str(source))
            replacement = PdfTextReplacement(
                page=1,
                field="line 001 net quantity",
                old_text="15.45 KG",
                new_text="1.44 KG",
                x_min=235,
                x_max=335,
                y=398,
            )

            applied = apply_page_replacements(writer.pages[0], writer, [replacement])
            with output.open("wb") as stream:
                writer.write(stream)

            text = PdfReader(str(output)).pages[0].extract_text()
            self.assertEqual(applied, [replacement])
            self.assertIn("1.44 KG", text)
            self.assertNotIn("15.45 KG", text)

    def test_right_aligned_replacement_preserves_original_right_edge(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            output = Path(temp_dir) / "output.pdf"
            self.make_invoice_footer_pdf(source)
            old_text = "9,334.00 USD"
            new_text = "12,334.00 USD"
            old_x, old_y = self.text_position(source, old_text)
            old_right = old_x + pdfmetrics.stringWidth(old_text, "Helvetica", 9)
            expected_x = old_right - pdfmetrics.stringWidth(new_text, "Helvetica", 9)

            writer = PdfWriter(clone_from=str(source))
            replacement = PdfTextReplacement(
                page=1,
                field="invoice value",
                old_text=old_text,
                new_text=new_text,
                x_min=200,
                x_max=390,
                y=old_y,
            )

            applied = apply_page_replacements(writer.pages[0], writer, [replacement])
            with output.open("wb") as stream:
                writer.write(stream)
            new_x, _ = self.text_position(output, new_text)

        self.assertEqual(applied, [replacement])
        self.assertLess(new_x, old_x)
        self.assertAlmostEqual(new_x, expected_x, places=2)

    def test_invoice_footer_replacements_are_right_aligned(self) -> None:
        original_document = SimpleNamespace(
            pages=1,
            total_entered_value="9334",
            mpf_total="32.33",
            hmf_total=None,
            total_other_fees="32.33",
            other_total="32.33",
            duty_total="2296.16",
            grand_total="2328.49",
            invoice_value="9334",
            invoice_entered_value="9334",
        )
        document = SimpleNamespace(
            total_entered_value="12334",
            calculated_mpf_total="42.72",
            calculated_hmf_total=None,
            calculated_other_total="42.72",
            calculated_duty_total="3034.16",
            calculated_grand_total="3076.88",
            invoice_value="12334",
            invoice_entered_value="12334",
        )
        original_line = SimpleNamespace(
            page=1,
            line_no="001",
            hts="6115.96.9020",
            gross_weight="9203",
            gross_unit="KG",
            net_quantity="25997",
            net_unit="DPR",
            entered_value="9334",
            rate="14.6%",
            duty_amount="1362.76",
            chapter_99_amounts="933.40",
            mpf_amount="32.33",
            hmf_amount=None,
        )
        line = SimpleNamespace(
            page=1,
            line_no="001",
            hts="6115.96.9020",
            gross_weight="9203",
            gross_unit="KG",
            net_quantity="34260",
            net_unit="DPR",
            entered_value="12334",
            rate="14.6%",
            chapter_99_rates="10%",
            calculated_base_duty="1800.76",
            calculated_mpf_amount="42.72",
            calculated_hmf_amount=None,
        )
        parsed = SimpleNamespace(document=original_document, lines=[original_line])

        with patch("web_app.app.parser.parse_pdf", return_value=parsed), patch(
            "web_app.app.original_line_targets",
            return_value={
                (1, "001"): {
                    "original": original_line,
                    "hts_y": 360,
                    "chapter_ys": [350],
                    "mpf_y": 330,
                    "hmf_y": None,
                }
            },
        ):
            replacements = build_pdf_text_replacements(
                Path("source.pdf"),
                document,
                [line],
                [line_field_key(line, "entered_value")],
            )

        footer_replacements = {
            replacement.field: replacement
            for replacement in replacements
            if replacement.field in {"invoice value", "invoice entered value"}
        }
        self.assertEqual(footer_replacements["invoice value"].alignment, "right")
        self.assertEqual(footer_replacements["invoice entered value"].alignment, "right")


if __name__ == "__main__":
    unittest.main()
