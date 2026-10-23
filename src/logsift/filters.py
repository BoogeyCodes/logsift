"""Predicates that decide whether a :class:`Record` should be kept."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from logsift.models import Level, Record
from logsift.parsers import parse_timestamp

Predicate = Callable[[Record], bool]

_DURATION = re.compile(
    r"^(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|weeks?|w)$",
    re.IGNORECASE,
)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> timedelta:
    """``'15m'`` -> 15 minutes, ``'2h'`` -> 2 hours, ``'1.5d'`` -> 36 hours."""
    m = _DURATION.match(text.strip())
    if not m:
        raise ValueError(f"Unrecognised duration: {text!r} (try 30s, 15m, 2h, 1d, 1w)")
    unit = m.group("unit").lower()[0]
    return timedelta(seconds=float(m.group("n")) * _UNIT_SECONDS[unit])


def parse_time_bound(text: str, *, now: datetime | None = None) -> datetime:
    """Accept an absolute timestamp or a relative duration such as ``'15m'`` meaning 15 minutes ago."""
    now = now or datetime.now(tz=timezone.utc)
    try:
        return now - parse_duration(text)
    except ValueError:
        pass
    parsed = parse_timestamp(text)
    if parsed is None:
        raise ValueError(f"Unrecognised time: {text!r} (use ISO-8601 or a duration like 15m)")
    return parsed


@dataclass(slots=True)
class FilterSpec:
    """Everything the CLI collects; :meth:`build` turns it into one predicate."""

    min_level: Level | None = None
    exact_levels: set[Level] | None = None
    since: datetime | None = None
    until: datetime | None = None
    grep: list[re.Pattern[str]] | None = None
    exclude: list[re.Pattern[str]] | None = None
    where: dict[str, Any] | None = None
    keep_untimed: bool = True

    def build(self) -> Predicate:
        checks: list[Predicate] = []

        if self.exact_levels:
            levels = self.exact_levels
            checks.append(lambda r: r.level in levels)
        elif self.min_level is not None:
            threshold = self.min_level
            checks.append(lambda r: r.level is not Level.UNKNOWN and r.level >= threshold)

        if self.since is not None or self.until is not None:
            since, until, keep = self.since, self.until, self.keep_untimed

            def in_window(r: Record) -> bool:
                if r.timestamp is None:
                    return keep
                after_start = since is None or r.timestamp >= since
                before_end = until is None or r.timestamp <= until
                return after_start and before_end

            checks.append(in_window)

        if self.grep:
            patterns = self.grep
            checks.append(lambda r: all(p.search(r.message) or p.search(r.raw) for p in patterns))

        if self.exclude:
            excluded = self.exclude
            checks.append(lambda r: not any(p.search(r.message) or p.search(r.raw) for p in excluded))

        if self.where:
            conditions = self.where
            checks.append(lambda r: all(_field_matches(r.fields, k, v) for k, v in conditions.items()))

        def predicate(record: Record) -> bool:
            return all(check(record) for check in checks)

        return predicate


def _field_matches(fields: dict[str, Any], dotted_key: str, expected: Any) -> bool:
    value: Any = fields
    for part in dotted_key.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    if value == expected:
        return True
    # Allow "status=500" to match the integer 500 and vice versa.
    return str(value) == str(expected)


def parse_where(text: str) -> tuple[str, Any]:
    """``'status=500'`` -> ``('status', 500)``; ``'user.name=ann'`` -> ``('user.name', 'ann')``."""
    if "=" not in text:
        raise ValueError(f"--where expects key=value, got {text!r}")
    key, _, raw = text.partition("=")
    key = key.strip()
    if not key:
        raise ValueError(f"--where expects key=value, got {text!r}")
    raw = raw.strip()
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return key, lowered == "true"
    if lowered in ("null", "none"):
        return key, None
    try:
        return key, int(raw)
    except ValueError:
        pass
    try:
        return key, float(raw)
    except ValueError:
        return key, raw


def apply(records: Iterable[Record], predicate: Predicate) -> Iterator[Record]:
    return (r for r in records if predicate(r))
