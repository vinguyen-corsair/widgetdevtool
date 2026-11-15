r"""Widget Personalization (D4): which values a widget really receives, as opposed to what its instance stores.

Source (iCUE v5.55, NOT verified against release 5.52.93 / 5.46.67):
- widgetshelpers/src/WidgetPersonalizationSettings.cpp: the six keys (wpSettings) and the two markers.
- mod_win/dashlcd/dashlcdui/models/WidgetBuilderPersonalizationSettings.cpp settings(): the map a widget gets.
    1. Present (default TRUE when missing) is false           -> the instance map as it is.
    2. CustomStyleEnabled (default false, toBool) is true     -> the instance map as it is.
    3. otherwise, ONLY for keys the instance map already contains:
       textColor <- active.textColor, accentColor <- active.accentColor, accentColor2 <- active.accentColor2,
       backgroundColor <- active.widgetBackgroundColor, transparency <- active.widgetTransparency (int),
       backgroundMedia <- the stored map with pathToAsset = "" (other fields kept).
  The decorator never writes: the stored (raw) map is untouched. setValue() on it is an assert.
- WidgetBuilderPanelSettingsModel.cpp: when the settings panel opens, Present := "the map has any of the six keys".
  The Custom Style switch exists only then; with Custom Style off the six editors are hidden (EditorSettingsModels.cpp).
- ActivePersonalizationSettingsModel.cpp: active = the Screen's PersonalizationSettings when DeviceSettings.themeByScreenEnabled
  (default false) else the Device's. PersonalizationSettings.h defaults: text white, accent / accent2 0xF08E33, widget
  background black, widget transparency 80.
- Unknown: how a QColor reaches the widget (we use "#rrggbb", the same hypothesis as for stored colors), the range of the
  transparency control, and whether iCUE calls onDataUpdated again when a personalization change leaves the map equal.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .instance_store import CUSTOM_STYLE_ENABLED_KEY, CUSTOM_STYLE_PRESENT_KEY, WidgetInstance

WP_KEYS = ("textColor", "accentColor", "accentColor2", "backgroundColor", "transparency", "backgroundMedia")
DEFAULT_TEXT_COLOR = "#ffffff"
DEFAULT_ACCENT_COLOR = "#f08e33"  # QColor(0x00F08E33): the QRgb constructor makes it opaque
DEFAULT_WIDGET_BACKGROUND = "#000000"
DEFAULT_TRANSPARENCY = 80
FREE_KEY = "free"  # the personalization scope used when no device is selected (WidgetDevTool only)


@dataclass
class PersonalizationValues:
    """PersonalizationSettings.h (the five values that reach HTML widgets)."""
    text_color: str = DEFAULT_TEXT_COLOR
    accent_color: str = DEFAULT_ACCENT_COLOR
    accent_color2: str = DEFAULT_ACCENT_COLOR
    widget_background_color: str = DEFAULT_WIDGET_BACKGROUND
    widget_transparency: int = DEFAULT_TRANSPARENCY

    FIELDS = ("text_color", "accent_color", "accent_color2", "widget_background_color", "widget_transparency")


# widget key -> PersonalizationValues field (WidgetBuilderPersonalizationSettings::settings())
KEY_TO_FIELD = {"textColor": "text_color", "accentColor": "accent_color", "accentColor2": "accent_color2",
                "backgroundColor": "widget_background_color", "transparency": "widget_transparency"}


@dataclass
class PersonalizationModel:
    """Per device: the Device settings, the Screen settings and themeByScreenEnabled. Window state: never part of an instance."""
    _device: dict[str, PersonalizationValues] = field(default_factory=dict)
    _screen: dict[str, PersonalizationValues] = field(default_factory=dict)
    _by_screen: dict[str, bool] = field(default_factory=dict)

    def values(self, scope: str, key: str) -> PersonalizationValues:
        if scope not in ("device", "screen"):
            raise ValueError(f"unknown scope {scope!r}")
        return (self._device if scope == "device" else self._screen).setdefault(key, PersonalizationValues())

    def theme_by_screen(self, key: str) -> bool:
        return self._by_screen.get(key, False)

    def set_theme_by_screen(self, key: str, value: bool) -> None:
        self._by_screen[key] = bool(value)

    def active(self, key: str) -> PersonalizationValues:
        """ActivePersonalizationSettingsModel::updateActiveSettings()."""
        return self.values("screen" if self.theme_by_screen(key) else "device", key)

    def set_value(self, scope: str, key: str, field_name: str, value: Any) -> None:
        if field_name not in PersonalizationValues.FIELDS:
            raise ValueError(f"unknown personalization value {field_name!r}")
        setattr(self.values(scope, key), field_name, int(value) if field_name == "widget_transparency" else str(value).lower())


def to_bool(value: Any) -> bool:
    """QVariant::toBool() for the types a settings map holds."""
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, (dict, list)):
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in ("", "0", "false")


def has_personalization(raw: dict) -> bool:
    """hasWidgetPersonalizationSettings(): does the stored map contain any of the six keys."""
    return any(key in raw for key in WP_KEYS)


def custom_style_enabled(raw: dict) -> bool:
    return to_bool(raw.get(CUSTOM_STYLE_ENABLED_KEY))


def personalization_applies(raw: dict) -> bool:
    """Steps 1 and 2 of settings(): is the Device/Screen personalization applied to this map."""
    return to_bool(raw.get(CUSTOM_STYLE_PRESENT_KEY, True)) and not custom_style_enabled(raw)


def effective_settings(raw: dict, active: PersonalizationValues | None) -> dict:
    """WidgetBuilderPersonalizationSettings::settings(): a NEW map; `raw` is never modified."""
    result = copy.deepcopy(raw)
    if active is None or not personalization_applies(raw):
        return result
    for key, field_name in KEY_TO_FIELD.items():
        if key in result:  # only keys the instance already has: nothing is added
            result[key] = getattr(active, field_name)
    if "backgroundMedia" in result:
        media = dict(result["backgroundMedia"]) if isinstance(result["backgroundMedia"], dict) else {}
        media["pathToAsset"] = ""
        result["backgroundMedia"] = media
    return result


def sync_present(instance: WidgetInstance) -> bool:
    """What opening the settings panel does: Present := hasWidgetPersonalizationSettings. iCUE also writes false for a widget
    without the six keys; WidgetDevTool writes nothing then, so no property the widget did not declare appears (a
    documented difference: the effective map is the same). Returns True when the instance changed."""
    if has_personalization(instance.data):
        return instance.set_value(CUSTOM_STYLE_PRESENT_KEY, True, mark_edited=False)
    return False


def override_report(raw: dict, effective: dict) -> dict[str, dict[str, Any]]:
    """What the UI shows against what the widget receives, per personalization key present in the instance."""
    report = {}
    for key in WP_KEYS:
        if key in raw:
            report[key] = {"stored": raw[key], "runtime": effective.get(key), "overridden": raw[key] != effective.get(key)}
    return report