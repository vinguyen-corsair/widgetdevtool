"""Import from iCUE dialog (E2): choose the storage file, pick a widget, read the masked preview, confirm. Read-only."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from . import theme
from .ui_kit import button
from .icue_import import (Candidate, StorageReport, default_storage_path, installed_manifest_version, matching_entries, preview_text, read_storage)


class ImportDialog(QDialog):
    importRequested = Signal(object, object, object, bool)  # candidate, entry, report, apply_personalization

    def __init__(self, entries, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import an instance from iCUE (read-only)")
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        self.resize(900, 640)
        self.entries = list(entries)
        self.report: StorageReport | None = None
        self.device = None  # the window's current device (only used to warn when it does not offer the stored size)
        theme.apply()
        self.file_button = button("Choose iCUE Storage File...", "folder-open",
                                  tooltip="Pick a copy of (or the original) %APPDATA%\\Corsair\\CUE5\\dashlcd\\storage. It is only read.")
        self.file_button.clicked.connect(self.choose_file)
        self.status = QLabel("Choose a copy of (or the original) iCUE dashlcd\\storage file. It is only read; nothing is written or locked.")
        self.status.setWordWrap(True)
        self.listing = QListWidget()
        self.listing.currentRowChanged.connect(self._show)
        self.match_combo = QComboBox()
        self.match_combo.currentIndexChanged.connect(lambda _i: self._show(self.listing.currentRow()))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.apply_personalization = QCheckBox("Also copy the stored device/screen personalization into the current device scope")
        self.import_button = button("Import as New Instance", "import", variant="primary",
                                    tooltip="Create a NEW instance (new id, own storage) with the stored settings. iCUE is not changed.")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._confirm)
        close = button("Close", tooltip="Close the dialog (nothing is imported)")
        close.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.import_button)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        self.status.setProperty("role", "muted")
        for widget in (self.file_button, self.status, self.listing, QLabel("Widget source in the widgets folder (matched by typeId):"), self.match_combo, self.preview,
                       self.apply_personalization):
            layout.addWidget(widget)
        layout.addLayout(buttons)
        self.listing.setMaximumHeight(150)

    def choose_file(self) -> None:
        start = default_storage_path()
        path, _ = QFileDialog.getOpenFileName(self, "iCUE dashlcd storage (read-only)", str(start if start.exists() else start.parent))
        if path:
            self.load(path)

    def load(self, path: str | Path) -> bool:
        self.listing.clear()
        self.preview.clear()
        self.import_button.setEnabled(False)
        try:
            self.report = read_storage(path)
        except (OSError, ValueError) as exc:
            self.report = None
            self.status.setText(f"Cannot read this file: {exc}")
            return False
        for candidate in self.report.candidates:
            self.listing.addItem(candidate.label)
        skipped = f"; {len(self.report.skipped)} other attachment(s) skipped (not HTML widgets)" if self.report.skipped else ""
        self.status.setText(f"{self.report.file_name}: {len(self.report.candidates)} HTML widget(s){skipped}. Widgets folder: {len(self.entries)} widgets.")
        if self.report.candidates:
            self.listing.setCurrentRow(0)
        return True

    def current(self) -> tuple[Candidate | None, list]:
        row = self.listing.currentRow()
        if self.report is None or not 0 <= row < len(self.report.candidates):
            return None, []
        candidate = self.report.candidates[row]
        return candidate, matching_entries(candidate, self.entries)

    def _show(self, _row: int) -> None:
        candidate, matches = self.current()
        if candidate is None or self.report is None:
            return
        self.match_combo.blockSignals(True)
        if self.match_combo.count() != len(matches) or any(self.match_combo.itemData(i) != str(m.folder) for i, m in enumerate(matches)):
            self.match_combo.clear()
            for entry in matches:
                self.match_combo.addItem(f"{entry.name} - {entry.folder}", str(entry.folder))
            self.match_combo.setCurrentIndex(0 if len(matches) == 1 else -1)
        self.match_combo.blockSignals(False)
        chosen = self._chosen(matches)
        self.preview.setPlainText(preview_text(self.report, candidate, matches if chosen is None else [chosen], installed_manifest_version(candidate), len(self.entries), self.device))
        self.import_button.setEnabled(chosen is not None and not candidate.blocked)

    def _chosen(self, matches: list):
        index = self.match_combo.currentIndex()
        return matches[index] if 0 <= index < len(matches) else None

    def _confirm(self) -> None:
        candidate, matches = self.current()
        entry = self._chosen(matches)
        if candidate is not None and entry is not None and self.report is not None:
            self.importRequested.emit(candidate, entry, self.report, self.apply_personalization.isChecked())
            self.status.setText("Imported as a new instance (see the instance list). Choose another widget or close.")