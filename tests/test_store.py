from datetime import datetime, timezone

from logsift.models import Level
from logsift.parsers import parse_lines
from logsift.store import LogStore

SAMPLE = [
    '{"timestamp":"2025-03-10T09:00:01Z","level":"info","message":"started","env":"prod"}',
    '{"timestamp":"2025-03-10T09:00:02Z","level":"error","message":"boom","status":500}',
    "2025-03-10 09:00:03 WARN something odd happened",
]


def make_store() -> LogStore:
    return LogStore(":memory:")


def test_add_and_list_dataset():
    store = make_store()
    ds = store.add_dataset("api", list(parse_lines(SAMPLE)), "api.jsonl")
    assert ds.line_count == 3
    assert [d.id for d in store.list_datasets()] == [ds.id]
    assert store.get_dataset(ds.id).name == "api"
    assert store.dataset_count() == 1


def test_records_round_trip_structure():
    store = make_store()
    ds = store.add_dataset("api", list(parse_lines(SAMPLE)), "src")
    records = list(store.records(ds.id))
    assert len(records) == 3

    info, error, warn = records
    assert info.level is Level.INFO
    assert info.fields["env"] == "prod"
    assert isinstance(info.timestamp, datetime)
    assert info.timestamp.tzinfo is not None

    assert error.level is Level.ERROR
    assert error.fields["status"] == 500
    assert warn.level is Level.WARN  # parsed from the plain-text line


def test_timestamps_are_preserved_in_utc():
    store = make_store()
    ds = store.add_dataset("api", list(parse_lines(SAMPLE[:1])), "src")
    record = next(iter(store.records(ds.id)))
    assert record.timestamp == datetime(2025, 3, 10, 9, 0, 1, tzinfo=timezone.utc)


def test_missing_dataset_returns_none():
    assert make_store().get_dataset(123) is None
