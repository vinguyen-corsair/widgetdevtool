"""Editors generated from `SettingParameter` (D3): one PySide6 widget per declared control.

Behaviour source (iCUE v5.55, NOT verified against release 5.52.93):
- EditorSettingsModels.cpp: a model reads `value(property).toXxx()` with NO default fallback, writes through
  `setValue(property, value)` and never clamps or validates. A key that is missing from the instance shows the
  empty value of the type (0 / false / "" / -1) and stays missing until the user edits it.
- ComboBoxEditorSetting.qml: `currentIndex = values.findIndex(key === value)` (-1 when not found, the stored value
  is NOT corrected), `onActivated` commits the key.
- SliderEditorSetting.qml: Slider from/to/stepSize, `onMoved` commits a number.
- SliderEnumEditorSetting.qml: ListSlider over the option list, `onMoved` commits `currentValue`.
- TextFieldEditorSetting.qml: commit on Enter or when the focus is lost; Esc reverts to the stored value.
- SwitchEditorSetting.qml: `onToggled` commits a bool. TimeFormatEditorSettingModel: commits the whole map.
- TabButtonsEditorSetting / ColorEditorSetting QML files were NOT read: the look is WidgetDevTool's own.

Not simulated, shown as "Unsupported": see UNSUPPORTED_REASONS. Differences from iCUE are listed in AI_HANDOFF.md.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider,
    QVBoxLayout, QWidget)

from . import theme
from .theme import color_swatch_style
from .properties import SettingParameter, _qt_to_int
from .sensor_source import active_source, sources
from .sensors_mock import DEFAULT_SENSOR_COLOR, PARSE_STUBS
from .ui_kit import FlowLayout, narrow_combo

EDITABLE_TYPES = ("switch", "slider", "combobox", "tab-buttons", "slider-enum", "textfield", "color", "time-format", "sensors-factory")
OPTION_TYPES = ("combobox", "tab-buttons", "slider-enum")
# widgetshelpers/src/WidgetPersonalizationSettings.cpp wpSettings: iCUE replaces these from the Screen/Device personalization
# unless "Custom Style" is on (personalization.py, phase D4). The editor shows the STORED value; the runtime note says what
# the widget really receives.
from .personalization import WP_KEYS as PERSONALIZATION_KEYS
UNSUPPORTED_REASONS = {
    "media-selector": "needs the iCUE media assets controller (library, crop, position) - not implemented",
    "colors": "colors list / palette editor - not implemented",
    "sensors-combobox": "needs the iCUE sensors provider - not implemented",
    "search-combobox": "needs an asynchronous provider call (data-values) - not implemented",
    "accounts-list": "accounts editor - not implemented",
    "app-status": "status of an external application (Stream Deck) - not simulated",
    "stream-deck-virtual-device": "Stream Deck integration - not implemented",
}


def unsupported_reason(param: SettingParameter) -> str | None:
    """Why the control cannot be edited here, or None when it can."""
    if param.data_type not in EDITABLE_TYPES:
        return UNSUPPORTED_REASONS.get(param.data_type, f"data-type {param.data_type!r} - not implemented")
    error = param.meta.get("evalErrors", {}).get("data-values")
    if param.data_type in OPTION_TYPES and error:
        return f"the options could not be evaluated (plugin / module / provider missing?): {error}"
    return None


# ---- Qt-like conversions of a stored QVariant (what the model getters do) -------------------------
def _qt_bool(value: Any) -> bool:
    if isinstance(value, str):
        return not (value == "" or value.lower() in ("0", "false"))
    if isinstance(value, (list, dict)) or value is None:
        return False
    return bool(value)


def _qt_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(round(value))
    return _qt_to_int(value) if isinstance(value, str) else 0


def _qt_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value) if isinstance(value, int) else ""


def format_value(value: Any) -> str:
    if value is None:
        return "(no value)"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return _qt_string(value) if not isinstance(value, str) else value


class _CommitLineEdit(QLineEdit):
    """Esc reverts to the committed text and leaves the field (TextFieldEditorSetting.qml)."""

    def __init__(self) -> None:
        super().__init__()
        self.revert_text = ""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt naming)
        if event.key() == Qt.Key.Key_Escape:
            self.setText(self.revert_text)
            self.clearFocus()
            event.accept()
            return
        super().keyPressEvent(event)


class SettingEditor(QWidget):
    """Label + control + status line for one parameter. `committed` is emitted only by user actions."""

    committed = Signal(str, object)  # key, value

    def __init__(self, param: SettingParameter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.param = param
        self.reason = unsupported_reason(param)
        self.supported = self.reason is None
        self.control: QWidget | None = None
        self.buttons: dict[str, QPushButton] = {}
        self.checks: dict[str, QCheckBox] = {}
        self._updating = False
        self._present = False
        self._value: Any = None
        self._runtime_note: str | None = None
        self._apply: Callable[[bool, Any], None] = lambda present, value: None
        self.setObjectName(f"editor:{param.name}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # field -> field spacing is the card's (theme.FIELD_GAP)
        layout.setSpacing(theme.LABEL_GAP)
        self.label = QLabel(param.label or param.name)
        self.label.setWordWrap(True)
        self.label.setToolTip(f"{param.name}  ({param.data_type})")
        layout.addWidget(self.label)
        body = QHBoxLayout()
        layout.addLayout(body)
        if self.supported:
            getattr(self, "_build_" + param.data_type.replace("-", "_"))(body)
        else:
            self._build_unsupported(body)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("role", "hint")
        layout.addWidget(self.status)

    # ---- public --------------------------------------------------------------------------------
    def set_value(self, present: bool, value: Any) -> None:
        """Show the stored value (programmatic: never emits `committed`)."""
        self._present, self._value = present, value
        self._updating = True
        try:
            self._apply(present, value)
        finally:
            self._updating = False
        self._refresh_status()

    def set_runtime_note(self, note: str | None) -> None:
        """What the widget receives when it differs from the stored value (Widget Personalization, D4)."""
        self._runtime_note = note
        self._refresh_status()

    # ---- internals -----------------------------------------------------------------------------
    def _commit(self, value: Any) -> None:
        if not self._updating:
            self.committed.emit(self.param.name, value)

    def _options(self) -> list[dict[str, str]]:
        return list(self.param.meta.get("options", []))

    def _refresh_status(self) -> None:
        lines: list[str] = []
        if not self.supported:
            lines.append("Unsupported: " + (self.reason or ""))
        elif not self._present:
            lines.append("Key not stored in this instance: the widget gets no value for it until it is edited "
                         "(the control shows the empty value of its type).")
        if self.param.name in PERSONALIZATION_KEYS:
            lines.append("Personalization key: the stored value reaches the widget only while Custom Style is on; otherwise "
                         "the Device/Screen personalization replaces it (the stored value is kept).")
            if self._runtime_note:
                lines.append(self._runtime_note)
        if self.supported and self.param.data_type == "sensors-factory":
            if active_source().is_live:
                lines.append("LIVE sensors of this PC (Olympus Hub, Sensors tab). Add Sensor appends {sensorId: data-default, "
                             "color: #f08e33} as iCUE's SensorsFactoryParametersModel does; data-default is evaluated again against "
                             "the live sensors when the button is pressed.")
            else:
                lines.append("MOCK sensors (WidgetDevTool catalog, not this PC's hardware). Values: Sensors tab. Add Sensor appends "
                             "{sensorId: data-default, color: #f08e33} as iCUE's SensorsFactoryParametersModel does.")
        lines += self._value_warnings()
        lines += self.param.notes
        self.status.setText("\n".join(lines))
        self.status.setVisible(bool(lines))
        if not self.supported:
            theme.set_tone(self.status, "error")

    def _value_warnings(self) -> list[str]:
        if not (self.supported and self._present):
            return []
        t, v = self.param.data_type, self._value
        if t == "slider":
            lo, hi = self.param.meta.get("min", 0.0), self.param.meta.get("max", 0.0)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and not lo <= v <= hi:
                return [f"Stored value {format_value(v)} is outside [{format_value(lo)}, {format_value(hi)}]: kept as is."]
        elif t in OPTION_TYPES and _qt_string(v) not in {o["key"] for o in self._options()}:
            return [f"Stored value {format_value(v)!r} is not among the options: kept as is (iCUE shows no selection)."]
        return []

    # ---- builders (one per data-type) ----------------------------------------------------------
    def _build_unsupported(self, body: QHBoxLayout) -> None:
        box = QLabel()
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.setWordWrap(True)
        box.setEnabled(False)
        body.addWidget(box)
        self.control = box
        self._apply = lambda present, value: box.setText("current value: " + (format_value(value) if present else "(not stored)"))

    def _build_switch(self, body: QHBoxLayout) -> None:
        box = QCheckBox("On")
        body.addWidget(box)
        self.control = box
        box.toggled.connect(lambda checked: self._commit(bool(checked)))
        self._apply = lambda present, value: box.setChecked(_qt_bool(value) if present else False)

    def _build_slider(self, body: QHBoxLayout) -> None:
        meta = self.param.meta
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(int(meta.get("min", 0)), int(meta.get("max", 0)))
        step = max(1, int(meta.get("step", 1)))
        slider.setSingleStep(step)  # keyboard step; dragging gives integers (iCUE snapping is UNVERIFIED)
        slider.setPageStep(step)
        shown = QLabel()
        shown.setMinimumWidth(44)
        shown.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider.setMinimumWidth(60)
        body.addWidget(slider, 1)
        body.addWidget(shown)
        self.control = slider
        unit = meta.get("unitLabel") or ""
        slider.valueChanged.connect(lambda v: self._commit(float(v)))

        def apply(present: bool, value: Any) -> None:
            slider.setValue(_qt_int(value) if present else 0)  # the slider clamps only what it DRAWS
            shown.setText((format_value(value) if present else "0") + unit)
        self._apply = apply

    def _build_combobox(self, body: QHBoxLayout) -> None:
        combo = narrow_combo(QComboBox(), 6)  # long option texts are elided in the button (full text in the list and as tooltip)
        for option in self._options():
            combo.addItem(option["value"], option["key"])
            combo.setItemData(combo.count() - 1, option["value"], Qt.ItemDataRole.ToolTipRole)
        body.addWidget(combo, 1)
        self.control = combo
        combo.activated.connect(lambda index: self._commit(combo.itemData(index)))

        def apply(present: bool, value: Any) -> None:
            key = _qt_string(value) if present else ""
            index = combo.findData(key) if present else -1
            combo.setCurrentIndex(index)
            combo.setPlaceholderText(f"(not in options) {key}" if present and index < 0 and key else "")
        self._apply = apply

    def _build_tab_buttons(self, body: QHBoxLayout) -> None:
        group = QButtonGroup(self)
        holder = QWidget()
        flow = FlowLayout(holder, h_spacing=6, v_spacing=6)  # wraps to the next row instead of widening the panel
        for option in self._options():
            button = QPushButton(option["value"])
            button.setCheckable(True)
            button.setToolTip(option["value"])
            group.addButton(button)
            flow.addWidget(button)
            self.buttons[option["key"]] = button
            button.clicked.connect(lambda _checked=False, key=option["key"]: self._commit(key))
        body.addWidget(holder, 1)
        self.control = group

        def apply(present: bool, value: Any) -> None:
            key = _qt_string(value) if present else ""
            group.setExclusive(False)
            for k, button in self.buttons.items():
                button.setChecked(present and k == key)
            group.setExclusive(True)
        self._apply = apply

    def _build_slider_enum(self, body: QHBoxLayout) -> None:
        keys = [o["key"] for o in self._options()]
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, max(0, len(keys) - 1))
        slider.setEnabled(bool(keys))
        shown = QLabel()
        shown.setMinimumWidth(44)
        slider.setMinimumWidth(60)
        body.addWidget(slider, 1)
        body.addWidget(shown)
        self.control = slider
        slider.valueChanged.connect(lambda i: self._commit(keys[i]) if 0 <= i < len(keys) else None)

        def apply(present: bool, value: Any) -> None:
            key = _qt_string(value) if present else ""
            slider.setValue(keys.index(key) if key in keys else 0)
            shown.setText(key if present else "")
        self._apply = apply

    def _build_textfield(self, body: QHBoxLayout) -> None:
        edit = _CommitLineEdit()
        body.addWidget(edit, 1)
        self.control = edit
        edit.editingFinished.connect(lambda: self._commit_text(edit))

        def apply(present: bool, value: Any) -> None:
            edit.revert_text = _qt_string(value) if present else ""
            edit.setText(edit.revert_text)
        self._apply = apply

    def _commit_text(self, edit: _CommitLineEdit) -> None:
        """Enter or focus lost. The instance ignores a text equal to the stored one."""
        if edit.text() != edit.revert_text:
            edit.revert_text = edit.text()
            self._commit(edit.text())

    def _build_color(self, body: QHBoxLayout) -> None:
        button = QPushButton()
        body.addWidget(button, 1)
        self.control = button
        self._color_name = ""
        button.clicked.connect(self._choose_color)

        def apply(present: bool, value: Any) -> None:
            color = QColor(value) if present and isinstance(value, str) else QColor()
            self._color_name = color.name() if color.isValid() else ""
            button.setText(self._color_name or ("(invalid color)" if present else "(not set)"))
            button.setToolTip(f"Choose a color for {self.param.label or self.param.name}")
            button.setStyleSheet(color_swatch_style(self._color_name, color.lightness() > 127) if color.isValid() else "")
        self._apply = apply

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._color_name), self, self.param.label or self.param.name)
        if color.isValid():
            self.pick_color(color)

    def pick_color(self, color: QColor) -> None:
        """A color chosen by the user. Stored as '#rrggbb' (how it reaches widgets in iCUE: HYPOTHESIS)."""
        self._commit(color.name())

    def _build_time_format(self, body: QHBoxLayout) -> None:
        # The labels of TimeFormatEditorSetting.qml were not read: the keys are shown instead.
        column = QVBoxLayout()  # stacked: two key names side by side are wider than the panel
        column.setSpacing(4)
        for key in ("time12hourFormat", "amPmEnable"):
            box = QCheckBox(key)
            self.checks[key] = box
            box.toggled.connect(lambda _checked, self=self: self._commit_time_format())
            if key == "amPmEnable":
                box.setVisible(bool(self.param.meta.get("amPmControlShow", True)))
            column.addWidget(box)
        body.addLayout(column, 1)
        self.control = self.checks["time12hourFormat"]

        def apply(present: bool, value: Any) -> None:
            stored = value if present and isinstance(value, dict) else {}
            for key, box in self.checks.items():
                box.setChecked(_qt_bool(stored.get(key)))
        self._apply = apply

    def _build_sensors_factory(self, body: QHBoxLayout) -> None:
        """iCUE SensorsFactoryEditorSetting: a list of {sensorId, color}; Add Sensor appends the data-default id + the default colour,
        each row picks a sensor and a colour or is removed. The whole list is committed (updateSensors -> setValue(property, values))."""
        box = QWidget()
        rows = QVBoxLayout(box)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(theme.SPACING)
        body.addWidget(box, 1)
        self.control = box
        self.sensor_rows: list[tuple[QComboBox, QPushButton, QPushButton]] = []
        self._sensor_items: list[dict] = []
        self.add_sensor_button = QPushButton("Add Sensor")
        self.add_sensor_button.setToolTip("Append a sensor (the widget's default sensor and color #f08e33), as iCUE's Add Sensor")
        self.add_sensor_button.clicked.connect(self._add_sensor)

        def apply(present: bool, value: Any) -> None:
            self._sensor_items = [dict(v) for v in value if isinstance(v, dict)] if present and isinstance(value, list) else []
            self._rebuild_sensor_rows(rows)
        self._apply = apply
        self._sensor_layout = rows
        rows.addWidget(self.add_sensor_button)
        # The sensor list follows the active source (mock / live) and the live discovery, debounced: no rebuild per value change.
        self._sensor_source = None
        self._sensor_rebuild = QTimer(self)
        self._sensor_rebuild.setSingleShot(True)
        self._sensor_rebuild.setInterval(300)
        self._sensor_rebuild.timeout.connect(self._on_sensor_list_changed)
        sources().activeChanged.connect(self._on_sensor_source_switched)
        self._watch_sensor_source(active_source())

    def _rebuild_sensor_rows(self, rows: QVBoxLayout) -> None:
        while rows.count() > 1:  # the Add Sensor button stays last
            item = rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.sensor_rows = []
        source = active_source()
        sensors = [s for s in (source.get(sensor_id) for sensor_id in source.ids()) if s is not None]
        for index, item in enumerate(self._sensor_items):
            line = QWidget()
            row = QHBoxLayout(line)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            combo = narrow_combo(QComboBox(), 6)
            for sensor in sensors:
                combo.addItem(f"{sensor.name} - {sensor.device}", sensor.id)
            sensor_id = str(item.get("sensorId", ""))
            found = combo.findData(sensor_id.strip("{}"))
            combo.setCurrentIndex(found)
            if found < 0:
                combo.setPlaceholderText(f"(not in the {source.label}) {sensor_id}" if sensor_id else "(no sensor)")
            combo.activated.connect(lambda _i, c=combo, n=index: self._edit_sensor(n, "sensorId", c.currentData()))
            color = QColor(str(item.get("color", "")))
            swatch = QPushButton(color.name() if color.isValid() else "(color)")
            swatch.setStyleSheet(color_swatch_style(color.name(), color.lightness() > 127) if color.isValid() else "")
            swatch.clicked.connect(lambda _c=False, n=index: self._choose_sensor_color(n))
            swatch.setToolTip("Choose the color of this sensor")
            remove = QPushButton("Remove")
            remove.setToolTip("Remove this sensor from the list")
            swatch.setFixedWidth(theme.SWATCH_WIDTH)
            remove.clicked.connect(lambda _c=False, n=index: self._remove_sensor(n))
            row.addWidget(combo, 1)
            row.addWidget(swatch)
            row.addWidget(remove)
            rows.insertWidget(rows.count() - 1, line)
            self.sensor_rows.append((combo, swatch, remove))

    def _watch_sensor_source(self, source) -> None:
        for signal in ("added", "removed", "stateChanged"):
            if self._sensor_source is not None:
                try:
                    getattr(self._sensor_source, signal).disconnect(self._sensor_rebuild.start)
                except (RuntimeError, TypeError):
                    pass
            getattr(source, signal).connect(self._sensor_rebuild.start)
        self._sensor_source = source

    def _on_sensor_source_switched(self, _old, new) -> None:
        self._watch_sensor_source(new)
        self._sensor_rebuild.start()

    def _on_sensor_list_changed(self) -> None:
        if any(combo.view().isVisible() for combo, _swatch, _remove in self.sensor_rows):
            self._sensor_rebuild.start()  # never rebuild under an open drop-down
            return
        self._rebuild_sensor_rows(self._sensor_layout)
        self._refresh_status()

    def _default_sensor_id(self) -> str:
        """data-default evaluated now (the parse-time value was computed with the source active at that time)."""
        stored = str(self.param.meta.get("defaultSensorId", ""))
        expression = self.param.attrs.get("data-default", "")
        if "Sensorsdataprovider" not in expression:
            return stored
        from .properties import _JsEnv
        text, error = _JsEnv(lambda s: s, None, PARSE_STUBS).property(expression)
        return stored if error is not None else str(text or "")

    def _commit_sensors(self) -> None:
        self._commit([dict(item) for item in self._sensor_items])

    def _add_sensor(self) -> None:
        self._sensor_items.append({"sensorId": self._default_sensor_id(), "color": DEFAULT_SENSOR_COLOR})
        self._commit_sensors()

    def _edit_sensor(self, index: int, key: str, value: str) -> None:
        if 0 <= index < len(self._sensor_items) and self._sensor_items[index].get(key) != value:
            self._sensor_items[index][key] = value
            self._commit_sensors()

    def _remove_sensor(self, index: int) -> None:
        if 0 <= index < len(self._sensor_items):
            del self._sensor_items[index]
            self._commit_sensors()

    def _choose_sensor_color(self, index: int) -> None:
        color = QColorDialog.getColor(QColor(str(self._sensor_items[index].get("color", ""))), self, "Sensor color")
        if color.isValid():
            self._edit_sensor(index, "color", color.name())

    def _commit_time_format(self) -> None:
        self._commit({key: box.isChecked() for key, box in self.checks.items()})