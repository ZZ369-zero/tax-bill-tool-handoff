from __future__ import annotations

import argparse
import base64
import getpass
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


DEFAULT_URL = "https://tax-bill-tool.onrender.com"
EXCLUDED_PDF_MARKERS = ("副本", "自动修改", "adjusted")


def discover_file(folder: Path, suffix: str, *, original_pdf: bool = False) -> Path:
    candidates = [
        path
        for path in folder.iterdir()
        if path.is_file()
        and path.suffix.lower() == suffix
        and not path.name.startswith("~$")
        and (
            not original_pdf
            or not any(marker.lower() in path.stem.lower() for marker in EXCLUDED_PDF_MARKERS)
        )
    ]
    if len(candidates) != 1:
        names = ", ".join(path.name for path in candidates) or "none"
        raise ValueError(f"Expected one {suffix} file in {folder}, found: {names}")
    return candidates[0]


def multipart_body(files: list[tuple[str, Path]]) -> tuple[bytes, str]:
    boundary = f"----TaxBillTool{uuid4().hex}"
    chunks: list[bytes] = []
    for field_name, path in files:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        safe_name = "tax-bill.pdf" if path.suffix.lower() == ".pdf" else "invoice.xlsx"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                (
                    f'Content-Disposition: form-data; name="{field_name}"; '
                    f'filename="{safe_name}"\r\n'
                ).encode("ascii"),
                f"Content-Type: {content_type}\r\n\r\n".encode("ascii"),
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), boundary


def unique_output_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"Unable to choose a unique output name near {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a new CBP 7501 PDF from worksheet 2 through the online tax tool."
    )
    parser.add_argument("folder", help="Folder containing the original PDF and two-sheet Excel workbook.")
    parser.add_argument("--pdf", help="Optional explicit original PDF path.")
    parser.add_argument("--excel", help="Optional explicit Excel path.")
    parser.add_argument("--output", help="Optional output PDF path. Existing files are never overwritten.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Tax tool base URL.")
    parser.add_argument("--username", default=os.getenv("TAX_TOOL_USERNAME"))
    parser.add_argument("--password", default=os.getenv("TAX_TOOL_PASSWORD"))
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    try:
        folder = Path(args.folder).expanduser().resolve()
        if not folder.is_dir():
            raise ValueError(f"Folder does not exist: {folder}")
        pdf_path = Path(args.pdf).expanduser().resolve() if args.pdf else discover_file(folder, ".pdf", original_pdf=True)
        excel_path = Path(args.excel).expanduser().resolve() if args.excel else discover_file(folder, ".xlsx")
        output_path = (
            Path(args.output).expanduser().resolve()
            if args.output
            else folder / f"{pdf_path.stem} - 自动修改.pdf"
        )
        output_path = unique_output_path(output_path)

        password = args.password
        if args.username and not password and sys.stdin.isatty():
            password = getpass.getpass("Online tax tool password: ")

        body, boundary = multipart_body(
            [("pdf_file", pdf_path), ("excel_file", excel_path)]
        )
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if args.username and password:
            token = base64.b64encode(f"{args.username}:{password}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        request = Request(
            args.url.rstrip("/") + "/api/generate-from-excel",
            data=body,
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=args.timeout) as response:
            output_path.write_bytes(response.read())
            sheet_name = response.headers.get("X-Excel-Sheet", "worksheet 2")
            change_count = response.headers.get("X-Modified-Fields", "unknown")
        print(f"Original PDF: {pdf_path}")
        print(f"Excel workbook: {excel_path}")
        print(f"Source sheet: {sheet_name}")
        print(f"Modified fields: {change_count}")
        print(f"Generated PDF: {output_path}")
        return 0
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except json.JSONDecodeError:
            pass
        print(f"Online generation failed ({exc.code}): {detail}", file=sys.stderr)
    except (URLError, OSError, ValueError) as exc:
        print(f"Generation failed: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())