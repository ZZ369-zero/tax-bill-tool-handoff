# 7501 Tax Bill Tool Prototype

中文上线步骤见 [`DEPLOYMENT.zh-CN.md`](DEPLOYMENT.zh-CN.md)，日常使用与维护流程见
[`docs/user-guide.zh-CN.md`](docs/user-guide.zh-CN.md)。

This workspace contains a local prototype for parsing CBP 7501 PDF files from a
folder of original and manually adjusted copies.

## Current Goal

The first step is to make the data layer reliable:

1. Pair original PDFs with adjusted PDFs by filename.
2. Extract header fields, totals, and line items from the PDF text layer.
3. Extract MPF/HMF fee details where available.
4. Calculate duty, MPF, HMF, and variance columns for validation.
5. Export review workbooks for manual validation.

The parser does not modify any source PDF files.

## Run

### Web app

```powershell
python -m pip install -r requirements.txt
python -m uvicorn web_app.app:app --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000/
```

Current web flow:

1. Upload one CBP 7501 PDF.
2. Parse document header fields and line items.
3. Edit line-item HTS, net quantity, entered value, or rate in the browser.
   Net quantity is displayed with its required unit, such as `NO` or `KG`.
4. Recalculate duty, MPF, optional HMF, and document totals.
   Entered value is normalized to whole U.S. dollars before tax calculation.
   Changing HTS queries the current USITC HTS API for the official description,
   reporting units, General Rate, and Chapter 99 references.
5. Generate an updated PDF by replacing only changed text objects inside the
   uploaded original. Unchanged text, form lines, fonts, and page geometry are
   preserved, and related invoice totals on continuation pages are updated.
6. Optionally export the working JSON for troubleshooting or audit review.

The browser tracks fields edited by the user. With no edits, PDF generation
returns the uploaded file byte-for-byte. If an edited text object cannot be
matched safely in the original PDF, generation stops instead of approximating
the position or rebuilding the form.

The sidebar also supports an Excel automation flow:

1. Select the original CBP 7501 PDF.
2. Select the two-sheet Sample Commercial Invoice & Packing List workbook.
3. Generate a new tax bill from worksheet 2 values without overwriting the
   original PDF.

Uploaded PDFs are stored under `uploads/`, which is ignored by git.

### Local checks

Run the same unittest suite used by GitHub Actions, plus a lightweight app
health import check:

```powershell
.\tools\check.ps1
```

On a fresh machine, install dependencies first:

```powershell
.\tools\check.ps1 -InstallDependencies
```

### CBP calculation rules

- Each line's entered value is reported in whole U.S. dollars using half-up
  rounding before duty and fee calculation.
- Base duty and each Chapter 99 component are calculated separately and rounded
  to cents before line and document totals are summed.
- Formal-entry MPF is `0.3464%`, subject to the configured fiscal-year minimum
  and maximum. HMF is `0.125%` only when applicable to the vessel movement.
- `499 - MPF` is calculated from the sum of rounded line-item MPF records, then
  the formal-entry minimum and maximum are applied.
- A modified `KG` net quantity cannot exceed the line's `KG` gross weight.
- USITC lookup suggestions do not replace a broker or binding CBP classification
  ruling. Chapter 99 applicability still requires country, date, program, and
  exclusion review.

### Render deployment

The included `render.yaml` can deploy this FastAPI app as a Render Web Service.

1. Push the repository to GitHub and create a Render Blueprint from it.
2. Set secret values for `APP_USERNAME` and `APP_PASSWORD` in Render.
3. Keep `/api/health` as the health check path.
4. GitHub Actions runs the test suite; Render deploys after the checks pass.
5. Use the generated `onrender.com` HTTPS URL for the pilot, then attach a
   custom domain when ready.

Render's default filesystem is ephemeral. This pilot intentionally treats
uploaded tax PDFs as temporary working files. For multi-user or long-term
retention, replace local uploads with encrypted object storage and add user
accounts plus an audit database before production use.

### Excel worksheet 2 workflow

Use the original 7501 PDF plus a two-sheet commercial invoice workbook to generate a new PDF through the deployed service. The command reads worksheet 2, matches rows to the tax form by HTS, and updates:

- Gross weight from the populated item-level gross-weight column.
- Net quantity from item quantity, net weight when the tax-form reporting unit is `KG`,
  quantity ÷ 144 for `GR`, quantity ÷ 1,000 for `K`, and quantity ÷ 12
  rounded to whole dozens for `DPR`.
- Entered value from the FOB total-value column.
- 501-HMF is controlled by transport mode: `auto` keeps the original PDF's HMF
  state, `ocean` recalculates HMF at 0.125%, and `air` excludes HMF.
  For routine batches, use `auto` unless you are intentionally overriding a
  known source-template issue.

The original PDF is never overwritten. If the default output name already exists, the command adds `(2)`, `(3)`, and so on.

```powershell
$env:TAX_TOOL_USERNAME="your-login-name"
$env:TAX_TOOL_PASSWORD="your-login-password"
python .\tools\excel_workflow.py "C:\path\to\131-80596740"
```

Optional arguments:

```powershell
python .\tools\excel_workflow.py "C:\path\to\folder" `
  --pdf "C:\path\to\original.pdf" `
  --excel "C:\path\to\invoice.xlsx" `
  --output "C:\path\to\new-tax-bill.pdf" `
  --url "https://tax-bill-tool.onrender.com" `
  --transport-mode auto
```

The workbook must be saved after worksheet 2 is updated so formula results are available to the server.

For monthly or date-folder batches, preview first:

```powershell
python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --dry-run
```

For a smaller trial batch, limit the first run:

```powershell
python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --limit 10 --dry-run
```

Then generate PDFs and a CSV report:

```powershell
python .\tools\batch_excel_workflow.py "C:\Users\Administrator\Desktop\事项\7501\7月" --limit 10 --transport-mode auto
```

Use `--entry`, `--entry-pattern`, `--from-entry`, and `--to-entry` to limit the
batch to specific entry folders. Existing `- 自动修改.pdf` outputs are skipped by
default; pass `--regenerate` to create a new numbered output file.
Use `--transport-mode auto` for routine work so the tool follows the original
tax form's HMF state. Use `--transport-mode ocean` or `--transport-mode air`
only when you deliberately need to override that auto-detection.

### Regression fixtures

Use `tests/fixtures/` for small, sanitized, versioned samples that should remain
stable across future parser and PDF-generation changes. Do not place daily real
business uploads there. Routine uploaded files still belong in `uploads/`, which
is ignored by git.

Add a new fixture only when it captures a reusable case: a new 7501 layout, a
new Excel worksheet format, a fixed bug, or an edge case such as Chapter 99,
KG/NO unit handling, MPF, or HMF.

### Batch parser

```powershell
python .\tools\7501_parser.py --input "C:\path\to\7501" --output .\output
```

Outputs:

- `output/7501_pairs.xlsx`
- `output/7501_documents.xlsx`
- `output/7501_line_items.xlsx`
- `output/7501_pair_summary.xlsx`
- `output/7501_line_compare.xlsx`
- `output/7501_extraction.json`

## Notes

- The current sample set is electronic PDF, not scanned PDF, so the main parser
  uses PDF text and coordinates instead of OCR.
- OCR should be added later as a fallback for scanned or damaged files.
- PDF generation edits matched text objects in the uploaded original and refuses
  to generate when a requested field cannot be located safely.
- HMF is parsed from `501 - Harbor Maintenance Fee` rows and document-level
  `501 - HMF` fee summary rows.
- MPF is calculated with the fiscal-year constants in
  `tools/7501_parser.py`: rate `0.3464%`, min `$33.58`, max `$651.50`.
