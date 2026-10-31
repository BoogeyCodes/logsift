from datetime import datetime, timezone

from logsift.models import Level
from logsift.parsers import parse_json_line, parse_line, parse_lines, parse_text_line, parse_timestamp


class TestParseTimestamp:
    def test_iso_with_z(self):
        assert parse_timestamp("2025-03-10T09:00:01.120Z") == datetime(2025, 3, 10, 9, 0, 1, 120000, tzinfo=timezone.utc)

    def test_iso_with_offset(self):
        ts = parse_timestamp("2025-03-10T14:00:00+05:00")
        assert ts == datetime(2025, 3, 10, 9, 0, 0, tzinfo=timezone.utc)

    def test_space_separator_and_comma_millis(self):
        assert parse_timestamp("2025-03-10 09:00:00,015") == datetime(2025, 3, 10, 9, 0, 0, 15000, tzinfo=timezone.utc)

    def test_naive_assumed_utc(self):
        ts = parse_timestamp("2025-03-10T09:00:00")
        assert ts is not None and ts.tzinfo is timezone.utc

    def test_epoch_seconds_and_millis(self):
        assert parse_timestamp(1741597201) == datetime(2025, 3, 10, 9, 0, 1, tzinfo=timezone.utc)
        assert parse_timestamp(1741597201000) == datetime(2025, 3, 10, 9, 0, 1, tzinfo=timezone.utc)
        assert parse_timestamp("1741597201") == datetime(2025, 3, 10, 9, 0, 1, tzinfo=timezone.utc)

    def test_syslog_style_uses_current_year(self):
        ts = parse_timestamp("Mar 10 09:00:01")
        assert ts is not None
        assert (ts.month, ts.day, ts.hour) == (3, 10, 9)

    def test_garbage_returns_none(self):
        assert parse_timestamp("yesterday-ish") is None
        assert parse_timestamp("") is None
        assert parse_timestamp(None) is None
        assert parse_timestamp(True) is None


class TestJsonLines:
    def test_serilog_shape(self):
        r = parse_json_line('{"timestamp":"2025-03-10T09:00:01Z","level":"Information","message":"hi","status":200}', 1, "a")
        assert r is not None
        assert r.level is Level.INFO
        assert r.message == "hi"
        assert r.fields == {"status": 200}
        assert r.timestamp == datetime(2025, 3, 10, 9, 0, 1, tzinfo=timezone.utc)
        assert r.source == "a"

    def test_pino_numeric_level_and_epoch_millis(self):
        r = parse_json_line('{"level":50,"time":1741597201000,"msg":"boom","pid":12}', 3, "p")
        assert r is not None
        assert r.level is Level.ERROR
        assert r.message == "boom"
        assert r.timestamp is not None and r.timestamp.year == 2025
        assert r.fields == {"pid": 12}

    def test_compact_serilog_keys(self):
        r = parse_json_line('{"@t":"2025-03-10T09:00:01Z","@l":"Warning","@mt":"Slow {Elapsed}","Elapsed":1830}', 1, "s")
        assert r is not None
        assert r.level is Level.WARN
        assert r.message == "Slow {Elapsed}"
        assert r.fields == {"@t": "2025-03-10T09:00:01Z", "Elapsed": 1830}

    def test_missing_level_is_unknown(self):
        r = parse_json_line('{"message":"no level here"}', 1, "x")
        assert r is not None
        assert r.level is Level.UNKNOWN
        assert r.timestamp is None

    def test_non_object_json_is_not_json_record(self):
        assert parse_json_line("[1,2,3]", 1, "x") is None
        assert parse_json_line('"just a string"', 1, "x") is None
        assert parse_json_line("{not json", 1, "x") is None


class TestTextLines:
    def test_python_logging_format(self):
        r = parse_text_line("2025-03-10 09:00:04,880 ERROR worker.handler: Failed to process order 121", 6, "w")
        assert r.level is Level.ERROR
        assert r.timestamp == datetime(2025, 3, 10, 9, 0, 4, 880000, tzinfo=timezone.utc)
        assert r.message == "worker.handler: Failed to process order 121"

    def test_bracketed_level(self):
        r = parse_text_line("2025-03-10T09:00:00Z [WRN] Disk at 91%", 1, "t")
        assert r.level is Level.WARN
        assert r.message == "Disk at 91%"

    def test_level_token_inside_word_is_not_matched(self):
        r = parse_text_line("2025-03-10T09:00:00Z INFO Loaded INFORMATION_SCHEMA for errors table", 1, "t")
        assert r.level is Level.INFO
        assert "INFORMATION_SCHEMA" in r.message

    def test_key_value_pairs_become_fields(self):
        r = parse_text_line('2025-03-10T09:00:00Z INFO request done user=ann status=200 path="/a b" ok=true', 1, "t")
        assert r.fields == {"user": "ann", "status": 200, "path": "/a b", "ok": True}
        assert r.message == "request done"

    def test_unparseable_line_is_kept(self):
        r = parse_text_line('  File "/app/worker/handler.py", line 88, in process', 10, "w")
        assert r.level is Level.UNKNOWN
        assert r.timestamp is None
        assert r.raw.startswith("  File")

    def test_syslog_line(self):
        r = parse_text_line("Mar 10 09:00:01 host sshd[123]: error: Could not load host key", 1, "s")
        assert r.timestamp is not None
        assert r.level is Level.ERROR


class TestParseLines:
    def test_mixed_input_and_blank_lines_skipped(self):
        lines = [
            '{"level":"info","message":"json"}\n',
            "\n",
            "2025-03-10T09:00:00Z WARN text\n",
            "   \n",
        ]
        records = list(parse_lines(lines, source="mix"))
        assert [r.line_no for r in records] == [1, 3]
        assert [r.level for r in records] == [Level.INFO, Level.WARN]

    def test_parse_line_prefers_json(self):
        r = parse_line('{"level":"error","message":"2025-03-10T09:00:00Z looks like text"}', 1)
        assert r.level is Level.ERROR
        assert r.fields == {}
