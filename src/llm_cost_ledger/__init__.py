"""In-process ledger of tokens and cost per LLM call with caps, warnings and a budget check."""

from __future__ import annotations

import logging

from .entry import Entry
from .errors import BudgetExceededError, LedgerCorruptError, LedgerError
from .ledger import CostLedger, LedgerEvent
from .stores import JsonlLedgerStore, LedgerStore, MemoryLedgerStore

__all__ = [
    "BudgetExceededError",
    "CostLedger",
    "Entry",
    "JsonlLedgerStore",
    "LedgerCorruptError",
    "LedgerError",
    "LedgerEvent",
    "LedgerStore",
    "MemoryLedgerStore",
    "__version__",
]

__version__ = "0.1.0"

logging.getLogger(__name__).addHandler(logging.NullHandler())
