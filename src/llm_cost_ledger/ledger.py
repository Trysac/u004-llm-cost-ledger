"""The cost ledger: aggregation, caps, warnings and the pre-call budget check."""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, timezone, tzinfo
from decimal import Decimal
from typing import Literal, TypedDict

from .entry import MAX_COST, Entry, build_entry, validate_cost
from .errors import BudgetExceededError, LedgerError, Period
from .stores import LedgerStore

logger = logging.getLogger(__name__)

MAX_DAYS = 400
MAX_MONTHS = 36
MAX_PROMPT_CHARS = 4_194_304
MAX_HASH_KEY_BYTES = 1024
UNTAGGED = "(untagged)"
DEFAULT_WARN_RATIO = Decimal("0.8")
_ZERO = Decimal(0)


class LedgerEvent(TypedDict):
    """Payload passed to ``on_event``. It never contains prompt text."""

    type: Literal["recorded", "warning", "blocked"]
    period: Period
    spent: Decimal
    cap: Decimal | None
    model: str | None


def _validate_cap(cap: Decimal | None, name: str) -> Decimal | None:
    if cap is None:
        return None
    if not isinstance(cap, Decimal) or not cap.is_finite():
        raise LedgerError("invalid_argument", f"{name} must be a finite Decimal")
    if cap <= 0 or cap > MAX_COST:
        raise LedgerError("invalid_argument", f"{name} must be positive and at most {MAX_COST}")
    return cap


def _validate_ratio(ratio: Decimal) -> Decimal:
    if not isinstance(ratio, Decimal) or not ratio.is_finite():
        raise LedgerError("invalid_argument", "warn_ratio must be a finite Decimal")
    if not 0 < ratio <= 1:
        raise LedgerError("invalid_argument", "warn_ratio must be in (0, 1]")
    return ratio


class CostLedger:
    """In-process ledger of LLM spend with daily and monthly caps.

    On construction the store is replayed once into per-local-day and per-local-month
    aggregates (at most 400 days and 36 months are kept, oldest dropped; ``summary``
    reads the store). ``record`` and ``check`` are guarded by a lock; check-then-record
    is not atomic across processes and multi-process locking is out of scope.

    ``prompt_sha256`` is an unsalted correlation hash, not anonymization, unless
    ``hash_key`` is given (then HMAC-SHA256 is used).
    """

    def __init__(
        self,
        store: LedgerStore,
        *,
        daily_cap: Decimal | None = None,
        monthly_cap: Decimal | None = None,
        warn_ratio: Decimal = DEFAULT_WARN_RATIO,
        tz: tzinfo = timezone.utc,
        clock: Callable[[], datetime],
        hash_key: bytes | None = None,
        on_event: Callable[[LedgerEvent], None] | None = None,
    ) -> None:
        daily = _validate_cap(daily_cap, "daily_cap")
        monthly = _validate_cap(monthly_cap, "monthly_cap")
        ratio = _validate_ratio(warn_ratio)
        if not isinstance(tz, tzinfo):
            raise LedgerError("invalid_argument", "tz must be a tzinfo")
        if not callable(clock):
            raise LedgerError("invalid_argument", "clock must be callable")
        if on_event is not None and not callable(on_event):
            raise LedgerError("invalid_argument", "on_event must be callable")
        if hash_key is not None and (
            not isinstance(hash_key, bytes) or not 0 < len(hash_key) <= MAX_HASH_KEY_BYTES
        ):
            raise LedgerError("invalid_argument", "hash_key must be 1 to 1024 bytes")
        self._store = store
        self._tz = tz
        self._clock = clock
        self._hash_key = hash_key
        self._on_event = on_event
        self._lock = threading.Lock()
        self._limits: tuple[tuple[Period, Decimal | None, Decimal | None], ...] = (
            ("daily", daily, None if daily is None else daily * ratio),
            ("monthly", monthly, None if monthly is None else monthly * ratio),
        )
        self._monthly_cap = monthly
        self._days: dict[date, Decimal] = {}
        self._months: dict[tuple[int, int], Decimal] = {}
        for entry in store.entries():
            self._add(entry)

    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise LedgerError("invalid_argument", "clock must return an aware datetime")
        return now.astimezone(timezone.utc)

    def _keys(self, moment: datetime) -> tuple[date, tuple[int, int]]:
        try:
            local = moment.astimezone(self._tz)
        except OverflowError:
            raise LedgerError("invalid_argument", "timestamp out of range") from None
        return local.date(), (local.year, local.month)

    def _add(self, entry: Entry) -> None:
        day, month = self._keys(entry.at)
        self._days[day] = self._days.get(day, _ZERO) + entry.cost
        self._months[month] = self._months.get(month, _ZERO) + entry.cost
        while len(self._days) > MAX_DAYS:
            del self._days[min(self._days)]
        while len(self._months) > MAX_MONTHS:
            del self._months[min(self._months)]

    def _spent_for(self, period: Period, day: date, month: tuple[int, int]) -> Decimal:
        if period == "daily":
            return self._days.get(day, _ZERO)
        return self._months.get(month, _ZERO)

    def _hash_prompt(self, prompt: str | None) -> str | None:
        if prompt is None:
            return None
        if not isinstance(prompt, str) or len(prompt) > MAX_PROMPT_CHARS:
            raise LedgerError("invalid_argument", "prompt must be a string within the size limit")
        try:
            data = prompt.encode("utf-8")
        except UnicodeEncodeError:
            raise LedgerError("invalid_argument", "prompt is not valid text") from None
        if self._hash_key is None:
            return hashlib.sha256(data).hexdigest()
        return hmac.new(self._hash_key, data, hashlib.sha256).hexdigest()

    def _emit(self, events: list[LedgerEvent]) -> None:
        if self._on_event is None:
            return
        for event in events:
            try:
                self._on_event(event)
            except Exception as exc:
                logger.warning("on_event callback failed: %s", type(exc).__name__)

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: Decimal,
        tags: tuple[str, ...] = (),
        prompt: str | None = None,
    ) -> Entry:
        """Record one LLM call and return the stored ``Entry``.

        Emits a ``recorded`` event and, when a period crosses ``warn_ratio * cap``
        for the first time, a ``warning`` event. Raises ``LedgerError`` with code
        ``invalid_argument`` for bad input or ``store_full`` when the store is full.
        """
        digest = self._hash_prompt(prompt)
        with self._lock:
            entry = build_entry(
                at=self._now(),
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
                tags=tags,
                prompt_sha256=digest,
            )
            day, month = self._keys(entry.at)
            before = {p: self._spent_for(p, day, month) for p, _, _ in self._limits}
            self._store.append(entry)
            self._add(entry)
            events: list[LedgerEvent] = [
                {
                    "type": "recorded",
                    "period": "monthly",
                    "spent": before["monthly"] + entry.cost,
                    "cap": self._monthly_cap,
                    "model": entry.model,
                }
            ]
            for period, cap, threshold in self._limits:
                if cap is None or threshold is None:
                    continue
                after = before[period] + entry.cost
                if before[period] < threshold <= after:
                    events.append(
                        {
                            "type": "warning",
                            "period": period,
                            "spent": after,
                            "cap": cap,
                            "model": entry.model,
                        }
                    )
        self._emit(events)
        return entry

    def check(self, projected_cost: Decimal) -> None:
        """Raise ``BudgetExceededError`` if ``spent + projected_cost`` passes a cap.

        Reaching a cap exactly is allowed. The daily cap is reported first.
        """
        projected = validate_cost(projected_cost, "projected_cost")
        blocked: tuple[Period, Decimal, Decimal] | None = None
        with self._lock:
            day, month = self._keys(self._now())
            for period, cap, _ in self._limits:
                if cap is None:
                    continue
                spent = self._spent_for(period, day, month)
                if spent + projected > cap:
                    blocked = (period, cap, spent)
                    break
        if blocked is None:
            return
        period, cap, spent = blocked
        self._emit(
            [{"type": "blocked", "period": period, "spent": spent, "cap": cap, "model": None}]
        )
        raise BudgetExceededError(period, cap, spent, projected)

    def spent_today(self) -> Decimal:
        """Return the spend of the current local day."""
        with self._lock:
            day, month = self._keys(self._now())
            return self._spent_for("daily", day, month)

    def spent_this_month(self) -> Decimal:
        """Return the spend of the current local month."""
        with self._lock:
            day, month = self._keys(self._now())
            return self._spent_for("monthly", day, month)

    def summary(
        self,
        group_by: Literal["model", "provider", "tag"],
        period: Literal["day", "month"] = "month",
    ) -> dict[str, Decimal]:
        """Return spend per group for the current local day or month, read from the store.

        With ``group_by='tag'`` an entry counts once per distinct tag; untagged entries
        are grouped under ``"(untagged)"``.
        """
        if group_by not in ("model", "provider", "tag"):
            raise LedgerError("invalid_argument", "group_by must be model, provider or tag")
        if period not in ("day", "month"):
            raise LedgerError("invalid_argument", "period must be day or month")
        totals: dict[str, Decimal] = {}
        with self._lock:
            now_day, now_month = self._keys(self._now())
            for entry in self._store.entries():
                day, month = self._keys(entry.at)
                if (day != now_day) if period == "day" else (month != now_month):
                    continue
                if group_by == "model":
                    names: tuple[str, ...] = (entry.model,)
                elif group_by == "provider":
                    names = (entry.provider,)
                else:
                    names = tuple(dict.fromkeys(entry.tags)) or (UNTAGGED,)
                for name in names:
                    totals[name] = totals.get(name, _ZERO) + entry.cost
        return totals
