r"""Widget library + thumbnail inspector (D7a): browse the widgets under a folder, see which image iCUE picks and why, how it is
grouped for the selected device, and open one in the renderer / settings editor.

Nothing here reads a path or a widget list from the code: the folder is chosen by the user and the widgets are found from
manifest.json + index.html. The images are the QPainter redraws of thumbnails.py (a simulation of the QML rules, not iCUE itself).
The inspector shows a STATIC image: a live HTML widget preview is a different thing and is not what these panels display.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .device_profiles import DeviceProfile
from .thumbnails import ImageStats, analyze, render_preview_tile, render_type_tile
from .widget_library import GroupInfo, Resolved, Unlisted, WidgetEntry, build_groups, scan_library

PATH_ROLE = Qt.ItemDataRole.UserRole


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


class LibraryPanel(QWidget):
    openRequested = Signal(str)  # folder of the selected widget

    def __init__(self) -> None:
        super().__init__()
        self.root: Path | None = None
        self.entries: list[WidgetEntry] = []
        self.groups: list[GroupInfo] = []
        self.unlisted: list[Unlisted] = []
        self.device: DeviceProfile | None = None

        self.choose_button = QPushButton("Choose library folder...")
        self.choose_button.clicked.connect(self.choose_folder)
        self.choose_button.setToolTip("Choose a folder that contains widget folders (e.g. WidgetsXE); it is only read")
        self.open_button = QPushButton("Open selected widget")
        self.open_button.setEnabled(False)
        self.open_button.setToolTip("Open the selected widget (an existing instance of that folder is shown again; double-click works too)")
        self.open_button.clicked.connect(lambda: self.selected_folder() and self.openRequested.emit(self.selected_folder()))
        self.path_label = QLabel("No library folder chosen.")
        self.path_label.setWordWrap(True)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Widget / group", "typeId", "Group (iCUE)", "Preview source", "Status"])
        self.tree.setColumnWidth(0, 190)
        self.tree.currentItemChanged.connect(lambda *_: self._show_selection())
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_button.click())
        self.preview_label = QLabel()
        self.type_label = QLabel()
        self.type_selected_label = QLabel()
        for label in (self.preview_label, self.type_label, self.type_selected_label):
            label.setStyleSheet("background:#6b6b6b;")
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(210)
        self.limits = QLabel("Static image inspector: it redraws the QML rules (WidgetPreviewPanel 128x56 crop on layer1; WidgetTypesPanel "
                             "16x16 alpha-only overlay). The panel background behind the tiles, SVG rasterisation and fonts are not "
                             "iCUE's; a live HTML preview is a different thing. iCUE v5.55 source.")
        self.limits.setWordWrap(True)
        self.limits.setProperty("role", "hint")

        previews = QHBoxLayout()
        for caption, label in (("Preview panel row (x2)", self.preview_label), ("Type tile (x3)", self.type_label),
                               ("Type tile selected (x3)", self.type_selected_label)):
            box = QVBoxLayout()
            box.addWidget(QLabel(caption))
            box.addWidget(label)
            previews.addLayout(box)
        top = QHBoxLayout()
        top.addWidget(self.choose_button)
        top.addWidget(self.open_button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.path_label)
        layout.addWidget(self.summary)
        layout.addWidget(self.tree, 1)
        layout.addLayout(previews)
        layout.addWidget(self.report)
        layout.addWidget(self.limits)

    # ---- data ----
    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select a folder with widgets", str(self.root or ""))
        if folder:
            self.set_library(folder)

    def set_library(self, root: str | Path) -> int:
        self.root = Path(root)
        self.entries = scan_library(self.root)
        self.path_label.setText(str(self.root))
        self.refresh()
        return len(self.entries)

    def set_device(self, device: DeviceProfile | None) -> None:
        self.device = device
        self.refresh()

    def refresh(self) -> None:
        self.groups, self.unlisted = build_groups(self.entries, self.device)
        listed = sum(len(g.widgets) for g in self.groups)
        self.summary.setText(f"{len(self.entries)} widgets found, {listed} listed for {self.device.name if self.device else 'no device (all)'} "
                             f"in {len(self.groups)} groups, {len(self.unlisted)} not listed.")
        self.tree.clear()
        for group in self.groups:
            item = QTreeWidgetItem([group.name, "", "declared" if group.declared else "implicit (widget name)",
                                    "", f"{len(group.widgets)} widget(s); preview panel: {'yes' if group.preview_panel else 'no'}"])
            item.setData(0, PATH_ROLE, None)
            for entry in group.widgets:
                preview = entry.preview_image()
                child = QTreeWidgetItem([entry.name, entry.type_id, entry.group_name, preview.source,
                                         "ok" if preview.exists else "preview file MISSING"])
                child.setData(0, PATH_ROLE, str(entry.folder))
                item.addChild(child)
            self.tree.addTopLevelItem(item)
            item.setExpanded(True)
        if self.unlisted:
            parent = QTreeWidgetItem([f"Not listed ({len(self.unlisted)})", "", "", "", ""])
            for gone in self.unlisted:
                child = QTreeWidgetItem([gone.entry.name, gone.entry.type_id, "", "", gone.reason])
                child.setData(0, PATH_ROLE, str(gone.entry.folder))
                parent.addChild(child)
            self.tree.addTopLevelItem(parent)
        self._show_selection()

    # ---- selection ----
    def selected_folder(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, PATH_ROLE) if item is not None else None

    def selected_entry(self) -> WidgetEntry | None:
        folder = self.selected_folder()
        return next((e for e in self.entries if str(e.folder) == folder), None)

    def select_widget(self, folder: str | Path) -> bool:
        target = str(folder)

        def walk(item: QTreeWidgetItem) -> bool:
            if item.data(0, PATH_ROLE) == target:
                self.tree.setCurrentItem(item)
                return True
            return any(walk(item.child(i)) for i in range(item.childCount()))

        return any(walk(self.tree.topLevelItem(i)) for i in range(self.tree.topLevelItemCount()))

    def _show_selection(self) -> None:
        entry = self.selected_entry()
        self.open_button.setEnabled(entry is not None and entry.valid)
        if entry is None:
            for label in (self.preview_label, self.type_label, self.type_selected_label):
                label.clear()
            self.report.setPlainText("Select a widget to see which images iCUE would use and why.")
            return
        preview, icon = entry.preview_image(), entry.type_image()
        self._pix(self.preview_label, render_preview_tile(preview, current=True), 2)
        self._pix(self.type_label, render_type_tile(icon), 3)
        self._pix(self.type_selected_label, render_type_tile(icon, selected=True), 3)
        group = next((g for g in self.groups if entry in g.widgets), None)
        lines = [f"{entry.name}  [{entry.type_id}]  {entry.folder}", ""]
        lines += [describe(preview, analyze(preview)), "", describe(icon, analyze(icon)), "",
                  describe(entry.group_image_widgets_pipeline()), "",
                  describe(entry.group_icon_dash_pipeline()), ""]
        lines.append(f"manifest preview_icon: {entry.manifest_preview_icon or '(none)'} - required by the validator, NOT used for any "
                     "thumbnail in the source")
        if group is not None:
            lines.append(f"group: {group.name} ({'declared by x-icue-widget-group' if entry.group_declared else 'implicit: named after the widget'}); "
                         f"{len(group.widgets)} widget(s); {group.preview_reason}")
        else:
            reason = next((u.reason for u in self.unlisted if u.entry is entry), "not listed")
            lines.append(f"NOT in a panel for {self.device.name if self.device else 'no device'}: {reason}")
        lines += [f"note: {n}" for n in entry.notes]
        self.report.setPlainText("\n".join(lines))

    @staticmethod
    def _pix(label: QLabel, image, scale: int) -> None:
        pixmap = QPixmap.fromImage(image)
        label.setPixmap(pixmap.scaled(pixmap.size() * scale, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))