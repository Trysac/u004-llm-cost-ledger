# llm-cost-ledger

[![PyPI version](https://img.shields.io/pypi/v/trysac-llm-cost-ledger)](https://pypi.org/project/trysac-llm-cost-ledger/)
[![License: MIT](https://img.shields.io/pypi/l/trysac-llm-cost-ledger)](LICENSE)
[![CI](https://github.com/Trysac/u004-llm-cost-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/Trysac/u004-llm-cost-ledger/actions/workflows/ci.yml)

In-process ledger of tokens and cost per LLM call with daily and monthly caps in a chosen time zone, one-time warnings and a pre-call budget check

## Problem

Describe in two or three sentences the concrete problem this utility solves, who hits it, and what
they do today without it. Replace this paragraph when the first real feature lands.

## Install

```sh
pip install trysac-llm-cost-ledger
# or
uv add trysac-llm-cost-ledger
```

Requires Python 3.10 or newer. No runtime dependencies.

## Usage

```python
import llm_cost_ledger

# normalize() trims surrounding whitespace and lower-cases the text.
print(llm_cost_ledger.normalize("  Hello World "))  # -> "hello world"

# Blank input is rejected explicitly instead of returning an empty string.
try:
    llm_cost_ledger.normalize("   ")
except ValueError as exc:
    print(f"rejected: {exc}")
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/decisions/)
- [Testing](docs/TESTING.md) and [security](docs/SECURITY.md)
- [Project record](docs/PROJECT.md) and [stack](docs/STACK.md)
- [Changelog](CHANGELOG.md) and [contributing guide](CONTRIBUTING.md)

## License

[MIT](LICENSE) (c) 2026 Miguel Gomez
