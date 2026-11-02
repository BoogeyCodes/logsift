"""Turn raw log lines into :class:`Record` objects.

Two families of input are supported:

* **JSON lines** - one object per line, as produced by Serilog, pino, bunyan,
  structlog, zap, logrus and most cloud logging agents. Timestamp, level and
  message keys are discovered from a list of common names.
* **Plain text** - the usual ``<timestamp> <level> <message>`` shapes, with
  the timestamp and level found by regular expressions. Anything that does not
  match is still returned as a record with an unknown level so no line is
  silently dropped.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any

from logsift.models import Level, Record

TIMESTAMP_KEYS = ("timestamp", "@timestamp", "time", "ts", "Timestamp", "t", "datetime", "date")
LEVEL_KEYS = ("level", "Level", "severity", "lvl", "loglevel", "log.level", "@l", "l")
MESSAGE_KEYS = ("message", "msg", "Message", "@m", "@mt", "MessageTemplate", "event", "text")

# ISO-8601 or "YYYY-MM-DD HH:MM:SS[,.fff]" with optional zone.
_TS_PATTERN = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
# Syslog style: "Jan  2 15:04:05"
_SYSLOG_TS = re.compile(r"(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s\d{2}:\d{2}:\d{2})")
# Level tokens. A bare token must be upper-case ("INFO") so ordinary words inside a
# message are not mistaken for a level; a decorated token ("[info]", "error:") may be any case.
_LEVEL_NAMES = (
    r"TRACE|TRC|VERBOSE|VRB|DEBUG|DBG|INFO|INF|INFORMATION|NOTICE|"
    r"WARN|WRN|WARNING|ERROR|ERR|SEVERE|FATAL|FTL|CRITICAL|CRIT|PANIC|EMERG"
)
_LEVEL_BARE = re.compile(rf"(?<![A-Za-z_])(?P<level>{_LEVEL_NAMES})(?![A-Za-z_])")
_LEVEL_DECORATED = re.compile(
    rf"(?<![A-Za-z_])(?:\[(?P<l1>{_LEVEL_NAMES})\]|\((?P<l2>{_LEVEL_NAMES})\)|(?P<l3>{_LEVEL_NAMES}):)(?![A-Za-z_])",
    re.IGNORECASE,
)
# Trailing structured pairs on text lines:  key=value or key="quoted value"
_KV_PATTERN = re.compile(r'(?P<key>[A-Za-z_][A-Za-z0-9_.]*)=(?P<val>"[^"]*"|\S+)')

_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1
)}


def parse_timestamp(value: Any) -> datetime | None:
    """Best-effort conversion of a timestamp value into an aware ``datetime``.

    Accepts ISO-8601 strings (with ``Z`` or offsets), epoch seconds or
    milliseconds as numbers, and syslog-style ``Mon DD HH:MM:SS``. Naive values
    are assumed to be UTC so that comparisons never raise.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e11:  # milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.replace(".", "", 1).isdigit():
        return parse_timestamp(float(text))

    iso = text.replace(",", ".", 1)
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = _parse_syslog(text)
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_syslog(text: str) -> datetime | None:
    m = _SYSLOG_TS.match(text)
    if not m:
        return None
    parts = m.group("ts").split()
    month = _MONTHS.get(parts[0])
    if month is None:
        return None
    day = int(parts[1])
    hh, mm, ss = (int(p) for p in parts[2].split(":"))
    year = datetime.now(tz=timezone.utc).year
    try:
        return datetime(year, month, day, hh, mm, ss, tzinfo=timezone.utc)
    except ValueError:
        return None


def _first_key(obj: dict[str, Any], candidates: Iterable[str]) -> str | None:
    for key in candidates:
        if key in obj:
            return key
    return None


def parse_json_line(raw: str, line_no: int, source: str) -> Record | None:
    """Parse one JSON object line. Returns ``None`` if the line is not a JSON object."""
    stripped = raw.strip()
    if not stripped.startswith("{"):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    fields = dict(obj)
    ts_key = _first_key(obj, TIMESTAMP_KEYS)
    lvl_key = _first_key(obj, LEVEL_KEYS)
    msg_key = _first_key(obj, MESSAGE_KEYS)

    timestamp = parse_timestamp(fields.pop(ts_key)) if ts_key else None
    level = Level.parse(fields.pop(lvl_key)) if lvl_key else Level.UNKNOWN
    message = str(fields.pop(msg_key)) if msg_key else ""

    return Record(
        raw=raw.rstrip("\n"),
        line_no=line_no,
        source=source,
        timestamp=timestamp,
        level=level,
        message=message,
        fields=fields,
    )


def parse_text_line(raw: str, line_no: int, source: str) -> Record:
    """Parse a plain-text line. Never fails; unknown shapes yield level UNKNOWN."""
    line = raw.rstrip("\n")
    rest = line

    timestamp = None
    m = _TS_PATTERN.search(rest) or _SYSLOG_TS.match(rest)
    if m:
        timestamp = parse_timestamp(m.group("ts"))
        if timestamp is not None:
            rest = (rest[: m.start()] + rest[m.end():]).strip()

    level = Level.UNKNOWN
    m = _LEVEL_DECORATED.search(rest) or _LEVEL_BARE.search(rest)
    if m:
        level = Level.parse(next(g for g in m.groups() if g))
        rest = (rest[: m.start()] + " " + rest[m.end():]).strip()

    fields: dict[str, Any] = {}
    for kv in _KV_PATTERN.finditer(rest):
        val = kv.group("val")
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1]
        fields[kv.group("key")] = _coerce(val)
    if fields:
        # Strip the pairs from the message but keep any leading free text.
        rest = _KV_PATTERN.sub("", rest).strip()

    message = re.sub(r"\s{2,}", " ", rest).strip(" -:|")

    return Record(
        raw=line,
        line_no=line_no,
        source=source,
        timestamp=timestamp,
        level=level,
        message=message,
        fields=fields,
    )


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "none", "nil"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def parse_line(raw: str, line_no: int, source: str = "-") -> Record:
    """Parse a line, preferring JSON and falling back to text."""
    return parse_json_line(raw, line_no, source) or parse_text_line(raw, line_no, source)


def parse_lines(lines: Iterable[str], source: str = "-") -> Iterator[Record]:
    """Parse an iterable of lines, skipping blank ones."""
    for line_no, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        yield parse_line(raw, line_no, source)
