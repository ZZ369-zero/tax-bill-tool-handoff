from __future__ import annotations

from pathlib import Path
import unittest


STATIC_DIR = Path(__file__).resolve().parents[1] / "web_app" / "static"


class ExcelUploadUiTests(unittest.TestCase):
    def test_excel_generation_form_is_available(self) -> None:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn('id="excel-form"', html)
        self.assertIn('id="excel-pdf-file"', html)
        self.assertIn('id="excel-file"', html)
        self.assertIn("按 Excel 表2生成新税单", html)

    def test_excel_generation_javascript_calls_api(self) -> None:
        script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("async function generateFromExcel", script)
        self.assertIn('fetch("/api/generate-from-excel"', script)
        self.assertIn("responseFileName(response)", script)


if __name__ == "__main__":
    unittest.main()
