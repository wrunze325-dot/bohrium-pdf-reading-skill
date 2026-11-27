# Test fixtures

Both PDFs are produced by `tests/make_fixtures.py`. The text was written for this
project and carries no third-party material, so the fixtures can be redistributed.

| file | what it exercises |
|---|---|
| `two_column.pdf` | two-column body drawn in visual row order, full-width title,
spanning heading, figure caption, Table 1 spanning pages 1-2 without a repeated
header, footnote, running head, page number, display formula with sup/sub and `(2)` |
| `scanned.pdf` | page 2 rasterised, no text layer, OCR branch |

`truth.json` lists the expected reading order and the expected Table 1 mapping.
