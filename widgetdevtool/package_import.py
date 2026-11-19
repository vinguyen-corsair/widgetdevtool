r"""Open Package: add a downloaded `.icuewidget` (e.g. from the Elgato marketplace) to the widgets folder as source, then open it.

A `.icuewidget` is a ZIP archive with the widget files at its root (manifest.json, index.html, resources/...). Some archives wrap them in
one top folder; that folder is stripped. Layout created (the WidgetsXE convention):

    <widgets folder>\<widget name>\<package file>               the original package, kept for reference
    <widgets folder>\<widget name>\widget\<package base name>\  the extracted widget (manifest.json + index.html)

Rules:
- Nothing is ever overwritten: a widget whose typeId is already in the widgets folder is NOT extracted (the caller opens the existing
  one), and an existing destination folder is refused.
- Every archive path is checked before anything is written: no absolute path, drive, `..` or link (zip-slip), a file-count and size
  limit. Extraction goes to a staging folder next to the destination and is renamed into place only when complete.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .widget_library import WidgetEntry

MAX_FILES = 5000
MAX_TOTAL_BYTES = 500 * 1024 * 1024
WINDOWS_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class PackageError(ValueError):
    """The file is not a usable widget package (the message says why)."""


@dataclass
class PackageInfo:
    path: Path
    type_id: str
    name: str
    version: str
    prefix: str                      # top folder stripped from every member ("" = files at the root)
    members: list[str] = field(default_factory=list)  # file members (posix), prefix included
    total_bytes: int = 0

    @property
    def base_name(self) -> str:
        """The widget folder name: the package file name up to `-<version>` (doodle-pad-1.0.1.icuewidget -> doodle-pad). A marketplace
        name such as "Daily Quote-1.1.1-windows" (spaces, platform suffix) gives the last part of the typeId instead (daily-quote)."""
        stem = self.path.stem
        if self.version:
            cut = stem.lower().find("-" + self.version.lower())
            stem = stem[:cut] if cut > 0 else stem
        stem = safe_name(stem)
        if not stem or " " in stem:
            stem = safe_name(self.type_id.split(".")[-1]) or stem.replace(" ", "-").lower()
        return stem or "widget"

    @property
    def project_name(self) -> str:
        return safe_name(self.name) or self.base_name


def safe_name(text: str) -> str:
    """A Windows-safe folder name (invalid characters removed, no trailing dot/space, not a reserved device name)."""
    cleaned = WINDOWS_BAD.sub("", text or "").strip().rstrip(". ")
    return "" if cleaned.lower() in RESERVED else cleaned[:80]


def _check_member(info: zipfile.ZipInfo) -> str:
    name = info.filename.replace("\\", "/")
    parts = PurePosixPath(name).parts
    if name.startswith("/") or (parts and re.match(r"^[A-Za-z]:", parts[0])) or ".." in parts:
        raise PackageError(f"unsafe path in the archive: {info.filename!r} (nothing was extracted)")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise PackageError(f"the archive contains a link: {info.filename!r} (nothing was extracted)")
    return name


def read_package(path: str | Path) -> PackageInfo:
    """Open and check a package without extracting anything. PackageError when it is not a widget package."""
    path = Path(path)
    if not path.is_file():
        raise PackageError(f"file not found: {path}")
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageError(f"not a .icuewidget (ZIP) file: {exc}") from exc
    with archive:
        files, total = [], 0
        for info in archive.infolist():
            name = _check_member(info)
            if info.is_dir():
                continue
            files.append(name)
            total += info.file_size
        if len(files) > MAX_FILES or total > MAX_TOTAL_BYTES:
            raise PackageError(f"the archive is too large ({len(files)} files, {total // (1024 * 1024)} MB)")
        prefix = ""
        if "manifest.json" not in files:
            tops = {n.split("/", 1)[0] for n in files}
            if len(tops) == 1 and all("/" in n for n in files) and f"{next(iter(tops))}/manifest.json" in files:
                prefix = next(iter(tops)) + "/"
            else:
                raise PackageError("manifest.json is not at the root of the archive: this is not a widget package")
        if prefix + "index.html" not in files:
            raise PackageError("index.html is missing from the package")
        try:
            manifest = json.loads(archive.read(prefix + "manifest.json").decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise PackageError(f"manifest.json cannot be read: {exc}") from exc
    if not isinstance(manifest, dict) or not str(manifest.get("id", "")).strip():
        raise PackageError("manifest.json has no \"id\" (typeId)")
    return PackageInfo(path, str(manifest["id"]).strip(), str(manifest.get("name", "")).strip(), str(manifest.get("version", "")).strip(),
                       prefix, files, total)


def find_existing(info: PackageInfo, entries: list[WidgetEntry]) -> list[WidgetEntry]:
    """Widgets of the widgets folder with the same typeId (the package is then NOT extracted)."""
    return [e for e in entries if e.type_id == info.type_id]


def destination(info: PackageInfo, root: str | Path) -> tuple[Path, Path]:
    """(project folder, widget folder) for the package under the widgets folder."""
    project = Path(root) / info.project_name
    return project, project / "widget" / info.base_name


def extract(info: PackageInfo, root: str | Path) -> Path:
    """Extract the package into the widgets folder (see the module doc). Returns the widget folder. Never overwrites."""
    project, target = destination(info, root)
    if target.exists():
        raise PackageError(f"the destination already exists, nothing was overwritten: {target}")
    keep = project / info.path.name
    new_project = not project.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".wdt-extract-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        with zipfile.ZipFile(info.path) as archive:
            for member in info.members:
                relative = member[len(info.prefix):]
                out = staging.joinpath(*PurePosixPath(relative).parts)
                if os.path.commonpath([str(staging.resolve()), str(out.resolve())]) != str(staging.resolve()):
                    raise PackageError(f"unsafe path in the archive: {member!r}")
                out.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if new_project:  # a failed extraction leaves nothing behind
            shutil.rmtree(project, ignore_errors=True)
        raise
    if not keep.exists():
        shutil.copy2(info.path, keep)
    return target
