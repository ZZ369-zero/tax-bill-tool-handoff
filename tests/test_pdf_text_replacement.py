from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

from web_app.app import (
    PdfTextReplacement,
    apply_page_replacements,
    build_pdf_text_replacements,
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


if __name__ == "__main__":
    unittest.main()
