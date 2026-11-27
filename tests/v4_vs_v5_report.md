# v4 vs v5 — local comparison (same inputs)

| case | question | expected | v4 result | v5 result | v5 evidence |
|---|---|---|---|---|---|
| case1_page1 | How many spliced lines does the 'Two-column (stream order)' layout produce? Answer with the number and the table row it comes from. | 5 | status=ok, answer=None, unfilled=['answer', 'evidence'] | status=complete, answer='5' | p.1 · row='Two-column (stream order)' col='Spliced' value='5' |
| case2_table_cell | In Table 1, what order accuracy is reported for the 'Two-column + skill' layout? Give the value with its row and column. | 0.98 | status=ok, answer=None, unfilled=['answer', 'evidence'] | status=complete, answer='0.98' | p.2 · row='Two-column + skill' col='Order acc.' value='0.98' |
| case3_no_table_not_reported | What accuracy does method B report in Table 2? | no number; explain why | status=ok, answer=None, unfilled=['answer', 'missing'] | status=blocked, answer=None | missing_information=['no_such_object', 'not_reported'] |

## Example in `examples/`

- `example_result.incomplete.json`: status `incomplete` (parse done, answer pending) — the state before the agent answers
- `example_result.json`: status `complete`, answer `'0.98'`, evidence `[2]`
- `answers.example.json`: the agent answers that turn one into the other
