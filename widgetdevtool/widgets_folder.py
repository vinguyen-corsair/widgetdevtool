r"""The widgets folder (replaces the Widget Library dock): the folder that holds the user's widget folders (e.g. WidgetsXE).

It has no UI of its own. It is chosen automatically the first time a widget is opened (the nearest parent folder that holds more than
one widget), remembered in workspace.json, and can be changed from the iCUE tab. Two features need it:
- the iCUE tab counts a widget's group among these widgets (a group is shared between widgets);
- Import from iCUE finds the source of a stored widget here by its typeId (iCUE's storage file holds no widget code).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .device_profiles import DeviceProfile
from .widget_library import WidgetEntry, scan_library

MAX_LEVELS_UP = 3  # widget/basketballscores -> widget -> Basketball Scores -> WidgetsXE


def _canonical(folder: str | Path) -> str:
    return os.path.normcase(os.path.realpath(str(folder)))


def _never_scanned() -> set[str]:
    """Folders too large / too generic to be a widgets folder: never scanned while detecting."""
    blocked = {_canonical(Path.home()), _canonical(tempfile.gettempdir())}
    return blocked


def detect_root(widget_folder: str | Path) -> Path | None:
    """The nearest parent of `widget_folder` (at most MAX_LEVELS_UP up) that holds more than one widget; None if there is none.
    The drive root, the home folder and the temp folder are never scanned."""
    blocked = _never_scanned()
    folder = Path(widget_folder).resolve()
    for parent in list(folder.parents)[:MAX_LEVELS_UP]:
        if parent.parent == parent or _canonical(parent) in blocked:
            return None
        if len(scan_library(parent)) > 1:
            return parent
    return None


class WidgetsFolder(QObject):
    changed = Signal()  # root, entries or device changed

    def __init__(self) -> None:
        super().__init__()
        self.root: Path | None = None
        self.entries: list[WidgetEntry] = []
        self.device: DeviceProfile | None = None
        self._rescanned: set[str] = set()  # folders a rescan was already done for (a folder the scan cannot see must not loop)

    def set_root(self, root: str | Path | None) -> int:
        """Use `root` (None: no widgets folder) and scan it. Returns the number of widgets found."""
        self.root = Path(root) if root else None
        self.entries = scan_library(self.root) if self.root and self.root.is_dir() else []
        self.changed.emit()
        return len(self.entries)

    def rescan(self) -> int:
        return self.set_root(self.root)

    def set_device(self, device: DeviceProfile | None) -> None:
        self.device = device
        self.changed.emit()

    def contains(self, folder: str | Path) -> bool:
        key = _canonical(folder)
        return any(_canonical(e.folder) == key for e in self.entries)

    def adopt(self, widget_folder: str | Path) -> bool:
        """Called when a widget is opened. No widgets folder yet: detect one from the widget's parents. A widget that is inside the
        folder but not scanned yet (a new one): rescan. A widget elsewhere does not change the chosen folder. True if anything changed."""
        if self.root is None:
            root = detect_root(widget_folder)
            if root is None:
                return False
            self.set_root(root)
            return True
        key = _canonical(widget_folder)
        if not self.contains(widget_folder) and key.startswith(_canonical(self.root) + os.sep) and key not in self._rescanned:
            self._rescanned.add(key)
            self.rescan()
            return True
        return False
