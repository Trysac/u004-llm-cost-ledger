"""Ledger entry model, validation and JSONL encoding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .errors import LedgerCorruptError, LedgerError

MAX_LINE_BYTES = 65_536
MAX_NAME_LENGTH = 128
MAX_TAGS = 32
MAX_TAG_LENGTH = 64
MAX_COST = Decimal("1000000000000000")
MAX_COST_TEXT = 64
SCHEMA_VERSION = 1
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class Entry:
    """One recorded LLM call.

    ``at`` is an aware UTC datetime. ``prompt_sha256`` is a correlation hash of the
    prompt (never the prompt itself).
    """

    at: datetime
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: Decimal
    tags: tuple[str, ...] = ()
    prompt_sha256: str | None = None


def _invalid(message: str) -> LedgerError:
    return LedgerError("invalid_argument", message)


def validate_cost(value: object, name: str = "cost") -> Decimal:
    """Return ``value`` if it is a finite Decimal within bounds, else raise."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise _invalid(f"{name} must be a finite Decimal")
    if value < 0 or value > MAX_COST:
        raise _invalid(f"{name} must be between 0 and {MAX_COST}")
    return value


def _name(value: object, field: str) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= MAX_NAME_LENGTH:
        raise _invalid(f"{field} must be a string of 1 to {MAX_NAME_LENGTH} characters")
    return value


def _tokens(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _invalid(f"{field} must be a non-negative integer")
    return value


def _tags(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple | list) or len(value) > MAX_TAGS:
        raise _invalid(f"tags must be a sequence of at most {MAX_TAGS} strings")
    for tag in value:
        if not isinstance(tag, str) or not 0 < len(tag) <= MAX_TAG_LENGTH:
            raise _invalid(f"each tag must be a string of 1 to {MAX_TAG_LENGTH} characters")
    return tuple(value)


def _digest(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _HEX:
        raise _invalid("prompt_sha256 must be 64 lowercase hex characters")
    return value


def build_entry(
    *,
    at: datetime,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost: Decimal,
    tags: tuple[str, ...] | list[str] = (),
    prompt_sha256: str | None = None,
) -> Entry:
    """Validate every field and return an immutable ``Entry`` with ``at`` in UTC."""
    if not isinstance(at, datetime) or at.utcoffset() is None:
        raise _invalid("timestamp must be an aware datetime")
    return Entry(
        at=at.astimezone(timezone.utc),
        provider=_name(provider, "provider"),
        model=_name(model, "model"),
        input_tokens=_tokens(input_tokens, "input_tokens"),
        output_tokens=_tokens(output_tokens, "output_tokens"),
        cost=validate_cost(cost),
        tags=_tags(tags),
        prompt_sha256=_digest(prompt_sha256),
    )


def encode_entry(entry: Entry) -> bytes:
    """Encode ``entry`` as one newline-terminated JSON line."""
    payload = {
        "v": SCHEMA_VERSION,
        "at": entry.at.isoformat(),
        "provider": entry.provider,
        "model": entry.model,
        "input_tokens": entry.input_tokens,
        "output_tokens": entry.output_tokens,
        "cost": str(entry.cost),
        "tags": list(entry.tags),
        "prompt_sha256": entry.prompt_sha256,
    }
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def decode_entry(body: bytes, line: int) -> Entry:
    """Decode one JSON line (without its newline) or raise ``LedgerCorruptError``."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        raise LedgerCorruptError(line, "invalid JSON") from None
    if not isinstance(data, dict):
        raise LedgerCorruptError(line, "record is not an object")
    version = data.get("v")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise LedgerCorruptError(line, "unsupported schema version")
    at_raw = data.get("at")
    cost_raw = data.get("cost")
    tags_raw = data.get("tags")
    if not isinstance(at_raw, str) or not isinstance(cost_raw, str):
        raise LedgerCorruptError(line, "malformed field")
    if not isinstance(tags_raw, list) or len(cost_raw) > MAX_COST_TEXT:
        raise LedgerCorruptError(line, "malformed field")
    try:
        return build_entry(
            at=datetime.fromisoformat(at_raw),
            provider=data.get("provider"),
            model=data.get("model"),
            input_tokens=data.get("input_tokens"),
            output_tokens=data.get("output_tokens"),
            cost=Decimal(cost_raw),
            tags=tags_raw,
            prompt_sha256=data.get("prompt_sha256"),
        )
    except (LedgerError, ValueError, ArithmeticError):
        raise LedgerCorruptError(line, "invalid field value") from None
