r"""iCUE tab of the inspector: how iCUE identifies the ACTIVE widget (typeId, group, preview panel, thumbnails) and what is wrong with it.

One place for this information, whatever opened the widget (Open Widget, Import from iCUE, the instance list). `inspect()` is pure.

The group is computed against the widgets of the widgets folder (widgets_folder.py: detected from the first opened widget, remembered,
changeable at the bottom of this tab); without one only the widget itself is known, and the panel says so. The images are the QPainter redraws of thumbnails.py
(a simulation of the QML rules of iCUE v5.55, not iCUE itself).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme
from .device_profiles import DeviceProfile
from .thumbnails import ImageStats, analyze, render_preview_tile, render_type_tile
from .ui_kit import button, label
from .widget_library import SRC_DEFAULT, SRC_ICON, SRC_PREVIEW, GroupInfo, Resolved, WidgetEntry, build_groups, load_entry

TONE_MARK = {"error": "✖", "warning": "⚠", "success": "✔", "info": "ℹ", "neutral": "•"}
TONE_ORDER = ("error", "warning", "info", "neutral", "success")


@dataclass(frozen=True)
class Finding:
    tone: str   # error | warning | info | neutral | success
    text: str


@dataclass
class Inspection:
    entry: WidgetEntry
    device: DeviceProfile | None
    group: GroupInfo | None
    unlisted_reason: str | None
    in_library: bool                         # the group was computed with the other widgets of the widgets folder
    preview: Resolved
    icon: Resolved
    preview_stats: ImageStats | None = None
    icon_stats: ImageStats | None = None
    findings: list[Finding] = field(default_factory=list)
    report: str = ""


# ---- text -------------------------------------------------------------------------------------------------------------------
def describe(resolved: Resolved, stats: ImageStats | None = None) -> str:
    lines = [f"{resolved.role}: {resolved.source}"]
    if resolved.declared:
        lines.append(f"  declared: {resolved.declared}")
    if resolved.path:
        lines.append(f"  file: {resolved.path}  ({'exists' if resolved.exists else 'MISSING'})")
    lines.append(f"  why: {resolved.reason}")
    if stats is not None:
        if stats.readable:
            lines.append(f"  image: {stats.kind} {stats.natural[0]}x{stats.natural[1]}, alpha={'yes' if stats.has_alpha else 'no'}, "
                         f"opaque={stats.opaque_fraction:.0%}, mean luminance of opaque pixels={stats.mean_luminance:.0f}/255, "
                         f"16x16 overlay coverage={stats.overlay_coverage:.0%}")
            if stats.dark_on_layer1:
                lines.append("  note: dark pixels on the black (layer1) AssetIcon background: nearly invisible in the preview panel "
                             "(WidgetDevTool heuristic, luminance <= 32). The 16x16 type tile recolours the image, so it is not affected.")
        else:
            lines.append(f"  image: not readable ({stats.note})")
    return "\n".join(lines)


def _image_size(stats: ImageStats | None) -> str:
    return f" ({stats.kind} {stats.natural[0]}×{stats.natural[1]})" if stats is not None and stats.readable else ""


def findings(entry: WidgetEntry, device: DeviceProfile | None, group: GroupInfo | None, unlisted_reason: str | None,
             in_library: bool = True, preview_stats: ImageStats | None = None, icon_stats: ImageStats | None = None) -> list[Finding]:
    """What a widget author has to know, most severe first. Without stats the images are only checked for existence."""
    out: list[Finding] = []
    if not entry.valid:
        out += [Finding("error", f"Invalid widget: {m}") for m in entry.check.errors]
    preview, icon = entry.preview_image(), entry.type_image()
    # preview (widgetPreviewSource)
    if preview.source == SRC_PREVIEW and not preview.exists:
        out.append(Finding("error", f"Preview file missing: {preview.declared}. iCUE shows an empty image (no fallback to the icon)."))
    elif preview.source == SRC_DEFAULT:
        out.append(Finding("warning", "No preview and no <link rel=\"icon\">: iCUE shows its default web icon everywhere."))
    elif preview.source == SRC_PREVIEW:
        if preview_stats is not None and not preview_stats.readable:
            out.append(Finding("error", f"Preview cannot be decoded: {preview.declared} ({preview_stats.note})."))
        else:
            out.append(Finding("success", f"Preview: {preview.declared}{_image_size(preview_stats)}"))
            if preview_stats is not None and preview_stats.dark_on_layer1:
                out.append(Finding("warning", "Preview is very dark: nearly invisible on the black preview panel background."))
    elif preview.source == SRC_ICON and preview.exists:
        out.append(Finding("info", "No x-icue-widget-preview: the preview panel shows the icon instead."))
    # icon (imageSource): the default case is already reported above
    if icon.source == SRC_ICON:
        if not icon.exists:
            out.append(Finding("error", f"Icon file missing: {icon.declared or '(empty href)'}. iCUE shows an empty image "
                                        "(no fallback to the default icon)."))
        elif icon_stats is not None and not icon_stats.readable:
            out.append(Finding("error", f"Icon cannot be decoded: {icon.declared} ({icon_stats.note})."))
        else:
            out.append(Finding("success", f"Icon: {icon.declared}{_image_size(icon_stats)}"))
            if icon_stats is not None and icon_stats.dark_on_layer1 and preview.source == SRC_ICON:
                out.append(Finding("warning", "Icon is very dark and is used as the preview: nearly invisible on the black preview "
                                              "panel background."))
            elif icon_stats is not None and icon_stats.dark_on_layer1:
                out.append(Finding("info", "Icon is very dark: fine in the 16×16 type tile (recoloured), but it would be nearly "
                                           "invisible wherever it is drawn on black."))
    # device
    if entry.valid:
        if device is None:
            out.append(Finding("neutral", "No device selected: device support is not checked."))
        elif unlisted_reason:
            out.append(Finding("warning", f"Not listed for {device.name}: {unlisted_reason}"))
        else:
            out.append(Finding("success", f"Listed for {device.name}"))
    if entry.group_declared and not in_library and entry.valid:
        out.append(Finding("info", f"Group “{entry.group_name}” is declared, but only this widget is known: choose the widgets "
                                   "folder (bottom of this tab) to count the other widgets of the group."))
    out += [Finding("warning", n) for n in entry.notes]
    return sorted(out, key=lambda f: TONE_ORDER.index(f.tone))


def summary(items: Iterable[Finding]) -> tuple[str, str]:
    """(tone, short text) for a list of findings: '2 errors', '1 warning', 'ok'."""
    items = list(items)
    errors = sum(f.tone == "error" for f in items)
    warnings = sum(f.tone == "warning" for f in items)
    if errors:
        return "error", f"{errors} error{'s' if errors > 1 else ''}" + (f", {warnings} warning{'s' if warnings > 1 else ''}" if warnings else "")
    if warnings:
        return "warning", f"{warnings} warning{'s' if warnings > 1 else ''}"
    return "success", "ok"


def _canonical(folder: str | Path) -> str:
    return os.path.normcase(os.path.realpath(str(folder)))


def place(entry: WidgetEntry, library: Iterable[WidgetEntry], device: DeviceProfile | None) -> tuple[GroupInfo | None, str | None, bool]:
    """(group, unlisted reason, in_library) of `entry` among the library widgets (the library copy of the same folder is replaced)."""
    library = list(library)
    key = _canonical(entry.folder)
    others = [e for e in library if _canonical(e.folder) != key]
    groups, unlisted = build_groups(others + [entry], device)
    group = next((g for g in groups if any(w is entry for w in g.widgets)), None)
    reason = next((u.reason for u in unlisted if u.entry is entry), None)
    return group, reason, len(others) < len(library)


def inspect(folder: str | Path, library: Iterable[WidgetEntry] = (), device: DeviceProfile | None = None,
            entry: WidgetEntry | None = None) -> Inspection:
    entry = entry or load_entry(folder)
    group, reason, in_library = place(entry, library, device)
    preview, icon = entry.preview_image(), entry.type_image()
    preview_stats, icon_stats = analyze(preview), analyze(icon)
    result = Inspection(entry, device, group, reason, in_library, preview, icon, preview_stats, icon_stats)
    result.findings = findings(entry, device, group, reason, in_library, preview_stats, icon_stats)
    result.report = report(result)
    return result


def report(i: Inspection) -> str:
    entry = i.entry
    lines = [f"{entry.name}  [{entry.type_id}]  {entry.folder}", ""]
    lines += [describe(i.preview, i.preview_stats), "", describe(i.icon, i.icon_stats), "",
              describe(entry.group_image_widgets_pipeline()), "", describe(entry.group_icon_dash_pipeline()), ""]
    lines.append(f"manifest preview_icon: {entry.manifest_preview_icon or '(none)'} - required by the validator, NOT used for any "
                 "thumbnail in the source")
    if i.group is not None:
        lines.append(f"group: {i.group.name} ({'declared by x-icue-widget-group' if entry.group_declared else 'implicit: named after the widget'}); "
                     f"{len(i.group.widgets)} widget(s){'' if i.in_library else ' (only this widget is known: the widgets folder does not contain it)'}; "
                     f"{i.group.preview_reason}")
    else:
        lines.append(f"NOT in a panel for {i.device.name if i.device else 'no device'}: {i.unlisted_reason or 'not listed'}")
    lines += [f"note: {n}" for n in entry.notes]
    return "\n".join(lines)


# ---- widget -----------------------------------------------------------------------------------------------------------------
class FitImage(QLabel):
    """A pixel-art tile: drawn at `scale`, shrunk smoothly to the available width (never wider than the panel)."""

    def __init__(self, scale: int) -> None:
        super().__init__()
        self.scale = scale
        self._image: QImage | None = None
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    def set_image(self, image: QImage | None) -> None:
        self._image = image
        self._render()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if self._image is None or self._image.isNull():
            self.clear()
            return
        pixmap = QPixmap.fromImage(self._image)
        big = pixmap.scaled(pixmap.size() * self.scale, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
        if self.width() > 1 and big.width() > self.width():
            big = big.scaledToWidth(self.width(), Qt.TransformationMode.SmoothTransformation)
        self.setFixedHeight(big.height())
        self.setPixmap(big)


class IcueInfoPanel(QScrollArea):
    """The iCUE tab: header, checks, group, thumbnails, full report (collapsed)."""

    EMPTY_TEXT = "Open a widget to see how iCUE identifies it: typeId, group, preview panel and thumbnails."
    changeFolderRequested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.inspection: Inspection | None = None
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(*[theme.PANEL_MARGIN] * 4)
        box.setSpacing(theme.CARD_GAP)

        self.empty = label(self.EMPTY_TEXT, "muted", wrap=True)
        self.title = label("", "title", clipped=True)
        self.type_id = label("", "muted", clipped=True)
        self.type_id.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status = label("", wrap=True)
        head = QVBoxLayout()
        head.setSpacing(2)
        for w in (self.title, self.type_id, self.status):
            head.addWidget(w)

        self.checks_box, self.checks_layout = self._section("Checks")
        self.group_box, group_layout = self._section("Group")
        self.group_grid = QGridLayout()
        self.group_grid.setHorizontalSpacing(theme.FIELD_GAP)
        self.group_grid.setVerticalSpacing(theme.LABEL_GAP)
        self.group_grid.setColumnStretch(1, 1)
        group_layout.addLayout(self.group_grid)

        self.images_box, images_layout = self._section("Thumbnails")
        self.preview_tile = FitImage(2)
        self.type_tile, self.type_selected_tile = FitImage(3), FitImage(3)
        self.preview_caption = label("Preview panel row", "hint", clipped=True)
        images_layout.addWidget(self.preview_caption)
        images_layout.addWidget(self.preview_tile)
        tiles = QHBoxLayout()
        tiles.setSpacing(theme.FIELD_GAP)
        for caption, tile in (("Type tile", self.type_tile), ("Selected", self.type_selected_tile)):
            column = QVBoxLayout()
            column.setSpacing(theme.LABEL_GAP)
            column.addWidget(label(caption, "hint", clipped=True))
            column.addWidget(tile)
            tiles.addLayout(column)
        tiles.addStretch(1)
        images_layout.addLayout(tiles)
        images_layout.addWidget(label("Redrawn from iCUE v5.55's QML rules (128×56 crop on black; 16×16 alpha-only overlay). "
                                      "Not a live preview: background, SVG rendering and fonts are not iCUE's.", "hint", wrap=True))

        self.details_box, details_layout = self._section("Full report")
        row = QHBoxLayout()
        self.details_toggle = button("Show", "chevron-down", variant="ghost", checkable=True,
                                     tooltip="Show every image iCUE resolves, the file it reads and why")
        self.copy_button = button("Copy", "copy", variant="ghost", tooltip="Copy the full report to the clipboard")
        row.addWidget(self.details_toggle)
        row.addWidget(self.copy_button)
        row.addStretch(1)
        details_layout.addLayout(row)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setProperty("role", "mono")
        self.report.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.report.setMinimumHeight(260)
        self.report.setVisible(False)
        details_layout.addWidget(self.report)
        self.details_toggle.toggled.connect(self._toggle_details)
        self.copy_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.report.toPlainText()))

        self.folder_box, folder_layout = self._section("Widgets folder")
        self.folder_label = label("", clipped=True)  # a long path cannot wrap: clipped, the tooltip has it all
        self.folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.folder_count = label("", "hint", wrap=True)
        self.folder_button = button("Change...", "folder-open", variant="ghost",
                                    tooltip="The folder that holds your widget folders (e.g. WidgetsXE). Used to count groups here and by "
                                            "Import from iCUE to find a widget's source. It is only read.")
        self.folder_button.clicked.connect(self.changeFolderRequested.emit)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_button)
        folder_row.addStretch(1)
        folder_layout.addWidget(self.folder_label)
        folder_layout.addWidget(self.folder_count)
        folder_layout.addLayout(folder_row)
        self.set_widgets_folder(None, 0)

        box.addWidget(self.empty)
        box.addLayout(head)
        for section in (self.checks_box, self.group_box, self.images_box, self.details_box, self.folder_box):
            box.addWidget(section)
        box.addStretch(1)
        self.setWidget(content)
        self.clear()

    @staticmethod
    def _section(title: str) -> tuple[QGroupBox, QVBoxLayout]:
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(*[theme.CARD_PADDING] * 4)
        layout.setSpacing(theme.LABEL_GAP)
        return group, layout

    def set_widgets_folder(self, root: Path | None, count: int) -> None:
        self.folder_label.setText(str(root) if root else "Not chosen yet")
        self.folder_count.setText(f"{count} widgets found. Chosen from the first opened widget and remembered." if root else
                                  "Chosen automatically from the first widget you open (its nearest parent folder with several widgets).")
        self.folder_button.setText("Change..." if root else "Choose...")

    def _toggle_details(self, on: bool) -> None:
        self.report.setVisible(on)
        self.details_toggle.setText("Hide" if on else "Show")

    # ---- content ----
    def clear(self, message: str | None = None) -> None:
        self.inspection = None
        self.empty.setText(message or self.EMPTY_TEXT)
        self.empty.setVisible(True)
        for w in (self.title, self.type_id, self.status, self.checks_box, self.group_box, self.images_box, self.details_box):
            w.setVisible(False)
        self.report.clear()

    def show_inspection(self, inspection: Inspection) -> None:
        self.inspection = inspection
        entry = inspection.entry
        self.empty.setVisible(False)
        for w in (self.title, self.type_id, self.status, self.checks_box, self.group_box, self.images_box, self.details_box):
            w.setVisible(True)
        self.title.setText(entry.name or entry.folder.name)
        self.type_id.setText(entry.type_id)
        tone, text = summary(inspection.findings)
        self.status.setText(f"{TONE_MARK[tone]}  {'No problems found' if text == 'ok' else text}")
        theme.set_tone(self.status, tone)

        self._clear_layout(self.checks_layout)
        for item in inspection.findings:
            row = label(f"{TONE_MARK[item.tone]}  {item.text}", wrap=True)
            row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            theme.set_tone(row, item.tone)
            self.checks_layout.addWidget(row)

        self._clear_layout(self.group_grid)
        group = inspection.group
        rows: list[tuple[str, str]] = []
        if group is not None:
            count = f"{len(group.widgets)}" + ("" if inspection.in_library else " (only this widget is known)")
            rows += [("Name", f"{group.name} ({'declared' if entry.group_declared else 'implicit: the widget name'})"),
                     ("Widgets", count),
                     ("Preview panel", "Yes: iCUE lets the user pick one of the group's widgets" if group.preview_panel else
                      "No: a single-widget group is selected directly")]
            if group.preview_panel:
                rows.append(("Members", ", ".join(w.name for w in group.widgets)))
        else:
            rows.append(("Listed", f"No ({inspection.unlisted_reason or 'not listed'})"))
        rows += [("Preview", f"{inspection.preview.source}" + (f": {inspection.preview.declared}" if inspection.preview.declared else "")),
                 ("Icon", f"{inspection.icon.source}" + (f": {inspection.icon.declared}" if inspection.icon.declared else "")),
                 ("preview_icon", f"{entry.manifest_preview_icon or '(none)'}: manifest only, not used for thumbnails")]
        for r, (name, value) in enumerate(rows):
            key = label(name, "muted")
            key.setAlignment(Qt.AlignmentFlag.AlignTop)
            val = label(value, wrap=True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.group_grid.addWidget(key, r, 0)
            self.group_grid.addWidget(val, r, 1)

        self.preview_tile.set_image(render_preview_tile(inspection.preview, current=True))
        self.type_tile.set_image(render_type_tile(inspection.icon))
        self.type_selected_tile.set_image(render_type_tile(inspection.icon, selected=True))
        self.report.setPlainText(inspection.report)

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
