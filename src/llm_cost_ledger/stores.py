"""Storage backends for ledger entries."""

from __future__ import annotations

import os
import threading
from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Protocol

from .entry import MAX_LINE_BYTES, Entry, decode_entry, encode_entry
from .errors import LedgerCorruptError, LedgerError

DEFAULT_MAX_ENTRIES = 100_000
DEFAULT_MAX_BYTES = 268_435_456


class LedgerStore(Protocol):
    """Append-only storage of ledger entries."""

    def append(self, entry: Entry) -> None:
        """Persist ``entry``."""

    def entries(self) -> Iterable[Entry]:
        """Return every stored entry, oldest first."""


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LedgerError("invalid_argument", f"{name} must be a positive integer")
    return value


class MemoryLedgerStore:
    """Thread-safe in-memory store that drops the oldest entry when full.

    Dropping entries never changes the aggregates held by a ``CostLedger``.
    """

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._items: deque[Entry] = deque(maxlen=_positive_int(max_entries, "max_entries"))
        self._lock = threading.Lock()

    def append(self, entry: Entry) -> None:
        """Store ``entry``, evicting the oldest one if the store is full."""
        with self._lock:
            self._items.append(entry)

    def entries(self) -> Iterable[Entry]:
        """Return a snapshot of the stored entries, oldest first."""
        with self._lock:
            return tuple(self._items)


class JsonlLedgerStore:
    """Append-only JSON Lines file store.

    Each line is one JSON object (schema version 1) of at most 65,536 bytes. An append
    that would grow the file past ``max_bytes`` raises ``LedgerError`` with code
    ``store_full`` and leaves the file unchanged. Reading raises ``LedgerCorruptError``
    for an unreadable line, an over-long line or a truncated final line.
    """

    def __init__(
        self,
        path: Path,
        *,
        fsync: bool = False,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._path = Path(path)
        self._fsync = fsync
        self._max_bytes = _positive_int(max_bytes, "max_bytes")
        self._lock = threading.Lock()

    def append(self, entry: Entry) -> None:
        """Append ``entry`` as one line."""
        data = encode_entry(entry)
        if len(data) - 1 > MAX_LINE_BYTES:
            raise LedgerError("invalid_argument", "entry is too large for a ledger line")
        with self._lock:
            try:
                size = self._path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size + len(data) > self._max_bytes:
                raise LedgerError("store_full", "ledger file size limit reached")
            with self._path.open("ab") as handle:
                handle.write(data)
                handle.flush()
                if self._fsync:
                    os.fsync(handle.fileno())

    def entries(self) -> Iterator[Entry]:
        """Lazily read every entry; a missing file is an empty ledger."""
        if not self._path.exists():
            return
        with self._path.open("rb") as handle:
            number = 0
            while True:
                raw = handle.readline(MAX_LINE_BYTES + 2)
                if not raw:
                    return
                number += 1
                if not raw.endswith(b"\n"):
                    raise LedgerCorruptError(number, "line too long or truncated")
                body = raw[:-1]
                if len(body) > MAX_LINE_BYTES:
                    raise LedgerCorruptError(number, "line too long")
                yield decode_entry(body, number)
