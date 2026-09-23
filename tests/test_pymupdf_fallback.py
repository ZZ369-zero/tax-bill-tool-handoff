from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from web_app.app import parser


class PyMuPdfFallbackTests(unittest.TestCase):
    def make_pdf(self, path: Path) -> None:
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(595.28, 841.89))
        pdf.setFont("Helvetica", 10)
        pdf.drawString(72, 700, "ENTRY SUMMARY")
        pdf.drawString(72, 680, "1. Filer Code/Entry Number")
        pdf.drawString(72, 660, "933-35102502")
        pdf.drawString(72, 620, "32. Description of Merchandise")
        pdf.drawString(72, 600, "001 ARTICLE OF CHINA,US NTE 20(F)")
        pdf.drawString(72, 580, "3924.90.5650")
        pdf.save()
        path.write_bytes(buffer.getvalue())

    def test_pymupdf_extracts_text_and_pdf_coordinates(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.pdf"
            self.make_pdf(source)

            fragments = parser.extract_pymupdf_fragments(source)

        entry_summary = next(fragment for fragment in fragments if "ENTRY SUMMARY" in fragment.text)
        self.assertEqual(entry_summary.page, 1)
        self.assertGreater(entry_summary.y, 690)
        self.assertLess(entry_summary.y, 705)

    def test_extract_fragments_uses_pymupdf_when_pypdf_sources_are_empty(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.pdf"
            self.make_pdf(source)
            reader = PdfReader(str(source))

            with patch.object(parser, "extract_content_stream_fragments", return_value=[]), patch.object(
                parser,
                "extract_visitor_fragments",
                return_value=[],
            ):
                fragments = parser.extract_fragments(reader, source)

        self.assertTrue(any("ENTRY SUMMARY" in fragment.text for fragment in fragments))

    def test_extract_fragments_prefers_source_with_complete_line_items(self) -> None:
        with TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.pdf"
            self.make_pdf(source)
            reader = PdfReader(str(source))

            content_fragments = [
                parser.TextFragment(page=2, x=23, y=662, size=8, font="Courier", text="2"),
                parser.TextFragment(
                    page=2,
                    x=70,
                    y=646,
                    size=8,
                    font="Courier",
                    text="8516.79.0000      558.24            1400NO",
                ),
            ]
            visitor_fragments = [
                parser.TextFragment(page=1, x=2, y=108, size=7, font="Courier", text="1"),
                parser.TextFragment(
                    page=1,
                    x=2,
                    y=94,
                    size=7,
                    font="Courier",
                    text="3924.10.4000           394.76            1200NO,394.76KG",
                ),
                parser.TextFragment(page=1, x=24, y=291, size=8, font="Courier", text="Other Fee Summary"),
                parser.TextFragment(page=2, x=23, y=662, size=8, font="Courier", text="2"),
                parser.TextFragment(
                    page=2,
                    x=70,
                    y=646,
                    size=8,
                    font="Courier",
                    text="8516.79.0000      558.24            1400NO",
                ),
            ]
            pymupdf_fragments = [
                parser.TextFragment(page=1, x=23, y=408, size=7, font="Courier", text="1"),
                parser.TextFragment(
                    page=1,
                    x=63,
                    y=394,
                    size=7,
                    font="Courier",
                    text="3924.10.4000           394.76            1200NO,394.76KG",
                ),
                parser.TextFragment(page=1, x=24, y=291, size=8, font="Courier", text="Other Fee Summary"),
                parser.TextFragment(page=2, x=23, y=662, size=8, font="Courier", text="2"),
                parser.TextFragment(
                    page=2,
                    x=70,
                    y=646,
                    size=8,
                    font="Courier",
                    text="8516.79.0000      558.24            1400NO",
                ),
            ]

            with patch.object(parser, "extract_content_stream_fragments", return_value=content_fragments), patch.object(
                parser,
                "extract_visitor_fragments",
                return_value=visitor_fragments,
            ), patch.object(parser, "extract_pymupdf_fragments", return_value=pymupdf_fragments):
                fragments = parser.extract_fragments(reader, source)

        self.assertIs(fragments, pymupdf_fragments)
        self.assertEqual(parser.fragment_line_start_count(fragments), 2)


if __name__ == "__main__":
    unittest.main()
