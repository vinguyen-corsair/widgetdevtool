"""Widget settings parser: <meta x-icue-property>, x-icue-groups and the default value of every control.

Source of truth (iCUE source, branch v5.55; NOT verified against the installed release):
- HtmlParser.cpp: parseMetaParameter (data-type dispatch), parse<Type>, parsMapOrList, parsList,
  buildSettings (x-icue-groups), evalJsExpression / evalJsExpressionProperty.
- JavaScriptExecutor.cpp: toJsFormat, importModule (manifest "modules"), tr().
- EditorSettingsParameters.h: toSetting() = the (key, default value) pair stored for each type.
- HtmlSettingsImpl.cpp: createWithDefaultsAsync stores toSetting() only for keys that do not exist yet.

Nothing is clamped, validated or corrected here: iCUE does not do it either at this stage (the slider
model, the combobox and the personalization code never clamp; validation runs once, at import time).
Behaviour that comes from Qt API semantics instead of iCUE code is marked "Qt semantics" and is
reported in `SettingParameter.notes` where it matters.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QColor
from PySide6.QtQml import QJSEngine, QJSValue

# data-type values handled by HtmlParser.cpp::parseMetaParameter (exact, case-sensitive comparison).
# Every one of them stores a value through toSetting(), except "app-status" (null value, status display).
VALUE_TYPES = frozenset({
    "color", "colors", "combobox", "media-selector", "sensors-combobox", "sensors-factory", "slider",
    "slider-enum", "switch", "tab-buttons", "textfield", "accounts-list", "time-format",
    "search-combobox", "stream-deck-virtual-device",
})
STATUS_TYPES = frozenset({"app-status"})
# Accepted by HtmlValidation.cpp but parseMetaParameter has no branch for it (null parameter) and
# EditorSettingsModelsFactory returns nullptr for it.
NO_PARAMETER_TYPES = frozenset({"media-session"})

META_NAMES = ("x-icue-property", "x-icue-info")  # both go to ParsedHeadData::metaProperties

_INT32 = (-(2 ** 31), 2 ** 31 - 1)
_INT_RE = re.compile(r"[+-]?[0-9]+")


@dataclass
class PropertySpec:
    """A raw <meta> element (name, data-type, data-default, every attribute)."""
    name: str
    type: str
    default_expr: str | None
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class SettingParameter:
    """One parsed control: the stored default plus every attribute a settings UI needs."""
    name: str
    data_type: str
    label: str
    has_value: bool                    # False: the control only shows a state (app-status)
    default: Any = None
    default_valid: bool = True         # False: the default could not be computed here (see notes)
    unresolved: bool = False           # True: iCUE computes the real default later (provider call)
    group: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    attrs: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        return "value" if self.has_value else "status"


@dataclass
class SettingsGroup:
    title: str
    info: str = ""
    description: str = ""
    parameters: list[SettingParameter] = field(default_factory=list)


@dataclass
class ParsedSettings:
    title: str
    groups: list[SettingsGroup]
    specs: list[PropertySpec]
    skipped: list[dict[str, str]]
    notes: list[str]

    @property
    def parameters(self) -> list[SettingParameter]:
        return [p for g in self.groups for p in g.parameters]

    def parameter(self, name: str) -> SettingParameter | None:
        return next((p for p in self.parameters if p.name == name), None)

    def defaults(self) -> dict[str, Any]:
        """{key: stored default}: what HtmlSettingsImpl::createWithDefaultsAsync would store."""
        return {p.name: p.default for p in self.parameters if p.has_value and p.default_valid}


# ---- HTML head ------------------------------------------------------------------------------------
class _HeadParser(HTMLParser):
    """<title>, the x-icue-groups JSON script and every x-icue-property / x-icue-info meta."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.specs: list[PropertySpec] = []
        self.title = ""
        self.groups_json = ""
        self._title_parts: list[str] | None = None
        self._groups_parts: list[str] | None = None

    def handle_starttag(self, tag, attrs):  # noqa: D102
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta" and a.get("name") in META_NAMES and a.get("content"):
            self.specs.append(PropertySpec(a["content"], a.get("data-type", ""), a.get("data-default"), a))
        elif tag == "title":
            self._title_parts = []
        elif tag == "script" and a.get("type") == "application/json" and a.get("id") == "x-icue-groups":
            self._groups_parts = []

    def handle_endtag(self, tag):  # noqa: D102
        if tag == "title" and self._title_parts is not None:
            self.title, self._title_parts = "".join(self._title_parts), None
        elif tag == "script" and self._groups_parts is not None:
            self.groups_json, self._groups_parts = "".join(self._groups_parts), None

    def handle_data(self, data):  # noqa: D102
        if self._title_parts is not None:
            self._title_parts.append(data)
        elif self._groups_parts is not None:
            self._groups_parts.append(data)


def _read_head(html_text: str) -> _HeadParser:
    parser = _HeadParser()
    parser.feed(html_text)
    parser.close()
    return parser


def parse_properties(html_text: str) -> list[PropertySpec]:
    """Raw meta elements in document order (kept for callers that only need the specs)."""
    return _read_head(html_text).specs


# ---- JavaScriptExecutor -----------------------------------------------------------------------------
def manifest_modules(manifest: dict | None, index_path: Path) -> list[Path]:
    """Paths of manifest "modules" (string entries only, like ManifestParser.cpp ModulesParser).

    Resolved against the folder of index.html (HtmlParser.cpp: pathToFile.resolved(QUrl(jsModule))).
    """
    entries = (manifest or {}).get("modules")
    if not isinstance(entries, list):
        return []
    return [(Path(index_path).parent / m).resolve() for m in entries if isinstance(m, str)]


class _TrBridge(QObject):
    """Exposes the widget translator to the JS engine as global tr() (JavaScriptExecutor.cpp)."""

    def __init__(self, translate: Callable[[str], str]) -> None:
        super().__init__()
        self._translate = translate

    @Slot(str, result=str)
    def translate(self, text: str) -> str:
        return self._translate(text)


def _json_load(text: str) -> tuple[bool, Any]:
    def _reject(constant):  # NaN / Infinity are not JSON (Qt rejects them as well)
        raise ValueError(constant)
    try:
        return True, json.loads(text, parse_constant=_reject)
    except ValueError:
        return False, None


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() and abs(value) < 1e15 else value
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    return None


def compact_json(value: Any) -> str:
    """QJsonDocument::toJson(Compact): no spaces, object keys sorted."""
    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def to_js_format(value: QJSValue) -> str:
    """JavaScriptExecutor::toJsFormat: JSON for objects/arrays, otherwise QJsonValue::toString().

    QJsonValue::toString() returns "" for anything that is not a string (Qt semantics): a number,
    a boolean, null or undefined therefore become "".
    """
    if value.isString():
        return value.toString()
    if value.isCallable():
        return ""
    if value.isArray() or value.isObject():
        return compact_json(value.toVariant())
    return ""


class _JsEnv:
    """One QJSEngine per parse: manifest modules as globals named after the file, plus tr()."""

    def __init__(self, translate: Callable[[str], str], modules: list[Path] | None, plugins: dict | None = None) -> None:
        self.notes: list[str] = []
        self.engine = QJSEngine()
        self._tr = _TrBridge(translate)  # must outlive the engine calls
        self.engine.globalObject().setProperty("tr", self.engine.newQObject(self._tr).property("translate"))
        # JavaScriptExecutor::importPlugin: each required plugin becomes plugins.<Name>. Only plugins with a WidgetDevTool parse stub
        # (MOCK) exist here; an undeclared or unmocked plugin stays undefined, so its data-default fails as before.
        self._plugins = {name: factory() for name, factory in (plugins or {}).items()}
        if self._plugins:
            holder = self.engine.newObject()
            for name, obj in self._plugins.items():
                holder.setProperty(name, self.engine.newQObject(obj))
            self.engine.globalObject().setProperty("plugins", holder)
            self.notes.append("plugins." + ", plugins.".join(self._plugins) + " in the settings parser: WidgetDevTool MOCK "
                              "(values from the mock catalog, not this PC)")
        for module_path in modules or []:
            if not Path(module_path).is_file():
                self.notes.append(f"module {module_path} does not exist (manifest.modules), skipped")
                continue
            module = self.engine.importModule(str(module_path))
            if module.isError():
                self.notes.append(f"module {Path(module_path).name} failed to load: {module.toString()}")
                continue
            self.engine.globalObject().setProperty(Path(module_path).name.split(".")[0], module)

    def expression(self, expr: str) -> str:
        """evalJsExpression: on a JS error the raw text is returned."""
        result = self.engine.evaluate(expr)
        return expr if result.isError() else to_js_format(result)

    def property(self, expr: str) -> tuple[str | None, str | None]:
        """evalJsExpressionProperty -> (text, error). An error yields (None, message): what iCUE
        stores for a failed evaluation is not visible in the source (UNVERIFIED)."""
        result = self.engine.evaluate(expr)
        if result.isError():
            return None, result.toString()
        return to_js_format(result), None

    def is_callable(self, expr: str) -> bool:
        """JavaScriptExecutor::getCallableObject: global[a].b.c must be a function."""
        parts = expr.split(".")
        obj = self.engine.globalObject().property(parts[0])
        for part in parts[1:]:
            obj = obj.property(part)
        return obj.isCallable()


# ---- attribute helpers (Qt semantics where noted) ---------------------------------------------------
def _qt_to_int(text: str) -> int:
    """QString::toInt(): 0 on failure (also on a decimal point or an out-of-range value)."""
    t = text.strip()
    if not _INT_RE.fullmatch(t):
        return 0
    n = int(t)
    return n if _INT32[0] <= n <= _INT32[1] else 0


def _qt_str(value: Any) -> str:  # QJsonValue::toString()
    return value if isinstance(value, str) else ""


def _object_json(value: Any) -> str:  # QJsonDocument(val.toObject()).toJson(Compact)
    return compact_json(value if isinstance(value, dict) else {})


def parse_list(raw: str) -> list[str] | None:
    """HtmlParser.cpp parsList(QString): None when the text is <= 2 chars or not a JSON array.

    Non-string elements become the JSON of `toObject()`, i.e. "{}" unless they are objects."""
    if len(raw) <= 2:
        return None
    ok, doc = _json_load(raw)
    if not ok or not isinstance(doc, list):
        return None
    return [v if isinstance(v, str) else _object_json(v) for v in doc]


def parse_map_or_list(raw: str) -> tuple[str, Any]:
    """HtmlParser.cpp parsMapOrList -> (form, payload); form is raw | list | pairs | map.

    "map" is sorted by key because the iCUE container is a QVariantMap."""
    if len(raw) <= 2:
        return "raw", raw
    ok, doc = _json_load(raw)
    if not ok:
        return "raw", raw
    if isinstance(doc, list):
        if not doc:
            return "list", []
        if isinstance(doc[0], str):
            return "list", [v if isinstance(v, str) else _object_json(v) for v in doc]
        if isinstance(doc[0], dict):
            return "pairs", [(_qt_str(o.get("key")), _qt_str(o.get("value"))) for o in doc if isinstance(o, dict)]
        return "list", []
    if isinstance(doc, dict):
        return "map", {k: (v if isinstance(v, str) else _object_json(v)) for k, v in sorted(doc.items())}
    return "raw", raw


def _options(form: str, payload: Any, accepted: tuple[str, ...]) -> list[dict[str, str]]:
    """Option list as the editor models expose it (ComboBox/TabButtons::values()): key + label."""
    if form not in accepted:
        return []
    if form == "list":
        return [{"key": v, "value": v} for v in payload]
    if form == "pairs":
        return [{"key": k, "value": v} for k, v in payload]
    return [{"key": k, "value": v} for k, v in payload.items()]


def _color_name(text: str) -> str | None:
    color = QColor(text)
    return color.name() if color.isValid() else None  # HYPOTHESIS: QColor reaches widgets as '#rrggbb'


# ---- per-type builders ------------------------------------------------------------------------------
def _text_property(env: _JsEnv, p: SettingParameter, attr: str) -> str:
    """evalJsExpressionProperty(attr) for a parameter; an error in data-default invalidates the default."""
    text, error = env.property(p.attrs.get(attr, ""))
    if error is not None:
        p.meta.setdefault("evalErrors", {})[attr] = error
        if attr == "data-default":
            p.default_valid = False
        p.notes.append(f"{p.name} ({p.data_type}): {attr} failed to evaluate: {error}")
        return ""
    return text or ""


def _build_parameter(spec: PropertySpec, env: _JsEnv) -> tuple[SettingParameter | None, str | None]:
    t, a = spec.type, spec.attrs
    if t in NO_PARAMETER_TYPES:
        return None, "validated by iCUE, but parseMetaParameter creates no parameter for it"
    if t not in VALUE_TYPES and t not in STATUS_TYPES:
        return None, f"control type {t!r} is not handled by parseMetaParameter"
    p = SettingParameter(spec.name, t, env.expression(a.get("data-label", "")), t in VALUE_TYPES, attrs=dict(a))
    if t == "slider":
        p.default = float(_qt_to_int(a.get("data-default", "")))  # stored as double (toInt, then double)
        p.meta.update(min=float(_qt_to_int(a.get("data-min", ""))), max=float(_qt_to_int(a.get("data-max", ""))),
                      step=float(_qt_to_int(a.get("data-step", ""))), unitLabel=env.expression(a.get("data-unit-label", "")))
    elif t == "switch":
        p.default = a.get("data-default", "") == "true"  # exact, case-sensitive
    elif t == "textfield":
        p.default = _text_property(env, p, "data-default")
    elif t in ("combobox", "tab-buttons", "slider-enum"):
        p.default = _text_property(env, p, "data-default")
        form, payload = parse_map_or_list(_text_property(env, p, "data-values"))
        accepted = {"combobox": ("list", "map", "pairs"), "tab-buttons": ("list", "pairs"), "slider-enum": ("list",)}[t]
        p.meta.update(options=_options(form, payload, accepted), optionsForm=form if form in accepted else None)
    elif t == "color":
        text = _text_property(env, p, "data-default")
        name = _color_name(text)
        p.default = name
        if name is None and p.default_valid:
            p.default_valid = False
            p.notes.append(f"{p.name} (color): {text!r} is not a valid color; iCUE stores an invalid QColor "
                           "(its runtime value is UNVERIFIED), no default emitted")
    elif t == "colors":
        form, payload = parse_map_or_list(_text_property(env, p, "data-values"))
        p.default = [_color_name(c) for c in payload] if form == "list" else []
        p.meta["optionsForm"] = form if form == "list" else None
    elif t == "media-selector":
        p.default = {"pathToAsset": "", "scale": 1.0, "positionX": 0.0, "positionY": 0.0, "angle": 0}
        p.meta["filters"] = parse_list(_text_property(env, p, "data-filters")) or []
    elif t == "time-format":
        ok, doc = _json_load(_text_property(env, p, "data-default"))
        doc = doc if ok and isinstance(doc, dict) else {}
        p.default = {"time12hourFormat": doc.get("time12hourFormat") is True, "amPmEnable": doc.get("amPmEnable") is True}
        p.meta["amPmControlShow"] = a.get("data-one-more-name", "true").lower() == "true"
    elif t == "search-combobox":
        p.default = ""  # parameter->value default; a callable default is awaited asynchronously by iCUE
        p.meta.update(placeholder=_text_property(env, p, "data-placeholder"), searchFunc=a.get("data-values", ""),
                      displayValueKey=spec.name + "DisplayValue")
        if env.is_callable(a.get("data-default", "")):
            p.unresolved = True
            p.notes.append(f"{p.name} (search-combobox): data-default is a provider call, resolved asynchronously by iCUE; "
                           "'' is used here")
    elif t == "sensors-combobox":
        p.default = _text_property(env, p, "data-default")
    elif t == "sensors-factory":
        p.default = []
        p.meta["defaultSensorId"] = _text_property(env, p, "data-default")
    elif t == "accounts-list":
        p.default = []
    elif t == "stream-deck-virtual-device":
        p.default = ""
    elif t == "app-status":
        p.meta.update(application=a.get("application", ""), storesNullKey=True)
        p.notes.append(f"{p.name} (app-status): display-only; iCUE's toSetting() returns a null value "
                       "(a key with a null QVariant is created, no value is generated here)")
    return p, None


def _parse_groups_json(raw: str, env: _JsEnv) -> list[tuple[SettingsGroup, list[str]]]:
    """parseSettingsGroupsFromJson: a non-empty JSON array of {title, properties, info, description}."""
    if not raw:
        return []
    ok, doc = _json_load(raw)
    if not ok or not isinstance(doc, list):
        return []
    out = []
    for item in doc:
        obj = item if isinstance(item, dict) else {}
        props = obj.get("properties")
        names = [_qt_str(n) for n in props] if isinstance(props, list) else []
        out.append((SettingsGroup(env.expression(_qt_str(obj.get("title"))),
                                  env.expression(_qt_str(obj.get("info"))),
                                  env.expression(_qt_str(obj.get("description")))), names))
    return out


def parse_settings(html_text: str, translate: Callable[[str], str] = lambda s: s,
                   modules: list[Path] | None = None, plugins: dict | None = None) -> ParsedSettings:
    """index.html -> groups of parameters, like buildSettings() (HtmlParser.cpp:779-831). `plugins`: {Name: QObject factory} exposed
    as plugins.<Name> while data-default / data-values are evaluated (JavaScriptExecutor creates the required plugins there)."""
    head = _read_head(html_text)
    env = _JsEnv(translate, modules, plugins)
    skipped: list[dict[str, str]] = []
    built: dict[int, SettingParameter] = {}
    for i, spec in enumerate(head.specs):
        param, reason = _build_parameter(spec, env)
        if param is None:
            skipped.append({"name": spec.name, "type": spec.type, "reason": reason or ""})
        else:
            built[i] = param
    remaining = list(range(len(head.specs)))  # document order; a property joins the first group naming it
    groups: list[SettingsGroup] = []
    for group, names in _parse_groups_json(head.groups_json, env):
        for name in names:
            idx = next((i for i in remaining if head.specs[i].name == name), None)
            if idx is not None:
                remaining.remove(idx)
                if idx in built:
                    built[idx].group = group.title
                    group.parameters.append(built[idx])
        groups.append(group)
    title = env.expression(head.title)
    default_group = next((g for g in groups if g.title == title), None)
    leftovers = [built[i] for i in remaining if i in built]
    if default_group is None and leftovers:
        default_group = SettingsGroup(title)
        groups.append(default_group)
    if default_group is not None:
        for param in leftovers:
            param.group = default_group.title
            default_group.parameters.append(param)
    notes = list(env.notes)
    notes += [f"{s['name']}: control type {s['type']!r} is not simulated ({s['reason']}), left undefined"
              for s in skipped]
    for param in (p for g in groups for p in g.parameters):
        notes += param.notes
    return ParsedSettings(title, groups, head.specs, skipped, notes)


def compute_defaults(
    specs: list[PropertySpec], translate: Callable[[str], str], modules: list[Path] | None = None
) -> tuple[dict[str, object], list[str]]:
    """Return ({property: default}, [notes]) for raw specs (no groups). See parse_settings()."""
    env = _JsEnv(translate, modules)
    defaults: dict[str, object] = {}
    notes = list(env.notes)
    for spec in specs:
        param, reason = _build_parameter(spec, env)
        if param is None:
            notes.append(f"{spec.name}: control type {spec.type!r} is not simulated ({reason}), left undefined")
            continue
        notes += param.notes
        if param.has_value and param.default_valid:
            defaults[param.name] = param.default
    return defaults, notes