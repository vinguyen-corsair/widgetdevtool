"""Recursive folder watcher for live reload (stdlib polling, no extra dependency).

Why polling: QFileSystemWatcher is not recursive and can miss atomic-save renames. Measured on the
31 WidgetsXE widgets: <= 30 files per widget, <= 0.09 ms per scan, so a 100 ms poll is negligible.

A change is reported only after the snapshot stayed unchanged for `debounce_ms` (settle debounce),
so one Save, even one that touches several files or writes a temp file first, gives ONE event.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

IGNORED_DIRS = {".git", ".hg", ".svn", "node_modules", ".idea", ".vscode", "__pycache__"}
IGNORED_SUFFIXES = (".swp", ".swo", ".tmp", ".temp", ".bak", "~", ".crdownload")
IGNORED_NAMES = {".ds_store", "thumbs.db"}

Snapshot = dict[str, tuple[int, int]]


def _ignored_file(name: str) -> bool:
    low = name.lower()
    return low in IGNORED_NAMES or low.endswith(IGNORED_SUFFIXES) or low.startswith((".#", "~$")) \
        or (low.startswith(".") and low.endswith(".swp")) or (low.startswith("#") and low.endswith("#"))


def snapshot(root: str | Path) -> Snapshot:
    """{relative posix path: (mtime_ns, size)} for every non-ignored file below root."""
    base = str(root)
    out: Snapshot = {}
    stack = [base]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name not in IGNORED_DIRS:
                                stack.append(e.path)
                        elif not _ignored_file(e.name):
                            st = e.stat()
                            out[Path(e.path).relative_to(base).as_posix()] = (st.st_mtime_ns, st.st_size)
                    except OSError:
                        pass  # file vanished mid-scan (atomic save)
        except OSError:
            pass
    return out


def diff(old: Snapshot, new: Snapshot) -> list[str]:
    return sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k))


class FolderWatcher(QObject):
    changed = Signal(list)  # sorted relative paths of added/removed/modified files

    def __init__(self, parent: QObject | None = None, interval_ms: int = 100, debounce_ms: int = 250) -> None:
        super().__init__(parent)
        self.debounce = debounce_ms / 1000.0
        self.folder: Path | None = None
        self._baseline: Snapshot = {}
        self._pending: Snapshot | None = None
        self._since = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.poll)

    @property
    def enabled(self) -> bool:
        return self._timer.isActive()

    def start(self, folder: str | Path) -> None:
        """Watch `folder` (baseline = its current content). Keeps the enabled/disabled setting."""
        was_enabled = self.enabled or self.folder is None
        self.folder = Path(folder)
        self.rebaseline()
        if was_enabled:
            self._timer.start()

    def rebaseline(self) -> None:
        """Declare the current content as already loaded (called when a reload starts)."""
        self._baseline = snapshot(self.folder) if self.folder else {}
        self._pending = None

    def set_enabled(self, on: bool) -> None:
        # Disabled: the baseline is NOT updated, so edits made meanwhile reload once it is re-enabled.
        if on and self.folder is not None:
            self._timer.start()
        else:
            self._timer.stop()
            self._pending = None

    def stop(self) -> None:
        self._timer.stop()

    def poll(self, now: float | None = None) -> None:
        if self.folder is None:
            return
        now = time.monotonic() if now is None else now
        cur = snapshot(self.folder)
        if self._pending is None:
            if cur != self._baseline:
                self._pending, self._since = cur, now
        elif cur != self._pending:
            self._pending, self._since = cur, now  # still being written: restart the settle window
        elif now - self._since >= self.debounce:
            paths = diff(self._baseline, cur)
            self._baseline, self._pending = cur, None
            if paths:
                self.changed.emit(paths)