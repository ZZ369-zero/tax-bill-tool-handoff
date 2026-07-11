# 7501 Tax Bill Tool Prototype

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

```powershell
python .\tools\7501_parser.py --input "C:\Users\Administrator\Desktop\事项\7501" --output .\output
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
- PDF regeneration should use fixed templates and coordinates after extraction
  accuracy is verified.
- HMF is parsed from `501 - Harbor Maintenance Fee` rows and document-level
  `501 - HMF` fee summary rows.
- MPF is calculated with the current prototype constants in
  `tools/7501_parser.py`: rate `0.3464%`, min `$33.58`, max `$651.50`.
