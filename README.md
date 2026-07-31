# file-integrity-monitor

Real-time file and directory integrity monitor with optional SQLite logging.

## Why

Detects unexpected or unauthorized changes to files by comparing their current state against a known-good baseline — the real-time layer that a full integrity-monitoring setup relies on, without needing a commercial product for personal machines, small servers, or dev environments.

## How it works

1. `watchdog` registers a watch on the target path using the native event API
   for the current OS (inotify on Linux, FSEvents on macOS,
   ReadDirectoryChangesW on Windows), so events arrive with near-zero latency
   instead of being polled for.
2. Every raw event is checked against your filters (`--ignore-hidden`,
   `--exclude`); filtered paths are dropped entirely and never reach the
   terminal or the database.
3. Events that pass filtering are optionally written to SQLite in full
   (`--log-db`), before any further noise reduction — so the database always
   has the complete, unfiltered record.
4. For the terminal, directory-level `MODIFIED` events (usually just a side
   effect of a child file changing) are suppressed by default, and exact
   duplicate events within a short window are collapsed to one line.
5. What's left is printed with a millisecond timestamp: event type, whether
   it's a file or directory, and the path (plus destination path for moves).

## Usage

```
usage: file-integrity-monitor [-h] [--no-recursive] [--ignore-hidden]
                              [--exclude PATTERN] [--log-db] [--db-path PATH]
                              [--show-dir-events] [--dedup-window SECONDS]
                              [--version]
                              path

Watch a file or directory and report changes in real time.

positional arguments:
  path                  file or directory to watch

options:
  -h, --help            show this help message and exit
  --no-recursive        do not descend into subdirectories (directories only)
  --ignore-hidden       skip paths where any component starts with '.' (e.g.
                        .config, .cache)
  --exclude PATTERN     glob pattern to exclude, matched against the full path
                        (repeatable)
  --log-db              persist events to a SQLite database
  --db-path PATH        SQLite database path (implies --log-db; default:
                        ~/.local/share/file-integrity-monitor/events.db)
  --show-dir-events     print directory MODIFIED events (usually just noise
                        from a child file changing)
  --dedup-window SECONDS
                        suppress repeat prints of the same event within this
                        window (default: 0.5); 0 disables
  --version             show program's version number and exit
```

## Installation

Requirements: Python 3.8+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv tool install git+https://github.com/p4p2r0/file-integrity-monitor
```

## License

This project is licensed under the [MIT License](LICENSE).
