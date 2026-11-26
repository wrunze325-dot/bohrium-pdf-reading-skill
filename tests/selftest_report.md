# Self-test: baseline vs skill

Fixture: `two_column.pdf`. Same PDF, same questions, same machine.

| metric | baseline:pypdf.extract_text | baseline:pymupdf.get_text(text) | skill:pdf_outline |
|---|---|---|---|
| wall seconds | 0.016 | 0.006 | 0.372 |
| tokens found | 37/42 | 38/42 | 38/42 |
| order inversions | 52 (rate 0.1048) | 52 (rate 0.0985) | 0 (rate 0.0) |
| cross-column alternations | 17 | 17 | 1 |
| splice-prone joins | 3 | 3 | 0 |
| elements with a page | 0/38 | 0/38 | 38/38 |
| elements with a containing bbox | 0/38 | 0/38 | 38/38 |
| table structure recovered | no | no | yes |
| table header correct | n/a | n/a | True |
| table rows correct | 0/0 | 0/0 | 3/3 |
| cross-page table merge | n/a | n/a | True |
| formula number kept | n/a | n/a | True |
| formula superscripts kept | n/a | n/a | True |

## Single-column control page

- columns detected `1` (expected 1), body blocks 4, all fixture lines present in order: True, 0.353 s

## Scanned page (no text layer)

- skill status `failed`, uncertainties ['no_text_layer', 'ocr_unavailable', 'unreadable_page'], blocks 0, 0.406 s
- baseline pypdf text: 0 chars, silently empty: True
- skill reports the gap instead of inventing text: True
