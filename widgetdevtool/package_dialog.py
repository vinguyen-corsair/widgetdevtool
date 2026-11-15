"""Build Package dialog (U2, two targets since U2.2): target (iCUE 5 / iCUE Apps), widget name/version, source folder, output folder (Browse,
remembered), package file, min_app_version for iCUE Apps, Build / Cancel, status, log, Open Output Folder."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QVBoxLayout)

from . import icons, legacy, packager, theme
from .packager import TARGET_ICUE5, TARGET_ICUEAPPS, TARGET_LABELS
from .ui_kit import button

SETTINGS_KEY = "package/outputDir"
TARGET_KEY = "package/target"
STATE_TEXT = {
    TARGET_ICUE5: {"checking": "Checking files and assets... (iCUE 5)", "packaging": "Packaging... (iCUE 5 format)"},
    TARGET_ICUEAPPS: {"staging": "Preparing the temporary build copy...", "validating": "Validating... (icuewidget validate)",
                      "packaging": "Packaging... (icuewidget package)"},
}
TARGET_HELP = {
    TARGET_ICUE5: "Packages the widget in the format the installed iCUE 5 accepts (files at the archive root, manifest unchanged, no "
                  "min_app_version added). icuewidget validate is not run; files, manifest basics, assets and the archive are checked.",
    TARGET_ICUEAPPS: "Runs the official icuewidget CLI on a temporary copy: validate, then package. A missing min_app_version is added to the "
                     "copy only; other validation errors are reported, not fixed.",
}


def default_output_dir() -> Path:
    documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    return Path(documents) / "WidgetDevTool" / "Packages"


def _card() -> QFrame:
    card = QFrame()
    card.setObjectName("card")
    return card


def _form(card: QFrame) -> QFormLayout:
    form = QFormLayout(card)
    form.setContentsMargins(14, 12, 14, 12)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(10)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    return form


def _caption(text: str) -> QLabel:
    caption = QLabel(text)
    caption.setProperty("role", "muted")
    caption.setMinimumWidth(110)
    return caption


def _selectable(text: str = "", role: str | None = None) -> QLabel:
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if role:
        widget.setProperty("role", role)
    return widget


class _Worker(QObject):
    progress = Signal(str)
    finished = Signal(object)

    def __init__(self, cli, source, output, manifest, confirm, cancel, target, min_app_version=None, legacy_mode=False) -> None:
        super().__init__()
        self.args = (cli, source, output, manifest)
        self.options = dict(target=target, min_app_version=min_app_version, legacy_mode=legacy_mode)
        self.confirm, self.cancel = confirm, cancel

    def work(self) -> None:
        cli, source, output, manifest = self.args
        result = packager.build(cli, source, output, manifest, confirm_overwrite=self.confirm, progress=self.progress.emit, cancel=self.cancel,
                                **self.options)
        self.finished.emit(result)


class PackageDialog(QDialog):
    buildFinished = Signal(object)  # BuildResult

    def __init__(self, source: str | Path, settings: QSettings | None = None, parent=None, path_env: str | None = None,
                 target: str | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        self.resize(820, 860)
        self.setMinimumSize(560, 480)
        self.settings = settings if settings is not None else QSettings("WidgetDevTool", "WidgetDevTool")
        self.source = packager.package_root(source)
        self.manifest = json.loads((self.source / "manifest.json").read_text(encoding="utf-8-sig"))
        self.path_env = path_env
        self.cli: packager.CliInfo | None = None
        self._cli_probed = False
        self.result: packager.BuildResult | None = None
        self._thread: QThread | None = None
        self._cancel = threading.Event()
        theme.apply()
        target = target or str(self.settings.value(TARGET_KEY, "") or TARGET_ICUE5)
        self.target = target if target in TARGET_LABELS else TARGET_ICUE5
        self.title = QLabel("Build Package")
        self.title.setProperty("role", "title")
        self.subtitle = QLabel()
        self.subtitle.setProperty("role", "subtitle")
        self.subtitle.setWordWrap(True)
        self.target_combo = QComboBox()
        for key, text in TARGET_LABELS.items():
            self.target_combo.addItem(f"Build for {text}", key)
        self.target_combo.setToolTip("Package target: iCUE 5 (the format the installed iCUE 5 accepts) or iCUE Apps (official icuewidget CLI)")
        self.target_combo.currentIndexChanged.connect(lambda _i: self.set_target(self.target_combo.currentData()))
        self.name_label = QLabel(str(self.manifest.get("name") or self.source.name))
        self.name_label.setProperty("role", "title")
        self.version_label = _selectable(str(self.manifest.get("version") or "(no version)"))
        self.source_label = _selectable(str(self.source), "muted")
        self.cli_caption = _caption("CLI")
        self.cli_label = QLabel()
        self.cli_label.setWordWrap(True)
        self.method_label = _selectable("", "hint")
        self.output_edit = QLineEdit(str(self.settings.value(SETTINGS_KEY, "") or default_output_dir()))
        self.output_edit.setToolTip("Folder for the .icuewidget file. Never inside the widget, its repository or the tool folder.")
        self.browse_button = button("Browse...", "folder", tooltip="Choose the output folder")
        self.browse_button.clicked.connect(self.browse)
        self.file_label = _selectable()
        # iCUE Apps: min_app_version (kept when valid; when missing added to the build copy only; never guessed: documented default)
        self.min_version_state, value = packager.min_app_version_status(self.manifest)
        self.needs_min_version = self.min_version_state == "missing"
        self.min_version_edit = QLineEdit(value or packager.DOCUMENTED_MIN_APP_VERSION)
        self.min_version_edit.setReadOnly(not self.needs_min_version)
        self.min_version_edit.setPlaceholderText("iCUE version, e.g. 5.47")
        self.min_version_edit.setToolTip("min_app_version written into the TEMPORARY build copy when manifest.json has none "
                                         "(manifest.json itself is never changed)")
        self.min_version_edit.textChanged.connect(lambda _t: self._update_build_enabled())
        self.min_version_source = _selectable()
        self.hints_label = _selectable()
        # Legacy Compatibility (U2.1, iCUE Apps only): OFF by default; self-closes the void elements in <head> of the build copy.
        self.advanced_toggle = button("Advanced options", "chevron-right", variant="ghost", checkable=True, tool=True,
                                      tooltip="Show or hide Legacy Compatibility")
        self.advanced_toggle.toggled.connect(self._on_advanced_toggled)
        self.legacy_check = QCheckBox("Legacy Compatibility")
        self.legacy_check.setToolTip("Also self-close the void elements in <head> of the temporary build copy (<link ...> -> <link ... />). "
                                     "The source folder is never changed.")
        self.legacy_check.toggled.connect(self._on_legacy_toggled)
        self.legacy_help = QLabel("Applies one more verified format fix to the temporary build copy. The source folder is never changed; "
                                  "anything else the CLI rejects is reported, not fixed.")
        self.legacy_help.setWordWrap(True)
        self.legacy_help.setProperty("role", "hint")
        self.preview_button = button("Preview Changes (Diff)", "diff", tooltip="Show the analysis and the diff of the temporary build copy in "
                                                                              "the log. Nothing is built.")
        self.preview_button.clicked.connect(self.preview_changes)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setProperty("role", "log")
        self.log.setPlaceholderText("Build output (checks, commands, exit codes, stdout/stderr) appears here.")
        self.build_tooltip = ""
        self.build_button = button("Build Package", "package", variant="primary")
        self.build_button.setDefault(True)
        self.build_button.clicked.connect(self.start)
        self.cancel_button = button("Close")
        self.cancel_button.clicked.connect(self.cancel_or_close)
        self.cancel_button.setToolTip("Close the dialog; while a build runs: cancel it (an existing package stays unchanged)")
        self.open_button = button("Open Output Folder", "folder-open", tooltip="Open the folder that contains the new package")
        self.open_button.setVisible(False)
        self.open_button.clicked.connect(self.open_output)

        widget_card = _card()
        widget_form = _form(widget_card)
        widget_form.addRow(_caption("Target"), self.target_combo)
        widget_form.addRow(_caption("Widget"), self.name_label)
        widget_form.addRow(_caption("Version"), self.version_label)
        widget_form.addRow(_caption("Source folder"), self.source_label)

        output_card = _card()
        self.output_form = _form(output_card)
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(self.browse_button)
        self.output_form.addRow(_caption("Output folder"), output_row)
        self.output_form.addRow(_caption("Package file"), self.file_label)
        self.output_form.addRow(_caption("Method"), self.method_label)
        self.output_form.addRow(self.cli_caption, self.cli_label)

        self.apps_card = _card()
        apps_form = _form(self.apps_card)
        apps_form.addRow(_caption("min_app_version"), self.min_version_edit)
        apps_form.addRow(_caption(""), self.min_version_source)
        apps_form.addRow(_caption("CLI pre-check"), self.hints_label)

        self.advanced_box = _card()
        advanced = QVBoxLayout(self.advanced_box)
        advanced.setContentsMargins(14, 12, 14, 12)
        advanced.setSpacing(8)
        advanced.addWidget(self.legacy_check)
        advanced.addWidget(self.legacy_help)
        preview_row = QHBoxLayout()
        preview_row.addWidget(self.preview_button)
        preview_row.addStretch(1)
        advanced.addLayout(preview_row)
        self.advanced_box.setVisible(False)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.build_button)
        log_caption = QLabel("OUTPUT LOG")
        log_caption.setProperty("role", "section")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)
        layout.addWidget(widget_card)
        layout.addWidget(output_card)
        layout.addWidget(self.apps_card)
        layout.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.advanced_box)
        layout.addWidget(self.status)
        layout.addWidget(log_caption)
        layout.addWidget(self.log, 1)
        layout.addLayout(buttons)
        self._show_min_version_source()
        self.preview_button.setVisible(False)
        self.set_target(self.target)

    # ---- target ----
    @property
    def apps(self) -> bool:
        return self.target == TARGET_ICUEAPPS

    def set_target(self, target: str) -> None:
        if target not in TARGET_LABELS or self.running:
            return
        self.target = target
        self.target_combo.blockSignals(True)
        self.target_combo.setCurrentIndex(self.target_combo.findData(target))
        self.target_combo.blockSignals(False)
        text = TARGET_LABELS[target]
        self.setWindowTitle(f"Build Package - {text}")
        self.title.setText(f"Build Package - {text}")
        self.subtitle.setText(TARGET_HELP[target])
        self.file_label.setText(packager.package_file_name(self.manifest, self.source, target))
        apps = self.apps
        self.apps_card.setVisible(apps)
        self.advanced_toggle.setVisible(apps)
        self.advanced_box.setVisible(apps and self.advanced_toggle.isChecked())
        self.output_form.setRowVisible(self.cli_label, apps)
        self.method_label.setText("icuewidget validate <temporary copy>, then icuewidget package <temporary copy> --output <output folder>"
                                  if apps else packager.ICUE5_METHOD)
        if apps and not self._cli_probed:
            self.detect_cli()
        if apps:
            self._update_hints()
        self.build_button.setText(f"Build for {text}")
        self.build_tooltip = (f"Build {packager.package_file_name(self.manifest, self.source, target)} into the output folder. "
                              + ("Validate, then package with the official icuewidget CLI." if apps else
                                 "Check files and assets, then package in the iCUE 5 format.") + " Nothing is imported into iCUE.")
        self.open_button.setVisible(False)
        self.status.setText("Ready. " + ("Build validates the temporary copy first; nothing is packaged when validation fails." if apps else
                                         "Build checks files and assets first; nothing is packaged when a check fails."))
        theme.set_tone(self.status, "neutral", banner=True)
        self._update_build_enabled()

    # ---- CLI ----
    def detect_cli(self) -> packager.CliInfo | None:
        found = packager.find_cli(self.path_env)
        self.cli = packager.probe_cli(found) if found else None
        self._cli_probed = True
        if self.cli is None:
            self.cli_label.setText("Not found - install it (see the log), then reopen this dialog.")
            theme.set_tone(self.cli_label, "error")
            self.log.setPlainText(packager.INSTALL_HINT)
        else:
            option = self.cli.output_flag or "no output option listed by 'package --help'"
            self.cli_label.setText(f"icuewidget {self.cli.version or '(version unknown)'}  |  output option: {option}\n{self.cli.location}")
            theme.set_tone(self.cli_label, "success" if self.cli.output_flag else "warning")
        return self.cli

    # ---- iCUE Apps: min_app_version, Legacy Compatibility ----
    def _show_min_version_source(self) -> None:
        value = self.min_version_edit.text()
        if self.min_version_state == "valid":
            text, tone = "Kept from manifest.json (unchanged).", "success"
        elif self.min_version_state == "invalid":
            text, tone = (f"manifest.json has min_app_version {value!r}, which is not in iCUE version format. It is NOT changed: "
                          "icuewidget validate will report it - fix the manifest."), "error"
        else:
            text, tone = (f"Not in manifest.json: added to the temporary build copy only. Default {packager.DOCUMENTED_MIN_APP_VERSION} = "
                          "documented minimum (docs.elgato.com/icue/widgets, Prerequisites). Change it only to a version the widget really "
                          "requires."), "neutral"
        self.min_version_source.setText(text)
        self.min_version_source.setToolTip(packager.DOCUMENTED_MIN_APP_VERSION_SOURCE)
        theme.set_tone(self.min_version_source, tone)

    def _update_hints(self) -> None:
        issues = legacy.analyze(self.source)
        fixed_by_legacy = self.legacy_check.isChecked()
        lines, failing = [], False
        for issue in issues:
            if issue.rule == "min_app_version":
                continue
            handled = issue.rule == "void-elements" and fixed_by_legacy
            failing = failing or not handled
            where = f" (lines {issue.lines[:6]})" if issue.lines else ""
            suffix = "handled by Legacy Compatibility" if handled else (
                "validation will fail; Legacy Compatibility can fix it" if issue.fixable else "validation will fail; NOT fixed by the tool")
            lines.append(f"{issue.detail}{where}: {suffix}")
        hints = legacy.min_version_hints(self.source)
        text = "\n".join(lines + ["Other errors (e.g. XML in inline <head> scripts) are found only by icuewidget validate."] if lines else
                         ["No known CLI issue found (icuewidget validate decides)."])
        if hints:
            text += "\nVersion statements in the widget's own files: " + "; ".join(f"{v} ({f}:{n})" for v, f, n, _ in hints[:6])
        self.hints_label.setText(text)
        theme.set_tone(self.hints_label, "warning" if failing else "neutral", banner=True)

    def _on_advanced_toggled(self, shown: bool) -> None:
        self.advanced_box.setVisible(shown and self.apps)
        self.advanced_toggle.setIcon(icons.icon("chevron-down" if shown else "chevron-right"))

    def _on_legacy_toggled(self, on: bool) -> None:
        self.preview_button.setVisible(on)
        self.advanced_toggle.setText("Advanced options  |  Legacy Compatibility ON" if on else "Advanced options")
        if on and not self.advanced_toggle.isChecked():
            self.advanced_toggle.setChecked(True)
        self._update_hints()
        self._update_build_enabled()

    def min_version_ready(self) -> tuple[bool, str]:
        """(can build, why not). iCUE Apps needs a valid min_app_version when the manifest has none."""
        if not self.apps or not self.needs_min_version:
            return True, ""
        if not legacy.APP_VERSION_RE.match(self.min_version_edit.text().strip()):
            return False, "enter min_app_version in iCUE version format (e.g. 5.47)"
        return True, ""

    def _update_build_enabled(self) -> None:
        ready, why = self.min_version_ready()
        self.build_button.setEnabled(ready and not self.running)
        self.build_button.setToolTip(self.build_tooltip + (f"\nNot available: {why}." if why else ""))

    def preview_changes(self) -> str:
        """Stage into a temporary copy, show the change list and the diff, delete the copy. Nothing is built."""
        value = self.min_version_edit.text().strip() if self.needs_min_version else None
        issues = legacy.analyze(self.source)
        lines = ["iCUE Apps build copy preview (nothing is built; the source folder is not changed)", "", "Analysis (CLI 0.5.1 rules):"]
        for issue in issues:
            where = f" - lines {issue.lines[:12]}" if issue.lines else ""
            lines.append(f"  [{'fixed' if issue.fixable else 'NOT FIXED'}] {issue.detail}{where}")
        if not issues:
            lines.append("  no known issue")
        try:
            staging = legacy.stage(self.source, value or None, self_close=self.legacy_check.isChecked())
        except (ValueError, RuntimeError, OSError) as exc:
            lines += ["", f"Staging not possible yet: {exc}"]
        else:
            try:
                lines += ["", "Changes in the build copy:"] + [f"  - {c.file}: {c.description}" for c in staging.changes] + ["", staging.diff or "(no diff)"]
            finally:
                staging.cleanup()
        text = "\n".join(lines)
        self.log.setPlainText(text)
        return text

    # ---- output folder ----
    def browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Output folder for packages", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def confirm_overwrite(self, target: Path) -> bool:
        return QMessageBox.question(self, "Overwrite package?", f"{target}\nalready exists. Overwrite it?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    # ---- build ----
    @property
    def running(self) -> bool:
        return getattr(self, "_thread", None) is not None

    def start(self) -> bool:
        if self.running:
            return False
        ready, why = self.min_version_ready()
        if not ready:
            self.status.setText(f"iCUE Apps: {why}.")
            theme.set_tone(self.status, "warning", banner=True)
            return False
        if self.apps and self.cli is None and self.detect_cli() is None:
            self._finish(packager.build(None, self.source, self.output_edit.text(), self.manifest, target=self.target))
            return False
        output = self.output_edit.text().strip()
        problem = packager.check_output_folder(output, self.source)
        if problem:
            self.status.setText(f"Failed: output folder - {problem}. Choose another folder.")
            theme.set_tone(self.status, "error", banner=True)
            self.log.setPlainText(problem)
            return False
        self.settings.setValue(SETTINGS_KEY, output)
        self.settings.setValue(TARGET_KEY, self.target)
        self.settings.sync()
        destination = Path(output) / packager.package_file_name(self.manifest, self.source, self.target)
        overwrite = destination.exists() and self.confirm_overwrite(destination)  # asked here, on the UI thread
        self._cancel.clear()
        self.open_button.setVisible(False)
        self.build_button.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.target_combo.setEnabled(False)
        self.cancel_button.setText("Cancel")
        self.log.clear()
        self._thread = QThread(self)
        self._set_state("staging" if self.apps else "checking")
        value = self.min_version_edit.text().strip() if self.apps and self.needs_min_version else None
        self._worker = _Worker(self.cli, self.source, output, self.manifest, lambda _path: overwrite, self._cancel, self.target, value,
                               self.apps and self.legacy_check.isChecked())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.work)
        self._worker.progress.connect(self._set_state)
        self._worker.finished.connect(self._finish)
        self._thread.start()
        return True

    def _set_state(self, state: str) -> None:
        self.status.setText(STATE_TEXT[self.target].get(state, state))
        theme.set_tone(self.status, "info", banner=True)

    def _finish(self, result: packager.BuildResult) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(10000)
            self._thread = None
        self.result = result
        good = result.ok
        self.status.setText(("Success: " if good else {"cancelled": "Cancelled: ", "cli-missing": "CLI missing: "}.get(result.state, "Failed: "))
                            + result.message.splitlines()[0] + f" [{TARGET_LABELS.get(result.target or self.target)}]")
        theme.set_tone(self.status, "success" if good else "warning" if result.state == "cancelled" else "error", banner=True)
        self.log.setPlainText(result.log())
        self.open_button.setVisible(good)
        self._update_build_enabled()
        self.browse_button.setEnabled(True)
        self.target_combo.setEnabled(True)
        self.cancel_button.setText("Close")
        self.buildFinished.emit(result)

    def cancel_or_close(self) -> None:
        if self.running:
            self._cancel.set()
            self.status.setText("Cancelling...")
        else:
            self.reject()

    def open_output(self) -> None:
        if self.result and self.result.output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result.output.parent)))

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self.running:
            self._cancel.set()
            self._thread.quit()
            self._thread.wait(15000)
        super().closeEvent(event)
