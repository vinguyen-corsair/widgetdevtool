r"""Read-only importer of widget instances from iCUE's `dashlcd\storage` (E2).

Verified against the iCUE v5.55 source and a real 5.52.93 file (see AI_HANDOFF.md, "E2"):
- File = cereal XML (`DashLCDStorage_s.cpp`): devices -> DeviceSettings (screenCollection{dashLCDScreens, activeScreenId},
  devicePersonalizationSettings, themeByScreenEnabled v>=501) -> ScreenSettings (widgets, screenId, name v>=501, personalizationSettings v>=502)
  -> Widget (id, slotIndex, size, attachment; Widget_s.cpp) -> polymorphic attachment (`WidgetBuilderWidgetAttachment`: base{id}, settings).
- settings = `HtmlSettingsData` (HtmlSettingsData_s.h, v505): base{id, typeId}, name, pathData{location, path}, data = map<key, QByteArray>;
  every value is `QDataStream << QVariant`, written by cereal as base64 (standard alphabet, NO padding). THREE ids exist: Widget id (slot),
  attachment id and settings id; the browser storage name uses the SETTINGS id.
- WidgetSize::EWidgetSize: SizeS = 1, SizeM = 2, SizeL = 4, SizeXL = 6.
NOT done by the importer: the load-time migrations of iCUE for older storage versions (reported instead), reading browser storage / cookies /
accounts, and any write to the iCUE file. The file is opened once, read-only, into memory.
Privacy: values are never printed or logged here; `mask_value` / `preview_lines` hide strings that could be URLs, paths, tokens or free text.
"""
from __future__ import annotations

import base64
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QDataStream, QUrl, QUuid
from PySide6.QtGui import QColor

from .instance_store import MARKER_KEYS, WidgetInstance

MAX_FILE_BYTES = 50 * 1024 * 1024
SUPPORTED_VERSIONS = {"HtmlSettingsData": 505, "Widget": 501, "ScreenSettings": 502, "DeviceSettings": 501}
SIZE_NAMES = {1: "SizeS", 2: "SizeM", 4: "SizeL", 6: "SizeXL"}
POLY_MASK = 0x3FFFFFFF
SENSITIVE_KEY = re.compile(r"token|secret|password|passwd|auth|api[-_]?key|apikey|cookie|session|bearer|credential|license|private", re.I)
SAFE_TEXT = re.compile(r"^[A-Za-z0-9 _.\-]{0,24}$")
COLOR_TEXT = re.compile(r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
STATUS_OK, STATUS_NULL, STATUS_BAD = "ok", "null", "undecodable"


@dataclass
class DecodedValue:
    key: str
    status: str                 # ok | null (stored QVariant is invalid) | undecodable (bytes cannot be read: NOT the same as null or missing)
    value: Any = None
    qtype: int = 0              # QMetaType id found in the stream header (0 = invalid/null)
    note: str = ""


@dataclass
class Candidate:
    device_key: str
    screen_id: str
    screen_name: str
    slot: int
    size: int
    widget_id: str
    attachment_id: str
    settings_id: str
    type_id: str
    name: str
    location: str
    path: str
    values: dict[str, DecodedValue] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)
    blocked: str = ""           # why it cannot be imported (storage newer than the importer understands)
    warnings: list[str] = field(default_factory=list)

    @property
    def size_name(self) -> str:
        return SIZE_NAMES.get(self.size, f"size {self.size} (unknown)")

    @property
    def label(self) -> str:
        return f"{self.name or self.type_id} ({self.type_id}) | device {self.device_key[:9]} | screen {self.screen_id[:9]} | slot {self.slot} | {self.size_name}"


@dataclass
class StorageReport:
    file_name: str
    file_bytes: int
    candidates: list[Candidate] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)          # non-HTML attachments (kind, device, slot)
    device_personalization: dict[str, dict] = field(default_factory=dict)
    screen_personalization: dict[str, dict] = field(default_factory=dict)
    theme_by_screen: dict[str, bool] = field(default_factory=dict)
    versions: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- values
def color_to_text(color: QColor) -> str:
    """What `toQmlString(QVariant(QColor))` gives the widget: the variant's string form (Qt 6.9.3: `#rrggbb`, `#aarrggbb` when not opaque;
    checked against QJSEngine in the tests; the Qt of iCUE 5.52.93 itself is not verified)."""
    return color.name(QColor.NameFormat.HexArgb) if color.alpha() != 255 else color.name()


def _convert(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, QColor):
        return color_to_text(obj)
    if isinstance(obj, QUuid):
        return obj.toString()
    if isinstance(obj, QUrl):
        return obj.toString()
    if isinstance(obj, dict):
        return {str(k): _convert(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_convert(v) for v in obj]
    raise TypeError(f"unsupported value type {type(obj).__name__}")


def decode_bytes(raw: bytes) -> tuple[str, Any, int, str]:
    """QDataStream QVariant bytes -> (status, value, qtype, note)."""
    if len(raw) < 4:
        return STATUS_BAD, None, -1, "stream shorter than a QVariant header"
    qtype = int.from_bytes(raw[:4], "big")
    if qtype == 0:
        return STATUS_NULL, None, 0, "invalid QVariant stored"
    stream = QDataStream(QByteArray(raw))
    stream.setVersion(QDataStream.Version.Qt_6_5)
    try:
        obj = stream.readQVariant()
        if stream.status() != QDataStream.Status.Ok:
            return STATUS_BAD, None, qtype, f"QDataStream status {stream.status().name} (type id {qtype} unknown here?)"
        if not stream.atEnd():
            return STATUS_BAD, None, qtype, "unread bytes after the value"
        return STATUS_OK, _convert(obj), qtype, ""
    except Exception as exc:  # noqa: BLE001 - any failure = cannot decode, never a guess
        return STATUS_BAD, None, qtype, f"{type(exc).__name__}"


def decode_value(key: str, text: str | None) -> DecodedValue:
    """cereal base64 text -> DecodedValue. Missing text is undecodable too (an empty <value/> is not a null)."""
    cleaned = (text or "").strip()
    if not cleaned:
        return DecodedValue(key, STATUS_BAD, None, -1, "empty value text")
    try:
        raw = base64.b64decode(cleaned.replace("-", "+").replace("_", "/") + "=" * (-len(cleaned) % 4), validate=True)
    except Exception:  # noqa: BLE001
        return DecodedValue(key, STATUS_BAD, None, -1, "not base64")
    status, value, qtype, note = decode_bytes(raw)
    return DecodedValue(key, status, value, qtype, note)


def parse_rgb(text: str | None) -> str | None:
    """cereal text of a QColor: `rgb(r g b)` / `rgba(r g b a)` with 0..1 floats, or `#rrggbb[aa]` -> `#rrggbb` (`#aarrggbb`)."""
    if not text:
        return None
    text = text.strip()
    if COLOR_TEXT.match(text):
        return text.lower()
    match = re.match(r"^rgba?\(([^)]*)\)$", text)
    if not match:
        return None
    try:
        parts = [float(p) for p in match.group(1).replace(",", " ").split()]
    except ValueError:
        return None
    if len(parts) not in (3, 4):
        return None
    channels = [max(0, min(255, round(p * 255))) for p in parts]
    color = QColor(channels[0], channels[1], channels[2], channels[3] if len(channels) == 4 else 255)
    return color_to_text(color)


# ---------------------------------------------------------------- privacy
def _secret_like(text: str) -> bool:
    return len(text) >= 16 and " " not in text and bool(re.search(r"[A-Za-z]", text)) and bool(re.search(r"[0-9]", text))


def mask_value(key: str, value: Any) -> str:
    """Printable form that never reveals text that could be private: numbers, bools and colors are shown, short plain words are shown,
    everything else (URLs, paths, long or token-like text, keys named token/secret/...) only as `<text N chars>`."""
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return repr(value)
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {mask_value(str(k), v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(mask_value(key, v) for v in value) + "]"
    text = str(value)
    if text == "":
        return "''"
    if COLOR_TEXT.match(text):
        return text
    if SENSITIVE_KEY.search(key) or _secret_like(text) or not SAFE_TEXT.match(text):
        return f"<text {len(text)} chars>"
    return repr(text)


def preview_lines(candidate: Candidate) -> list[str]:
    lines = []
    for key in sorted(candidate.values):
        item = candidate.values[key]
        if item.status == STATUS_OK:
            lines.append(f"{key} = {mask_value(key, item.value)}   [qtype {item.qtype}]")
        elif item.status == STATUS_NULL:
            lines.append(f"{key} = null   [stored invalid QVariant: kept as null]")
        else:
            lines.append(f"{key} = UNDECODABLE ({item.note})   [not imported]")
    return lines


# ---------------------------------------------------------------- storage file
def default_storage_path() -> Path:
    """Where iCUE keeps the file (only a starting folder for the file dialog; nothing is read from here automatically)."""
    return Path(os.environ.get("APPDATA", "")) / "Corsair" / "CUE5" / "dashlcd" / "storage"


def _text(element: ET.Element | None, tag: str, default: str = "") -> str:
    found = element.find(tag) if element is not None else None
    return (found.text or "").strip() if found is not None and found.text is not None else default


def _data(holder: ET.Element | None) -> ET.Element | None:
    """cereal shared pointer: <holder><ptr_wrapper><id/><data/></ptr_wrapper></holder>; a repeated pointer has no <data> (back reference)."""
    wrapper = holder.find("ptr_wrapper") if holder is not None else None
    return wrapper.find("data") if wrapper is not None else None


def _items(container: ET.Element | None) -> list[ET.Element]:
    return [c for c in container if re.fullmatch(r"value\d+", c.tag)] if container is not None else []


def _int(text: str, default: int = -1) -> int:
    try:
        return int(text)
    except ValueError:
        return default


def _personalization(data: ET.Element | None) -> dict:
    if data is None:
        return {}
    out = {}
    for tag, name in (("textColor", "textColor"), ("accentColor", "accentColor"), ("backgroundColor", "backgroundColor"),
                      ("widgetBackgroundColor", "widgetBackgroundColor")):
        out[name] = parse_rgb(_text(data, tag))
    out["widgetTransparency"] = _int(_text(data, "widgetTransparency"), -1) if _text(data, "widgetTransparency") else None
    return out


def read_storage(path: str | Path) -> StorageReport:
    r"""Parse an iCUE `dashlcd\storage` file (or a copy). READ-ONLY: the file is read once into memory and never written or locked."""
    source = Path(path)
    size = source.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError("file is larger than 50 MB: not an iCUE dashlcd storage")
    with open(source, "rb") as handle:
        raw = handle.read()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"not an XML (cereal) file: {exc}") from None
    report = StorageReport(source.name, size)
    names: dict[int, str] = {}
    for holder in root.iter():
        pid, pname = holder.find("polymorphic_id"), holder.find("polymorphic_name")
        if pid is not None and pname is not None and (pname.text or "").strip():
            names[_int(pid.text or "", 0) & POLY_MASK] = pname.text.strip()
    devices = root.find("value0/devices")
    if devices is None:
        raise ValueError("no <devices> in the file: this is not a dashlcd storage")
    first_versions = root.find("value0/cereal_class_version")
    report.versions["DashLCDStorage"] = _int(first_versions.text or "") if first_versions is not None else -1
    seen_html_version = seen_widget_version = None
    for device in _items(devices):
        device_key = _text(device, "key")
        device_data = _data(device.find("value"))
        if device_data is None:
            report.warnings.append(f"device {device_key[:9]}: repeated pointer without data (skipped)")
            continue
        report.device_personalization[device_key] = _personalization(_data(device_data.find("devicePersonalizationSettings")))
        report.theme_by_screen[device_key] = _text(device_data, "themeByScreenEnabled") == "true"
        collection = _data(device_data.find("screenCollection"))
        screens = collection.find("dashLCDScreens") if collection is not None else None
        for screen_holder in _items(screens):
            screen = _data(screen_holder)
            if screen is None:
                report.warnings.append("a screen is a repeated pointer without data (skipped)")
                continue
            screen_id, screen_name = _text(screen, "screenId"), _text(screen, "name")
            report.screen_personalization[screen_id] = _personalization(_data(screen.find("personalizationSettings")))
            for widget_holder in _items(screen.find("widgets")):
                widget = _data(widget_holder)
                if widget is None:
                    report.warnings.append("a widget is a repeated pointer without data (skipped)")
                    continue
                version = widget.find("cereal_class_version")
                seen_widget_version = _int(version.text or "") if version is not None else seen_widget_version
                attachment_holder = widget.find("attachment")
                poly = names.get(_int(_text(attachment_holder, "polymorphic_id"), 0) & POLY_MASK, "")
                slot = _int(_text(widget, "slotIndex"), -1)
                if not poly.endswith("WidgetBuilderWidgetAttachment"):
                    report.skipped.append(f"{poly.rsplit('::', 1)[-1] or 'unknown attachment'} (device {device_key[:9]}, slot {slot}): not an HTML widget")
                    continue
                attachment = _data(attachment_holder)
                settings_holder = attachment.find("settings") if attachment is not None else None
                settings = _data(settings_holder)
                if settings is None:
                    report.warnings.append(f"widget slot {slot}: settings is a repeated pointer without data (skipped)")
                    continue
                html_version = settings.find("cereal_class_version")
                seen_html_version = _int(html_version.text or "") if html_version is not None else seen_html_version
                base = settings.find("base")
                candidate = Candidate(
                    device_key, screen_id, screen_name, slot, _int(_text(widget, "size")), _text(widget, "id"),
                    _text(attachment.find("base"), "id") if attachment is not None else "", _text(base, "id"), _text(base, "typeId"),
                    _text(settings, "name"), _text(settings.find("pathData"), "location"), _text(settings.find("pathData"), "path"))
                for item in _items(settings.find("data")):
                    key = _text(item, "key")
                    candidate.values[key] = decode_value(key, _text(item, "value") or None)
                candidate.versions = {"HtmlSettingsData": seen_html_version or -1, "Widget": seen_widget_version or -1}
                report.candidates.append(candidate)
    report.versions.update({k: v for k, v in (("HtmlSettingsData", seen_html_version), ("Widget", seen_widget_version)) if v is not None})
    for candidate in report.candidates:
        html_version = report.versions.get("HtmlSettingsData", -1)
        candidate.versions["HtmlSettingsData"] = html_version
        if html_version > SUPPORTED_VERSIONS["HtmlSettingsData"]:
            candidate.blocked = f"settings were written by a newer iCUE (HtmlSettingsData v{html_version} > v{SUPPORTED_VERSIONS['HtmlSettingsData']} known)"
        elif 0 <= html_version < SUPPORTED_VERSIONS["HtmlSettingsData"]:
            candidate.warnings.append(f"older storage format (HtmlSettingsData v{html_version}): iCUE migrates ids, paths, sensors and backgroundMedia on load; "
                                      "the importer does NOT apply those migrations (values are imported as stored)")
        bad = sorted(k for k, v in candidate.values.items() if v.status == STATUS_BAD)
        if bad:
            candidate.warnings.append(f"{len(bad)} value(s) cannot be decoded and are NOT imported: {', '.join(bad)}")
    return report

# ---------------------------------------------------------------- library, comparison, instance
def matching_entries(candidate: Candidate, entries: list) -> list:
    """Library entries whose manifest id equals the typeId. Never a guess: no match = empty list (the caller says so)."""
    return [e for e in entries if e.type_id and e.type_id == candidate.type_id]


def installed_manifest_version(candidate: Candidate, roots: dict[str, Path] | None = None) -> str | None:
    """Version of the widget as iCUE has it installed (read-only look at its manifest.json), or None when it cannot be found."""
    if roots is None:
        roots = {"UserStorage": Path(os.environ.get("APPDATA", "")) / "Corsair" / "CUE5" / "html_widgets",
                 "InternalStorage": Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Corsair" / "Corsair iCUE5 Software" / "widgets"}
    root = roots.get(candidate.location)
    if root is None or not candidate.path:
        return None
    manifest = (root / Path(candidate.path.replace("\\", "/")).parent / "manifest.json")
    try:
        import json
        return str(json.loads(manifest.read_text(encoding="utf-8-sig")).get("version", "")) or None
    except (OSError, ValueError):
        return None


def comparison(candidate: Candidate, entry, installed_version: str | None) -> dict:
    """What differs between the stored instance and the library widget: versions, missing and no-longer-declared properties."""
    from .runtime_bridge import parse_widget_settings
    declared = set(parse_widget_settings(entry.check).defaults())
    stored = {k for k, v in candidate.values.items() if v.status != STATUS_BAD}
    undecodable = {k for k, v in candidate.values.items() if v.status == STATUS_BAD}
    manifest_version = str((entry.check.manifest or {}).get("version", "")) or None
    return {"library_version": manifest_version, "icue_version": installed_version,
            "version_differs": bool(manifest_version and installed_version and manifest_version != installed_version),
            "declared_missing_in_icue": sorted(declared - stored - undecodable - set(MARKER_KEYS)),
            "stored_not_declared": sorted(stored - declared - set(MARKER_KEYS)), "undecodable": sorted(undecodable)}


def build_instance(candidate: Candidate, entry) -> WidgetInstance:
    """A NEW, independent instance (random id, so its own storage): the stored values exactly as they are (restore semantics: no default is added,
    a null stays null, an undecodable value is left out and recorded). `edited` = every imported key, so a later live reload keeps them."""
    if candidate.blocked:
        raise ValueError(candidate.blocked)
    data = {k: v.value for k, v in candidate.values.items() if v.status in (STATUS_OK, STATUS_NULL)}
    instance = WidgetInstance.create(entry.type_id, None, name=entry.name or candidate.name, data=data)
    instance.edited = set(data)
    instance.size = SIZE_NAMES.get(candidate.size)  # E3: the stored size becomes THIS instance's own size (None when iCUE stored an unknown value)
    instance.origin = {
        "kind": "icue-import", "icueWidgetId": candidate.widget_id, "icueAttachmentId": candidate.attachment_id, "icueSettingsId": candidate.settings_id,
        "deviceKey": candidate.device_key, "screenId": candidate.screen_id, "slot": candidate.slot, "size": candidate.size, "sizeName": candidate.size_name,
        "storageVersions": dict(candidate.versions),
        "nullKeys": sorted(k for k, v in candidate.values.items() if v.status == STATUS_NULL),
        "undecodableKeys": sorted(k for k, v in candidate.values.items() if v.status == STATUS_BAD)}
    return instance


PERSONALIZATION_FIELDS = {"textColor": "text_color", "accentColor": "accent_color", "widgetBackgroundColor": "widget_background_color",
                          "widgetTransparency": "widget_transparency"}


def personalization_values(report: StorageReport, candidate: Candidate) -> dict[str, dict[str, Any]]:
    """{'device': {field: value}, 'screen': {...}} in the tool's PersonalizationValues field names (only values that were stored)."""
    out: dict[str, dict[str, Any]] = {}
    for scope, source in (("device", report.device_personalization.get(candidate.device_key, {})),
                          ("screen", report.screen_personalization.get(candidate.screen_id, {}))):
        out[scope] = {PERSONALIZATION_FIELDS[k]: v for k, v in source.items() if k in PERSONALIZATION_FIELDS and v is not None and v != -1}
    return out

def preview_text(report: StorageReport, candidate: Candidate, matches: list, installed_version: str | None, library_size: int = 0, device=None) -> str:
    """The text shown BEFORE the user confirms an import. Values are masked (see mask_value); ids are shortened; no file path is shown."""
    lines = [f"Source file: {report.file_name} ({report.file_bytes} bytes), read-only",
             f"Widget: {candidate.name or '(no name stored)'}   typeId: {candidate.type_id}   stored at: {candidate.location}",
             f"Device key {candidate.device_key[:9]}...   screen {candidate.screen_id[:9]}...   slot {candidate.slot}   stored size: {candidate.size_name}",
             f"Ids (kept only as origin info): widget {candidate.widget_id[:9]}..., attachment {candidate.attachment_id[:9]}..., settings {candidate.settings_id[:9]}...",
             "The import creates a NEW instance (new id, its own empty storage). Browser storage, cookies and account data of iCUE are NOT read or copied.",
             "The device is not selected by the import: its online/offline state is not stored in this file.",
             f"The stored size {candidate.size_name} becomes this instance's OWN size (kept even when the current device does not offer it)."]
    if device is not None and device.orientations() and candidate.size_name not in device.supported_sizes():
        lines.append(f"NOTE: the current device ({device.name}) does not offer {candidate.size_name}: the preview will show its default size until a device that does is selected.")
    if candidate.blocked:
        lines.append(f"BLOCKED: {candidate.blocked}")
    lines += [f"WARNING: {w}" for w in candidate.warnings]
    if not matches:
        lines.append(f"NO MATCH: no widget with typeId {candidate.type_id} among the {library_size} widgets of the widgets folder. Nothing is guessed: "
                     "open the widget once with Open Widget (or change the widgets folder in the iCUE tab), then try again.")
    else:
        lines.append(f"Library matches: {len(matches)} ({'choose one' if len(matches) > 1 else 'one'})")
        for entry in matches:
            info = comparison(candidate, entry, installed_version)
            lines.append(f" - {entry.folder.name}: library version {info['library_version'] or 'unknown'}, iCUE installed version {info['icue_version'] or 'not found'}"
                         + (" -> VERSIONS DIFFER (settings may not match this source)" if info["version_differs"] else ""))
            if info["declared_missing_in_icue"]:
                lines.append(f"   declared by this source but missing in iCUE's data (stay missing, no default is added): {', '.join(info['declared_missing_in_icue'])}")
            if info["stored_not_declared"]:
                lines.append(f"   stored by iCUE but not declared by this source: {', '.join(info['stored_not_declared'])}")
    markers = {k: candidate.values[k] for k in MARKER_KEYS if k in candidate.values}
    lines.append("Custom Style: " + (", ".join(f"{k.replace('widgetPersonalizationCustomStyle', '')}={v.value}" for k, v in markers.items()) if markers else "no marker stored"))
    lines.append(f"Values ({len(candidate.values)}; strings that could be private are shown only as lengths):")
    lines += ["  " + line for line in preview_lines(candidate)]
    return "\n".join(lines)