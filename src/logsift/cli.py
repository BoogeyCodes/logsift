"""Command-line entry point."""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import sys
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO

from logsift import __version__
from logsift.filters import FilterSpec, apply, parse_duration, parse_time_bound, parse_where
from logsift.models import Level, Record
from logsift.parsers import parse_lines
from logsift.stats import Summary, summarise

_LEVEL_COLOURS = {
    Level.TRACE: "\033[90m",
    Level.DEBUG: "\033[36m",
    Level.INFO: "\033[32m",
    Level.WARN: "\033[33m",
    Level.ERROR: "\033[31m",
    Level.FATAL: "\033[35m",
    Level.UNKNOWN: "",
}
_RESET = "\033[0m"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="logsift",
        description="Filter and summarise JSON or plain-text application logs.",
        epilog=(
            "examples:\n"
            "  logsift app.log --level warn\n"
            "  logsift api.jsonl --since 15m --where status=500\n"
            "  kubectl logs web | logsift --grep 'timeout' --format json\n"
            "  logsift stats app.log.gz --bucket 5m --top 5\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command")
    filter_parser = subparsers.add_parser("filter", help="print matching lines (default command)")
    stats_parser = subparsers.add_parser("stats", help="summarise matching lines")

    for sub in (filter_parser, stats_parser):
        _add_common_arguments(sub)

    filter_parser.add_argument(
        "--format", choices=("pretty", "raw", "json"), default="pretty",
        help="pretty: normalised one-liners (default); raw: original lines; json: one JSON object per line",
    )
    filter_parser.add_argument("--no-color", action="store_true", help="disable ANSI colours")
    filter_parser.add_argument("--limit", type=int, metavar="N", help="stop after N matching lines")
    filter_parser.add_argument(
        "-c", "--count", action="store_true", help="print only the number of matching lines, like grep -c",
    )

    stats_parser.add_argument("--bucket", metavar="DUR", help="group counts into time buckets, e.g. 1m, 5m, 1h")
    stats_parser.add_argument("--top", type=int, default=10, metavar="N", help="how many message templates to show")
    stats_parser.add_argument("--json", action="store_true", help="emit the summary as JSON")

    return parser


def _add_common_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("files", nargs="*", help="log files (.gz supported); reads stdin when omitted or '-'")
    sub.add_argument("-l", "--level", metavar="LEVEL", help="minimum level: trace, debug, info, warn, error, fatal")
    sub.add_argument("--only", metavar="LEVELS", help="comma-separated exact levels, e.g. warn,error")
    sub.add_argument("--since", metavar="TIME", help="ISO timestamp or relative duration (15m, 2h, 1d)")
    sub.add_argument("--until", metavar="TIME", help="ISO timestamp or relative duration")
    sub.add_argument(
        "--drop-untimed", action="store_true", help="with --since/--until, drop lines that have no timestamp"
    )
    sub.add_argument(
        "-g", "--grep", action="append", metavar="REGEX", help="keep lines matching REGEX (repeatable, AND)"
    )
    sub.add_argument("-v", "--exclude", action="append", metavar="REGEX", help="drop lines matching REGEX (repeatable)")
    sub.add_argument("-i", "--ignore-case", action="store_true", help="case-insensitive --grep/--exclude")
    sub.add_argument(
        "-w", "--where", action="append", metavar="KEY=VALUE", help="structured field equals value (repeatable, AND)"
    )


def _records(files: Sequence[str]) -> Iterator[Record]:
    """Stream records from each file in turn; '-' or no files means stdin. Gzip is detected by suffix."""
    for name in files or ["-"]:
        if name == "-":
            yield from parse_lines(sys.stdin, source="stdin")
            continue
        path = Path(name)
        if path.suffix == ".gz":
            with gzip.open(path, "rb") as binary, io.TextIOWrapper(binary, encoding="utf-8", errors="replace") as text:
                yield from parse_lines(text, source=path.name)
        else:
            with path.open("r", encoding="utf-8", errors="replace") as text:
                yield from parse_lines(text, source=path.name)


def _build_spec(args: argparse.Namespace, now: datetime) -> FilterSpec:
    flags = re.IGNORECASE if args.ignore_case else 0
    spec = FilterSpec(keep_untimed=not args.drop_untimed)

    if args.level:
        level = Level.parse(args.level)
        if level is Level.UNKNOWN:
            raise ValueError(f"Unknown level: {args.level!r}")
        spec.min_level = level
    if args.only:
        levels = {Level.parse(x) for x in args.only.split(",") if x.strip()}
        if Level.UNKNOWN in levels:
            raise ValueError(f"Unknown level in --only: {args.only!r}")
        spec.exact_levels = levels
    if args.since:
        spec.since = parse_time_bound(args.since, now=now)
    if args.until:
        spec.until = parse_time_bound(args.until, now=now)
    if args.grep:
        spec.grep = [re.compile(p, flags) for p in args.grep]
    if args.exclude:
        spec.exclude = [re.compile(p, flags) for p in args.exclude]
    if args.where:
        spec.where = dict(parse_where(w) for w in args.where)
    return spec


def format_pretty(r: Record, colour: bool) -> str:
    ts = r.timestamp.strftime("%Y-%m-%d %H:%M:%S") if r.timestamp else "-" * 19
    lvl = r.level.name if r.level is not Level.UNKNOWN else "?"
    lvl = f"{lvl:<5}"
    if colour and r.level in _LEVEL_COLOURS and _LEVEL_COLOURS[r.level]:
        lvl = f"{_LEVEL_COLOURS[r.level]}{lvl}{_RESET}"
    extras = ""
    if r.fields:
        extras = " " + " ".join(f"{k}={_short(v)}" for k, v in r.fields.items())
    message = r.message or r.raw
    return f"{ts} {lvl} {message}{extras}"


def _short(value: object) -> str:
    text = json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value)
    return text if len(text) <= 60 else text[:57] + "..."


def format_summary(s: Summary, top: int, bucket: timedelta | None) -> str:
    lines: list[str] = []
    lines.append(f"Lines:      {s.total:,}" + (f"  ({s.untimed:,} without timestamp)" if s.untimed else ""))
    if s.first_ts and s.last_ts:
        lines.append(f"Range:      {s.first_ts.isoformat()}  ->  {s.last_ts.isoformat()}")
        lines.append(f"Span:       {_human_delta(s.span)}")
    if len(s.by_source) > 1:
        lines.append("Sources:    " + ", ".join(f"{src} ({n:,})" for src, n in s.by_source.most_common()))

    lines.append("")
    lines.append("By level:")
    for lvl, n in sorted(s.by_level.items(), key=lambda kv: kv[0]):
        name = lvl.name if lvl is not Level.UNKNOWN else "UNKNOWN"
        pct = 100.0 * n / s.total if s.total else 0.0
        lines.append(f"  {name:<8}{n:>10,}  {pct:5.1f}%  {_bar(pct)}")

    if s.error_templates:
        lines.append("")
        lines.append(f"Top errors (ERROR and above), {min(top, len(s.error_templates))} of {len(s.error_templates)}:")
        for tpl, n in s.error_templates.most_common(top):
            lines.append(f"  {n:>7,}  {tpl}")

    lines.append("")
    lines.append(f"Top messages, {min(top, len(s.templates))} of {len(s.templates)}:")
    for tpl, n in s.templates.most_common(top):
        lines.append(f"  {n:>7,}  {tpl}")

    if bucket is not None and s.buckets:
        lines.append("")
        lines.append(f"Per {_human_delta(bucket)} (total / errors):")
        peak = max(s.buckets.values())
        for start, n in sorted(s.buckets.items()):
            errors = s.error_buckets.get(start, 0)
            bar = _bar(100.0 * n / peak, width=30)
            lines.append(f"  {start.strftime('%Y-%m-%d %H:%M')}  {n:>7,} / {errors:<6,} {bar}")
    return "\n".join(lines)


def _bar(pct: float, width: int = 20) -> str:
    filled = int(round(width * pct / 100.0))
    return "#" * filled + "." * (width - filled)


def _human_delta(delta: timedelta | None) -> str:
    if delta is None:
        return "-"
    seconds = int(delta.total_seconds())
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def main(argv: Sequence[str] | None = None, *, stdout: IO[str] | None = None, now: datetime | None = None) -> int:
    out = stdout or sys.stdout
    argv = list(sys.argv[1:] if argv is None else argv)
    # Default to the "filter" command when the first token is not a known subcommand.
    if not argv or argv[0] not in ("filter", "stats", "-h", "--help", "--version"):
        argv.insert(0, "filter")

    parser = build_parser()
    args = parser.parse_args(argv)
    now = now or datetime.now(tz=timezone.utc)

    try:
        spec = _build_spec(args, now)
        bucket = parse_duration(args.bucket) if getattr(args, "bucket", None) else None
    except (ValueError, re.error) as exc:
        parser.error(str(exc))

    try:
        records = apply(_records(args.files), spec.build())
        if args.command == "stats":
            summary = summarise(records, bucket=bucket)
            if args.json:
                out.write(json.dumps(summary.to_dict(args.top), indent=2) + "\n")
            else:
                out.write(format_summary(summary, args.top, bucket) + "\n")
            return 0

        if args.count:
            total = sum(1 for _ in records)
            out.write(f"{total}\n")
            return 0 if total else 1

        colour = not args.no_color and out.isatty()
        emitted = 0
        for r in records:
            if args.format == "raw":
                out.write(r.raw + "\n")
            elif args.format == "json":
                out.write(json.dumps(r.to_dict(), default=str) + "\n")
            else:
                out.write(format_pretty(r, colour) + "\n")
            emitted += 1
            if args.limit is not None and emitted >= args.limit:
                break
        return 0 if emitted else 1
    except FileNotFoundError as exc:
        print(f"logsift: {exc.filename}: no such file", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
