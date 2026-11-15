"""Settings panel (D3): groups and editors generated from `ParsedSettings` + the shown `WidgetInstance`.

Nothing here knows a widget: the structure comes from the HTML metadata only (properties.parse_settings).
The panel never writes the instance itself; it emits `settingCommitted(key, value)` for user actions and the
owner (MainWindow) applies them through the runtime bridge. Instance changes made elsewhere refresh the editors.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QScrollArea, QVBoxLayout, QWidget

from .instance_store import CUSTOM_STYLE_ENABLED_KEY, CUSTOM_STYLE_PRESENT_KEY, MARKER_KEYS, WidgetInstance
from .personalization import WP_KEYS, has_personalization, to_bool
from .properties import ParsedSettings
from . import theme
from .settings_editors import SettingEditor


class SettingsPanel(QWidget):
    settingCommitted = Signal(str, object)  # key, value (user actions only)
    customStyleCommitted = Signal(bool)  # the Custom Style switch (user actions only)

    CUSTOM_STYLE_OFF_TEXT = ("Widget Personalization is disabled, Screens Personalization settings will be applied to the "
                             "widget layout.")  # the text of iCUE's panel (WidgetBuilderPersonalizationPanel.qml)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.editors: dict[str, SettingEditor] = {}
        self.group_boxes: list[QGroupBox] = []
        self.settings: ParsedSettings | None = None
        self._instance: WidgetInstance | None = None
        self.personalization_group: QGroupBox | None = None  # the "Widget Personalization" panel of iCUE (the last group)
        self.custom_style_check: QCheckBox | None = None
        self.custom_style_info: QLabel | None = None
        self._updating = False

        self.header = QLabel()
        self.header.setWordWrap(True)
        self.header.setProperty("role", "hint")
        self.header.setContentsMargins(theme.PANEL_MARGIN, theme.PANEL_MARGIN, theme.PANEL_MARGIN, 0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # contents wrap / elide to the panel width
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.header)
        layout.addWidget(self._scroll, 1)
        self.clear("No widget loaded.")

    # ---- build ---------------------------------------------------------------------------------
    def clear(self, message: str = "") -> None:
        if self._instance is not None:
            self._instance.unsubscribe(self._on_instance_value)
        self._instance, self.settings = None, None
        self.personalization_group = self.custom_style_check = self.custom_style_info = None
        self.editors, self.group_boxes = {}, []
        old = self._scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(theme.PANEL_MARGIN, theme.PANEL_MARGIN, theme.PANEL_MARGIN, theme.PANEL_MARGIN)
        note = QLabel(message)
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        box.addWidget(note)
        box.addStretch(1)
        self._scroll.setWidget(content)
        self.header.setText("No instance")

    def set_settings(self, settings: ParsedSettings, instance: WidgetInstance) -> None:
        self.clear()
        self.settings, self._instance = settings, instance
        instance.subscribe(self._on_instance_value)
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(theme.PANEL_MARGIN, theme.PANEL_MARGIN, theme.PANEL_MARGIN, theme.PANEL_MARGIN)
        box.setSpacing(theme.CARD_GAP)
        for group in settings.groups:
            group_box = QGroupBox(group.title or "(untitled group)")
            group_box.setObjectName(f"group:{group.title}")
            inner = QVBoxLayout(group_box)
            inner.setContentsMargins(*[theme.CARD_PADDING] * 4)
            inner.setSpacing(theme.FIELD_GAP)
            for text in (group.info, group.description):
                if text:
                    label = QLabel(text)
                    label.setWordWrap(True)
                    label.setProperty("role", "muted")
                    inner.addWidget(label)
            for param in group.parameters:
                editor = SettingEditor(param)
                editor.committed.connect(self.settingCommitted)
                self.editors[param.name] = editor
                inner.addWidget(editor)
            self.group_boxes.append(group_box)
            box.addWidget(group_box)
        if settings.groups and has_personalization(instance.data):
            # WidgetBuilderPanelSettingsModel: with several groups the LAST one is the Widget Personalization panel, with a
            # single group that group is both (the panel then is the personalization panel as a whole).
            self.personalization_group = self.group_boxes[-1]
            self._build_custom_style(self.personalization_group)
        if settings.skipped:
            skipped = QLabel("Not editable (iCUE creates no control): "
                             + ", ".join(f"{s['name']} ({s['type'] or 'no data-type'})" for s in settings.skipped))
            skipped.setWordWrap(True)
            theme.set_tone(skipped, "error", banner=True)
            box.addWidget(skipped)
        if not settings.groups:
            empty = QLabel("This widget declares no settings.")
            empty.setProperty("role", "muted")
            box.addWidget(empty)
        box.addStretch(1)
        self._scroll.setWidget(content)
        self.header.setText(f"Instance {{{instance.id}}}")
        self.header.setToolTip("Settings are stored in this instance only.")
        self.refresh_all()
        self._refresh_personalization()

    # ---- values --------------------------------------------------------------------------------
    def refresh(self, key: str) -> None:
        editor = self.editors.get(key)
        if editor is not None and self._instance is not None:
            editor.set_value(key in self._instance.data, self._instance.data.get(key))

    def refresh_all(self) -> None:
        for key in self.editors:
            self.refresh(key)

    # ---- widget personalization (D4) ------------------------------------------------------------
    def _build_custom_style(self, group_box: QGroupBox) -> None:
        self.custom_style_check = QCheckBox("Custom Style")
        self.custom_style_check.setObjectName("customStyle")
        self.custom_style_check.toggled.connect(self._on_custom_style_toggled)
        self.custom_style_info = QLabel(self.CUSTOM_STYLE_OFF_TEXT)
        self.custom_style_info.setWordWrap(True)
        theme.set_tone(self.custom_style_info, "warning")
        layout = group_box.layout()
        layout.insertWidget(0, self.custom_style_check)
        layout.insertWidget(1, self.custom_style_info)

    def _on_custom_style_toggled(self, checked: bool) -> None:
        if not self._updating:
            self.customStyleCommitted.emit(bool(checked))

    def _refresh_personalization(self) -> None:
        """Custom Style switch from the instance; the six personalization editors are hidden while it is off, as in
        EditorSettingsModels.cpp (only when the Present marker is true; its default there is false)."""
        if self._instance is None:
            return
        data = self._instance.data
        enabled = to_bool(data.get(CUSTOM_STYLE_ENABLED_KEY))
        if self.custom_style_check is not None:
            self._updating = True
            try:
                self.custom_style_check.setChecked(enabled)
            finally:
                self._updating = False
            self.custom_style_info.setVisible(not enabled)
        hide = to_bool(data.get(CUSTOM_STYLE_PRESENT_KEY, False)) and not enabled
        for key in WP_KEYS:
            if key in self.editors:
                self.editors[key].setVisible(not hide)

    def show_effective(self, effective: dict) -> None:
        """Say, per personalization editor, what the widget receives when that is not the stored value."""
        if self._instance is None:
            return
        for key in WP_KEYS:
            editor = self.editors.get(key)
            if editor is None:
                continue
            stored, runtime = self._instance.data.get(key), effective.get(key)
            if key in self._instance.data and stored != runtime:
                editor.set_runtime_note(f"Runtime receives {format_runtime(runtime)} (stored: {format_runtime(stored)}).")
            else:
                editor.set_runtime_note(None)

    def _on_instance_value(self, key: str, value: Any) -> None:
        try:
            if key in MARKER_KEYS:
                self._refresh_personalization()
            self.refresh(key)
        except RuntimeError:  # the Qt object is gone (window closed)
            pass

    # ---- inspection (tests, reports) -----------------------------------------------------------
    def summary(self) -> dict[str, list]:
        return {
            "supported": [(k, e.param.data_type) for k, e in self.editors.items() if e.supported],
            "unsupported": [(k, e.param.data_type, e.reason) for k, e in self.editors.items() if not e.supported],
        }


def format_runtime(value: Any) -> str:
    text = repr(value) if not isinstance(value, str) else value
    return text if len(text) <= 80 else text[:77] + "..."
