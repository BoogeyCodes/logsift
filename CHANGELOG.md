# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `filter -c/--count` — print only the number of matching lines, like `grep -c`
  (exit code 1 when the count is zero).

## [0.1.0]

### Added
- Filter JSON-lines and plain-text logs by level, time window, regex and fields.
- `stats` summary with level counts, top message templates and time buckets.
- Pretty / raw / JSON output, gzip and stdin support; zero dependencies.
