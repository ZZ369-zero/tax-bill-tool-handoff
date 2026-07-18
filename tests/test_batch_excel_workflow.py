from __future__ import annotations

from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError

from tools.batch_excel_workflow import candidate_folders, error_detail, resolve_case
from tools.excel_workflow import discover_file


class BatchExcelWorkflowTests(unittest.TestCase):
    def test_original_pdf_discovery_excludes_adjusted_copies(self) -> None:
        with TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            original = folder / "131-80596740 税单.pdf"
            original.write_bytes(b"original")
            for name in (
                "131-80596740 税单 - 副本.pdf",
                "131-80596740 税单 更新.pdf",
                "131-80596740 税单 - 自动修改.pdf",
            ):
                (folder / name).write_bytes(b"adjusted")

            chosen = discover_file(folder, ".pdf", original_pdf=True)

        self.assertEqual(chosen, original)

    def test_candidate_folders_include_entry_directories(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entry = root / "7月" / "7-1" / "131-80596740"
            entry.mkdir(parents=True)
            (entry / "131-80596740 税单.pdf").write_bytes(b"pdf")
            (entry / "131-80596740-Sample Commercial Invoice & Packing List.xlsx").write_bytes(b"xlsx")

            folders = candidate_folders(root)

        self.assertIn(entry, folders)

    def test_resolve_case_chooses_default_output_name(self) -> None:
        with TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir) / "131-80596740"
            folder.mkdir()
            (folder / "131-80596740 税单.pdf").write_bytes(b"pdf")
            (folder / "131-80596740-Sample Commercial Invoice & Packing List.xlsx").write_bytes(b"xlsx")
            args = SimpleNamespace(output_name=None, skip_existing=True)

            case = resolve_case(folder, args)

        self.assertEqual(case.entry, "131-80596740")
        self.assertEqual(case.output_path.name, "131-80596740 税单 - 自动修改.pdf")

    def test_empty_http_error_detail_falls_back_to_status(self) -> None:
        exc = HTTPError(
            url="https://example.test",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=BytesIO(b""),
        )

        self.assertEqual(error_detail(exc), "HTTP 401 Unauthorized")


if __name__ == "__main__":
    unittest.main()
