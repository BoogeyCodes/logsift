"""SQLite persistence for uploaded log datasets and their parsed records.

Lines are parsed once on ingest (via :mod:`logsift.parsers`) and stored structured, so the web
console can filter and summarise a dataset repeatedly without reparsing. The store is thread-safe
(``check_same_thread=False`` plus a lock) so it can back a web server; it holds data only.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone

from logsift.models import Level, Record

_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    line_count  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS records (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    line_no     INTEGER NOT NULL,
    source      TEXT NOT NULL,
    ts          TEXT,
    level       INTEGER NOT NULL,
    message     TEXT NOT NULL,
    raw         TEXT NOT NULL,
    fields      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_dataset ON records(dataset_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Dataset:
    id: int
    name: str
    source: str
    created_at: str
    line_count: int


class LogStore:
    """Thread-safe SQLite storage for log datasets and their parsed records."""

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def add_dataset(self, name: str, records: Iterable[Record], source: str = "upload") -> Dataset:
        """Store a dataset and all of its parsed records, returning the dataset row."""
        created = _now()
        rows = [
            (
                r.line_no,
                r.source,
                r.timestamp.isoformat() if r.timestamp else None,
                int(r.level),
                r.message,
                r.raw,
                json.dumps(r.fields, default=str),
            )
            for r in records
        ]
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO datasets (name, source, created_at, line_count) VALUES (?, ?, ?, ?)",
                (name, source, created, len(rows)),
            )
            dataset_id = int(cur.lastrowid)
            self._conn.executemany(
                "INSERT INTO records (dataset_id, line_no, source, ts, level, message, raw, fields) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(dataset_id, *row) for row in rows],
            )
        return Dataset(dataset_id, name, source, created, len(rows))

    def list_datasets(self) -> list[Dataset]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM datasets ORDER BY id DESC").fetchall()
        return [self._dataset(row) for row in rows]

    def get_dataset(self, dataset_id: int) -> Dataset | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
        return self._dataset(row) if row else None

    def records(self, dataset_id: int) -> Iterator[Record]:
        """Yield every parsed record of a dataset in ingest order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM records WHERE dataset_id = ? ORDER BY id ASC", (dataset_id,)
            ).fetchall()
        for row in rows:
            yield self._record(row)

    def dataset_count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0])

    @staticmethod
    def _dataset(row: sqlite3.Row) -> Dataset:
        return Dataset(row["id"], row["name"], row["source"], row["created_at"], row["line_count"])

    @staticmethod
    def _record(row: sqlite3.Row) -> Record:
        ts = datetime.fromisoformat(row["ts"]) if row["ts"] else None
        return Record(
            raw=row["raw"],
            line_no=row["line_no"],
            source=row["source"],
            timestamp=ts,
            level=Level(row["level"]),
            message=row["message"],
            fields=json.loads(row["fields"]),
        )
