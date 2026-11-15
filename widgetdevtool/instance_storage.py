"""Per-instance WebEngine storage (D7b): one QWebEngineProfile per instance, all inside WidgetDevTool's own data folder.

iCUE v5.55 (NOT verified against release 5.52.93):
- lib/widgets/htmlwidgethelpers/src/HtmlHelpers.cpp htmlWidgetStorageName(uniqueId, typeId, isPreview) =
  `<uuid without braces>_<md5(typeId) hex>` plus `_p` for a preview.
- HtmlDataProvider.cpp keeps the profile storage of an instance under that name inside iCUE's HtmlWidgetsBrowserStorage.
  WidgetDevTool uses the same NAME, but a root of its own: it never reads or writes a path of iCUE.

HYPOTHESIS: iCUE passes the HtmlSettings id and the manifest id as uniqueId / typeId (the tool's WidgetInstance has one id only,
so there is no second identity to confuse). Preview = off-the-record here; the exact iCUE preview profile is not compared.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWebEngineCore import QWebEngineProfile

DATA_DIR_ENV = "WIDGETDEVTOOL_DATA_DIR"
_FORBIDDEN_PARTS = {"corsair"}  # iCUE keeps its data below a "Corsair" folder: never use such a root


def storage_name(unique_id: str, type_id: str, is_preview: bool = False) -> str:
    """htmlWidgetStorageName(): QUuid::fromString(id).toString(WithoutBraces) + "_" + md5(typeId) + ("_p")."""
    try:
        plain = str(uuid.UUID(unique_id.strip().strip("{}")))
    except ValueError:
        plain = str(uuid.UUID(int=0))  # QUuid::fromString() of garbage is the null uuid
    return f"{plain}_{hashlib.md5(type_id.encode('utf-8')).hexdigest()}" + ("_p" if is_preview else "")


def default_data_dir() -> Path:
    env = os.environ.get(DATA_DIR_ENV)
    return Path(env) if env else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WidgetDevTool"


class StorageLayout:
    """`<root>/storage/<name>/` per instance. Every path handed out is checked to lie inside `<root>/storage`."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        if _FORBIDDEN_PARTS & {p.lower() for p in self.root.parts}:
            raise ValueError(f"{self.root}: WidgetDevTool must not store data below an iCUE (Corsair) folder")
        self.storage_dir = self.root / "storage"

    def path(self, name: str) -> Path:
        target = (self.storage_dir / name).resolve()
        if not name or name != Path(name).name or target.parent != self.storage_dir.resolve():
            raise ValueError(f"storage name outside the tool storage: {name!r}")
        return target

    def names_on_disk(self) -> list[str]:
        return sorted(p.name for p in self.storage_dir.iterdir() if p.is_dir()) if self.storage_dir.is_dir() else []

    def make_profile(self, name: str, is_preview: bool, parent: QObject | None = None) -> QWebEngineProfile:
        """Preview: unnamed = off-the-record. Real instance: named profile with persistent storage in its own folder."""
        if is_preview:
            return QWebEngineProfile(parent)
        folder = self.path(name)
        folder.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile(name, parent)
        profile.setPersistentStoragePath(str(folder))
        profile.setCachePath(str(folder / "cache"))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        return profile

    def delete(self, unique_id: str, type_id: str) -> bool:
        """Remove the storage of an instance (real and preview name). Only call once its profile has been destroyed.
        Returns False when something is still locked (the caller retries later)."""
        ok = True
        for preview in (False, True):
            folder = self.path(storage_name(unique_id, type_id, preview))
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
                ok = ok and not folder.exists()
        return ok