import re
from datetime import datetime, timedelta, timezone

import pytest

from logsift.filters import FilterSpec, apply, parse_duration, parse_time_bound, parse_where
from logsift.models import Level, Record

NOW = datetime(2025, 3, 10, 10, 0, 0, tzinfo=timezone.utc)


def rec(level=Level.INFO, message="", ts=None, **fields):
    return Record(raw=message, line_no=1, timestamp=ts, level=level, message=message, fields=fields)


class TestParseDuration:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("30s", timedelta(seconds=30)),
            ("15m", timedelta(minutes=15)),
            ("2h", timedelta(hours=2)),
            ("1.5d", timedelta(hours=36)),
            ("1w", timedelta(weeks=1)),
            ("10 min", timedelta(minutes=10)),
            ("3 hours", timedelta(hours=3)),
        ],
    )
    def test_accepted_forms(self, text, expected):
        assert parse_duration(text) == expected

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_duration("soon")


class TestParseTimeBound:
    def test_relative_is_subtracted_from_now(self):
        assert parse_time_bound("15m", now=NOW) == NOW - timedelta(minutes=15)

    def test_absolute_is_parsed(self):
        assert parse_time_bound("2025-03-10T09:30:00Z", now=NOW) == NOW - timedelta(minutes=30)

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            parse_time_bound("later", now=NOW)


class TestParseWhere:
    def test_coerces_scalars(self):
        assert parse_where("status=500") == ("status", 500)
        assert parse_where("ratio=0.5") == ("ratio", 0.5)
        assert parse_where("ok=true") == ("ok", True)
        assert parse_where("err=null") == ("err", None)
        assert parse_where("user.name=ann") == ("user.name", "ann")
        assert parse_where("path=/a=b") == ("path", "/a=b")

    def test_rejects_missing_equals(self):
        with pytest.raises(ValueError):
            parse_where("status")
        with pytest.raises(ValueError):
            parse_where("=500")


class TestFilterSpec:
    def test_empty_spec_keeps_everything(self):
        pred = FilterSpec().build()
        assert pred(rec(Level.UNKNOWN))

    def test_min_level_excludes_lower_and_unknown(self):
        pred = FilterSpec(min_level=Level.WARN).build()
        assert not pred(rec(Level.INFO))
        assert pred(rec(Level.WARN))
        assert pred(rec(Level.FATAL))
        assert not pred(rec(Level.UNKNOWN))

    def test_exact_levels_override_min_level(self):
        pred = FilterSpec(min_level=Level.ERROR, exact_levels={Level.DEBUG}).build()
        assert pred(rec(Level.DEBUG))
        assert not pred(rec(Level.ERROR))

    def test_time_window(self):
        pred = FilterSpec(since=NOW - timedelta(minutes=10), until=NOW).build()
        assert pred(rec(ts=NOW - timedelta(minutes=5)))
        assert not pred(rec(ts=NOW - timedelta(minutes=11)))
        assert not pred(rec(ts=NOW + timedelta(seconds=1)))

    def test_untimed_records_kept_by_default_and_droppable(self):
        assert FilterSpec(since=NOW).build()(rec())
        assert not FilterSpec(since=NOW, keep_untimed=False).build()(rec())

    def test_grep_is_and_and_matches_raw_too(self):
        pred = FilterSpec(grep=[re.compile("order"), re.compile(r"\b121\b")]).build()
        assert pred(rec(message="Failed order 121"))
        assert not pred(rec(message="Failed order 122"))
        raw_only = Record(raw='{"message":"order 121"}', line_no=1, message="")
        assert pred(raw_only)

    def test_exclude(self):
        pred = FilterSpec(exclude=[re.compile("health", re.IGNORECASE)]).build()
        assert not pred(rec(message="GET /Health 200"))
        assert pred(rec(message="GET /orders 200"))

    def test_where_with_nested_keys_and_type_tolerance(self):
        pred = FilterSpec(where={"status": 500, "user.name": "ann"}).build()
        assert pred(rec(status=500, user={"name": "ann"}))
        assert pred(rec(status="500", user={"name": "ann"}))
        assert not pred(rec(status=200, user={"name": "ann"}))
        assert not pred(rec(status=500))

    def test_apply_streams(self):
        records = [rec(Level.INFO), rec(Level.ERROR), rec(Level.WARN)]
        kept = list(apply(iter(records), FilterSpec(min_level=Level.WARN).build()))
        assert [r.level for r in kept] == [Level.ERROR, Level.WARN]
