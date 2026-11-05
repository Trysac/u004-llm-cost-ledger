"""Typed errors raised by the ledger."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

ErrorCode = Literal["budget_exceeded", "corrupt", "invalid_argument", "store_full"]
Period = Literal["daily", "monthly"]

MAX_MESSAGE_LENGTH = 200


class LedgerError(Exception):
    """Base class of every error raised by this library.

    Attributes:
        code: Stable machine-readable error code.
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message[:MAX_MESSAGE_LENGTH])
        self.code: ErrorCode = code


class BudgetExceededError(LedgerError):
    """Raised by ``CostLedger.check`` when a projected cost would pass a cap.

    Attributes:
        period: ``"daily"`` or ``"monthly"``.
        cap: The cap that would be exceeded.
        spent: Spend already recorded in the period.
        projected: The projected cost that was checked.
    """

    def __init__(self, period: Period, cap: Decimal, spent: Decimal, projected: Decimal) -> None:
        super().__init__("budget_exceeded", f"{period} budget cap exceeded")
        self.period: Period = period
        self.cap = cap
        self.spent = spent
        self.projected = projected


class LedgerCorruptError(LedgerError):
    """Raised when a stored ledger line cannot be read.

    Attributes:
        line: One-based line number of the offending line.
    """

    def __init__(self, line: int, reason: str) -> None:
        super().__init__("corrupt", f"corrupt ledger line {line}: {reason}")
        self.line = line
