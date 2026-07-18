from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import web_app.app as webapp


class UploadCleanupTests(unittest.TestCase):
    def test_cleanup_removes_only_expired_temporary_uploads(self) -> None:
        now = 2_000_000.0
        with TemporaryDirectory() as temp_dir:
            upload_dir = Path(temp_dir)
            old_pdf = upload_dir / "old.pdf"
            old_xlsx = upload_dir / "old.xlsx"
            old_txt = upload_dir / "old.txt"
            fresh_pdf = upload_dir / "fresh.pdf"
            for path in (old_pdf, old_xlsx, old_txt, fresh_pdf):
                path.write_text("temporary", encoding="utf-8")
            old_time = now - (25 * 60 * 60)
            fresh_time = now - (2 * 60 * 60)
            for path in (old_pdf, old_xlsx, old_txt):
                os.utime(path, (old_time, old_time))
            os.utime(fresh_pdf, (fresh_time, fresh_time))

            with patch.object(webapp, "UPLOAD_DIR", upload_dir), patch.object(
                webapp,
                "UPLOAD_RETENTION_SECONDS",
                24 * 60 * 60,
            ):
                removed = webapp.cleanup_old_uploads(now=now)

            self.assertEqual(removed, 2)
            self.assertFalse(old_pdf.exists())
            self.assertFalse(old_xlsx.exists())
            self.assertTrue(old_txt.exists())
            self.assertTrue(fresh_pdf.exists())


if __name__ == "__main__":
    unittest.main()
