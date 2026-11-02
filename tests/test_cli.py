import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from logsift.cli import main

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOW = datetime(2025, 3, 10, 9, 10, 0, tzinfo=timezone.utc)


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    code = main(list(argv), stdout=out, now=NOW)
    return code, out.getvalue()


class TestFilterCommand:
    def test_default_command_is_filter_and_pretty_output(self):
        code, out = run(str(EXAMPLES / "api.jsonl"), "--level", "error")
        lines = out.strip().splitlines()
        assert code == 0
        assert len(lines) == 3
        assert lines[0].startswith("2025-03-10 09:01:12 ERROR HTTP POST /api/payments responded 500")
        assert "status=500" in lines[0]
        assert lines[-1].startswith("2025-03-10 09:06:55 FATAL")

    def test_json_output_round_trips(self):
        code, out = run("filter", str(EXAMPLES / "api.jsonl"), "--where", "status=500", "--format", "json")
        objs = [json.loads(line) for line in out.strip().splitlines()]
        assert code == 0
        assert len(objs) == 2
        assert all(o["level"] == "ERROR" and o["fields"]["status"] == 500 for o in objs)
        assert objs[0]["source"] == "api.jsonl"

    def test_raw_output_preserves_original_lines(self):
        _, out = run(str(EXAMPLES / "worker.log"), "--grep", "order 121", "--format", "raw")
        assert out.strip() == "2025-03-10 09:00:04,880 ERROR worker.handler: Failed to process order 121: connection refused to inventory-svc:8080"

    def test_since_relative_to_now(self):
        _, out = run(str(EXAMPLES / "api.jsonl"), "--since", "6m", "--drop-untimed")
        lines = out.strip().splitlines()
        assert [line[:19] for line in lines] == ["2025-03-10 09:04:02", "2025-03-10 09:06:55"]

    def test_multiple_files_and_gzip(self, tmp_path: Path):
        gz = tmp_path / "worker.log.gz"
        with gzip.open(gz, "wb") as fh:
            fh.write((EXAMPLES / "worker.log").read_bytes())

        code, out = run(str(EXAMPLES / "api.jsonl"), str(gz), "--only", "fatal,error", "--format", "json")
        objs = [json.loads(line) for line in out.strip().splitlines()]
        assert code == 0
        assert {o["source"] for o in objs} == {"api.jsonl", "worker.log.gz"}
        assert len(objs) == 5

    def test_exclude_and_ignore_case(self):
        _, out = run(str(EXAMPLES / "worker.log"), "--level", "info", "--exclude", "PROCESSED", "-i", "--format", "raw")
        assert "Processed order" not in out
        assert "Worker 1 started" in out

    def test_limit(self):
        _, out = run(str(EXAMPLES / "api.jsonl"), "--limit", "2")
        assert len(out.strip().splitlines()) == 2

    def test_no_matches_exit_code_1(self):
        code, out = run(str(EXAMPLES / "api.jsonl"), "--grep", "definitely-not-present")
        assert code == 1
        assert out == ""

    def test_missing_file_exit_code_2(self, capsys):
        code, _ = run("nope.log")
        assert code == 2
        assert "no such file" in capsys.readouterr().err

    def test_reads_stdin_when_no_files(self, monkeypatch):
        monkeypatch.setattr("sys.stdin", io.StringIO('{"level":"warn","message":"from stdin"}\n'))
        code, out = run("--level", "warn")
        assert code == 0
        assert "from stdin" in out

    def test_bad_level_is_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            run(str(EXAMPLES / "api.jsonl"), "--level", "loud")
        assert exc.value.code == 2


class TestStatsCommand:
    def test_text_summary(self):
        code, out = run("stats", str(EXAMPLES / "api.jsonl"), "--bucket", "5m", "--top", "3")
        assert code == 0
        assert "Lines:      11" in out
        assert "ERROR" in out and "FATAL" in out
        assert "HTTP POST /api/payments responded #" in out
        assert "Per 5m (total / errors):" in out
        assert "2025-03-10 09:00" in out and "2025-03-10 09:05" in out

    def test_json_summary(self):
        code, out = run("stats", str(EXAMPLES / "worker.log"), "--json", "--bucket", "5m")
        data = json.loads(out)
        assert code == 0
        assert data["total"] == 15
        assert data["untimed"] == 4
        assert data["by_level"]["ERROR"] == 2
        assert data["top_errors"][0]["template"] == "worker.handler: Failed to process order #: connection refused to inventory-svc:#"
        assert [b["errors"] for b in data["buckets"]] == [1, 1]

    def test_filters_apply_before_stats(self):
        _, out = run("stats", str(EXAMPLES / "api.jsonl"), "--where", "status=200", "--json")
        data = json.loads(out)
        assert data["total"] == 3
        assert data["by_level"] == {"INFO": 3}
