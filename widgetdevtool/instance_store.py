"""Widget instances: one id and one settings map per placed widget (HtmlSettingsImpl / HtmlSettingsData).

Mirrors the behaviour of the iCUE source (branch v5.55, NOT verified against release 5.52.93):

- create  = HtmlSettingsImpl::createWithDefaultsAsync: a new random id; the defaults are stored only
  for keys that do not exist yet.
- restore = SettingsFactoryImpl::createSettings(data) -> HtmlSettingsImpl(data): the stored data is used
  as it is. NO default is added, so a property that is missing in an old instance stays missing (the widget
  gets no `window.<key>` for it).
- merge   = HtmlSettingsImpl::update(old) on a freshly created instance: the id of the old instance is kept;
  when the type id is the same, the new defaults are overlaid with the old values of the keys that still
  exist (nested maps are merged recursively; keys that exist only in the old data are dropped, except
  the personalization switch).
- clone   = HtmlSettingsImpl::clone: same type, name and data, but a new id.
- edited  = WidgetDevTool only: the keys whose value was set by a user edit (set_value). It tells a live reload which values
  are the user's (kept) and which are source defaults (replaced by the new defaults of the edited HTML).
- set_value = HtmlSettingsImpl::setValue: nothing happens when the value is equal; no clamping, no validation.

The JSON file format of `InstanceStore.save` is WidgetDevTool's own; it is NOT iCUE's cereal format.
"""
from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# widgets/widgetshelpers/src/WidgetPersonalizationSettings.cpp: the only key update() carries over blindly.
CUSTOM_STYLE_ENABLED_KEY = "widgetPersonalizationCustomStyleEnabled"
CUSTOM_STYLE_PRESENT_KEY = "widgetPersonalizationCustomStylePresent"
# Host markers, not declared by any widget: a live reload must not drop them (see refresh_from_source).
MARKER_KEYS = (CUSTOM_STYLE_ENABLED_KEY, CUSTOM_STYLE_PRESENT_KEY)
STORE_VERSION = 1

ValueListener = Callable[[str, Any], None]


def new_instance_id() -> str:
    """QUuid::createUuid(): random version-4 UUID (lower case, no braces)."""
    return str(uuid.uuid4())


def _same(a: Any, b: Any) -> bool:
    """QVariant equality for the cases that matter here: a bool never equals a number."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


def update_settings_maps(new: dict, old: dict) -> dict:
    """HtmlSettingsImpl.cpp updateSettingsMaps(newSettings, oldSettings)."""
    merged = copy.deepcopy(new)
    for key, old_value in old.items():
        if key not in merged:
            continue
        if isinstance(merged[key], dict) and isinstance(old_value, dict):
            merged[key] = update_settings_maps(merged[key], old_value)
        else:
            merged[key] = copy.deepcopy(old_value)
    if CUSTOM_STYLE_ENABLED_KEY in old:
        merged[CUSTOM_STYLE_ENABLED_KEY] = copy.deepcopy(old[CUSTOM_STYLE_ENABLED_KEY])
    return merged


@dataclass
class WidgetInstance:
    id: str
    type_id: str
    name: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # WidgetDevTool only: folder of the widget this instance belongs to (needed to open it again)
    edited: set[str] = field(default_factory=set, compare=False)  # WidgetDevTool only: user-edited keys (see module doc)
    size: str | None = field(default=None, compare=False)  # WidgetDevTool only (E3): the instance's own widget size (SizeS/M/L/XL); None = device default
    orientation: str | None = field(default=None, compare=False)  # WidgetDevTool only (E3): the instance's own orientation
    origin: dict[str, Any] = field(default_factory=dict, compare=False)  # WidgetDevTool only: where an imported instance came from (ids, sizes; NEVER values)
    _listeners: list[ValueListener] = field(default_factory=list, repr=False, compare=False)

    # ---- construction ----
    @classmethod
    def create(cls, type_id: str, defaults: dict[str, Any] | None = None, *, id: str | None = None,
               name: str = "", data: dict[str, Any] | None = None) -> "WidgetInstance":
        """New instance: `data` first (values given by the caller), then defaults for missing keys only."""
        instance = cls(id or new_instance_id(), type_id, name, copy.deepcopy(data) if data else {})
        for key, value in (defaults or {}).items():
            if key not in instance.data:
                instance.data[key] = copy.deepcopy(value)
        return instance

    @classmethod
    def restore(cls, record: dict[str, Any]) -> "WidgetInstance":
        """Stored record -> instance, exactly as stored (no default is added)."""
        return cls(str(record["id"]), str(record.get("typeId", "")), str(record.get("name", "")),
                   copy.deepcopy(record.get("data", {})), str(record.get("source", "")),
                   {str(k) for k in record.get("edited", [])} & set(record.get("data", {})),
                   record.get("size"), record.get("orientation"), dict(record.get("origin", {})))

    @classmethod
    def merge(cls, old: "WidgetInstance", defaults: dict[str, Any], *, type_id: str | None = None,
              name: str = "") -> "WidgetInstance":
        """update(): new defaults, old values win for the keys that still exist; the old id is kept.

        When the type id differs the old data is ignored (only the id carries over), as in iCUE. The name
        is not carried over (update() copies only id and data)."""
        new_type = type_id if type_id is not None else old.type_id
        fresh = cls.create(new_type, defaults, name=name)
        fresh.id = old.id
        fresh.source = old.source
        fresh.origin = copy.deepcopy(old.origin)
        fresh.size, fresh.orientation = old.size, old.orientation
        if new_type == old.type_id:
            fresh.data = update_settings_maps(fresh.data, old.data)
            fresh.edited = set(old.edited) & set(fresh.data)
        return fresh

    def clone(self) -> "WidgetInstance":
        return WidgetInstance(new_instance_id(), self.type_id, self.name, copy.deepcopy(self.data), self.source,
                              set(self.edited), self.size, self.orientation, copy.deepcopy(self.origin))

    def refresh_from_source(self, defaults: dict[str, Any]) -> dict[str, Any]:
        """Live reload of an instance that follows its source: the data becomes the (new) defaults of the edited HTML, except
        that user-edited keys which are still declared keep the user's value. Keys that are no longer declared are dropped.
        Not iCUE's update() (there the old value of every surviving key wins; see merge()). Returns what changed."""
        old = self.data
        kept = {k for k in self.edited if k in defaults}
        self.data = {k: (copy.deepcopy(old[k]) if k in kept and k in old else copy.deepcopy(v)) for k, v in defaults.items()}
        for marker in MARKER_KEYS:  # Custom Style switch / marker belong to the instance, not to the source
            if marker in old and marker not in defaults:
                self.data[marker] = copy.deepcopy(old[marker])
                if marker in self.edited:
                    kept.add(marker)
        self.edited = kept
        return {"added": [k for k in defaults if k not in old],
                "dropped": [k for k in old if k not in defaults and k not in MARKER_KEYS],
                "defaults_changed": [k for k in defaults if k in old and k not in kept and not _same(old[k], defaults[k])],
                "user_values_kept": sorted(kept)}

    # ---- identity ----
    def unique_id(self, is_preview: bool = False) -> str:
        """The runtime `uniqueId`: widget id as QUuid::toString() ('{...}'), '-preview' for previews."""
        return "{" + self.id + "}" + ("-preview" if is_preview else "")

    # ---- values ----
    def subscribe(self, listener: ValueListener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: ValueListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def differs(self, key: str, value: Any) -> bool:
        """True when set_value(key, value) would change something (the key is missing or the value differs)."""
        return key not in self.data or not _same(self.data[key], value)

    def set_value(self, key: str, value: Any, *, mark_edited: bool = True) -> bool:
        """setValue: no-op when equal. The key is created even for a null value (QMap::operator[]).
        `mark_edited=False` is for host markers (widgetPersonalizationCustomStylePresent), which are not user edits."""
        if mark_edited:
            self.edited.add(key)
        existed = key in self.data
        old = self.data.setdefault(key, None)
        if existed and _same(old, value) or (not existed and value is None):
            return False
        self.data[key] = copy.deepcopy(value)
        for listener in list(self._listeners):
            listener(key, value)
        return True

    def clear(self) -> None:
        names = list(self.data)
        self.data.clear()
        self.edited.clear()
        for name in names:
            for listener in list(self._listeners):
                listener(name, None)

    def drop_keys(self, *keys: str) -> None:
        """Test helper: simulate an instance saved before a property existed (no notification)."""
        for key in keys:
            self.data.pop(key, None)
            self.edited.discard(key)

    def to_record(self) -> dict[str, Any]:
        record = {"id": self.id, "typeId": self.type_id, "name": self.name, "source": self.source,
                  "data": copy.deepcopy(self.data), "edited": sorted(self.edited)}
        if self.origin:
            record["origin"] = copy.deepcopy(self.origin)
        if self.size is not None:
            record["size"] = self.size
        if self.orientation is not None:
            record["orientation"] = self.orientation
        return record


class InstanceStore:
    """All instances of the workspace, keyed by instance id."""

    def __init__(self) -> None:
        self._instances: dict[str, WidgetInstance] = {}

    def __len__(self) -> int:
        return len(self._instances)

    def __contains__(self, instance_id: str) -> bool:
        return instance_id in self._instances

    def all(self) -> list[WidgetInstance]:
        return list(self._instances.values())

    def get(self, instance_id: str) -> WidgetInstance | None:
        return self._instances.get(instance_id)

    def of_type(self, type_id: str) -> list[WidgetInstance]:
        return [i for i in self._instances.values() if i.type_id == type_id]

    def add(self, instance: WidgetInstance) -> WidgetInstance:
        if instance.id in self._instances:
            raise ValueError(f"instance id already in the store: {instance.id}")
        self._instances[instance.id] = instance
        return instance

    def create(self, type_id: str, defaults: dict[str, Any] | None = None, *, name: str = "") -> WidgetInstance:
        return self.add(WidgetInstance.create(type_id, defaults, name=name))

    def replace(self, instance: WidgetInstance) -> WidgetInstance:
        """Put `instance` under its id (adds it when unknown): used when the runtime rebuilds the same instance."""
        self._instances[instance.id] = instance
        return instance

    def merge(self, instance_id: str, defaults: dict[str, Any], *, type_id: str | None = None) -> WidgetInstance:
        """Replace the instance by update(old) (same id, merged data)."""
        merged = WidgetInstance.merge(self._instances[instance_id], defaults, type_id=type_id)
        self._instances[instance_id] = merged
        return merged

    def clone(self, instance_id: str) -> WidgetInstance:
        return self.add(self._instances[instance_id].clone())

    def remove(self, instance_id: str) -> None:
        self._instances.pop(instance_id, None)

    # ---- persistence (WidgetDevTool's own JSON; load() is a restore: nothing is added) ----
    def to_json(self) -> str:
        return json.dumps({"version": STORE_VERSION, "instances": [i.to_record() for i in self._instances.values()]},
                          indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "InstanceStore":
        doc = json.loads(text)
        if not isinstance(doc, dict) or doc.get("version") != STORE_VERSION:
            raise ValueError("unsupported instance store file")
        store = cls()
        for record in doc.get("instances", []):
            store.add(WidgetInstance.restore(record))
        return store

    def save(self, path: str | Path) -> None:
        target = Path(path)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(self.to_json(), encoding="utf-8")
        tmp.replace(target)

    @classmethod
    def load(cls, path: str | Path) -> "InstanceStore":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))