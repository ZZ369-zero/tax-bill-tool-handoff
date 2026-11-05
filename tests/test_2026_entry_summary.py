from io import BytesIO
from pathlib import Path
import unittest

from pypdf import PdfReader, PdfWriter

from web_app.app import parser


class EntrySummary2026Tests(unittest.TestCase):
    def test_header_and_invoice_boxes(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.add_blank_page(width=595, height=842)
        buffer = BytesIO()
        writer.write(buffer)
        reader = PdfReader(BytesIO(buffer.getvalue()))
        fragment = lambda page, x, y, text: parser.TextFragment(
            page=page, x=x, y=y, size=9, font="Helvetica", text=text,
        )
        fragments = [
            fragment(1, 424, 760, "Summary Status"),
            fragment(1, 25, 312, "Other Fee Summary"),
            fragment(1, 25, 20, "CBP Form 7501 (02/26)"),
            fragment(1, 33, 721, "01O-0000362-6"),
            fragment(1, 146, 721, "01 ABI/A"),
            fragment(1, 198, 721, "11/12/26"),
            fragment(1, 398, 721, "3901"),
            fragment(1, 446, 721, "11/04/2026"),
            fragment(1, 168, 698, "40"),
            fragment(1, 304, 698, "CN"),
            fragment(1, 168, 673, "KRDONRUI1003DON"),
            fragment(1, 304, 674, "KR"),
            fragment(1, 168, 578, "SAME"),
            fragment(1, 304, 578, "43-256589800"),
            fragment(1, 304, 554, "JORWELL LLC"),
            fragment(1, 304, 545, "1 RIDGEGATE DR STE 205"),
            fragment(1, 47, 421, "Invoice Number 001/01-272-76224492"),
            fragment(1, 120, 300, "$34.58"),
            fragment(2, 250, 573, "6,330.00"),
            fragment(2, 250, 561, "6,330.00"),
        ]

        doc = parser.parse_header_document(Path("sample.pdf"), "original", "x", reader, "", fragments)

        self.assertEqual(doc.entry_type, "01 ABI/A")
        self.assertEqual(doc.summary_date, "11/12/26")
        self.assertEqual(doc.port_code, "3901")
        self.assertEqual(doc.manufacturer_id, "KRDONRUI1003DON")
        self.assertEqual(doc.mpf_total, "34.58")
        self.assertEqual(doc.invoice_number, "001/01-272-76224492")
        self.assertEqual(doc.invoice_value, "6,330.00")
        self.assertEqual(doc.invoice_entered_value, "6,330.00")
        self.assertEqual(doc.importer_of_record, "JORWELL LLC 1 RIDGEGATE DR STE 205")


if __name__ == "__main__":
    unittest.main()
