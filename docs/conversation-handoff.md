# 7501 Tax Bill Tool - Conversation Handoff

Date: 2026-07-11

This document summarizes the current conversation, decisions, requirements, and
prototype status so the project can be resumed from a GitHub link on another
computer.

## Project Objective

Build an internal web tool for adjusting CBP 7501 tax bill PDFs.

Target workflow:

1. Open a local/private web page.
2. Upload an official CBP 7501 PDF.
3. Parse the PDF into structured header fields and line items.
4. Edit product entered value on selected line items.
5. Automatically recalculate duty, MPF, HMF, and totals.
6. Generate a revised 7501-style PDF with stable layout, fonts, alignment, and
   spacing.

The revised PDF is for internal adjustment and customer reconciliation. It is
not intended for official CBP filing.

## Original Website Analysis

The referenced site was:

`https://www.aihtscode.com/#/ad?adType=imitateTaxBillUs&buttonType=noPermission`

Findings:

- The visible page is a permission/ad page.
- The functional pages are iframe-loaded from `codeflagai.com`.
- Relevant tools:
  - `模拟税单US`
  - `税单拆解`
- `模拟税单US` is closer to pre-entry 7501 simulation.
- `税单拆解` is closer to parsing an existing 7501 PDF into structured data.
- The user's target workflow is best described as:
  `税单拆解 + 货值调整 + 税费重算 + 7501模板重生成`.

Observed frontend/API concepts from the site:

- `模拟税单US`
  - Upload Excel invoice/packing data or use system document IDs.
  - Generate simulated 7501 tax bill data.
  - Relevant endpoints included list/create/export simulator APIs.
- `税单拆解`
  - Upload 7501 PDF.
  - Select recognition style such as NETC, SMTB, ACLK, Cargowise, OTHER.
  - Export parsed data.
  - Relevant endpoints included list/create-by-PDF/export/delete APIs.

Important conclusion:

The third-party site's true calculation/OCR/PDF generation logic is server-side.
The frontend revealed workflow and interface shape, but not the proprietary
calculation or parsing algorithms.

## User Requirements

Business scope:

- Product categories: electronics, textiles, consumer goods, steel, toys, and
  other Amazon marketplace-style categories.
- Main route: China to United States.
- Import type: normal import.
- Transport channels: both ocean and air.
- ADD/CVD: not required for now.
- Generated file: internal adjusted copy/customer reconciliation copy.
- Customer logo: not required.
- Watermark: not required.
- User can manually verify calculations.
- Real samples are available locally.
- Website use: internal company members only, not commercial SaaS for now.

PDF output requirement:

- The generated PDF must follow the submitted original PDF style.
- Fonts, spacing, alignment, and field positions must remain stable.
- Directly overlaying text on the old PDF is not preferred.
- Correct approach: parse the PDF into structured data, then regenerate the
  whole PDF using a controlled fixed-position template.

Sample file rule:

- Local sample root:
  `C:\Users\Administrator\Desktop\事项\7501`
- Files with `副本` or `更新` in the filename are manually modified copies.
- Files without those suffixes are original tax bills.

## OCR Decision

OCR should not be the primary flow for the current sample set.

Reason:

- All inspected PDFs have readable electronic text layers.
- The pages are mostly standard US Letter size `612 x 792`.
- Most files use Helvetica-family fonts.
- PDF text/coordinate parsing is more accurate than OCR for tax numbers, HTS
  codes, dollar values, percentages, and decimals.

OCR should be added later only as a fallback for:

- scanned PDFs,
- image-only PDFs,
- damaged PDFs,
- unusable text layers.

## Current Prototype

Current workspace:

`C:\Users\Administrator\Desktop\开发税单软件`

Implemented files:

- `README.md`
- `tools/7501_parser.py`

Generated outputs are intentionally ignored by git:

- `output/`
- uploaded files,
- PDF files,
- Excel reports,
- JSON dumps,
- archives.

Reason: those files may contain real tax/customer data.

Run command:

```powershell
python .\tools\7501_parser.py --input "C:\Users\Administrator\Desktop\事项\7501" --output .\output
```

Generated local reports:

- `output/7501_pairs.xlsx`
- `output/7501_documents.xlsx`
- `output/7501_line_items.xlsx`
- `output/7501_pair_summary.xlsx`
- `output/7501_line_compare.xlsx`
- `output/7501_extraction.json`

## Prototype Results

Full run against local samples:

- PDF files found: 209
- Parsed files: 209
- Errors: 0
- Original/modified pairs: 104 paired
- One original-only group requires review
- Extracted line items: 965 total

Original-file extraction:

- Original docs: 105
- Original line items: 485
- HMF documents identified: 16
- HMF line items identified: 82
- Duty exact match after current calculation: 89 / 105 original docs
- Other fee exact match: 100 / 105 original docs
- Grand total exact match: 88 / 105 original docs

Current calculation support:

- Base ad valorem duty, e.g. `3.4%`
- FREE rate
- Chapter 99 percentage duties
- Some compound rates, e.g. `$0.075 per NO + 3.2%`
- MPF at `0.3464%`, with prototype min `$33.58`, max `$651.50`
- HMF at `0.125%` for files containing HMF rows
- Variance columns for extracted vs calculated amounts

Known limitations:

- Modified copies are useful for learning differences, but they are not reliable
  as the main data source because manual edits fragment PDF text coordinates.
- The original PDF should be the primary source for parsing.
- Some remaining mismatches are caused by PDF text fragmentation, special
  compound rates, or layout variants.
- `layout` text extraction and additional coordinate rules should be added.
- PDF regeneration is not implemented yet.
- Web upload/edit UI is not implemented yet.

## Agreed Final Web Flow

The user confirmed the desired website should work by uploading a file on the
webpage, not by manually running scripts.

Target web flow:

1. Upload original 7501 PDF.
2. Parse header and line items.
3. Show editable table:
   - line number,
   - description,
   - HTS,
   - entered value,
   - rate,
   - duty,
   - MPF,
   - HMF,
   - totals,
   - variance/warnings.
4. User edits entered value.
5. System recalculates:
   - base duty,
   - Chapter 99/Section 122 duties,
   - MPF,
   - HMF for ocean shipments,
   - document totals.
6. User confirms.
7. System generates revised PDF using fixed-position template regeneration.

## Recommended Next Steps

1. Build local web app:
   - upload endpoint,
   - parse endpoint,
   - recalculation endpoint,
   - browser UI.

2. UI features:
   - upload 7501 PDF,
   - show document summary,
   - show editable line-item table,
   - allow entered value edit,
   - show recalculated duty/MPF/HMF,
   - show warnings when parsing confidence is low.

3. Improve parser:
   - add `layout` text fallback,
   - handle remaining fragmented rows,
   - improve special compound-rate parsing,
   - classify air vs ocean more explicitly.

4. PDF generation:
   - do not overlay old text.
   - regenerate from template coordinates.
   - preserve Helvetica-like fonts and right-aligned numeric fields.
   - validate rendered PDF by comparing positions and screenshots.

5. Later enhancements:
   - OCR fallback,
   - HTS official data integration,
   - Chapter 99 rules versioning,
   - audit log,
   - user permissions,
   - batch processing.

## Important Compliance Boundary

The tool is for internal adjustment, simulation, and customer reconciliation.
It should not present generated PDFs as official CBP filings unless reviewed and
processed through appropriate official correction/filing channels.

