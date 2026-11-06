"""Tests for the public API of llm_cost_ledger."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import llm_cost_ledger
from llm_cost_ledger import (
    BudgetExceededError,
    CostLedger,
    Entry,
    JsonlLedgerStore,
    LedgerCorruptError,
    LedgerError,
    LedgerEvent,
    MemoryLedgerStore,
)

UTC = timezone.utc
NOON = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
PROMPT_TEXT = "secret prompt"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make(store: Any = None, clock: Any = None, **kwargs: Any) -> CostLedger:
    return CostLedger(
        store if store is not None else MemoryLedgerStore(),
        clock=clock if clock is not None else Clock(NOON),
        **kwargs,
    )


def rec(ledger: CostLedger, cost: Any = "0.60", **overrides: Any) -> Entry:
    args: dict[str, Any] = {
        "provider": "openai",
        "model": "gpt-x",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost": Decimal(cost) if isinstance(cost, str) else cost,
    }
    args.update(overrides)
    return ledger.record(**args)


def good_record(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "v": 1,
        "at": "2026-03-15T12:00:00+00:00",
        "provider": "p",
        "model": "m",
        "input_tokens": 1,
        "output_tokens": 2,
        "cost": "0.5",
        "tags": ["a"],
        "prompt_sha256": None,
    }
    data.update(overrides)
    return data


def good_line(**overrides: Any) -> bytes:
    return json.dumps(good_record(**overrides)).encode("utf-8")


def write_lines(path: Path, lines: list[bytes]) -> None:
    path.write_bytes(b"".join(line + b"\n" for line in lines))


def sample_entry(**overrides: Any) -> Entry:
    args: dict[str, Any] = {
        "at": NOON,
        "provider": "p",
        "model": "m",
        "input_tokens": 1,
        "output_tokens": 1,
        "cost": Decimal("1"),
    }
    args.update(overrides)
    return Entry(**args)


def test_daily_cap_check() -> None:
    ledger = make(daily_cap=Decimal("1.00"))
    rec(ledger, "0.60")
    assert ledger.check(Decimal("0.40")) is None
    with pytest.raises(BudgetExceededError) as info:
        ledger.check(Decimal("0.41"))
    err = info.value
    assert err.code == "budget_exceeded"
    assert err.period == "daily"
    assert err.spent == Decimal("0.60")
    assert err.projected == Decimal("0.41")
    assert err.cap == Decimal("1.00")
    assert isinstance(err, LedgerError)
    assert "daily" in str(err)


def test_negative_offset_local_day_and_month() -> None:
    tz = timezone(timedelta(hours=-5))
    clock = Clock(datetime(2026, 3, 1, 4, 30, tzinfo=UTC))
    ledger = make(clock=clock, tz=tz)
    rec(ledger, "2")
    assert ledger.spent_today() == Decimal("2")
    assert ledger.spent_this_month() == Decimal("2")
    clock.now = datetime(2026, 2, 28, 23, 0, tzinfo=UTC)
    assert ledger.spent_today() == Decimal("2")
    clock.now = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)
    assert ledger.spent_today() == Decimal("0")
    assert ledger.spent_this_month() == Decimal("2")
    clock.now = datetime(2026, 3, 1, 4, 59, tzinfo=UTC)
    assert ledger.spent_today() == Decimal("2")
    clock.now = datetime(2026, 3, 1, 5, 0, tzinfo=UTC)
    assert ledger.spent_today() == Decimal("0")
    assert ledger.spent_this_month() == Decimal("0")
    assert ledger.summary("model") == {}
    assert ledger.summary("model", period="day") == {}


def test_leap_day_and_month_boundary() -> None:
    clock = Clock(datetime(2024, 2, 29, 12, 0, tzinfo=UTC))
    ledger = make(clock=clock)
    rec(ledger, "3")
    assert ledger.spent_today() == Decimal("3")
    clock.now = datetime(2024, 2, 29, 23, 59, tzinfo=UTC)
    assert ledger.spent_this_month() == Decimal("3")
    clock.now = datetime(2024, 3, 1, 0, 0, tzinfo=UTC)
    assert ledger.spent_today() == Decimal("0")
    assert ledger.spent_this_month() == Decimal("0")


def test_warning_once_and_none_after_reload(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    events: list[LedgerEvent] = []
    ledger = make(
        JsonlLedgerStore(path),
        monthly_cap=Decimal("10"),
        warn_ratio=Decimal("0.8"),
        on_event=events.append,
    )
    for cost in ("7", "1.5", "0.5"):
        rec(ledger, cost)
    warnings = [e for e in events if e["type"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["period"] == "monthly"
    assert warnings[0]["spent"] == Decimal("8.5")
    assert warnings[0]["cap"] == Decimal("10")
    reloaded_events: list[LedgerEvent] = []
    reloaded = make(
        JsonlLedgerStore(path),
        monthly_cap=Decimal("10"),
        warn_ratio=Decimal("0.8"),
        on_event=reloaded_events.append,
    )
    assert reloaded.spent_this_month() == Decimal("9.0")
    rec(reloaded, "0.1")
    assert [e["type"] for e in reloaded_events] == ["recorded"]


def test_warning_at_exact_threshold_daily_and_new_period() -> None:
    clock = Clock(NOON)
    events: list[LedgerEvent] = []
    ledger = make(
        clock=clock,
        daily_cap=Decimal("1"),
        monthly_cap=Decimal("100"),
        on_event=events.append,
    )
    rec(ledger, "0.5")
    rec(ledger, "0.3")
    rec(ledger, "0.1")
    daily = [e for e in events if e["type"] == "warning"]
    assert [e["period"] for e in daily] == ["daily"]
    assert daily[0]["spent"] == Decimal("0.8")
    clock.now = NOON + timedelta(days=1)
    rec(ledger, "0.9")
    warned = [e["period"] for e in events if e["type"] == "warning"]
    assert warned == ["daily", "daily"]


def test_jsonl_roundtrip_totals_and_decimal_string(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = make(JsonlLedgerStore(path, fsync=True))
    rec(ledger, "0.1", tags=("a", "b"))
    rec(ledger, "0.25")
    stored = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert stored["cost"] == "0.1"
    assert isinstance(stored["cost"], str)
    assert stored["v"] == 1
    assert list(stored) == [
        "v",
        "at",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "cost",
        "tags",
        "prompt_sha256",
    ]
    reloaded = make(JsonlLedgerStore(path))
    assert reloaded.spent_today() == Decimal("0.35")
    assert reloaded.spent_this_month() == Decimal("0.35")
    entries = list(JsonlLedgerStore(path).entries())
    assert entries[0].cost == Decimal("0.1")
    assert entries[0].tags == ("a", "b")
    assert entries[0].at == NOON


def test_empty_and_missing_store(tmp_path: Path) -> None:
    assert list(JsonlLedgerStore(tmp_path / "missing.jsonl").entries()) == []
    ledger = make()
    assert ledger.spent_today() == Decimal("0")
    assert ledger.summary("tag") == {}
    ledger.check(Decimal("0"))


def test_corrupt_line_three(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(path, [good_line(), good_line(), b"{not json"])
    with pytest.raises(LedgerCorruptError) as info:
        make(JsonlLedgerStore(path))
    assert info.value.code == "corrupt"
    assert info.value.line == 3
    assert isinstance(info.value, LedgerError)


@pytest.mark.parametrize("size", [65_537, 70_000, 200_000])
def test_overlong_line(tmp_path: Path, size: int) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(path, [good_line(), b"x" * size, good_line()])
    with pytest.raises(LedgerCorruptError) as info:
        make(JsonlLedgerStore(path))
    assert info.value.code == "corrupt"
    assert info.value.line == 2
    assert len(str(info.value)) <= 200


def test_truncated_final_line(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(good_line() + b"\n" + b'{"v":1')
    with pytest.raises(LedgerCorruptError) as info:
        list(JsonlLedgerStore(path).entries())
    assert info.value.line == 2
    path.write_bytes(good_line())
    with pytest.raises(LedgerCorruptError) as again:
        list(JsonlLedgerStore(path).entries())
    assert again.value.line == 1


@pytest.mark.parametrize(
    "body",
    [b"[]", b"3", b"not json", b"\xff\xfe", b"[" * 60_000, b"", b"{}"],
)
def test_bad_bodies(tmp_path: Path, body: bytes) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(path, [body])
    with pytest.raises(LedgerCorruptError) as info:
        list(JsonlLedgerStore(path).entries())
    assert info.value.line == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"v": 2},
        {"v": True},
        {"v": None},
        {"at": 5},
        {"at": "not-a-date"},
        {"at": "2026-03-15T12:00:00"},
        {"provider": ""},
        {"provider": 5},
        {"model": 1},
        {"input_tokens": -1},
        {"input_tokens": True},
        {"output_tokens": "2"},
        {"cost": 0.5},
        {"cost": "abc"},
        {"cost": "-1"},
        {"cost": "NaN"},
        {"cost": "1" * 65},
        {"cost": "1E+999999"},
        {"tags": "a"},
        {"tags": [1]},
        {"tags": ["a"] * 33},
        {"prompt_sha256": "xyz"},
        {"prompt_sha256": 5},
    ],
)
def test_bad_field_values(tmp_path: Path, overrides: dict[str, Any]) -> None:
    path = tmp_path / "ledger.jsonl"
    write_lines(path, [good_line(**overrides)])
    with pytest.raises(LedgerCorruptError) as info:
        list(JsonlLedgerStore(path).entries())
    assert info.value.code == "corrupt"
    assert info.value.line == 1


@pytest.mark.parametrize(
    "key",
    ["v", "at", "provider", "model", "input_tokens", "output_tokens", "cost", "tags"],
)
def test_missing_keys(tmp_path: Path, key: str) -> None:
    path = tmp_path / "ledger.jsonl"
    data = good_record()
    del data[key]
    write_lines(path, [json.dumps(data).encode("utf-8")])
    with pytest.raises(LedgerCorruptError):
        list(JsonlLedgerStore(path).entries())


def test_prompt_hash_and_no_leak(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    events: list[LedgerEvent] = []
    ledger = make(
        JsonlLedgerStore(path),
        monthly_cap=Decimal("1"),
        warn_ratio=Decimal("0.1"),
        on_event=events.append,
    )
    entry = rec(ledger, "0.5", prompt=PROMPT_TEXT)
    expected = hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()
    assert entry.prompt_sha256 == expected
    text = path.read_text(encoding="utf-8")
    assert PROMPT_TEXT not in text
    assert expected in text
    with pytest.raises(BudgetExceededError):
        ledger.check(Decimal("0.6"))
    assert {e["type"] for e in events} == {"recorded", "warning", "blocked"}
    assert PROMPT_TEXT not in repr(events)


def test_prompt_hmac_with_key() -> None:
    key = b"k" * 16
    ledger = make(hash_key=key)
    entry = rec(ledger, prompt=PROMPT_TEXT)
    expected = hmac.new(key, PROMPT_TEXT.encode("utf-8"), hashlib.sha256).hexdigest()
    assert entry.prompt_sha256 == expected
    assert expected != hashlib.sha256(PROMPT_TEXT.encode("utf-8")).hexdigest()
    assert rec(ledger).prompt_sha256 is None


def test_prompt_limits() -> None:
    ledger = make()
    for bad in ("a" * 4_194_305, "\ud800", 5):
        with pytest.raises(LedgerError) as info:
            rec(ledger, prompt=bad)
        assert info.value.code == "invalid_argument"
    assert ledger.spent_this_month() == Decimal("0")


def test_naive_clock_and_bad_clock_values() -> None:
    for value in (datetime(2026, 3, 1), "2026-03-01"):
        ledger = make(clock=lambda value=value: value)
        with pytest.raises(LedgerError) as info:
            rec(ledger)
        assert info.value.code == "invalid_argument"
        with pytest.raises(LedgerError):
            ledger.check(Decimal("1"))
        with pytest.raises(LedgerError):
            ledger.spent_today()
        with pytest.raises(LedgerError):
            ledger.spent_this_month()
        with pytest.raises(LedgerError):
            ledger.summary("model")


def test_clock_overflow_is_invalid_argument() -> None:
    ledger = make(
        clock=lambda: datetime(1, 1, 1, tzinfo=UTC),
        tz=timezone(timedelta(hours=-5)),
    )
    with pytest.raises(LedgerError) as info:
        ledger.spent_today()
    assert info.value.code == "invalid_argument"


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"input_tokens": True},
        {"input_tokens": 1.5},
        {"cost": Decimal("-0.01")},
        {"cost": Decimal("NaN")},
        {"cost": Decimal("Infinity")},
        {"cost": Decimal("1e16")},
        {"cost": 0.5},
        {"provider": ""},
        {"model": "m" * 129},
        {"model": None},
        {"tags": ("a",) * 33},
        {"tags": ("",)},
        {"tags": ("t" * 65,)},
        {"tags": "abc"},
        {"tags": (1,)},
    ],
)
def test_record_rejects_invalid_arguments(overrides: dict[str, Any]) -> None:
    ledger = make()
    with pytest.raises(LedgerError) as info:
        rec(ledger, **overrides)
    assert info.value.code == "invalid_argument"
    assert ledger.spent_this_month() == Decimal("0")


def test_memory_store_drops_oldest_but_aggregates_remain() -> None:
    store = MemoryLedgerStore(max_entries=2)
    ledger = make(store)
    for cost in ("1", "2", "4"):
        rec(ledger, cost)
    kept = list(store.entries())
    assert [e.cost for e in kept] == [Decimal("2"), Decimal("4")]
    assert ledger.spent_this_month() == Decimal("7")


def test_jsonl_store_full_leaves_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    first = make(JsonlLedgerStore(path))
    rec(first, "1")
    one_line = path.stat().st_size
    before = path.read_bytes()
    limited = make(JsonlLedgerStore(path, max_bytes=one_line + 10))
    with pytest.raises(LedgerError) as info:
        rec(limited, "1")
    assert info.value.code == "store_full"
    assert path.read_bytes() == before
    assert limited.spent_this_month() == Decimal("1")
    exact = make(JsonlLedgerStore(path, max_bytes=one_line * 2))
    rec(exact, "1")
    assert path.stat().st_size == one_line * 2
    with pytest.raises(LedgerError) as again:
        rec(exact, "1")
    assert again.value.code == "store_full"


def test_jsonl_store_rejects_oversized_entry(tmp_path: Path) -> None:
    store = JsonlLedgerStore(tmp_path / "ledger.jsonl")
    huge = sample_entry(tags=tuple("t" * 10_000 for _ in range(10)))
    with pytest.raises(LedgerError) as info:
        store.append(huge)
    assert info.value.code == "invalid_argument"


def test_check_reports_daily_first_then_monthly_and_emits_blocked() -> None:
    events: list[LedgerEvent] = []
    both = make(daily_cap=Decimal("1"), monthly_cap=Decimal("1"), on_event=events.append)
    with pytest.raises(BudgetExceededError) as info:
        both.check(Decimal("2"))
    assert info.value.period == "daily"
    assert events[-1]["type"] == "blocked"
    assert events[-1]["model"] is None
    clock = Clock(NOON)
    monthly = make(clock=clock, daily_cap=Decimal("5"), monthly_cap=Decimal("10"))
    rec(monthly, "9")
    clock.now = NOON + timedelta(days=1)
    with pytest.raises(BudgetExceededError) as second:
        monthly.check(Decimal("2"))
    assert second.value.period == "monthly"
    assert second.value.spent == Decimal("9")
    monthly.check(Decimal("1"))


@pytest.mark.parametrize("projected", [Decimal("-1"), Decimal("NaN"), 1, "1"])
def test_check_rejects_bad_projection(projected: Any) -> None:
    ledger = make(daily_cap=Decimal("1"))
    with pytest.raises(LedgerError) as info:
        ledger.check(projected)
    assert info.value.code == "invalid_argument"


def test_check_without_caps_never_raises() -> None:
    ledger = make()
    rec(ledger, "1000")
    ledger.check(Decimal("1000000"))


def test_summary_groupings() -> None:
    clock = Clock(NOON)
    ledger = make(clock=clock)
    rec(ledger, "1", model="a", provider="p1", tags=("x", "y"))
    rec(ledger, "2", model="b", provider="p1", tags=("x", "x"))
    rec(ledger, "4", model="a", provider="p2")
    assert ledger.summary("model") == {"a": Decimal("5"), "b": Decimal("2")}
    assert ledger.summary("provider") == {"p1": Decimal("3"), "p2": Decimal("4")}
    assert ledger.summary("tag") == {
        "x": Decimal("3"),
        "y": Decimal("1"),
        "(untagged)": Decimal("4"),
    }
    clock.now = NOON + timedelta(days=1)
    rec(ledger, "8", model="a")
    assert ledger.summary("model", period="day") == {"a": Decimal("8")}
    assert ledger.summary("model", period="month") == {"a": Decimal("13"), "b": Decimal("2")}


def test_summary_rejects_bad_arguments() -> None:
    ledger = make()
    with pytest.raises(LedgerError) as info:
        ledger.summary("user")  # type: ignore[arg-type]
    assert info.value.code == "invalid_argument"
    with pytest.raises(LedgerError):
        ledger.summary("model", period="year")  # type: ignore[arg-type]


def test_aggregates_keep_bounded_day_window() -> None:
    clock = Clock(datetime(2025, 1, 1, 12, 0, tzinfo=UTC))
    ledger = make(clock=clock)
    start = clock.now
    for i in range(401):
        clock.now = start + timedelta(days=i)
        rec(ledger, "1")
    clock.now = start
    assert ledger.spent_today() == Decimal("0")
    assert ledger.summary("model", period="day") == {"gpt-x": Decimal("1")}
    clock.now = start + timedelta(days=400)
    assert ledger.spent_today() == Decimal("1")


def test_aggregates_keep_bounded_month_window() -> None:
    clock = Clock(datetime(2020, 1, 1, 12, 0, tzinfo=UTC))
    ledger = make(clock=clock)

    def month_start(i: int) -> datetime:
        return datetime(2020 + i // 12, i % 12 + 1, 1, 12, 0, tzinfo=UTC)

    for i in range(37):
        clock.now = month_start(i)
        rec(ledger, "1")
    clock.now = month_start(0)
    assert ledger.spent_this_month() == Decimal("0")
    assert ledger.summary("model") == {"gpt-x": Decimal("1")}
    clock.now = month_start(36)
    assert ledger.spent_this_month() == Decimal("1")


def test_record_returns_utc_entry_and_is_immutable() -> None:
    plus_two = timezone(timedelta(hours=2))
    ledger = make(clock=lambda: datetime(2026, 3, 15, 14, 0, tzinfo=plus_two))
    entry = rec(ledger, "1", tags=["a"])
    assert entry.at == NOON
    assert entry.at.utcoffset() == timedelta(0)
    assert entry.tags == ("a",)
    with pytest.raises(AttributeError):
        entry.cost = Decimal("2")  # type: ignore[misc]


def test_failing_callback_is_swallowed_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    def boom(event: LedgerEvent) -> None:
        raise RuntimeError("nope")

    ledger = make(on_event=boom)
    with caplog.at_level(logging.WARNING, logger="llm_cost_ledger"):
        rec(ledger, "1", model="visible-model", prompt=PROMPT_TEXT)
    assert ledger.spent_this_month() == Decimal("1")
    assert "RuntimeError" in caplog.text
    assert PROMPT_TEXT not in caplog.text


def test_callback_may_reenter_the_ledger() -> None:
    seen: list[Decimal] = []
    holder: dict[str, CostLedger] = {}

    def on_event(event: LedgerEvent) -> None:
        seen.append(holder["ledger"].spent_today())

    holder["ledger"] = make(on_event=on_event)
    rec(holder["ledger"], "2")
    assert seen == [Decimal("2")]


def test_concurrent_records_are_consistent() -> None:
    ledger = make()

    def work() -> None:
        for _ in range(50):
            rec(ledger, "0.01")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert ledger.spent_this_month() == Decimal("4")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"daily_cap": Decimal("0")},
        {"daily_cap": Decimal("-1")},
        {"daily_cap": 1},
        {"monthly_cap": Decimal("NaN")},
        {"monthly_cap": Decimal("1e20")},
        {"warn_ratio": Decimal("0")},
        {"warn_ratio": Decimal("1.1")},
        {"warn_ratio": Decimal("NaN")},
        {"warn_ratio": 0.5},
        {"tz": "UTC"},
        {"clock": 5},
        {"hash_key": b""},
        {"hash_key": "text"},
        {"hash_key": b"k" * 1025},
        {"on_event": 5},
    ],
)
def test_constructor_validation(kwargs: dict[str, Any]) -> None:
    with pytest.raises(LedgerError) as info:
        make(**kwargs)
    assert info.value.code == "invalid_argument"


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "3"])
def test_store_validation(tmp_path: Path, value: Any) -> None:
    with pytest.raises(LedgerError) as info:
        MemoryLedgerStore(max_entries=value)
    assert info.value.code == "invalid_argument"
    with pytest.raises(LedgerError):
        JsonlLedgerStore(tmp_path / "x.jsonl", max_bytes=value)


def test_error_messages_are_capped_and_codes_stable() -> None:
    assert len(str(LedgerError("corrupt", "x" * 500))) == 200
    assert LedgerError("store_full", "m").code == "store_full"


def test_package_metadata_and_logging() -> None:
    assert llm_cost_ledger.__version__ == "0.1.0"
    handlers = logging.getLogger("llm_cost_ledger").handlers
    assert any(isinstance(h, logging.NullHandler) for h in handlers)
    for name in llm_cost_ledger.__all__:
        assert hasattr(llm_cost_ledger, name)
