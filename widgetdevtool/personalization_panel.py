r"""Device / Screen personalization editor (D4): the host-side values that replace a widget's own style while its Custom Style
is off (personalization.py). Not part of any widget instance: it belongs to the selected device (or to "free size").

Verified (iCUE v5.55): the five values and their defaults, Device vs Screen scope, themeByScreenEnabled (default off).
Our own choices, not iCUE facts: one Screen per device, the 0..100 range of the transparency control, the colour dialog.
Not simulated: the background image of the personalization (imageSource, scaling, position, rotation, blur, media index).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout)

from . import theme
from .personalization import PersonalizationModel, PersonalizationValues
from .theme import color_swatch_style

COLOR_FIELDS = (("text_color", "Text color (textColor)"), ("accent_color", "Accent color (accentColor)"),
                ("accent_color2", "Accent color 2 (accentColor2)"),
                ("widget_background_color", "Widget background (backgroundColor)"))
BACKGROUND_MEDIA_NOTE = ("backgroundMedia: with Custom Style off the widget receives the stored map with pathToAsset = \"\" "
                         "(iCUE). The personalization's own background image is not simulated, and the media-selector "
                         "editor is unsupported, so a widget's background media cannot be chosen here.")


class PersonalizationPanel(QGroupBox):
    valueCommitted = Signal(str, str, object)  # scope ("device" | "screen"), PersonalizationValues field, value
    themeByScreenCommitted = Signal(bool)

    def __init__(self) -> None:
        super().__init__("Device / Screen Personalization")
        self.setObjectName("personalizationPanel")
        self._updating = False
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Device settings", "device")
        self.scope_combo.addItem("Screen settings", "screen")
        self.theme_by_screen = QCheckBox("Theme by screen")  # a check box cannot wrap: short text, full meaning in the tooltip
        self.theme_by_screen.setToolTip("Theme by screen: the Screen settings are the active ones for widgets on this device.")
        self.active_label = QLabel()
        self.active_label.setWordWrap(True)  # its text changes with the device: it must wrap, not widen the window
        self.active_label.setStyleSheet("font-weight:600;")
        self.color_buttons: dict[str, QPushButton] = {}
        self.transparency = QSlider(Qt.Orientation.Horizontal)
        self.transparency.setRange(0, 100)  # our UI range: the iCUE control range was not read
        self.transparency_label = QLabel()
        self.transparency_label.setWordWrap(True)
        self.note = QLabel(BACKGROUND_MEDIA_NOTE)
        self.note.setWordWrap(True)
        self.note.setProperty("role", "hint")
        self.scope_combo.setMinimumContentsLength(8)
        self.scope_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*[theme.CARD_PADDING] * 4)
        layout.setSpacing(theme.FIELD_GAP)
        layout.addWidget(self.theme_by_screen)
        layout.addWidget(self.active_label)
        layout.addWidget(self.scope_combo)
        for field_name, caption in COLOR_FIELDS:
            row = QHBoxLayout()
            text = QLabel(caption)
            text.setWordWrap(True)  # long captions wrap inside the panel
            text.setMinimumWidth(1)
            row.addWidget(text, 1)
            button = QPushButton()
            button.setFixedWidth(theme.SWATCH_WIDTH)
            button.setObjectName(f"color:{field_name}")
            button.clicked.connect(lambda _c=False, f=field_name: self._ask_color(f))
            self.color_buttons[field_name] = button
            row.addWidget(button)
            layout.addLayout(row)
        caption = QLabel("Widget transparency")
        caption.setWordWrap(True)
        row = QHBoxLayout()
        self.transparency.setMinimumWidth(60)
        self.transparency_label.setMinimumWidth(28)
        self.transparency_label.setWordWrap(False)
        row.addWidget(self.transparency, 1)
        row.addWidget(self.transparency_label)
        field = QVBoxLayout()  # label -> control: LABEL_GAP
        field.setSpacing(theme.LABEL_GAP)
        field.addWidget(caption)
        field.addLayout(row)
        layout.addLayout(field)
        layout.addWidget(self.note)
        self._model: PersonalizationModel | None = None
        self._key = ""
        self.scope_combo.activated.connect(lambda _i: self._show_values())
        self.theme_by_screen.toggled.connect(self._on_theme_toggled)
        self.transparency.valueChanged.connect(self._on_transparency)

    # ---- model -> UI ----
    def show_model(self, model: PersonalizationModel, key: str, device_name: str = "") -> None:
        self._model, self._key = model, key
        # constant title: a title with the device name set this panel's minimum width, so choosing a device moved the splitter (window layout bug)
        self.device_name = device_name or "free size"
        self._show_values()

    @property
    def scope(self) -> str:
        return self.scope_combo.currentData()

    def _show_values(self) -> None:
        if self._model is None:
            return
        self._updating = True
        try:
            self.theme_by_screen.setChecked(self._model.theme_by_screen(self._key))
            active = "Screen" if self._model.theme_by_screen(self._key) else "Device"
            self.active_label.setText(f"Device: {self.device_name} | Active for widgets: {active} settings (editing: {self.scope_combo.currentText()})")
            values: PersonalizationValues = self._model.values(self.scope, self._key)
            for field_name, button in self.color_buttons.items():
                color = getattr(values, field_name)
                button.setText(color)
                caption = next(c for f, c in COLOR_FIELDS if f == field_name).split(" (")[0].lower()
                button.setToolTip(f"Choose the {caption} of the {self.scope_combo.currentText().lower()} ({color})")
                button.setStyleSheet(color_swatch_style(color, QColor(color).lightness() > 128))
            self.transparency.setValue(values.widget_transparency)
            self.transparency_label.setText(str(values.widget_transparency))
        finally:
            self._updating = False

    # ---- UI -> signals (user actions only) ----
    def pick_color(self, field_name: str, color: QColor) -> None:
        if color.isValid() and not self._updating:
            self.valueCommitted.emit(self.scope, field_name, color.name())

    def _ask_color(self, field_name: str) -> None:
        current = QColor(self.color_buttons[field_name].text())
        color = QColorDialog.getColor(current, self, "Personalization color")
        if color.isValid():
            self.pick_color(field_name, color)

    def _on_transparency(self, value: int) -> None:
        if not self._updating:
            self.valueCommitted.emit(self.scope, "widget_transparency", int(value))

    def _on_theme_toggled(self, checked: bool) -> None:
        if not self._updating:
            self.themeByScreenCommitted.emit(bool(checked))