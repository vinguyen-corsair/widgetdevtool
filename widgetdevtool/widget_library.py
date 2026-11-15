r"""Widget library (D7a): find widgets under a folder and resolve their thumbnail metadata and groups the way iCUE v5.55 does.

Source traced (iCUE v5.55, NOT verified against release 5.52.93 / 5.46.67):
- lib/widgets/htmlwidgetcore/src/HtmlParser.cpp: `<link rel="icon" href>` (the FIRST element whose rel is exactly "icon", even with an
  empty href) -> iconPath = pathToUrl(widgetDir + separator + href); `<meta name="x-icue-widget-preview" content>` -> previewSource
  (only a non-empty content; the last one wins); `<meta name="x-icue-widget-group" content>` -> groupName (last wins). title and
  group name are JS expressions (`evalJsExpression`: on a JS error the raw text is used, e.g. `tr('Clock Face')`).
- mod/widgets/htmlbase/src/HtmlTypeInterfacesImpl.cpp HtmlTypeData:
    imageSource()            = icon, else qrc:///cue-icons/4.0/screeneffects/web-url.svg
    widgetPreviewSource()    = preview, else imageSource()
    widgetGroupName()        = group name, else the widget name
    widgetGroupImageSource() = the RAW icon (empty when there is none: NO default here)
- NOTHING checks that these files exist: a missing file gives an empty QML Image, never a fallback.
- manifest.json `preview_icon`: ManifestValidator.cpp only requires it to be a string; no code reads it for a thumbnail.
- lib/widgets/htmlwidgetcore/src/models/WidgetGroupsModel.cpp: a type is listed only if `supported(isHw, device)`; types are grouped
  by (widgetGroupName, isCustom); the group image is the one of the first type that created the group.
- lib/widgets/htmlwidgetdeviceutils/src/HtmlWidgetDeviceUtils.cpp isDeviceSupported(): the device's SupportedHtmlWidgetDeviceTypes
  must contain a type named in the widget's manifest `supported_devices`; a "sensor-screen" feature also needs the device's
  HasTouchScreenSetupNotification. HtmlTypeData::supported(): no device -> true, no restrictions -> true.
- A preview panel exists only for a group with more than one widget (WidgetSetupPanelsController: widgets.size() > 1;
  DashboardWidgetPanelsModelImpl::createPreviewPanel: null when size() == 1; KeyboardScreenModulePanelsController: types.size() > 1).
Our own choices (not iCUE facts): the order of groups and of widgets inside a group, one `isCustom` value for the whole library.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from .device_profiles import DeviceProfile
from .icue_theme import DEFAULT_ICON_FILE, DEFAULT_ICON_URL
from .properties import _JsEnv, manifest_modules
from .translator import Translator
from .widget_loader import INDEX_NAME, MANIFEST_NAME, WidgetCheck, check_widget_folder

SRC_ICON, SRC_PREVIEW, SRC_DEFAULT, SRC_NONE = "link rel=icon", "x-icue-widget-preview", "default (web-url.svg)", "none"
# HtmlUtils.cpp deviceType(): manifest attribute (lower case) -> HtmlWidgetDeviceType
DEVICE_TYPE_NAMES = {"dashboard_lcd": "DashboardLCD", "keyboard_lcd": "KeyboardLCD", "pump_lcd": "PumpLCD",
                     "pump_dashboard_lcd": "PumpDashboardLCD"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}


class _Head(HTMLParser):
    """The three things the library needs from index.html (HtmlParser.cpp visitors)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title, self._title_parts = "", None
        self.icon_href: str | None = None  # None: no <link rel="icon">
        self.preview = ""
        self.group: str | None = None  # None: no x-icue-widget-group meta

    def handle_starttag(self, tag, attrs):  # noqa: D102
        a = {k: (v or "") for k, v in attrs}
        if tag == "link" and a.get("rel") == "icon" and self.icon_href is None:
            self.icon_href = a.get("href", "")
        elif tag == "meta" and a.get("name") == "x-icue-widget-preview" and a.get("content"):
            self.preview = a["content"]
        elif tag == "meta" and a.get("name") == "x-icue-widget-group":
            self.group = a.get("content", "")
        elif tag == "title" and not self.title:
            self._title_parts = []

    def handle_endtag(self, tag):  # noqa: D102
        if tag == "title" and self._title_parts is not None:
            self.title, self._title_parts = "".join(self._title_parts), None

    def handle_data(self, data):  # noqa: D102
        if self._title_parts is not None:
            self._title_parts.append(data)


def parse_head(html_text: str) -> _Head:
    head = _Head()
    head.feed(html_text)
    head.close()
    return head


@dataclass(frozen=True)
class Resolved:
    """One image iCUE would use, and how it was chosen."""
    role: str           # "type image" | "widget preview" | "group image (widgets pipeline)" | "group icon (dash pipeline)"
    source: str         # SRC_*
    declared: str       # the raw attribute text that was used ("" for the default / none)
    path: str           # file path ("" for the default icon or none)
    exists: bool        # the file exists (the default icon: always)
    reason: str         # why this source (and what was skipped)

    @property
    def usable_path(self) -> Path | None:
        if self.source == SRC_DEFAULT:
            return DEFAULT_ICON_FILE
        return Path(self.path) if self.path and self.exists else None

    @property
    def url(self) -> str:
        return DEFAULT_ICON_URL if self.source == SRC_DEFAULT else (Path(self.path).as_uri() if self.path else "")


@dataclass
class WidgetEntry:
    folder: Path
    check: WidgetCheck
    type_id: str = ""
    name: str = ""
    group_expr: str | None = None   # raw x-icue-widget-group content
    group_name: str = ""            # widgetGroupName(): evaluated group, else the name
    icon_href: str | None = None
    preview_expr: str = ""
    manifest_preview_icon: str = ""
    restrictions: dict[str, list[str]] = field(default_factory=dict)  # HtmlWidgetDeviceType name -> features
    notes: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.check.ok

    @property
    def group_declared(self) -> bool:
        return self.group_expr is not None

    def _path(self, relative: str) -> str:
        return str(self.folder.resolve()) + os.sep + relative  # absolutePath + QDir::separator() + attribute

    # ---- the four HtmlTypeData answers ----
    def type_image(self, role: str = "type image") -> Resolved:
        """HtmlTypeData::imageSource()."""
        if self.icon_href is not None:
            path = self._path(self.icon_href)
            ok = Path(path).is_file()
            why = ("<link rel=\"icon\"> is declared" if self.icon_href else "<link rel=\"icon\"> has an empty href (iCUE still uses it: "
                   "the path is the widget folder)")
            return Resolved(role, SRC_ICON, self.icon_href, path, ok, why + ("" if ok else "; the file does NOT exist: iCUE shows an "
                            "empty image and does NOT fall back to the default"))
        return Resolved(role, SRC_DEFAULT, "", "", True, "no <link rel=\"icon\"> in index.html: default icon")

    def preview_image(self) -> Resolved:
        """HtmlTypeData::widgetPreviewSource()."""
        if self.preview_expr:
            path = self._path(self.preview_expr)
            ok = Path(path).is_file()
            return Resolved("widget preview", SRC_PREVIEW, self.preview_expr, path, ok,
                            "x-icue-widget-preview is declared" + ("" if ok else "; the file does NOT exist: iCUE shows an empty "
                            "image and does NOT fall back to the icon"))
        base = self.type_image("widget preview")
        return Resolved("widget preview", base.source, base.declared, base.path, base.exists,
                        "no x-icue-widget-preview: fall back to the type image (" + base.reason + ")")

    def group_image_widgets_pipeline(self) -> Resolved:
        """HtmlTypeData::widgetGroupImageSource(): the raw icon, NO default."""
        if self.icon_href is None:
            return Resolved("group image (widgets pipeline)", SRC_NONE, "", "", False,
                            "no <link rel=\"icon\">: widgetGroupImageSource() is empty (there is no default for a group image)")
        base = self.type_image("group image (widgets pipeline)")
        return Resolved(base.role, base.source, base.declared, base.path, base.exists, "the raw icon: " + base.reason)

    def group_icon_dash_pipeline(self) -> Resolved:
        """The Dash LCD pipeline (WidgetGroupsModel.cpp of dashlcdui): group icon = typeData->imageSource() (with the default)."""
        base = self.type_image("group icon (dash pipeline)")
        return Resolved(base.role, base.source, base.declared, base.path, base.exists, base.reason)


def _evaluate(expr: str, env: _JsEnv | None) -> str:
    return env.expression(expr) if env is not None else expr


def load_entry(folder: str | Path) -> WidgetEntry:
    check = check_widget_folder(folder)
    entry = WidgetEntry(folder=Path(folder), check=check, name=check.widget_name)
    manifest = check.manifest or {}
    entry.type_id = str(manifest.get("id", Path(folder).name))
    entry.manifest_preview_icon = str(manifest.get("preview_icon", "")) if isinstance(manifest.get("preview_icon", ""), str) else ""
    for device in manifest.get("supported_devices", []) if isinstance(manifest.get("supported_devices"), list) else []:
        kind = DEVICE_TYPE_NAMES.get(str(device.get("type", "")).lower()) if isinstance(device, dict) else None
        if kind is None:
            entry.notes.append(f"supported_devices: unknown type {device!r} ignored")
            continue
        entry.restrictions[kind] = [str(f) for f in device.get("features", []) if isinstance(f, str)]
    if not check.index_path.is_file():
        return entry
    head = parse_head(check.index_path.read_text(encoding="utf-8-sig", errors="replace"))
    entry.icon_href, entry.preview_expr, entry.group_expr = head.icon_href, head.preview, head.group
    env = None
    if QCoreApplication.instance() is not None:
        try:
            env = _JsEnv(Translator(check.index_path, "en").translate, manifest_modules(manifest, check.index_path))
        except Exception as exc:  # noqa: BLE001 - a broken module must not hide the widget
            entry.notes.append(f"JS environment unavailable ({exc!r}): expressions used as text")
    if head.title.strip():
        entry.name = _evaluate(head.title.strip(), env)
    group = _evaluate(head.group, env) if head.group else ""
    entry.group_name = group if group else entry.name
    return entry


def scan_library(root: str | Path, max_depth: int = 4) -> list[WidgetEntry]:
    """Every folder under `root` (or `root` itself) holding manifest.json AND index.html; no widget folder is searched further."""
    root = Path(root)
    found: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        if (folder / MANIFEST_NAME).is_file() and (folder / INDEX_NAME).is_file():
            found.append(folder)
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir() and p.name not in SKIP_DIRS)
        except OSError:
            return
        for child in children:
            walk(child, depth + 1)

    if root.is_dir():
        walk(root, 0)
    return [load_entry(p) for p in found]


# ------------------------------------------------------------------------------------------------ support and groups
def support_reason(entry: WidgetEntry, device: DeviceProfile | None) -> str | None:
    """None when HtmlTypeData::supported(false, device) is true, else why not."""
    if device is None or not entry.restrictions:
        return None
    types = [t for t in device.html_types if t in entry.restrictions]
    if not device.html_types:
        return f"{device.name} declares no SupportedHtmlWidgetDeviceTypes"
    if not types:
        return (f"the widget supports {sorted(entry.restrictions)} but {device.name} offers {device.html_types} "
                "(manifest supported_devices)")
    if any("sensor-screen" not in entry.restrictions[t] or device.touch_setup for t in types):
        return None
    return f"feature 'sensor-screen' needs HasTouchScreenSetupNotification, which {device.name} does not have"


@dataclass
class GroupInfo:
    name: str
    declared: bool                   # at least one widget declares x-icue-widget-group with this name
    widgets: list[WidgetEntry]
    image_widgets: Resolved          # widgets pipeline (pump / keyboard): raw icon of the first widget, no default
    icon_dash: Resolved              # Dash LCD pipeline: imageSource() of the first widget (with default)
    preview_panel: bool              # iCUE opens a preview panel only for more than one widget
    preview_reason: str


@dataclass
class Unlisted:
    entry: WidgetEntry
    reason: str


def build_groups(entries: list[WidgetEntry], device: DeviceProfile | None = None) -> tuple[list[GroupInfo], list[Unlisted]]:
    """The grouping of WidgetGroupsModel.cpp for one device. A widget without x-icue-widget-group forms a group named after
    the widget (iCUE's rule; it is flagged `declared=False`, nothing is invented)."""
    unlisted: list[Unlisted] = []
    buckets: dict[str, list[WidgetEntry]] = {}
    for entry in sorted(entries, key=lambda e: (e.type_id, str(e.folder))):
        if not entry.valid:
            unlisted.append(Unlisted(entry, "invalid widget: " + "; ".join(entry.check.errors)))
            continue
        why = support_reason(entry, device)
        if why is not None:
            unlisted.append(Unlisted(entry, "not supported by the device: " + why))
            continue
        buckets.setdefault(entry.group_name, []).append(entry)
    groups = []
    for name in sorted(buckets):
        members = buckets[name]
        many = len(members) > 1
        groups.append(GroupInfo(
            name, any(m.group_declared for m in members), members, members[0].group_image_widgets_pipeline(),
            members[0].group_icon_dash_pipeline(), many,
            f"{len(members)} widgets share the group: iCUE opens the preview panel" if many else
            "single-widget group: iCUE opens no preview panel (WidgetSetupPanelsController widgets.size() > 1; Dash createPreviewPanel "
            "returns null for size() == 1; keyboard selects the widget directly)"))
    return groups, unlisted