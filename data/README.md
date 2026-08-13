# data/

## `samples/`

Small CSVs matching the three bundled import templates, for exercising the import pipeline by
hand. Values are invented; no real trading history or personal data appears here.

| File | Template | Exercises |
| --- | --- | --- |
| `generic-executions.csv` | `generic_executions` | a scale-out (one entry, two partial exits) |
| `us-equities-desktop.csv` | `us_equities_desktop` | `BOT`/`SLD` synonyms, US-Eastern timestamps, a short |
| `crypto-exchange.csv` | `crypto_exchange` | epoch timestamps, fractional quantities, fee-only costs |

Upload one at `/api/v1/imports` with an `account_id`; the template is auto-detected from the
headers.

## Market data

Demo candles are **generated**, not shipped: `python -m tradeloom.cli seed --demo` produces them
from a seeded random walk, so the demo workspace is identical on every machine without committing
megabytes of prices. They are synthetic and the source is flagged `is_realtime = false`.
