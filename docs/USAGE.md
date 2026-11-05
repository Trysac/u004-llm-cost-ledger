# Usage

`llm_cost_ledger` records every LLM call (tokens and cost) in an append-only store, aggregates
spend per local day and month from an injected clock, warns once per period at a threshold and
refuses calls that would pass a cap. It runs in-process and works with any client.

## Example

```python
from datetime import datetime, timezone
from decimal import Decimal

from llm_cost_ledger import CostLedger, MemoryLedgerStore

ledger = CostLedger(
    MemoryLedgerStore(),
    daily_cap=Decimal("1.00"),
    clock=lambda: datetime.now(timezone.utc),
)
ledger.check(Decimal("0.40"))  # raises BudgetExceededError if it would pass a cap
ledger.record(provider="openai", model="gpt-x", input_tokens=120, output_tokens=40, cost=Decimal("0.40"))
print(ledger.spent_today(), ledger.summary("model"))
```

## Semantics

- A period is the local date (or year-month) of `Entry.at` converted to `tz`.
- `check` raises when `spent + projected > cap`; reaching a cap exactly is allowed. When both
  caps are exceeded the daily one is reported.
- A `warning` event fires when a record moves a period from below `warn_ratio * cap` to at or
  above it. A period already above the threshold at load never warns again.
- `on_event` receives `recorded`, `warning` and `blocked` payloads (`type`, `period`, `spent`,
  `cap`, `model`). They never contain prompt text.
- The ledger keeps at most 400 daily and 36 monthly aggregates (oldest dropped); `summary` reads
  the store. `MemoryLedgerStore` drops its oldest entry when full without changing aggregates.
- `summary(group_by='tag')` counts an entry once per distinct tag; untagged entries are grouped
  under `(untagged)`.

## JSONL format

One JSON object per line with keys `v` (1), `at`, `provider`, `model`, `input_tokens`,
`output_tokens`, `cost` (a string), `tags`, `prompt_sha256`. Lines are limited to 65,536 bytes;
a line without a trailing newline at the end of the file is a truncated line and is corrupt.
`JsonlLedgerStore` refuses an append that would pass `max_bytes` with code `store_full`.

## Limits and threat model

- `prompt_sha256` is an unsalted correlation hash, not anonymization, unless `hash_key` is given
  (HMAC-SHA256). Prompt text is never stored, logged or emitted.
- `check` followed by `record` is not atomic across threads callers or processes; only each call
  is guarded by a lock. Multi-process locking is out of scope.
- Out of scope: computing cost from tokens, databases, currency conversion.
- Size limits: names 128 characters, 32 tags of 64 characters, prompts 4,194,304 characters,
  cost at most 1e15.
- Errors derive from `LedgerError` with a stable `code`: `budget_exceeded`, `corrupt`,
  `invalid_argument`, `store_full`.
