# Test fixtures

Synthetic, hand-written billing files used by automated tests and for manual upload
through the UI. They contain no real account data (account IDs are placeholders).

| File | Scenario |
| --- | --- |
| `billing_valid.csv` | 8 valid rows: `tag:` columns, a bare-date row, a negative credit, an untagged row, a `global` region |
| `billing_malformed.csv` | 2 accepted rows (one has a formula-like service name, stored as plain text) and 10 rejected rows, one per validation rule (timestamp, account, amount, currency, window, granularity, tags, region, duplicate ID, extra cells) |
| `billing_late_arriving.csv` | Upload after `billing_valid.csv`: one unchanged row, one corrected amount, one older day arriving late |
| `billing_empty.csv` | Header only → import fails with `empty_dataset` |
| `billing_mixed_currency.csv` | EUR and USD rows → kept separate, never summed together |
| `billing_not_utf8.csv` | Not UTF-8 → whole-file rejection |

The 120-day demo dataset is generated, not stored: see
`apps/api/src/nimbrix_api/modules/ingestion/synthetic.py`.
