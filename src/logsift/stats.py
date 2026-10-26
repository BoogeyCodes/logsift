"""Aggregations over a stream of records."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from logsift.models import Level, Record

_NUMBERS = re.compile(r"\b\d+(?:\.\d+)?\b")
_HEX = re.compile(r"\b[0-9a-fA-F]{8,}\b")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def template(message: str) -> str:
    """Collapse variable parts of a message so similar lines group together.

    ``"User 4521 logged in from 10.0.0.7"`` and ``"User 88 logged in from 10.0.0.9"``
    both become ``"User # logged in from #.#.#.#"``.
    """
    text = _UUID.sub("<uuid>", message)
    text = _QUOTED.sub("<str>", text)
    text = _HEX.sub("<hex>", text)
    text = _NUMBERS.sub("#", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(slots=True)
class Summary:
    total: int = 0
    by_level: Counter[Level] = field(default_factory=Counter)
    by_source: Counter[str] = field(default_factory=Counter)
    templates: Counter[str] = field(default_factory=Counter)
    error_templates: Counter[str] = field(default_factory=Counter)
    buckets: Counter[datetime] = field(default_factory=Counter)
    error_buckets: Counter[datetime] = field(default_factory=Counter)
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    untimed: int = 0

    def to_dict(self, top: int = 10) -> dict[str, Any]:
        return {
            "total": self.total,
            "untimed": self.untimed,
            "first": self.first_ts.isoformat() if self.first_ts else None,
            "last": self.last_ts.isoformat() if self.last_ts else None,
            "span_seconds": self.span.total_seconds() if self.span else None,
            "by_level": {lvl.name: n for lvl, n in sorted(self.by_level.items(), key=lambda kv: kv[0])},
            "by_source": dict(self.by_source.most_common()),
            "top_messages": [{"template": t, "count": n} for t, n in self.templates.most_common(top)],
            "top_errors": [{"template": t, "count": n} for t, n in self.error_templates.most_common(top)],
            "buckets": [
                {"start": start.isoformat(), "total": n, "errors": self.error_buckets.get(start, 0)}
                for start, n in sorted(self.buckets.items())
            ],
        }

    @property
    def span(self) -> timedelta | None:
        if self.first_ts and self.last_ts:
            return self.last_ts - self.first_ts
        return None


def floor_to_bucket(ts: datetime, size: timedelta) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    seconds = (ts - epoch).total_seconds()
    width = size.total_seconds()
    return epoch + timedelta(seconds=seconds - (seconds % width))


def summarise(records: Iterable[Record], bucket: timedelta | None = None) -> Summary:
    s = Summary()
    for r in records:
        s.total += 1
        s.by_level[r.level] += 1
        s.by_source[r.source] += 1

        tpl = template(r.message) if r.message else "<empty>"
        s.templates[tpl] += 1
        is_error = r.level >= Level.ERROR
        if is_error:
            s.error_templates[tpl] += 1

        if r.timestamp is None:
            s.untimed += 1
            continue
        if s.first_ts is None or r.timestamp < s.first_ts:
            s.first_ts = r.timestamp
        if s.last_ts is None or r.timestamp > s.last_ts:
            s.last_ts = r.timestamp
        if bucket is not None:
            start = floor_to_bucket(r.timestamp, bucket)
            s.buckets[start] += 1
            if is_error:
                s.error_buckets[start] += 1
    return s
