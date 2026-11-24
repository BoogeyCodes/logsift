# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0]

### Added
- **A web console** (`logsift serve`, the optional `[web]` extra):
  - Upload or paste JSON/text logs; they are parsed once and stored in SQLite.
  - A per-dataset dashboard: level breakdown, a timeline of volume with errors highlighted,
    the top message templates and top errors, and summary tiles.
  - An interactive viewer that filters by level, text (regex), structured field and time window,
    with pagination — reusing the same `FilterSpec`/`summarise` as the CLI.
  - A JSON twin of every view (`GET /datasets/{id}.json` with the same filters) — the console is
    also an API.
  - A `logsift.store.LogStore` (thread-safe SQLite) and a `logsift serve` command; a Dockerfile.
- `filter -c/--count` — print only the number of matching lines, like `grep -c` (exit code 1 when
  the count is zero).

### Changed
- The CLI remains dependency-free; the console is the optional `[web]` extra.

## [0.1.0]

### Added
- Filter JSON-lines and plain-text logs by level, time window, regex and fields.
- `stats` summary with level counts, top message templates and time buckets.
- Pretty / raw / JSON output, gzip and stdin support; zero dependencies.
