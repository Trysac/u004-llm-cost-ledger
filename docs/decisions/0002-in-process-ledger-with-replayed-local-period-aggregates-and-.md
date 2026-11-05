# In-process ledger with replayed local-period aggregates and pluggable append-only stores

Status: accepted (generated, pending human review)

## Context

Agents and batch jobs need per-call spend tracking, caps in a chosen time zone, one-time warnings and a pre-call check. Proxy budgets such as LiteLLM's live in a server, and home-made counters often get month boundaries and time zones wrong.

## Decision

Keep the ledger in-process behind a LedgerStore protocol (memory and JSONL). On construction replay the store once into per-local-day and per-local-month Decimal aggregates, bounded to 400 days and 36 months. Time comes from an injected clock and a tzinfo. Costs are Decimal and stored as JSON strings. Prompts are only stored as a SHA-256 or HMAC digest. One LedgerError base with stable codes, and a lock around record and check. Warnings fire only when a record crosses the threshold, so reloads never repeat them.

## Consequences

No server or dependency is needed and behaviour is deterministic and testable. Check-then-record is not atomic across processes, and summaries beyond the aggregate window read the store. Old entries beyond the window do not count toward caps. Unsalted prompt hashes allow correlation unless a hash_key is configured.
