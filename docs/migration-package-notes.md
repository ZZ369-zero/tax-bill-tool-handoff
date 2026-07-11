# Migration Package Notes

This package is intended to move the current 7501 tax bill tool work to another
computer.

## Recommended Use

1. Copy the full folder `7501税单工具迁移包` to the other computer.
2. Open the GitHub repository or the local `project_source` folder.
3. Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

4. Run the parser against the full sample folder:

```powershell
python .\project_source\tools\7501_parser.py --input ".\all_local_samples\7501" --output ".\parser_outputs_new"
```

5. Use `sample_subset` for faster development tests.

## Folder Layout

- `project_source`: source code, handoff documents, and requirements.
- `parser_outputs`: current reports produced on the original computer.
- `sample_subset`: representative original/modified PDF pairs plus related
  local files for fast testing.
- `all_local_samples`: full local sample copy from the original machine.

## Privacy

The package may contain real tax bill PDFs and Excel files. Treat it as private
company data.

