# logsift

[![CI](https://github.com/BoogeyCodes/logsift/actions/workflows/ci.yml/badge.svg)](https://github.com/BoogeyCodes/logsift/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB)
![No dependencies](https://img.shields.io/badge/dependencies-none-success)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Filter and summarise application logs from the terminal. Point it at JSON-lines output from
Serilog, pino, structlog, zap or a cloud agent, or at classic `timestamp LEVEL message` text,
and ask questions like *"what errored in the last 15 minutes?"* or *"which messages dominate
this file?"* without writing a `jq` pipeline.

```
$ logsift stats api.jsonl --bucket 5m --top 3

Lines:      11
Range:      2025-03-10T09:00:01+00:00  ->  2025-03-10T09:06:55+00:00
Span:       6m 54s

By level:
  DEBUG            1    9.1%  ##..................
  INFO             5   45.5%  #########...........
  WARN             2   18.2%  ####................
  ERROR            2   18.2%  ####................
  FATAL            1    9.1%  ##..................

Top errors (ERROR and above), 2 of 2:
        2  HTTP POST /api/payments responded #
        1  Unhandled exception, host is shutting down

Per 5m (total / errors):
  2025-03-10 09:00       10 / 2      ##############################
  2025-03-10 09:05        1 / 1      ###...........................
```

## Why

Production debugging usually starts with a log file and a vague question. Log platforms answer
those questions well, but a surprising amount of time is spent on files that never reached one:
a `kubectl logs` dump, a `.log.gz` pulled from a server, a CI job's output. `logsift` is the
small, dependency-free tool for that gap. It understands both structured and unstructured logs,
normalises levels and timestamps, and streams, so it is comfortable with multi-gigabyte files.

## Install

```bash
pip install git+https://github.com/BoogeyCodes/logsift.git
```

Or clone and `pip install -e ".[dev]"` to hack on it. Python 3.10 or newer, no runtime dependencies.

## Usage

### Filtering

```bash
# everything at WARN or above
logsift app.log --level warn

# only these exact levels
logsift app.log --only error,fatal

# a time window: ISO timestamps or durations relative to now
logsift api.jsonl --since 15m
logsift api.jsonl --since 2025-03-10T09:00:00Z --until 2025-03-10T09:05:00Z

# regex include / exclude (repeatable, --grep is AND-ed), case-insensitive
logsift app.log -g timeout -g payments -v healthcheck -i

# match structured fields, including nested ones
logsift api.jsonl --where status=500 --where user.name=ann

# several files, gzip handled transparently, or stdin
logsift api.jsonl worker.log.gz --level error
kubectl logs deploy/web | logsift --since 5m --level warn

# just the count of matches, like grep -c (exit 1 when zero)
logsift api.jsonl --level error --count
```

Output formats:

| Flag            | Output                                                   |
| --------------- | -------------------------------------------------------- |
| default         | `timestamp LEVEL message key=value ...`, colour on a TTY |
| `--format raw`  | the original lines, untouched                            |
| `--format json` | one normalised JSON object per line, for piping to `jq`  |

Exit code is `0` when something matched, `1` when nothing did, `2` for usage or file errors,
which makes it usable in scripts: `logsift build.log --level error && exit 1`.

### Summarising

```bash
logsift stats app.log
logsift stats api.jsonl --bucket 1m          # per-minute totals and error counts
logsift stats api.jsonl --json | jq .top_errors
```

All filter flags work with `stats` too, so `logsift stats api.jsonl --where status=500 --bucket 5m`
shows *when* the 500s happened.

Messages are grouped by **template**: numbers, UUIDs, long hex strings and quoted strings are
replaced with placeholders, so `Processed order 118 in 571 ms` and `Processed order 119 in 494 ms`
count as one message. That is usually what you want when asking "what is this service mostly
saying?"

## What it understands

**JSON lines.** Any object per line. Timestamp, level and message are looked up under the usual
names (`timestamp`, `@timestamp`, `time`, `ts`, `@t`; `level`, `severity`, `lvl`, `@l`;
`message`, `msg`, `@mt`...). Remaining keys become fields you can `--where` on. Numeric levels
follow the pino/bunyan convention (`30` = info, `50` = error).

**Text lines.** A timestamp anywhere on the line in ISO-8601, `YYYY-MM-DD HH:MM:SS,mmm` or syslog
`Mon DD HH:MM:SS` form; a level token such as `INFO`, `[WRN]`, `error:`; trailing `key=value`
pairs are extracted into fields. Lines with neither, like stack-trace frames, are kept with an
unknown level rather than dropped, so `--grep` still finds them.

**Levels** are normalised across ecosystems: `Information`, `INF`, `info` and `30` are all `INFO`;
`CRITICAL`, `PANIC`, `FTL` are all `FATAL`.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

The test suite covers the parsers against real-world line shapes, the filter predicates, the
aggregation maths and the CLI end to end on the files in [`examples/`](examples/). CI runs it on
Linux and Windows across Python 3.10 to 3.13.

## Project layout

```
src/logsift
├── cli.py        argument parsing, input streaming, output formatting
├── parsers.py    JSON and text line parsing, timestamp normalisation
├── filters.py    FilterSpec -> predicate, duration and key=value parsing
├── stats.py      message templating and bucketed summaries
└── models.py     Level enum and the Record dataclass
tests/            pytest suite
examples/         sample JSON and text logs used by the tests and the README
```

## Ideas for later

- `--follow` to tail a growing file
- Output as a histogram of field values (`--count-by status`)
- A `--format csv` for spreadsheets

## License

MIT
