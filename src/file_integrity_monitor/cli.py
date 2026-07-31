from __future__ import annotations

import argparse
import fnmatch
import os
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import __version__

_APP_NAME = "file-integrity-monitor"
_DEFAULT_DEDUP_WINDOW = 0.5

_EVENT_LABELS = {
    "created": "CREATED",
    "deleted": "DELETED",
    "modified": "MODIFIED",
    "moved": "MOVED",
    "closed": "CLOSED",
}


def _default_db_path() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / _APP_NAME / "events.db"


def _is_hidden(path: str) -> bool:
    return any(part.startswith(".") for part in Path(path).parts)


def _matches_any(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


class EventLogger:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                is_directory INTEGER NOT NULL,
                src_path TEXT NOT NULL,
                dest_path TEXT
            )
            """
        )
        self._conn.commit()

    def log(self, ts: str, event_type: str, is_directory: bool, src_path: str, dest_path: str | None) -> None:
        self._conn.execute(
            "INSERT INTO events (timestamp, event_type, is_directory, src_path, dest_path) VALUES (?, ?, ?, ?, ?)",
            (ts, event_type, int(is_directory), src_path, dest_path),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class PrintHandler(FileSystemEventHandler):
    def __init__(
        self,
        out=sys.stdout,
        ignore_hidden: bool = False,
        exclude: list[str] | None = None,
        logger: EventLogger | None = None,
        show_dir_events: bool = False,
        dedup_window: float = _DEFAULT_DEDUP_WINDOW,
    ) -> None:
        self._out = out
        self._ignore_hidden = ignore_hidden
        self._exclude = exclude or []
        self._logger = logger
        self._show_dir_events = show_dir_events
        self._dedup_window = dedup_window
        self._last_printed: dict[tuple[str, str], float] = {}

    def _is_filtered(self, path: str) -> bool:
        if self._ignore_hidden and _is_hidden(path):
            return True
        if self._exclude and _matches_any(path, self._exclude):
            return True
        return False

    def _is_noisy_dir_event(self, event: FileSystemEvent) -> bool:
        return event.is_directory and event.event_type == "modified" and not self._show_dir_events

    def _is_duplicate(self, key: tuple[str, str]) -> bool:
        now = time.monotonic()
        last = self._last_printed.get(key)
        self._last_printed[key] = now
        return last is not None and (now - last) < self._dedup_window

    def _emit(self, event: FileSystemEvent) -> None:
        is_moved = event.event_type == "moved"
        if self._is_filtered(event.src_path):
            return
        if is_moved and self._is_filtered(event.dest_path):
            return

        label = _EVENT_LABELS.get(event.event_type, event.event_type.upper())
        kind = "DIR" if event.is_directory else "FILE"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        if self._logger:
            dest = event.dest_path if is_moved else None
            self._logger.log(ts, event.event_type, event.is_directory, event.src_path, dest)

        if self._is_noisy_dir_event(event):
            return
        if self._is_duplicate((event.event_type, event.src_path)):
            return

        if is_moved:
            line = f"[{ts}] {label:<8} {kind:4} {event.src_path} -> {event.dest_path}"
        else:
            line = f"[{ts}] {label:<8} {kind:4} {event.src_path}"
        self._out.write(line + "\n")
        self._out.flush()

    def on_created(self, event):
        self._emit(event)

    def on_deleted(self, event):
        self._emit(event)

    def on_modified(self, event):
        self._emit(event)

    def on_moved(self, event):
        self._emit(event)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="file-integrity-monitor",
        description="Watch a file or directory and report changes in real time.",
    )
    parser.add_argument("path", type=Path, help="file or directory to watch")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="do not descend into subdirectories (directories only)",
    )
    parser.add_argument(
        "--ignore-hidden",
        action="store_true",
        help="skip paths where any component starts with '.' (e.g. .config, .cache)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="glob pattern to exclude, matched against the full path (repeatable)",
    )
    parser.add_argument(
        "--log-db",
        action="store_true",
        help="persist events to a SQLite database",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"SQLite database path (implies --log-db; default: {_default_db_path()})",
    )
    parser.add_argument(
        "--show-dir-events",
        action="store_true",
        help="print directory MODIFIED events (usually just noise from a child file changing)",
    )
    parser.add_argument(
        "--dedup-window",
        type=float,
        default=_DEFAULT_DEDUP_WINDOW,
        metavar="SECONDS",
        help=f"suppress repeat prints of the same event within this window (default: {_DEFAULT_DEDUP_WINDOW}); 0 disables",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    target = args.path.resolve()

    if not target.exists():
        print(f"error: path does not exist: {target}", file=sys.stderr)
        return 1

    watch_dir = target if target.is_dir() else target.parent
    recursive = target.is_dir() and not args.no_recursive

    logger = None
    if args.log_db or args.db_path:
        db_path = args.db_path or _default_db_path()
        logger = EventLogger(db_path)
        print(f"file-integrity-monitor: logging events to {logger.db_path}")

    handler = PrintHandler(
        ignore_hidden=args.ignore_hidden,
        exclude=args.exclude,
        logger=logger,
        show_dir_events=args.show_dir_events,
        dedup_window=args.dedup_window,
    )
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=recursive)
    observer.start()

    print(f"file-integrity-monitor: watching {target} (recursive={recursive})")
    print("Press Ctrl+C to stop.\n")

    stop = False

    def _handle_signal(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _handle_signal)
        except (OSError, ValueError):
            pass

    try:
        while not stop:
            time.sleep(0.5)
    finally:
        observer.stop()
        observer.join()
        if logger:
            logger.close()
        print("\nStopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())