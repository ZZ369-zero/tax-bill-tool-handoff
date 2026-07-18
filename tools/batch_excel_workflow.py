from __future__ import annotations

import argparse
import base64
import csv
from dataclasses import dataclass
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from tools.excel_workflow import (
        DEFAULT_URL,
        discover_file,
        multipart_body,
        unique_output_path,
    )
except ModuleNotFoundError:  # Allows running as python .\tools\batch_excel_workflow.py
    from excel_workflow import (  # type: ignore
        DEFAULT_URL,
        discover_file,
        multipart_body,
        unique_output_path,
    )


ENTRY_PATTERN = re.compile(r"\d{3}-\d{8}")


@dataclass(frozen=True)
class CaseFiles:
    folder: Path
    entry: str
    pdf_path: Path
    excel_path: Path
    output_path: Path


@dataclass(frozen=True)
class CaseResult:
    status: str
    entry: str
    folder: str
    pdf: str
    excel: str
    output: str
    detail: str
    matched_lines: str = ""
    modified_fields: str = ""
    sheet_name: str = ""


def entry_from_path(path: Path) -> str:
    for part in reversed(path.parts):
        match = ENTRY_PATTERN.search(part)
        if match:
            return match.group(0)
    match = ENTRY_PATTERN.search(str(path))
    return match.group(0) if match else path.name


def wanted_entry(entry: str, args: argparse.Namespace) -> bool:
    if args.entry and entry not in args.entry:
        return False
    if args.entry_pattern and not re.search(args.entry_pattern, entry):
        return False
    if args.from_entry and entry < args.from_entry:
        return False
    if args.to_entry and entry > args.to_entry:
        return False
    return True


def candidate_folders(root: Path) -> list[Path]:
    folders = {root}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".pdf", ".xlsx"} and not path.name.startswith("~$"):
            folders.add(path.parent)
    return sorted(folders)


def has_case_files(folder: Path) -> bool:
    files = [path for path in folder.iterdir() if path.is_file() and not path.name.startswith("~$")]
    has_pdf = any(path.suffix.lower() == ".pdf" for path in files)
    has_excel = any(path.suffix.lower() == ".xlsx" for path in files)
    return has_pdf and has_excel


def resolve_case(folder: Path, args: argparse.Namespace) -> CaseFiles:
    pdf_path = discover_file(folder, ".pdf", original_pdf=True)
    excel_path = discover_file(folder, ".xlsx")
    entry = entry_from_path(folder)
    output_name = args.output_name or f"{pdf_path.stem} - 自动修改.pdf"
    output_path = folder / output_name
    if output_path.exists() and args.skip_existing:
        raise FileExistsError(f"Output already exists: {output_path.name}")
    if output_path.exists():
        output_path = unique_output_path(output_path)
    return CaseFiles(
        folder=folder,
        entry=entry,
        pdf_path=pdf_path,
        excel_path=excel_path,
        output_path=output_path,
    )


def authorization_header(username: str | None, password: str | None) -> dict[str, str]:
    if not username or not password:
        return {}
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def generate_case(case: CaseFiles, args: argparse.Namespace, headers: dict[str, str]) -> CaseResult:
    if args.dry_run:
        return CaseResult(
            status="ready",
            entry=case.entry,
            folder=str(case.folder),
            pdf=case.pdf_path.name,
            excel=case.excel_path.name,
            output=case.output_path.name,
            detail="Dry run only; no files uploaded and no output written.",
        )

    body, boundary = multipart_body([("pdf_file", case.pdf_path), ("excel_file", case.excel_path)])
    request_headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", **headers}
    request = Request(
        args.url.rstrip("/") + "/api/generate-from-excel",
        data=body,
        headers=request_headers,
        method="POST",
    )
    with urlopen(request, timeout=args.timeout) as response:
        case.output_path.write_bytes(response.read())
        return CaseResult(
            status="success",
            entry=case.entry,
            folder=str(case.folder),
            pdf=case.pdf_path.name,
            excel=case.excel_path.name,
            output=str(case.output_path),
            detail="Generated.",
            matched_lines=response.headers.get("X-Matched-Lines", ""),
            modified_fields=response.headers.get("X-Modified-Fields", ""),
            sheet_name=response.headers.get("X-Excel-Sheet", ""),
        )


def error_detail(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed_detail = str(json.loads(detail).get("detail", detail))
        except json.JSONDecodeError:
            parsed_detail = detail
        if parsed_detail:
            return parsed_detail
        return f"HTTP {exc.code} {exc.reason}"
    detail = str(exc)
    return detail or exc.__class__.__name__


def write_report(path: Path, results: list[CaseResult]) -> None:
    fieldnames = [
        "status",
        "entry",
        "folder",
        "pdf",
        "excel",
        "output",
        "matched_lines",
        "modified_fields",
        "sheet_name",
        "detail",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(result.__dict__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch-generate adjusted 7501 PDFs from folders containing one original PDF and one Excel workbook."
    )
    parser.add_argument("root", help="Month, date, or entry folder to scan.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Tax tool base URL.")
    parser.add_argument("--username", default=os.getenv("TAX_TOOL_USERNAME"))
    parser.add_argument("--password", default=os.getenv("TAX_TOOL_PASSWORD"))
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--entry", action="append", help="Only process this entry number. Can be repeated.")
    parser.add_argument("--entry-pattern", help="Only process entries matching this regular expression.")
    parser.add_argument("--from-entry", help="Process entries lexically greater than or equal to this value.")
    parser.add_argument("--to-entry", help="Process entries lexically less than or equal to this value.")
    parser.add_argument("--limit", type=int, help="Process at most this many matching case folders.")
    parser.add_argument("--output-name", help="Optional fixed output file name inside each case folder.")
    parser.add_argument("--report", help="Optional report CSV path.")
    parser.add_argument("--dry-run", action="store_true", help="List matched cases without uploading or writing PDFs.")
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Generate a unique output name even when the default output already exists.",
    )
    args = parser.parse_args()
    args.skip_existing = not args.regenerate
    if args.limit is not None and args.limit < 1:
        print("Batch generation failed: --limit must be greater than 0.", file=sys.stderr)
        return 1

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"Batch generation failed: folder does not exist: {root}", file=sys.stderr)
        return 1

    password = args.password
    if args.username and not password and sys.stdin.isatty() and not args.dry_run:
        password = getpass.getpass("Online tax tool password: ")
    headers = authorization_header(args.username, password)

    results: list[CaseResult] = []
    for folder in candidate_folders(root):
        if not has_case_files(folder):
            continue
        entry = entry_from_path(folder)
        if not wanted_entry(entry, args):
            continue
        if args.limit is not None and len(results) >= args.limit:
            break
        try:
            case = resolve_case(folder, args)
            results.append(generate_case(case, args, headers))
        except FileExistsError as exc:
            results.append(
                CaseResult(
                    status="skipped",
                    entry=entry,
                    folder=str(folder),
                    pdf="",
                    excel="",
                    output="",
                    detail=str(exc),
                )
            )
        except (HTTPError, URLError, OSError, ValueError) as exc:
            results.append(
                CaseResult(
                    status="failed",
                    entry=entry,
                    folder=str(folder),
                    pdf="",
                    excel="",
                    output="",
                    detail=error_detail(exc),
                )
            )

    for result in results:
        print(f"{result.status.upper():8} {result.entry} {result.detail}")

    counts = {
        status: sum(1 for result in results if result.status == status)
        for status in ("ready", "success", "skipped", "failed")
    }
    print(
        "Summary: "
        f"ready={counts['ready']}, "
        f"success={counts['success']}, "
        f"skipped={counts['skipped']}, "
        f"failed={counts['failed']}"
    )

    report_path = (
        Path(args.report).expanduser().resolve()
        if args.report
        else root / f"batch_report_{datetime.now():%Y%m%d_%H%M%S}.csv"
    )
    if results and (args.report or not args.dry_run):
        write_report(report_path, results)
        print(f"Report: {report_path}")

    if not results:
        print("No matching case folders found.")
        return 1
    return 1 if any(result.status == "failed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
