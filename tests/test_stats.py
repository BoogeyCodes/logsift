from datetime import datetime, timedelta, timezone

from logsift.models import Level, Record
from logsift.stats import floor_to_bucket, summarise, template

T0 = datetime(2025, 3, 10, 9, 0, 0, tzinfo=timezone.utc)


def rec(level, message, ts=None, source="a"):
    return Record(raw=message, line_no=1, source=source, timestamp=ts, level=level, message=message)


class TestTemplate:
    def test_numbers_collapse(self):
        assert template("User 4521 logged in from 10.0.0.7") == template("User 88 logged in from 10.0.0.9")
        assert template("Processed order 118 in 571 ms") == "Processed order # in # ms"

    def test_uuid_hex_and_quoted_strings_collapse(self):
        a = template('Request 6f1c2a7e-9d1b-4b9c-8f1e-1a2b3c4d5e6f for "alice" token deadbeefcafe1234')
        b = template('Request 0a9c7e55-2b3c-4d5e-8f1e-9a8b7c6d5e4f for "bob" token 0123456789abcdef')
        assert a == b
        assert a == "Request <uuid> for <str> token <hex>"

    def test_short_words_untouched(self):
        assert template("Cache hit") == "Cache hit"


class TestFloorToBucket:
    def test_floors_to_five_minutes(self):
        ts = T0 + timedelta(minutes=7, seconds=31)
        assert floor_to_bucket(ts, timedelta(minutes=5)) == T0 + timedelta(minutes=5)

    def test_exact_boundary_unchanged(self):
        assert floor_to_bucket(T0, timedelta(hours=1)) == T0


class TestSummarise:
    def test_counts_levels_sources_and_span(self):
        records = [
            rec(Level.INFO, "started", T0),
            rec(Level.ERROR, "Failed order 1", T0 + timedelta(minutes=1), source="b"),
            rec(Level.ERROR, "Failed order 2", T0 + timedelta(minutes=6)),
            rec(Level.UNKNOWN, "  File x.py", None),
        ]
        s = summarise(records, bucket=timedelta(minutes=5))

        assert s.total == 4
        assert s.untimed == 1
        assert s.by_level[Level.ERROR] == 2
        assert s.by_source == {"a": 3, "b": 1}
        assert s.first_ts == T0
        assert s.last_ts == T0 + timedelta(minutes=6)
        assert s.span == timedelta(minutes=6)
        assert s.error_templates.most_common(1) == [("Failed order #", 2)]
        assert s.buckets == {T0: 2, T0 + timedelta(minutes=5): 1}
        assert s.error_buckets == {T0: 1, T0 + timedelta(minutes=5): 1}

    def test_to_dict_is_json_friendly(self):
        s = summarise([rec(Level.WARN, "w", T0)], bucket=timedelta(minutes=1))
        d = s.to_dict(top=3)
        assert d["by_level"] == {"WARN": 1}
        assert d["first"] == T0.isoformat()
        assert d["buckets"] == [{"start": T0.isoformat(), "total": 1, "errors": 0}]
        assert d["top_messages"] == [{"template": "w", "count": 1}]

    def test_empty_input(self):
        s = summarise([])
        assert s.total == 0
        assert s.span is None
        assert s.to_dict()["first"] is None
