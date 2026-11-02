from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any


class Level(IntEnum):
    """Normalised severity. Ordered so that comparisons mean 'at least this severe'."""

    TRACE = 0
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40
    FATAL = 50
    UNKNOWN = -1

    @classmethod
    def parse(cls, value: Any) -> Level:
        """Map the many spellings found in the wild onto one of the members."""
        if value is None:
            return cls.UNKNOWN
        if isinstance(value, bool):
            return cls.UNKNOWN
        if isinstance(value, int):
            return _from_number(value)
        text = str(value).strip().upper()
        if not text:
            return cls.UNKNOWN
        if text.isdigit():
            return _from_number(int(text))
        return _ALIASES.get(text, cls.UNKNOWN)


_ALIASES: dict[str, Level] = {
    "TRACE": Level.TRACE,
    "TRC": Level.TRACE,
    "VERBOSE": Level.TRACE,
    "VRB": Level.TRACE,
    "DEBUG": Level.DEBUG,
    "DBG": Level.DEBUG,
    "INFO": Level.INFO,
    "INF": Level.INFO,
    "INFORMATION": Level.INFO,
    "NOTICE": Level.INFO,
    "WARN": Level.WARN,
    "WRN": Level.WARN,
    "WARNING": Level.WARN,
    "ERROR": Level.ERROR,
    "ERR": Level.ERROR,
    "SEVERE": Level.ERROR,
    "FATAL": Level.FATAL,
    "FTL": Level.FATAL,
    "CRITICAL": Level.FATAL,
    "CRIT": Level.FATAL,
    "PANIC": Level.FATAL,
    "EMERG": Level.FATAL,
    "EMERGENCY": Level.FATAL,
}


def _from_number(n: int) -> Level:
    # pino / bunyan convention: 10 trace, 20 debug, 30 info, 40 warn, 50 error, 60 fatal.
    if n >= 60:
        return Level.FATAL
    if n >= 50:
        return Level.ERROR
    if n >= 40:
        return Level.WARN
    if n >= 30:
        return Level.INFO
    if n >= 20:
        return Level.DEBUG
    return Level.TRACE


@dataclass(slots=True)
class Record:
    """One parsed log line."""

    raw: str
    line_no: int
    source: str = "-"
    timestamp: datetime | None = None
    level: Level = Level.UNKNOWN
    message: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "level": self.level.name if self.level is not Level.UNKNOWN else None,
            "message": self.message,
            "fields": self.fields,
            "source": self.source,
            "line": self.line_no,
        }
