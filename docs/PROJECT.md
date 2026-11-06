# Project record

## Objective

In-process ledger of tokens and cost per LLM call with daily and monthly caps in a chosen time zone, one-time warnings and a pre-call budget check

## Scope

- In scope: the public API documented in [README.md](../README.md), typed and tested.
- Out of scope: anything that needs runtime dependencies or I/O without an accepted ADR.

## Timeline

| Date | Event |
|---|---|
| 2026 | Repository created from the factory utility template. |

## Effort metrics

Measured values only; a value that has not been measured stays "pending measurement".

| Metric | Value |
|---|---|
| Tokens (input / output) | 397383 / 60994 |
| Hours (human) | pending measurement |
| Cost (USD) | 0.4904 |

## Factory run u004 (measured from API usage)

| Phase | Model | Batch | Input | Output | Cache write | Cache read | Cost (USD) |
|---|---|---|---|---|---|---|---|
| generation | claude-sonnet-5-5 | yes | 2439 | 51715 | 0 | 4246 | 0.2614 |
| fix-1 | claude-sonnet-5-5 | no | 4 | 447 | 5830 | 24424 | 0.0215 |
| fix-2 | claude-sonnet-5-5 | no | 6 | 494 | 6097 | 39683 | 0.0242 |
| fix-3 | claude-sonnet-5-5 | no | 12 | 2158 | 9537 | 93806 | 0.0548 |
| fix-4 | claude-sonnet-5-5 | no | 14 | 2006 | 11427 | 117486 | 0.0604 |
| docs | claude-haiku-5-5 | no | 115263 | 4174 | 0 | 0 | 0.0681 |

Total: $0.4904. Prices: Anthropic list prices.
