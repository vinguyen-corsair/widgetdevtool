"""Small widgets of the U1 layout: clipped labels, segmented controls, a wrapping row layout and button helpers."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget, QWidgetItem)

from . import icons, theme


class ClippedLabel(QLabel):
    """A label whose text can never widen its layout (and so never the window): too long text is clipped and the full text is the
    tooltip. A plain QLabel reports its whole text as MINIMUM width, so every longer device/measurement text made the main window
    grow and it never shrank back (bug found after A2)."""

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(1, super().minimumSizeHint().height())

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        super().setText(text)
        self.setToolTip(text)


def narrow_combo(combo: QComboBox, characters: int = 8) -> QComboBox:
    """A combo box that does not ask for the width of its longest item as minimum (items are elided in the drop-down button)."""
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(characters)
    combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return combo


def label(text: str = "", role: str | None = None, *, clipped: bool = False, wrap: bool = False) -> QLabel:
    widget = ClippedLabel(text) if clipped else QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def button(text: str = "", icon: str | None = None, *, variant: str | None = None, tooltip: str = "", shortcut: str | None = None,
           checkable: bool = False, tool: bool = False, icon_only: bool = False) -> QPushButton | QToolButton:
    """A themed button: optional icon, variant (primary/ghost/danger/toggle), tooltip (+ shortcut shown in it)."""
    widget = QToolButton() if (tool or icon_only) else QPushButton()
    if text:
        widget.setText(text)
    if icon:
        widget.setIcon(icons.icon(icon, "#ffffff" if variant == "primary" else None))
        widget.setIconSize(QSize(theme.ICON_SIZE, theme.ICON_SIZE))
    if isinstance(widget, QToolButton):
        widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if icon_only else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        widget.setFixedHeight(theme.CONTROL_HEIGHT)
        if icon_only:
            widget.setProperty("iconOnly", True)
            widget.setFixedSize(theme.CONTROL_HEIGHT, theme.CONTROL_HEIGHT)
            widget.setAccessibleName(tooltip.split("\n")[0] if tooltip else text)
    if variant:
        widget.setProperty("variant", variant)
    if shortcut:
        widget.setShortcut(QKeySequence(shortcut))
        tooltip = f"{tooltip}  ({QKeySequence(shortcut).toString(QKeySequence.SequenceFormat.NativeText)})" if tooltip else shortcut
    if tooltip:
        widget.setToolTip(tooltip)
    widget.setCheckable(checkable)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def captioned(caption: str, control: QWidget, tooltip: str = "") -> QWidget:
    """A small upper-case caption above a control (keeps a toolbar row narrow)."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    head = label(caption.upper(), "section")
    if tooltip:
        head.setToolTip(tooltip)
        control.setToolTip(control.toolTip() or tooltip)
    layout.addWidget(head)
    layout.addWidget(control)
    box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return box


class SegmentedControl(QWidget):
    """Exclusive buttons in one row. Keeps the QComboBox API the window and the tests use (addItem, itemText, itemData, findData,
    currentIndex/currentData, setCurrentIndex, activated, clear, count), plus per-item enabled state and tooltip.
    `activated(index)` is emitted for user choices only (click, Left/Right keys), as QComboBox.activated."""

    activated = Signal(int)
    currentIndexChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[QToolButton] = []
        self._data: list = []
        self._current = -1
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ---- QComboBox-like API ----
    def addItem(self, text: str, data=None, icon: str | None = None, tooltip: str = "") -> None:  # noqa: N802
        item = QToolButton(self)
        item.setText(text)
        item.setCheckable(True)
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        item.setFixedHeight(theme.CONTROL_HEIGHT)
        if len(text) <= 2:  # S / M / L / XL: equal widths
            item.setMinimumWidth(theme.SEGMENT_MIN_WIDTH)
        if icon:
            item.setIcon(icons.icon(icon, None, "#ffffff"))
            item.setIconSize(QSize(theme.ICON_SIZE, theme.ICON_SIZE))
            item.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        if tooltip:
            item.setToolTip(tooltip)
        index = len(self._buttons)
        item.clicked.connect(lambda _c=False, i=index: self._on_clicked(i))
        item.installEventFilter(self)  # Left/Right on a focused segment moves the choice (QAbstractButton would only move focus)
        self._group.addButton(item, index)
        self._buttons.append(item)
        self._data.append(data)
        self._layout.addWidget(item)
        self._restyle()

    def clear(self) -> None:
        for item in self._buttons:
            self._group.removeButton(item)
            self._layout.removeWidget(item)
            item.hide()
            item.setParent(None)
            item.deleteLater()
        self._buttons, self._data, self._current = [], [], -1

    def count(self) -> int:
        return len(self._buttons)

    def itemText(self, index: int) -> str:  # noqa: N802
        return self._buttons[index].text() if 0 <= index < len(self._buttons) else ""

    def itemData(self, index: int):  # noqa: N802
        return self._data[index] if 0 <= index < len(self._data) else None

    def findData(self, data) -> int:  # noqa: N802
        return next((i for i, d in enumerate(self._data) if d == data), -1)

    def currentIndex(self) -> int:  # noqa: N802
        return self._current

    def currentData(self):  # noqa: N802
        return self.itemData(self._current)

    def currentText(self) -> str:  # noqa: N802
        return self.itemText(self._current)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        index = index if 0 <= index < len(self._buttons) else -1
        self._group.setExclusive(False)
        for i, item in enumerate(self._buttons):
            item.setChecked(i == index)
        self._group.setExclusive(True)
        if index != self._current:
            self._current = index
            if not self.signalsBlocked():
                self.currentIndexChanged.emit(index)

    def setItemEnabled(self, index: int, enabled: bool) -> None:  # noqa: N802
        self._buttons[index].setEnabled(enabled)

    def isItemEnabled(self, index: int) -> bool:  # noqa: N802
        return self._buttons[index].isEnabled()

    def setItemToolTip(self, index: int, text: str) -> None:  # noqa: N802
        self._buttons[index].setToolTip(text)

    def button(self, index: int) -> QToolButton:
        return self._buttons[index]

    def enabled_texts(self) -> list[str]:
        return [b.text() for b in self._buttons if b.isEnabled()]

    # ---- internals ----
    def _restyle(self) -> None:
        last = len(self._buttons) - 1
        for i, item in enumerate(self._buttons):
            theme.set_props(item, segment="only" if last == 0 else "first" if i == 0 else "last" if i == last else "middle")

    def _on_clicked(self, index: int) -> None:
        if not self._buttons[index].isEnabled():
            return
        self.setCurrentIndex(index)
        self.activated.emit(index)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.KeyPress and watched in self._buttons:
            step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(event.key())
            if step is not None:
                self.step(step)
                return True
        return super().eventFilter(watched, event)

    def step(self, step: int) -> None:
        """Choose the next (+1) / previous (-1) ENABLED item, as the Left/Right keys do; stops at the ends."""
        index = self._current
        while 0 <= index + step < len(self._buttons):
            index += step
            if self._buttons[index].isEnabled():
                self._buttons[index].setFocus()
                self._on_clicked(index)
                return


class FlowLayout(QLayout):
    """Items left to right, wrapping to the next row when the width runs out. Its minimum width is the widest item, so a narrow window
    wraps the row instead of growing (the window size belongs to the user)."""

    def __init__(self, parent: QWidget | None = None, h_spacing: int = 16, v_spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._h, self._v = h_spacing, v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def addWidget(self, widget: QWidget) -> None:  # noqa: N802
        self.addChildWidget(widget)
        self.addItem(QWidgetItem(widget))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            if not item.isEmpty():
                size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line > 0:
                x, y, line = area.x(), y + line + self._v, 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line = max(line, hint.height())
        return y + line - rect.y() + margins.bottom()