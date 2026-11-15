"""Main window: pick a widget folder and render its index.html in QtWebEngine."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QSize, QTimer, Qt, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import (
    QAbstractButton,
    QDockWidget,
    QFrame,
    QScrollArea,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QMenu,
    QStatusBar,
    QToolButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme

from .icue_import import build_instance, personalization_values
from .import_dialog import ImportDialog
from .package_dialog import PackageDialog
from .packager import TARGET_ICUE5, TARGET_ICUEAPPS
from .instance_runtime import InstanceRuntime, new_instance_for, type_id_of
from .instance_storage import StorageLayout, storage_name
from .instance_store import CUSTOM_STYLE_ENABLED_KEY, InstanceStore, WidgetInstance
from .live_reload import FolderWatcher
from .personalization import FREE_KEY, PersonalizationModel, PersonalizationValues
from .qrc_fonts import register_fonts
from .console_log import ConsoleEntry, ConsoleLog
from .device_profiles import LANDSCAPE, PORTRAIT, SIZE_LABEL, DeviceProfile, load_profiles
from .runtime_bridge import RuntimeBridge, parse_widget_settings
from .library_panel import LibraryPanel
from .personalization import has_personalization
from .personalization_panel import PersonalizationPanel
from .sensors_panel import SensorsPanel
from .settings_panel import SettingsPanel
from .ui_kit import ClippedLabel, FlowLayout, SegmentedControl, button, captioned, label, narrow_combo  # noqa: F401 (re-exported)
from .viewport import FIT, MODE_NOMINAL, MODE_PHYSICAL, NATIVE, Viewport, ViewportFrame, compute_viewport, exact_zooms
from .widget_loader import WidgetCheck, check_widget_folder

# Shown permanently: a rendered page is not proof that the widget works.
RUNTIME_NOTICE = (
    "Runtime bridge v0.2 + full-page live reload (not HMR) injects iCUE globals, lifecycle, a RECORDING MOCK of Linkprovider and a MOCK "
    "Sensorsdataprovider (invented sensors by default; Sensors tab > Data source: live sensors from the Olympus Hub), based on the "
    "iCUE v5.55 source (UNVERIFIED against release 5.52.93). NOT simulated: other plugins, device, "
    "permissions, focus/touch handling, tick() semantics. "
    "A rendered page or a clean console does not prove the widget works."
)

VIEWPORT_NOTICE = (
    "Device viewport: the page gets the NOMINAL widget size iCUE computes (WidgetSizeWatcherImpl::widgetSizePx: relative size x "
    "mimicScreenSize; keyboard/pump: fixed manifest size). The formula is verified in the v5.55 source; that the real host gives "
    "the web view exactly these pixels is UNKNOWN. Physical mode is EXPERIMENTAL. Preview scale: Native 1:1 (default) shows the page at "
    "exactly that size with the display's own devicePixelRatio and scrolls when it is larger than the window. Visual Fit / zoom is "
    "EXPERIMENTAL: it uses the page zoom, which multiplies window.devicePixelRatio and can make the page measure a fraction of a pixel "
    "less - do not use it to debug sizes or DPR. Not simulated: portrait zoom of the iCUE assignment view, auto-rotation."
)
STORAGE_NOTICE = (
    "Every instance has its OWN WebEngine profile and storage folder inside the WidgetDevTool data folder (name = iCUE's "
    "htmlWidgetStorageName: id + md5(typeId)). Close keeps the storage, Remove deletes it. The tool never touches iCUE's storage. "
    "Hypothesis: the iCUE profile type/limits are not compared; only the isolation was measured here."
)
SIZE_TOOLTIP = ("Widget size: how big the widget is on the device (S / M / L / XL). It changes the page's viewport and is stored per "
                "instance. Sizes the device does not offer are disabled.")
SCALE_TOOLTIP = ("Preview scale: how the viewport is drawn in this window. It never changes the page size. Native 1:1 is exact; "
                 "Visual Fit and zooms are EXPERIMENTAL (they change devicePixelRatio).")
CONSOLE_HEIGHT = 170
PREVIEW_COMFORT_WIDTH = 520
PREVIEW_COMFORT_HEIGHT = 420
CONSOLE_MIN_HEIGHT = 96
INSTANCES_WIDTH = 248
INSPECTOR_WIDTH = 330
INSPECTOR_MIN_WIDTH = 280
WORKSPACE_VERSION = 1
WORKSPACE_FILE = "workspace.json"


class State(Enum):
    IDLE = "idle"
    LOADING = "loading"
    LOADED = "loaded"          # the page finished loading; says nothing about widget logic
    LOAD_FAILED = "load_failed"  # QtWebEngine reported a navigation failure
    INVALID = "invalid"        # folder rejected before loading


STATE_TONES = {State.IDLE: "neutral", State.LOADING: "info", State.LOADED: "success", State.LOAD_FAILED: "error", State.INVALID: "error"}


class MainWindow(QMainWindow):
    # Emitted once a load attempt has settled (True = QtWebEngine loadFinished(ok)).
    loadSettled = Signal(bool)  # the ACTIVE instance finished a load
    instanceSettled = Signal(str, bool)  # any instance (id, ok) finished a load

    def __init__(self, data_dir: str | Path | None = None, persist: bool | None = None) -> None:
        """`data_dir`: the tool's own data folder (storage of every instance + workspace.json). None = a private temporary
        folder, deleted on shutdown (tests, throw-away runs). `persist` (default: only with a data_dir) saves the workspace
        after every change and on shutdown."""
        super().__init__()
        self.setWindowTitle("iCUE Widget Dev Tool")
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)  # tooltips also while another window is active
        self.resize(1280, 840)

        register_fonts()  # qrc:/fonts/*, as in iCUE (see qrc_fonts.py)

        self._owned_tmp = tempfile.mkdtemp(prefix="wdt_data_") if data_dir is None else None
        self.storage = StorageLayout(data_dir if data_dir is not None else self._owned_tmp)
        self.persist = (data_dir is not None) if persist is None else persist
        self.workspace_path = self.storage.root / WORKSPACE_FILE
        self.pending_storage_deletes: list[tuple[str, str]] = []  # (id, typeId) of removed instances whose folder is still locked
        self._shut_down = False
        self._restoring = False
        self._minimum_fixed = False

        # The running instances (insertion order = list order); each owns profile + page + view + bridge + console (D7b).
        self.runtimes: dict[str, InstanceRuntime] = {}
        self._active_id: str | None = None
        self._idle_state, self._idle_current = State.IDLE, None
        self._idle_console = ConsoleLog(self)
        self._watchers: dict[str, FolderWatcher] = {}  # one per source folder
        self._null_watcher = FolderWatcher(self)  # answers `watcher` while nothing is open
        self._invalid_watcher: FolderWatcher | None = None
        self._invalid: tuple | None = None
        self._recent: list[str] = []  # instance ids, most recently shown last (picks the instance when a folder has several)
        self.store = InstanceStore()  # every instance created or opened in this session
        # Device viewport (D6): window state only, never part of an instance, so changing it cannot lose settings.
        try:
            self.profiles: list[DeviceProfile] = load_profiles()
        except (OSError, ValueError):
            self.profiles = []
        # E3: the DEVICE (and mode) are shared by the window; size and orientation belong to each INSTANCE (WidgetInstance.size / .orientation).
        # `_default_*` is only the last explicit choice: it seeds a NEW instance, it is never applied to an existing one.
        self.device: DeviceProfile | None = None
        self.device_mode = MODE_NOMINAL
        self._default_size: str | None = None
        self._default_orientation: str | None = None
        self.import_notice = ""
        # Device / Screen personalization (D4): window state, never part of an instance.
        self.personalization = PersonalizationModel()

        self._build_ui()
        self._refresh_instance_list()  # instance actions start disabled: nothing to act on yet
        self._set_watch("Not watching (no widget open)", "neutral")
        self._set_state(State.IDLE, "Open a widget folder to start (Open Widget, Widget Library or Import from iCUE).")

    # ---- UI ---------------------------------------------------------------
    # Layout (U1): top bar | [Instances | Preview (header + viewport) / Console | Inspector (Settings, Personalization)] | status bar.
    def _build_ui(self) -> None:
        theme.apply()
        self._build_top_bar()
        self._build_instances_panel()
        self._build_device_bar()
        self._build_preview_column()
        self._build_console_panel()
        self._build_inspector()

        self.center_splitter = QSplitter(Qt.Orientation.Vertical)
        self.center_splitter.addWidget(self.preview_column)
        self.center_splitter.addWidget(self.console_panel)
        self.center_splitter.setStretchFactor(0, 1)
        self.center_splitter.setChildrenCollapsible(False)
        self.center_splitter.setHandleWidth(1)
        self.center_splitter.setSizes([600, CONSOLE_HEIGHT])
        self.center_splitter.splitterMoved.connect(self._remember_console_height)
        self.center_splitter.installEventFilter(self)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        for widget in (self.instances_panel, self.center_splitter, self.inspector):
            self.splitter.addWidget(widget)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.splitter.setSizes([INSTANCES_WIDTH, 1280 - INSTANCES_WIDTH - INSPECTOR_WIDTH, INSPECTOR_WIDTH])
        self._side_widths = [INSTANCES_WIDTH, INSPECTOR_WIDTH]  # the widths the user gave the side panels (splitter drags)
        self._fitting = False
        self.splitter.splitterMoved.connect(self._remember_side_widths)
        self.splitter.installEventFilter(self)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top_bar)
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(container)
        self._build_status_bar()
        # The window size belongs to the user: the layout must never resize it (default constraint = the layout minimum becomes the
        # window minimum and the window grows with every longer text). Content that does not fit is clipped / scrolled / wrapped.
        self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setMinimumSize(900, 600)

    def _build_top_bar(self) -> None:
        self.top_bar = QFrame()
        self.top_bar.setObjectName("topBar")
        self.brand_label = label("iCUE Widget Dev Tool", "brand", clipped=True)
        self.open_button = button("Open Widget", "folder-open", shortcut="Ctrl+O",
                                  tooltip="Open a widget folder (must contain manifest.json and index.html).\n"
                                          "A folder that already has an instance shows that instance again.")
        self.open_button.clicked.connect(self.choose_folder)
        self.library_button = button("Widget Library", "library", checkable=True, shortcut="Ctrl+L",
                                     tooltip="Show or hide the Widget Library: browse a folder of widgets and open one.")
        self.import_icue_button = button("Import from iCUE", "import", shortcut="Ctrl+I",
                                         tooltip="Read-only: read an iCUE dashlcd storage file and create a NEW instance with the stored settings.")
        self.import_icue_button.clicked.connect(self.open_import_dialog)
        self.package_button = button("Build Package", "package", variant="primary", shortcut="Ctrl+Shift+B",
                                     tooltip="Build a .icuewidget file of the active widget: choose Build for iCUE 5 or Build for iCUE Apps.")
        self.package_menu = QMenu(self.package_button)
        self.package_icue5_action = self.package_menu.addAction(icons.icon("package"), "Build for iCUE 5")
        self.package_icue5_action.setToolTip("iCUE 5 format (as the working WidgetsXE packages); manifest unchanged, no icuewidget validate")
        self.package_icue5_action.triggered.connect(lambda: self.open_package_dialog(target=TARGET_ICUE5))
        self.package_icueapps_action = self.package_menu.addAction(icons.icon("package"), "Build for iCUE Apps")
        self.package_icueapps_action.setToolTip("Official icuewidget CLI on a temporary copy: validate, then package")
        self.package_icueapps_action.triggered.connect(lambda: self.open_package_dialog(target=TARGET_ICUEAPPS))
        self.package_menu.setToolTipsVisible(True)
        self.package_button.setMenu(self.package_menu)
        self.instances_toggle = button(icon="panel-left", variant="ghost", icon_only=True, checkable=True,
                                       tooltip="Show or hide the Instances panel", shortcut="Ctrl+Shift+E")
        self.inspector_toggle = button(icon="panel-right", variant="ghost", icon_only=True, checkable=True,
                                       tooltip="Show or hide the inspector (Settings, Personalization, Sensors)", shortcut="Ctrl+Shift+S")
        self.library_panel = LibraryPanel()
        self.library_panel.openRequested.connect(self.open_library_widget)
        self.library_dock = QDockWidget("Widget Library", self)
        self.library_dock.setWidget(self.library_panel)
        self.library_dock.setVisible(False)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.library_dock)
        for name, tip in (("qt_dockwidget_floatbutton", "Detach the Widget Library into its own window (double-click its title to dock it again)"),
                          ("qt_dockwidget_closebutton", "Close the Widget Library (reopen it with Widget Library, Ctrl+L)")):
            title_button = self.library_dock.findChild(QAbstractButton, name)
            if title_button is not None:
                title_button.setToolTip(tip)
        self.library_button.clicked.connect(lambda: self.library_dock.setVisible(self.library_dock.isHidden()))
        self.library_dock.visibilityChanged.connect(self._on_library_visibility)
        bar = QHBoxLayout(self.top_bar)
        bar.setContentsMargins(10, 6, 10, 6)
        bar.setSpacing(theme.SPACING)
        bar.addWidget(self.brand_label, 1)
        for widget in (self.open_button, self.library_button, self.import_icue_button):
            bar.addWidget(widget)
        bar.addSpacing(12)
        bar.addWidget(self.instances_toggle)
        bar.addWidget(self.inspector_toggle)
        bar.addSpacing(8)
        bar.addWidget(self.package_button)
        self._top_texts = {b: b.text() for b in (self.open_button, self.library_button, self.import_icue_button)}
        self._top_compact = False
        self.top_bar.installEventFilter(self)

    def _fit_top_bar(self) -> None:
        """A narrow window (or large fonts / display scaling) shows the secondary top-bar actions as icons (tooltips keep the names) instead
        of truncating them or making the window wider. The primary action keeps its text."""
        shortcuts = {w: w.shortcut() for w in self._top_texts}  # QAbstractButton.setText() replaces the shortcut by the text's mnemonic
        if self._top_compact:
            for widget, text in self._top_texts.items():
                widget.setText(text)
        bar = self.top_bar.layout()
        margins = bar.contentsMargins()
        need = margins.left() + margins.right() + 12 + 8 + bar.spacing() * (bar.count() - 1) + self.brand_label.fontMetrics().horizontalAdvance("iCUE")
        need += sum(w.sizeHint().width() for w in (self.open_button, self.library_button, self.import_icue_button, self.instances_toggle,
                                                   self.inspector_toggle, self.package_button))
        compact = self.top_bar.width() < need
        if compact:
            for widget in self._top_texts:
                widget.setText("")
        for widget, shortcut in shortcuts.items():
            widget.setShortcut(shortcut)
        self._top_compact = compact

    def _build_instances_panel(self) -> None:
        self.instances_panel = QFrame()
        self.instances_panel.setObjectName("panel")
        self.instance_list = QListWidget()
        self.instance_list.setIconSize(QSize(10, 10))
        self.instance_list.currentItemChanged.connect(lambda item, _prev: self._on_list_selected(item))
        self.instance_count = label("", "hint", clipped=True)
        self.new_instance_button = button("Add", "plus", tooltip="Add another instance of the active widget (own id, default settings, "
                                                                  "own storage). The other instances keep running.")
        self.new_instance_button.clicked.connect(self.new_instance)
        self.new_instance_button.setMinimumWidth(self.new_instance_button.sizeHint().width())
        # Selected-instance actions: four distinct icons, each with a tooltip that says what is kept and what is deleted.
        self.clone_button = button(icon="copy", icon_only=True, tooltip="Clone\nDuplicate instance with independent storage: same settings, "
                                                                        "new id, empty storage. The original keeps running.")
        self.clone_button.clicked.connect(lambda: self.clone_instance())
        self.reset_button = button(icon="eraser", icon_only=True, tooltip="Reset Storage\nClear browser storage (localStorage, cache), keep "
                                                                          "instance settings and id. Asks first; the page restarts.")
        self.reset_button.clicked.connect(self._on_reset_clicked)
        self.close_button = button(icon="power", icon_only=True, tooltip="Close\nStop instance, keep settings and storage. Reopen it from "
                                                                         "the instance list.")
        self.close_button.clicked.connect(lambda: self.close_instance())
        self.remove_button = button(icon="trash", icon_only=True, variant="danger",
                                    tooltip="Remove\nDelete instance and its storage. Cannot be undone.")
        self.remove_button.clicked.connect(lambda: self.remove_instance())
        # Less frequent actions: one menu. Handlers unchanged (open_instance restore/merge, choose_save/open_instances).
        self.more_button = button(icon="more", icon_only=True, tooltip="More actions\nReopen the selected instance (as stored / with new "
                                                                       "defaults), save or open an instance file")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more_button)
        menu.setToolTipsVisible(True)
        self.restore_action = menu.addAction(icons.icon("restore"), "Reopen as Stored")
        self.restore_action.setToolTip("Restore: reopen the selected instance with exactly its stored settings (no default is added, a missing "
                                       "key stays missing). Same id and storage; a running page is reloaded. The stored instance is not changed.")
        self.restore_action.triggered.connect(lambda: self.open_instance(self._selected_id(), "restore"))
        self.merge_action = menu.addAction(icons.icon("merge"), "Reopen with New Defaults")
        self.merge_action.setToolTip("Merge (iCUE update()): CHANGES the stored instance - defaults of the current widget HTML are added, keys "
                                     "no longer declared are dropped, stored values win for the others - then reopens it (same id and storage).")
        self.merge_action.triggered.connect(lambda: self.open_instance(self._selected_id(), "merge"))
        menu.addSeparator()
        self.save_instances_action = menu.addAction(icons.icon("save"), "Save Instance File...")
        self.save_instances_action.setToolTip("Write ALL instances (ids, settings, sources) to a JSON file of your choice. The workspace and "
                                              "the open pages are not changed.")
        self.save_instances_action.triggered.connect(self.choose_save_instances)
        self.open_instances_action = menu.addAction(icons.icon("folder"), "Open Instance File...")
        self.open_instances_action.setToolTip("REPLACE the instance list with a JSON file. Nothing is opened or reloaded; running instances "
                                              "that are not in the file stay. A running instance with the same id uses the file's data the next "
                                              "time it is reopened.")
        self.open_instances_action.triggered.connect(self.choose_open_instances)
        self.more_button.setMenu(menu)
        self.storage_notice = label("Close keeps an instance and its storage; Remove deletes both.", "hint", wrap=True)
        self.storage_notice.setToolTip(STORAGE_NOTICE)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(label("INSTANCES", "section"))
        head.addWidget(self.instance_count, 1)
        head.addWidget(self.new_instance_button)
        actions = QHBoxLayout()
        actions.setSpacing(theme.SPACING)
        for widget in (self.clone_button, self.reset_button, self.close_button):
            actions.addWidget(widget)
        actions.addSpacing(6)
        actions.addWidget(self.remove_button)  # destructive action set apart
        actions.addStretch(1)
        actions.addWidget(self.more_button)
        layout = QVBoxLayout(self.instances_panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(theme.SPACING)
        layout.addLayout(head)
        layout.addWidget(self.instance_list, 1)
        layout.addWidget(label("SELECTED INSTANCE", "section"))
        layout.addLayout(actions)
        layout.addWidget(self.storage_notice)
        self.instances_panel.setMinimumWidth(max(210, self.instances_panel.minimumSizeHint().width()))
        self.instances_toggle.setChecked(True)
        self.instances_toggle.toggled.connect(lambda on: (self.instances_panel.setVisible(on), self._fit_side_panels()))

    def _build_preview_column(self) -> None:
        self.widget_title = label("No widget open", "title", clipped=True)
        self.path_label = label("", "muted", clipped=True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.auto_reload = button("Auto Reload", variant="toggle", checkable=True,
                                  tooltip="Reload every instance of a widget when a file in its folder changes (full page reload, not HMR).")
        self.auto_reload.setIcon(icons.icon("dot", theme.FAINT, theme.TONES["success"]))
        self.auto_reload.setChecked(True)
        self.auto_reload.toggled.connect(self._on_auto_toggled)
        self.reload_button = button("Reload", "refresh", shortcut="F5",
                                    tooltip="Reload the selected widget: full page reload of every instance of its folder "
                                            "(also while Auto Reload is off).")
        self.reload_button.clicked.connect(self.reload_now)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self.widget_title)
        title_row.addWidget(self.path_label, 1)
        title_row.addWidget(self.auto_reload)
        title_row.addWidget(self.reload_button)

        self.preview_toolbar = QWidget()
        flow = FlowLayout(self.preview_toolbar, h_spacing=12, v_spacing=theme.SPACING)
        flow.addWidget(captioned("Device", self.device_combo))
        flow.addWidget(captioned("Orientation", self.orientation_combo, "Orientation of the widget on the device. Stored per instance."))
        flow.addWidget(captioned("Widget size", self.size_combo, SIZE_TOOLTIP))
        flow.addWidget(captioned("Preview scale", self.scale_combo, SCALE_TOOLTIP))

        info_row = QHBoxLayout()
        info_row.setSpacing(12)
        pixels = label("Pixels", "hint")
        for widget, stretch in ((self.device_info, 3), (self.measured_label, 2), (pixels, 0), (self.mode_combo, 0)):
            info_row.addWidget(widget, stretch, Qt.AlignmentFlag.AlignVCenter)

        self.preview_header = QFrame()
        self.preview_header.setObjectName("previewHeader")
        head = QVBoxLayout(self.preview_header)
        head.setContentsMargins(12, 8, 12, 6)
        head.setSpacing(theme.SPACING)
        head.addLayout(title_row)
        head.addWidget(self.preview_toolbar)
        head.addLayout(info_row)

        self.problems = QPlainTextEdit()
        self.problems.setObjectName("problems")
        self.problems.setReadOnly(True)
        self.problems.setMaximumHeight(72)
        self.problems.setVisible(False)
        self.viewport_frame = ViewportFrame()
        self.viewport_frame.applied.connect(self._schedule_measure)

        self.preview_column = QWidget()
        column = QVBoxLayout(self.preview_column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.preview_header)
        column.addWidget(self.problems)
        column.addWidget(self.viewport_frame, 1)
        self.preview_column.setMinimumWidth(320)

    def _build_console_panel(self) -> None:
        self.console_view = QPlainTextEdit()
        self.console_view.setReadOnly(True)
        self.console_view.setProperty("role", "mono")
        self.console_view.setPlaceholderText("JavaScript console: widget logs, uncaught errors and bridge notices of the active instance.")
        self.console_count = label("", "hint")
        self.console_toggle = button(icon="chevron-down", variant="ghost", icon_only=True, shortcut="Ctrl+`",
                                     tooltip="Collapse the console: the preview gets its height")
        self.console_toggle.clicked.connect(lambda: self.set_console_collapsed(not self.console_collapsed))
        self.console_header = QFrame()
        self.console_header.setObjectName("panelHeader")
        head = QHBoxLayout(self.console_header)
        head.setContentsMargins(14, 2, 6, 2)
        head.setSpacing(8)
        head.addWidget(label("CONSOLE", "section"))
        head.addWidget(self.console_count, 1)
        head.addWidget(self.console_toggle)
        self.console_panel = QWidget()
        self.console_panel.setObjectName("panel")
        box = QVBoxLayout(self.console_panel)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        box.addWidget(self.console_header)
        box.addWidget(self.console_view, 1)
        self.console_collapsed = False
        self._console_height = CONSOLE_HEIGHT

    def set_console_collapsed(self, collapsed: bool) -> None:
        """Collapse the console to its header (the preview gets the room) or expand it to its last height."""
        if collapsed == self.console_collapsed:
            return
        sizes = self.center_splitter.sizes()
        if collapsed:
            self._console_height = max(sizes[1], 80)
            self.console_view.setVisible(False)
            header = self.console_header.sizeHint().height()
            self.console_panel.setMaximumHeight(header)
            self.center_splitter.setSizes([sum(sizes) - header, header])
        else:
            self.console_panel.setMaximumHeight(16777215)
            self.console_view.setVisible(True)
            total = sum(sizes)
            self.center_splitter.setSizes([max(1, total - self._console_height), self._console_height])
        self.console_collapsed = collapsed
        self.console_toggle.setIcon(icons.icon("chevron-up" if collapsed else "chevron-down"))
        self.console_toggle.setToolTip(("Expand the console (JavaScript log of the active instance)" if collapsed else
                                        "Collapse the console: the preview gets its height") + "  (Ctrl+`)")

    def _refresh_console_count(self) -> None:
        entries = self.console.entries
        errors = sum(1 for e in entries if e.level == "error")
        warnings = sum(1 for e in entries if e.level == "warning")
        parts = [f"{len(entries)} message(s)"] + ([f"{errors} error(s)"] if errors else []) + ([f"{warnings} warning(s)"] if warnings else [])
        self.console_count.setText("  ·  ".join(parts) if entries else "")
        theme.set_tone(self.console_count, "error" if errors else "warning" if warnings else "neutral")

    def _build_inspector(self) -> None:
        self.settings_panel = SettingsPanel()
        self.settings_panel.settingCommitted.connect(self._on_setting_committed)
        self.settings_panel.customStyleCommitted.connect(self.set_custom_style)
        self.personalization_panel = PersonalizationPanel()
        self.personalization_panel.valueCommitted.connect(self.set_personalization)
        self.personalization_panel.themeByScreenCommitted.connect(self.set_theme_by_screen)
        self.personalization_scroll = QScrollArea()
        self.personalization_scroll.setWidgetResizable(True)
        self.personalization_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.personalization_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(*[theme.PANEL_MARGIN] * 4)
        holder_layout.addWidget(self.personalization_panel)
        holder_layout.addStretch(1)
        self.personalization_scroll.setWidget(holder)
        self.inspector = QTabWidget()
        self.inspector.setObjectName("inspector")
        self.inspector.setDocumentMode(True)
        self.inspector.addTab(self.settings_panel, "Settings")  # text only: three tabs must fit the minimum panel width
        self.inspector.addTab(self.personalization_scroll, "Personalization")
        self.sensors_panel = SensorsPanel()
        self.sensors_scroll = QScrollArea()
        self.sensors_scroll.setWidgetResizable(True)
        self.sensors_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.sensors_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sensors_scroll.setWidget(self.sensors_panel)
        self.inspector.addTab(self.sensors_scroll, "Sensors")
        self.inspector.setTabToolTip(2, "Sensors: the data source of the Sensorsdataprovider for every instance - MOCK catalog (invented, "
                                        "editable) or LIVE sensors of this PC from the Olympus Hub.")
        bar = self.inspector.tabBar()
        bar.setUsesScrollButtons(False)  # never hidden tabs behind arrows: a too narrow panel elides the tab texts instead
        bar.setElideMode(Qt.TextElideMode.ElideRight)
        bar.setExpanding(False)
        self.inspector.setTabToolTip(0, "Settings of the active instance, generated from the widget's HTML metadata.")
        self.inspector.setTabToolTip(1, "Device / Screen personalization: host values a widget receives while its Custom Style is off.")
        # explicit: what a panel shows (texts per device) must never move the splitter / resize the preview
        self.inspector.setMinimumWidth(INSPECTOR_MIN_WIDTH)
        self.inspector_toggle.setChecked(True)
        self.inspector_toggle.toggled.connect(lambda on: (self.inspector.setVisible(on), self._fit_side_panels()))

    def _build_status_bar(self) -> None:
        self.status_icon = QLabel()
        self.status = label("", clipped=True)
        self.notice = button("Simulated runtime", "alert", variant="ghost", tool=True,
                             tooltip=RUNTIME_NOTICE + "\n\nClick for all simulation notes.")
        self.notice.setIcon(icons.icon("alert", theme.TONES["warning"]))
        self.notice.clicked.connect(self.show_simulation_notes)
        self.watch_label = label("", clipped=True)
        self.watch_label.setMinimumWidth(1)
        self.watch_label.setMaximumWidth(360)
        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        bar.addWidget(self.status_icon)
        bar.addWidget(self.status, 1)
        bar.addPermanentWidget(self.watch_label)
        bar.addPermanentWidget(self.notice)
        self.setStatusBar(bar)

    def show_simulation_notes(self) -> None:
        box = QMessageBox(QMessageBox.Icon.Information, "What the preview simulates",
                          "\n\n".join((RUNTIME_NOTICE, VIEWPORT_NOTICE, STORAGE_NOTICE)), QMessageBox.StandardButton.Close, self)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.open()
        self._notes_box = box

    # ---- window size: the layout never resizes the window (see _build_ui) ----------------------------------
    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if not self._minimum_fixed:  # once: the layout minimum of the freshly built window becomes the explicit minimum size
            self._minimum_fixed = True
            hint = self.centralWidget().minimumSizeHint()
            available = self.screen().availableGeometry().size()
            minimum = QSize(max(900, hint.width()), max(600, hint.height())).boundedTo(available)
            self.setMinimumSize(minimum)
            self.resize(self.size().expandedTo(minimum).boundedTo(available))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.Resize:
            if watched is getattr(self, "top_bar", None):
                self._fit_top_bar()
            elif watched is getattr(self, "splitter", None):
                self._fit_side_panels()
            elif watched is getattr(self, "center_splitter", None):
                self._fit_console()
        return super().eventFilter(watched, event)

    def _remember_console_height(self, *_args) -> None:
        if not self._fitting and not self.console_collapsed:
            self._console_height = self.center_splitter.sizes()[1]

    def _fit_console(self) -> None:
        """The console keeps its height; a low window takes it down to CONSOLE_MIN_HEIGHT before the preview gets less than
        PREVIEW_COMFORT_HEIGHT. A taller window gives the extra height to the preview."""
        if self.console_collapsed:
            return
        total = self.center_splitter.height() - self.center_splitter.handleWidth()
        if total <= 0:
            return
        console = max(CONSOLE_MIN_HEIGHT, min(self._console_height, total - PREVIEW_COMFORT_HEIGHT))
        self._fitting = True
        try:
            self.center_splitter.setSizes([max(1, total - console), console])
        finally:
            self._fitting = False

    def _remember_side_widths(self, *_args) -> None:
        if self._fitting:
            return
        sizes = self.splitter.sizes()
        if self.instances_panel.isVisible():
            self._side_widths[0] = sizes[0]
        if self.inspector.isVisible():
            self._side_widths[1] = sizes[2]

    def _fit_side_panels(self) -> None:
        """The side panels keep the width the user gave them; only when the preview would get less than PREVIEW_COMFORT_WIDTH they shrink
        (proportionally, down to their minimum). A larger window gives the extra width to the preview only."""
        if not hasattr(self, "_side_widths"):
            return
        total = self.splitter.width() - self.splitter.handleWidth() * 2
        if total <= 0:
            return
        shown = [self.instances_panel.isVisible() or not self.isVisible() and self.instances_toggle.isChecked(),
                 self.inspector.isVisible() or not self.isVisible() and self.inspector_toggle.isChecked()]
        widths = [w if on else 0 for w, on in zip(self._side_widths, shown)]
        minimums = [self.instances_panel.minimumWidth() if shown[0] else 0, self.inspector.minimumWidth() if shown[1] else 0]
        excess = sum(widths) + PREVIEW_COMFORT_WIDTH - total
        if excess > 0:
            room = [w - m for w, m in zip(widths, minimums)]
            spare = sum(room)
            if spare > 0:
                widths = [w - round(excess * r / spare) if excess < spare else m for w, r, m in zip(widths, room, minimums)]
        self._fitting = True
        try:
            self.splitter.setSizes([widths[0], max(1, total - sum(widths)), widths[1]])
        finally:
            self._fitting = False

    def _on_library_visibility(self, visible: bool) -> None:
        """Opening the Widget Library takes width from the central area: make room once, never shrink, never beyond the screen."""
        self.library_button.setChecked(visible)
        if not (visible and self._minimum_fixed):
            return
        self.resizeDocks([self.library_dock], [360], Qt.Orientation.Horizontal)  # not the panel's (huge) size hint
        need = self.centralWidget().minimumSizeHint().width() + 360 + 16
        target = min(max(self.width(), need), self.screen().availableGeometry().width())
        if target > self.width():
            self.resize(target, self.height())

    # ---- device viewport (D6) ---------------------------------------------------------------------------
    def _build_device_bar(self) -> None:
        self.device_combo, self.mode_combo, self.scale_combo = QComboBox(), QComboBox(), QComboBox()
        self.size_combo, self.orientation_combo = SegmentedControl(), SegmentedControl()
        for combo, characters in ((self.device_combo, 14), (self.mode_combo, 8), (self.scale_combo, 10)):
            narrow_combo(combo, characters)
        self.device_combo.addItem("Free size (no device)", None)
        self.device_combo.setItemData(0, "No device: the page fills the preview area.", Qt.ItemDataRole.ToolTipRole)
        for profile in self.profiles:
            self.device_combo.addItem(f"{profile.name} ({profile.kind})", profile.id)
            self.device_combo.setItemData(self.device_combo.count() - 1, f"{profile.name}\nid: {profile.id}\nkind: {profile.kind}",
                                          Qt.ItemDataRole.ToolTipRole)
        self.device_combo.setToolTip("The device the widget is shown on (shared by every instance).")
        self.mode_combo.addItem("Nominal", MODE_NOMINAL)
        self.mode_combo.addItem("Physical (Experimental)", MODE_PHYSICAL)
        self.mode_combo.setItemData(0, "Nominal (iCUE widgetSizePx): the page size iCUE computes.", Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setItemData(1, "Physical pixels of the device screen (EXPERIMENTAL).", Qt.ItemDataRole.ToolTipRole)
        self.mode_combo.setToolTip("Which pixels define the page size: Nominal (iCUE widgetSizePx, default) or Physical (EXPERIMENTAL).")
        self._fill_scales(None)
        self.device_info = label("Free size: the page fills the preview area.", "hint", clipped=True)
        self.device_info.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.device_info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.measured_label = label("", "hint", clipped=True)
        self.device_combo.activated.connect(lambda _i: self.select_device(self.device_combo.currentData()))
        for combo in (self.size_combo, self.orientation_combo, self.mode_combo):
            combo.activated.connect(lambda _i: self._on_device_choice())
        self.scale_combo.activated.connect(lambda _i: self.viewport_frame.set_scale(self.scale_combo.currentData()))
        self._fill_choices(None)

    def _fill_scales(self, viewport: Viewport | None) -> None:
        """Native 1:1, Visual Fit, then only the zooms at which the page still measures exactly the viewport (see viewport.exact_zooms)."""
        keep = self.scale_combo.currentData() if self.scale_combo.count() else NATIVE
        self.scale_combo.blockSignals(True)
        self.scale_combo.clear()
        self.scale_combo.addItem("Native 1:1", NATIVE)
        self.scale_combo.addItem("Visual Fit (Experimental)", FIT)
        if viewport is not None:
            for zoom in exact_zooms(viewport.width, viewport.height):
                if zoom != 1.0:  # 100% is Native 1:1
                    self.scale_combo.addItem(f"{zoom * 100:.0f}% zoom (Experimental)", zoom)
        self.scale_combo.setItemData(0, "The page at exactly its viewport size; scrolls when larger than the preview.", Qt.ItemDataRole.ToolTipRole)
        self.scale_combo.setItemData(1, "Zoomed to fit the preview. EXPERIMENTAL: changes devicePixelRatio; do not debug sizes with it.",
                                     Qt.ItemDataRole.ToolTipRole)
        index = self.scale_combo.findData(keep)
        self.scale_combo.setCurrentIndex(max(0, index))
        self.scale_combo.setEnabled(viewport is not None)
        self.scale_combo.blockSignals(False)
        self.viewport_frame.set_scale(self.scale_combo.currentData()) if hasattr(self, "viewport_frame") else None

    def _fill_choices(self, profile: DeviceProfile | None) -> None:
        """Size S..XL and both orientations are always shown; what the device does not offer is disabled (with the reason)."""
        for combo in (self.size_combo, self.orientation_combo):
            combo.blockSignals(True)
            combo.clear()
        offered = profile is not None and bool(profile.orientations())
        sizes = profile.supported_sizes() if offered else []
        why = "Choose a device first." if profile is None else f"{profile.name} has one fixed widget size."
        for size, text in SIZE_LABEL.items():
            self.size_combo.addItem(text, size)
            index = self.size_combo.count() - 1
            self.size_combo.setItemEnabled(index, size in sizes)
            self.size_combo.setItemToolTip(index, f"Widget size {text}" if size in sizes else
                                           (f"{text} is not offered by {profile.name}." if offered else why))
        for orientation, text in ((LANDSCAPE, "Landscape"), (PORTRAIT, "Portrait")):
            self.orientation_combo.addItem(text, orientation)
            index = self.orientation_combo.count() - 1
            self.orientation_combo.setItemEnabled(index, offered and orientation in profile.orientations())
            self.orientation_combo.setItemToolTip(index, text if offered else why)
        for combo in (self.size_combo, self.orientation_combo):
            combo.setEnabled(offered)
            combo.blockSignals(False)
        self.mode_combo.setEnabled(offered)

    def _size_tooltips(self, profile: DeviceProfile, orientation: str | None) -> None:
        """Each offered size says the page size it gives (so Widget size is never mistaken for the preview scale)."""
        for index in range(self.size_combo.count()):
            size = self.size_combo.itemData(index)
            if self.size_combo.isItemEnabled(index):
                try:
                    viewport = compute_viewport(profile, size, orientation, self.device_mode)
                except ValueError:
                    continue
                self.size_combo.setItemToolTip(index, f"Widget size {SIZE_LABEL[size]}: page {viewport.width} x {viewport.height} px "
                                                      f"({orientation}). Stored per instance.")
    # ---- per-instance size and orientation (E3) ---------------------------------------------------------
    def choice_of(self, instance: WidgetInstance | None) -> tuple[str | None, str | None, str]:
        """(size, orientation, notice) the instance shows on the CURRENT device. The instance keeps what it stored; when the device does not
        offer it, the device default is shown and the notice says so (nothing stored is changed or lost)."""
        profile = self.device
        if profile is None or not profile.orientations():
            return None, None, ""
        sizes, orientations = profile.supported_sizes(), profile.orientations()
        default_size = "SizeM" if "SizeM" in sizes else sizes[0]
        stored_size, stored_orientation = (instance.size, instance.orientation) if instance is not None else (self._default_size, self._default_orientation)
        notes = []
        size = stored_size if stored_size in sizes else default_size
        if stored_size and stored_size not in sizes:
            notes.append(f"stored size {stored_size} is not offered by {profile.name}: showing {size}")
        orientation = stored_orientation if stored_orientation in orientations else orientations[0]
        if stored_orientation and stored_orientation not in orientations:
            notes.append(f"stored orientation {stored_orientation} is not offered by {profile.name}: showing {orientation}")
        return size, orientation, "; ".join(notes)

    def size_notice(self, instance: WidgetInstance | None = None) -> str:
        return self.choice_of(instance or self._instance)[2]

    @property
    def device_size(self) -> str | None:
        return self.choice_of(self._instance)[0]

    @property
    def device_orientation(self) -> str | None:
        return self.choice_of(self._instance)[1]

    def instance_viewport(self, instance: WidgetInstance | None) -> Viewport | None:
        """The page size of ONE instance (device x its own size/orientation x the shared mode); None without a device."""
        if self.device is None:
            return None
        size, orientation, _ = self.choice_of(instance)
        return compute_viewport(self.device, size, orientation, self.device_mode)

    @property
    def viewport(self) -> Viewport | None:
        """The viewport of the ACTIVE instance (or of the last explicit choice while nothing is open)."""
        return self.instance_viewport(self._instance)

    def _seed_instance(self, instance: WidgetInstance) -> None:
        """A new instance starts with the last explicit choice (an imported one keeps the size stored in iCUE); an existing one is never changed."""
        if self.device is not None and self.device.orientations():
            if instance.size is None:
                instance.size = self._default_size
            if instance.orientation is None:
                instance.orientation = self._default_orientation

    def _apply_viewports(self) -> None:
        """Every instance's view gets ITS OWN viewport; only the pages whose size really changes are resized (no reload)."""
        if self.device is None:
            self.viewport_frame.set_free()
            return
        for runtime in self.runtimes.values():
            viewport = self.instance_viewport(runtime.instance)
            self.viewport_frame.set_viewport(viewport.width, viewport.height, view=runtime.view)

    def _refresh_device_controls(self) -> None:
        """Device bar from the device and the ACTIVE instance (size/orientation combos, scale choices, info text with the size notice)."""
        profile = self.device
        if profile is None:
            self._fill_choices(None)
            self.device_combo.setCurrentIndex(0)
            self.mode_combo.setCurrentIndex(0)
            self._fill_scales(None)
            self.device_info.setText("Free size: the page fills the preview area.")
            theme.set_tone(self.device_info, "neutral")
            return
        size, orientation, notice = self.choice_of(self._instance)
        viewport = self.viewport
        self._fill_choices(profile)
        self.device_combo.setCurrentIndex(self.device_combo.findData(profile.id))
        if profile.orientations():
            self.size_combo.setCurrentIndex(self.size_combo.findData(size))
            self.orientation_combo.setCurrentIndex(self.orientation_combo.findData(orientation))
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(self.device_mode))
        self._fill_scales(viewport)
        if profile.orientations():
            self._size_tooltips(profile, orientation)
        self.device_info.setText(viewport.description + (" | EXPERIMENTAL" if viewport.experimental else "") + (f" | NOTE: {notice}" if notice else ""))
        theme.set_tone(self.device_info, "warning" if notice or viewport.experimental else "neutral")

    def select_device(self, device_id: str | None, size: str | None = None, orientation: str | None = None,
                      mode: str = MODE_NOMINAL) -> Viewport | None:
        """Choose the shared device and mode, and size / orientation of the ACTIVE instance (None = the device default). Other instances keep
        their own size. Nothing but page sizes changes: no reload, no instance data change. None = free size. ValueError (state untouched)
        for a choice the device does not offer."""
        if device_id is None:
            self.device, self.device_mode, self._default_size, self._default_orientation = None, MODE_NOMINAL, None, None
            self.viewport_frame.set_free()
            self._refresh_device_controls()
            self._apply_personalization()
            self.library_panel.set_device(None)
            self._changed()
            return None
        profile = next((p for p in self.profiles if p.id == device_id), None)
        if profile is None:
            raise ValueError(f"unknown device profile {device_id!r}")
        explicit = size is not None or orientation is not None  # only an explicit size/orientation is stored in the instance
        if profile.orientations():
            sizes = profile.supported_sizes()
            size = size or ("SizeM" if "SizeM" in sizes else sizes[0])
            orientation = orientation or profile.orientations()[0]
        elif size or orientation:
            raise ValueError(f"{profile.name} has one fixed widget size")
        compute_viewport(profile, size, orientation, mode)  # validates before anything changes
        self.device, self.device_mode = profile, mode
        active = self.active
        if profile.orientations() and explicit:  # choosing only the DEVICE never overwrites a stored size (shown with a notice if not offered)
            self._default_size, self._default_orientation = size, orientation
            if active is not None:
                active.instance.size, active.instance.orientation = size, orientation
        self._apply_viewports()
        self._refresh_device_controls()
        self._apply_personalization()  # the active personalization is per device
        self.library_panel.set_device(self.device)
        self._changed()
        return self.viewport

    def _on_device_choice(self) -> None:
        if self.device is None:
            return
        try:
            self.select_device(self.device.id, self.size_combo.currentData(), self.orientation_combo.currentData(),
                               self.mode_combo.currentData())
        except ValueError as exc:
            self.device_info.setText(f"Unsupported: {exc}")
            theme.set_tone(self.device_info, "error")

    def _schedule_measure(self) -> None:
        QTimer.singleShot(300, self.refresh_measurement)

    def refresh_measurement(self) -> None:
        """Ask the page what it measures (innerWidth x innerHeight) and show it next to the expected viewport."""
        if self._page is None or not (self.current and self.current.ok):
            return

        def show(text) -> None:  # runJavaScript() returns "" for arrays in PySide6 6.9.3: the value travels as JSON
            try:
                value = json.loads(text)
            except (TypeError, ValueError):
                return
            width, height, ratio = int(value[0]), int(value[1]), float(value[2])
            expected = self.viewport.size if self.viewport else None
            ok = expected is None or (width, height) == expected
            zoom, display = self.viewport_frame.zoom, self.screen().devicePixelRatio()
            dpr_ok = abs(ratio - display * zoom) < 0.01
            text = (f"page: {width}x{height} (dpr {ratio:g}, display scaling {display * 100:.0f}%, zoom {zoom:.3g})"
                    + ("" if ok else f" != expected {expected[0]}x{expected[1]}")
                    + ("" if dpr_ok else f" dpr != expected {display * zoom:g}")
                    + (" | EXPERIMENTAL scale: dpr/size are not native" if zoom != 1 else ""))
            self.measured_label.setText(text)
            good = ok and dpr_ok and zoom == 1
            theme.set_tone(self.measured_label, "success" if good else "error")

        self._page.runJavaScript("JSON.stringify([window.innerWidth, window.innerHeight, window.devicePixelRatio])", show)

    def _show_problems(self, check: WidgetCheck | None) -> None:
        lines: list[str] = []
        if check:
            lines += [f"ERROR: {m}" for m in check.errors]
            lines += [f"WARNING: {m}" for m in check.warnings]
        self.problems.setPlainText("\n".join(lines))
        self.problems.setVisible(bool(lines))

    # ---- the instances (D7b) -------------------------------------------------------------------------------
    @property
    def active(self) -> InstanceRuntime | None:
        return self.runtimes.get(self._active_id) if self._active_id else None

    # The single-preview API of D1-D7a, now answered by the ACTIVE instance (None / idle values when nothing is shown).
    @property
    def view(self):
        return self.active.view if self.active else None

    @property
    def _page(self):
        return self.active.page if self.active else None

    @property
    def _profile(self):
        return self.active.profile if self.active else None

    @property
    def bridge(self) -> RuntimeBridge | None:
        return self.active.bridge if self.active else None

    @property
    def console(self) -> ConsoleLog:
        return self.active.console if self.active else self._idle_console

    @property
    def current(self) -> WidgetCheck | None:
        return self.active.check if self.active else self._idle_current

    @property
    def state(self) -> State:
        return self.active.state if self.active else self._idle_state

    @property
    def reload_count(self) -> int:
        return self.active.reload_count if self.active else 0

    @property
    def _reloading(self) -> bool:
        return self.active.reloading if self.active else False

    @property
    def _instance(self) -> WidgetInstance | None:
        return self.active.instance if self.active else None

    @property
    def _instance_id(self) -> str | None:
        return self._active_id

    @property
    def watcher(self) -> FolderWatcher:
        active = self.active
        return self._watchers.get(self._key(active.check.folder), self._null_watcher) if active else self._null_watcher

    # ---- actions ----------------------------------------------------------
    def choose_folder(self) -> None:
        start = str(self.current.folder) if self.current else ""
        folder = QFileDialog.getExistingDirectory(self, "Select widget folder", start)
        if folder:
            self.open_folder(folder)

    def load_folder(self, folder: str | Path, instance: WidgetInstance | None = None, follow_source: bool | None = None) -> bool:
        """Validate a widget folder and show it as an instance. Returns False if it was rejected.

        Without `instance` a NEW instance (random id, defaults) gets its own WebEngine stack and becomes the active one; the other
        instances keep running. With one, its stored data is used as it is (restore: no default is added) and every live reload
        keeps using it; an instance that is already running is rebuilt (new page load), as the single preview always was."""
        check = check_widget_folder(folder)
        self.path_label.setText(str(check.folder))
        self.widget_title.setText(check.widget_name or check.folder.name if check.ok else "Invalid widget")
        self._show_problems(check)
        self.console_view.clear()
        self.console_count.setText("")
        if not check.ok:
            self._show_invalid(check, instance, follow_source)
            return False
        self._stop_invalid_watch()
        if instance is not None and instance.id in self.runtimes:
            self._close_runtime(instance.id)
        given = instance is not None
        instance = instance if given else new_instance_for(check)
        follows = (not given) if follow_source is None else follow_source
        runtime = self._create_runtime(check, instance, follows)
        runtime.install_bridge(check, refresh_defaults=given and follows, personalization=self._active_personalization)
        self._register_instance(runtime)
        self._activate(runtime)
        self._set_state(State.LOADING, f"Loading {check.widget_name}...", runtime)
        runtime.view.load(QUrl(check.index_url))
        self._changed()
        return True

    def _show_invalid(self, check: WidgetCheck, instance: WidgetInstance | None, follow_source: bool | None) -> None:
        """A rejected folder: nothing is shown (the instances keep running in the background); fixing the folder opens it."""
        self._active_id = None
        self.viewport_frame.show_view(None)
        self._idle_current = check
        self.settings_panel.clear("Invalid widget: no settings.")
        self._stop_invalid_watch()
        self._invalid = (check.folder, instance, follow_source)
        self._invalid_watcher = FolderWatcher(self)
        self._invalid_watcher.changed.connect(self._retry_invalid)
        self._invalid_watcher.start(check.folder)
        self._invalid_watcher.set_enabled(self.auto_reload.isChecked())
        self._refresh_instance_list()
        self._set_watch("Watching the invalid folder", "neutral")
        self._set_state(State.INVALID, f"Invalid widget: {check.errors[0]}")

    def _stop_invalid_watch(self) -> None:
        if self._invalid_watcher is not None:
            self._invalid_watcher.stop()
            self._invalid_watcher.deleteLater()
        self._invalid_watcher, self._invalid = None, None

    def _retry_invalid(self, _paths: list | None = None) -> bool:
        if self._invalid is None:
            return False
        folder, instance, follow = self._invalid
        check = check_widget_folder(folder)
        self._show_problems(check)
        if not check.ok:
            self._set_watch(f"Invalid widget, kept previous page: {check.errors[0]}", "error")
            return False
        return self.load_folder(folder, instance, follow)

    def _create_runtime(self, check: WidgetCheck, instance: WidgetInstance, follows_source: bool) -> InstanceRuntime:
        runtime = InstanceRuntime(check, instance, self.storage, follows_source=follows_source, parent=self)
        runtime.loadStarted.connect(self._on_load_started)
        runtime.loadProgress.connect(self._on_load_progress)
        runtime.loadFinished.connect(self._on_load_finished)
        runtime.console.added.connect(lambda entry, rt=runtime: self._on_console_added(rt, entry))
        self.runtimes[instance.id] = runtime
        self._seed_instance(instance)
        self.viewport_frame.add_view(runtime.view)
        if self.device is not None:
            viewport = self.instance_viewport(instance)
            self.viewport_frame.set_viewport(viewport.width, viewport.height, view=runtime.view)
        self._watch(check.folder)
        return runtime

    def _activate(self, runtime: InstanceRuntime) -> None:
        """Show `runtime`: its view, console, settings, personalization and status. Nothing is reloaded or changed."""
        self._active_id = runtime.instance.id
        self._touch(runtime.instance.id)
        self.viewport_frame.show_view(runtime.view)
        self._refresh_device_controls()  # size / orientation / notice of THIS instance
        self.path_label.setText(str(runtime.check.folder))
        self.widget_title.setText(runtime.check.widget_name or runtime.check.folder.name)
        self._show_problems(runtime.check)
        self.console_view.setPlainText("\n".join(e.format() for e in runtime.console.entries))
        self._refresh_console_count()
        if runtime.bridge is not None:
            self.settings_panel.set_settings(runtime.bridge.settings_spec, runtime.instance)
        self._refresh_instance_list()
        self._show_personalization()
        self._set_watch(self._watching_text(), "neutral")
        if runtime.state is not None:
            self._set_state(runtime.state, runtime.status_text, runtime)

    def select_instance(self, instance_id: str | None) -> bool:
        """Make a running instance the one whose settings are edited and whose page is shown (no reload)."""
        runtime = self.runtimes.get(instance_id) if instance_id else None
        if runtime is None:
            return False
        if self._active_id != runtime.instance.id:
            self._activate(runtime)
            self._changed()
        return True

    # ---- watching and live reload -----------------------------------------
    @staticmethod
    def _key(folder: str | Path) -> str:
        return str(Path(folder).resolve())

    def _watch(self, folder: str | Path) -> FolderWatcher:
        """ONE watcher per source folder, however many instances use it: an edit then reloads each of them once."""
        key = self._key(folder)
        watcher = self._watchers.get(key)
        if watcher is None:
            watcher = FolderWatcher(self)
            watcher.changed.connect(lambda paths, k=key: self._on_source_changed(k, paths))
            self._watchers[key] = watcher
            watcher.start(folder)
            watcher.set_enabled(self.auto_reload.isChecked())
        return watcher

    def _unwatch(self, folder: str | Path) -> None:
        key = self._key(folder)
        if any(self._key(r.check.folder) == key for r in self.runtimes.values()):
            return
        watcher = self._watchers.pop(key, None)
        if watcher is not None:
            watcher.stop()
            watcher.deleteLater()

    def _set_watch(self, text: str, tone: str) -> None:
        self.watch_label.setText(text)
        theme.set_tone(self.watch_label, tone)

    def _watching_text(self) -> str:
        if not self.auto_reload.isChecked():
            return f"Auto Reload OFF | reloads: {self.reload_count}"
        return f"Watching | reloads: {self.reload_count}"

    def _on_auto_toggled(self, on: bool) -> None:
        for watcher in [*self._watchers.values(), *([self._invalid_watcher] if self._invalid_watcher else [])]:
            watcher.set_enabled(on)
        if not self._reloading:
            self._set_watch(self._watching_text(), "neutral")

    def _on_source_changed(self, key: str, paths: list) -> None:
        check = None
        for runtime in [r for r in self.runtimes.values() if self._key(r.check.folder) == key]:
            check = check or check_widget_folder(runtime.check.folder)  # validated once for every instance of the source
            self.reload(paths, runtime, check)

    def reload_now(self) -> bool:
        """Manual full-page reload of every instance of the active source (also works while Auto Reload is OFF)."""
        active = self.active
        if active is None:
            return self._retry_invalid()
        self.watcher.rebaseline()
        key, active_ok = self._key(active.check.folder), False
        for runtime in [r for r in self.runtimes.values() if self._key(r.check.folder) == key]:
            reloaded = self.reload([], runtime)
            active_ok = reloaded if runtime is active else active_ok
        return active_ok

    def reload(self, paths: list[str], runtime: InstanceRuntime | None = None, check: WidgetCheck | None = None) -> bool:
        """Full-page reload of ONE instance with the SAME view, page and profile. Not HMR.

        Order: re-validate -> new bridge (same instance: user-edited values kept, new source defaults applied) -> hard reload, so the new
        document starts with fresh scripts and a fresh web channel, as on a first load."""
        runtime = runtime or self.active
        if runtime is None or not runtime.alive:
            return False
        is_active = runtime is self.active
        check = check or check_widget_folder(runtime.check.folder)
        if is_active:
            self._show_problems(check)
        if not check.ok:
            runtime.console.notice(f"reload skipped, kept the previous page: {check.errors[0]}", "warning")
            if is_active:
                self._set_watch(f"Invalid widget, kept previous page: {check.errors[0]}", "error")
            return False
        was_loaded = runtime.state is State.LOADED
        runtime.console.notice(f"--- reload #{runtime.reload_count + 1}: {', '.join(paths) or 'manual'} ---", "info")
        bridge = runtime.install_bridge(check, runtime.follows_source, self._active_personalization)
        if is_active:
            self.path_label.setText(str(check.folder))
        self._report_refresh(runtime, bridge.refresh_report)
        self._register_instance(runtime)  # the same instance object; the panel shows the new metadata
        runtime.reloading = True
        if is_active:
            self._set_watch(f"Reloading... | reloads: {runtime.reload_count}", "info")
        self._set_state(State.LOADING, f"Reloading {check.widget_name}...", runtime)
        if was_loaded:
            runtime.page.triggerAction(QWebEnginePage.WebAction.ReloadAndBypassCache)
        else:  # blank/failed page: there is nothing to reload, load it
            runtime.view.load(QUrl(check.index_url))
        return True

    # ---- settings and instances ---------------------------------------------------------------------
    def _register_instance(self, runtime: InstanceRuntime) -> None:
        """The runtime's instance goes into the store; the panels are rebuilt when it is the active one."""
        runtime.instance.source = str(runtime.check.folder)
        self.store.replace(runtime.instance)
        if runtime is self.active:
            self.settings_panel.set_settings(runtime.bridge.settings_spec, runtime.instance)
            self._show_personalization()
        self._refresh_instance_list()

    def _report_refresh(self, runtime: InstanceRuntime, report: dict | None) -> None:
        """Say in the console which values a live reload took from the source and which are the user's."""
        if not report:
            return
        for label, keys in (("new defaults from source", report["defaults_changed"]), ("added by source", report["added"]),
                            ("no longer declared, dropped", report["dropped"]), ("user-edited, kept", report["user_values_kept"])):
            if keys:
                runtime.console.notice(f"live reload - {label}: {', '.join(keys)}", "info")

    def _refresh_instance_list(self) -> None:
        self.instance_list.blockSignals(True)
        self.instance_list.clear()
        for item in self.store.all():
            running = item.id in self.runtimes
            entry = QListWidgetItem(icons.icon("dot", theme.TONES["success"] if running else theme.FAINT),
                                    f"{item.name or item.type_id}\n{item.id[:8]}  ·  {'running' if running else 'closed'}")
            entry.setData(Qt.ItemDataRole.UserRole, item.id)
            entry.setToolTip(f"{'Running' if running else 'Closed (select it, then Restore or Merge to open it)'}\nid: {item.id}\n"
                             f"source: {item.source}\nstorage: {storage_name(item.unique_id(), item.type_id)}")
            self.instance_list.addItem(entry)
            if item.id == self._active_id:
                self.instance_list.setCurrentItem(entry)
        self.instance_list.blockSignals(False)
        running = sum(1 for i in self.store.all() if i.id in self.runtimes)
        self.instance_count.setText(f"{running}/{len(self.store)} running" if len(self.store) else "")
        for widget in (self.clone_button, self.close_button, self.reset_button, self.remove_button, self.restore_action, self.merge_action):
            widget.setEnabled(len(self.store) > 0)
        self.new_instance_button.setEnabled(self.active is not None)

    def _selected_id(self) -> str | None:
        item = self.instance_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_list_selected(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self.select_instance(item.data(Qt.ItemDataRole.UserRole))

    def _on_setting_committed(self, key: str, value) -> None:
        """A user edit of the ACTIVE instance: instance data -> its runtime bridge -> dataChanged (onDataUpdated). No page reload,
        no other instance is touched. A value equal to the stored one is ignored, like HtmlSettingsImpl::setValue."""
        if self.bridge is None or not self.bridge.instance.differs(key, value):
            return
        self.bridge.set_properties({key: value})
        self.settings_panel.show_effective(self.bridge.effective_data())
        self._changed()

    # ---- widget personalization (D4) ------------------------------------------------------------
    def _personalization_key(self) -> str:
        return self.device.id if self.device is not None else FREE_KEY

    def _active_personalization(self) -> PersonalizationValues:
        return self.personalization.active(self._personalization_key())

    def _show_personalization(self) -> None:
        """Panel texts and editors from the current state: what the widget receives against what the instance stores."""
        self.personalization_panel.show_model(self.personalization, self._personalization_key(),
                                              self.device.name if self.device else "")
        if self.bridge is not None:
            self.settings_panel.show_effective(self.bridge.effective_data())

    def _apply_personalization(self) -> bool:
        """The personalization (or the device that selects it) changed. Device and screen are window state shared by every
        instance, so EVERY running instance gets its new EFFECTIVE settings (only when they differ, no page reload; the
        instances are not touched). Returns True when the active instance was told."""
        sent = False
        for runtime in self.runtimes.values():
            changed = runtime.bridge.refresh_effective() if runtime.bridge is not None else False
            sent = sent or (changed and runtime is self.active)
        self._show_personalization()
        return sent

    def set_custom_style(self, enabled: bool) -> bool:
        """The Custom Style switch of the Widget Personalization panel. It exists only for an instance that has any of the
        six personalization keys (iCUE: hasWidgetPersonalizationSettings). Writes the instance (raw) and tells the widget."""
        if self.bridge is None or not has_personalization(self.bridge.instance.data):
            return False
        if self.bridge.instance.differs(CUSTOM_STYLE_ENABLED_KEY, bool(enabled)):
            self.bridge.set_properties({CUSTOM_STYLE_ENABLED_KEY: bool(enabled)})
            self._changed()
        self._show_personalization()
        return True

    def set_personalization(self, scope: str, field_name: str, value) -> bool:
        """Edit a Device or Screen personalization value (WidgetDevTool state). Returns True when the widget was told."""
        self.personalization.set_value(scope, self._personalization_key(), field_name, value)
        return self._apply_personalization()

    def set_theme_by_screen(self, enabled: bool) -> bool:
        self.personalization.set_theme_by_screen(self._personalization_key(), enabled)
        return self._apply_personalization()

    def open_library_widget(self, folder: str | Path) -> bool:
        """Open a widget chosen in the library (same rules as Open Folder / the command line: see open_folder)."""
        return self.open_folder(folder)

    # ---- opening a folder never duplicates an instance (A1) ---------------------------------------------
    @staticmethod
    def _canonical(folder: str | Path) -> str:
        return os.path.normcase(os.path.realpath(str(folder)))

    def _touch(self, instance_id: str) -> None:
        if instance_id in self._recent:
            self._recent.remove(instance_id)
        self._recent.append(instance_id)

    def instances_of_source(self, folder: str | Path) -> list[WidgetInstance]:
        """Instances of the store whose source is this folder (canonical path), in store order. Running or closed."""
        key = self._canonical(folder)
        return [i for i in self.store.all() if i.source and self._canonical(i.source) == key]

    def pick_instance(self, folder: str | Path) -> WidgetInstance | None:
        """The instance to show for a folder: the most recently shown one; else the last one in the store."""
        found = self.instances_of_source(folder)
        for instance_id in reversed(self._recent):
            for instance in found:
                if instance.id == instance_id:
                    return instance
        return found[-1] if found else None

    def open_folder(self, folder: str | Path) -> bool:
        """Open a widget folder from the UI, the library or the command line WITHOUT creating a duplicate.

        A folder that already has instance(s) shows one of them: a running instance is just selected (no reload); a closed one
        is opened again with the same id (so the same storage), settings and device state (it then follows its source like a live
        reload: user-edited values kept). Several instances of one folder (Add/Clone) are never merged or deleted: the most recently
        shown is used, the instance list picks another. Only a folder with no instance gets a new one; Add/Clone create more."""
        existing = self.pick_instance(folder)
        if existing is None:
            return self.load_folder(folder)
        if existing.id in self.runtimes:
            return self.select_instance(existing.id)
        return self.load_folder(folder, instance=existing, follow_source=True)

    # ---- Reset Storage (A1) ---------------------------------------------------------------------------
    def _ask_reset(self, instance: WidgetInstance) -> bool:
        answer = QMessageBox.question(
            self, "Reset Storage",
            f"Delete the localStorage and cache of {instance.name or instance.type_id} ({instance.id[:8]})?\n\n"
            "Its settings and id are kept. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _on_reset_clicked(self) -> None:
        instance = self.store.get(self._active_id or self._selected_id() or "")
        if instance is not None and self._ask_reset(instance):
            self.reset_storage(instance.id)

    def reset_storage(self, instance_id: str | None = None, timeout: float = 8.0) -> bool:
        """Delete the browser storage (localStorage, cache) of ONE instance and start it again with the same id.

        Order: close its WebEngine stack (page, profile) -> delete `<data dir>/storage/<name>` (retried while WebEngine still holds
        files) -> reopen the same instance object. The InstanceStore is not touched, no other instance is touched, and only the tool
        storage is addressed (StorageLayout refuses anything else). Returns False when the folder could not be deleted in time
        (the instance is reopened anyway; nothing is queued for later deletion, so a running instance can never lose storage unasked)."""
        instance = self.store.get(instance_id or self._active_id or "")
        if instance is None:
            return False
        runtime = self.runtimes.get(instance.id)
        previous_active = self._active_id
        follows = runtime.follows_source if runtime is not None else True
        if runtime is not None:
            self._close_runtime(instance.id)
        deadline = time.monotonic() + timeout
        deleted = self.storage.delete(instance.id, instance.type_id)
        while not deleted and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.05)
            deleted = self.storage.delete(instance.id, instance.type_id)
        if runtime is not None:
            if instance.source and Path(instance.source).is_dir():
                self.load_folder(instance.source, instance=instance, follow_source=follows)
                if previous_active != instance.id and previous_active in self.runtimes:
                    self.select_instance(previous_active)
            else:
                self._after_removal()
        self._changed()
        return deleted
    def new_instance(self) -> bool:
        """Add: another instance (own id, default settings, own storage) of the active widget. The others keep running."""
        active = self.active
        if active is None:
            return False
        size, orientation, _ = self.choice_of(active.instance)
        if size is not None:  # Add starts like the active instance (then has its own size)
            self._default_size, self._default_orientation = size, orientation
        return self.load_folder(active.check.folder)

    def clone_instance(self, instance_id: str | None = None) -> bool:
        """Clone: same settings, NEW id (and therefore new storage; localStorage is not copied, as in iCUE)."""
        source = self.store.get(instance_id or self._active_id or "")
        if source is None:
            return False
        if not source.source or not Path(source.source).is_dir():
            self._set_state(State.INVALID, f"The widget folder of this instance is not available: {source.source or '(unknown)'}")
            return False
        original = self.runtimes.get(source.id)
        clone = self.store.clone(source.id)
        if self.load_folder(clone.source, instance=clone, follow_source=original.follows_source if original else False):
            return True
        self.store.remove(clone.id)
        self._refresh_instance_list()
        return False

    def close_instance(self, instance_id: str | None = None) -> bool:
        """Close the page of an instance. It stays in the store and its storage on disk is kept (closing is not removing)."""
        instance_id = instance_id or self._active_id
        if not instance_id or instance_id not in self.runtimes:
            return False
        self._close_runtime(instance_id)
        self._after_removal()
        self._changed()
        return True

    def remove_instance(self, instance_id: str | None = None) -> bool:
        """Remove: close the page, forget the instance and delete its storage (after WebEngine released it)."""
        instance_id = instance_id or self._active_id or self._selected_id()
        instance = self.store.get(instance_id) if instance_id else None
        if instance is None:
            return False
        if instance_id in self.runtimes:
            self._close_runtime(instance_id)
        self.store.remove(instance_id)
        if instance_id in self._recent:
            self._recent.remove(instance_id)
        self._delete_storage(instance.id, instance.type_id)
        self._after_removal()
        self._changed()
        return True

    def _close_runtime(self, instance_id: str) -> None:
        runtime = self.runtimes.pop(instance_id)
        if self._active_id == instance_id:
            self._active_id = None
        self.viewport_frame.remove_view(runtime.view)
        folder = runtime.check.folder
        runtime.dispose()
        self._unwatch(folder)

    def _after_removal(self) -> None:
        """Show the last remaining instance, or the empty state."""
        if self._active_id is None and self.runtimes:
            self._activate(list(self.runtimes.values())[-1])
        elif self._active_id is None:
            self.viewport_frame.show_view(None)
            self.settings_panel.clear("No instance open.")
            self.console_view.clear()
            self._idle_current = None
            self.widget_title.setText("No widget open")
            self.path_label.setText("")
            self._refresh_console_count()
            self._set_watch("Not watching (no widget open)", "neutral")
            self._set_state(State.IDLE, "No instance open.")
        self._refresh_instance_list()

    def _delete_storage(self, unique_id: str, type_id: str, attempt: int = 0) -> None:
        """Delete the storage folders of a removed instance. WebEngine can hold files a moment after the profile is gone,
        so a failure is retried; one that never succeeds is remembered (saved with the workspace) and retried at the next start."""
        key = (unique_id, type_id)
        if self.storage.delete(unique_id, type_id):
            if key in self.pending_storage_deletes:
                self.pending_storage_deletes.remove(key)
            return
        if key not in self.pending_storage_deletes:
            self.pending_storage_deletes.append(key)
        if attempt < 20 and not self._shut_down:
            QTimer.singleShot(250, lambda: self._delete_storage(unique_id, type_id, attempt + 1))

    def open_instance(self, instance_id: str | None, mode: str) -> bool:
        """Show a stored instance. mode 'restore': data as stored. mode 'merge': WidgetInstance.merge (update())."""
        if mode not in ("restore", "merge"):
            raise ValueError(f"unknown mode {mode!r}")
        instance = self.store.get(instance_id) if instance_id else None
        if instance is None:
            self._set_state(State.IDLE, "No instance selected.")
            return False
        if not instance.source or not Path(instance.source).is_dir():
            self._set_state(State.INVALID, f"The widget folder of this instance is not available: {instance.source or '(unknown)'}")
            return False
        if mode == "merge":
            check = check_widget_folder(instance.source)
            if not check.ok:
                self._set_state(State.INVALID, f"Invalid widget: {check.errors[0]}")
                return False
            instance = self.store.merge(instance.id, parse_widget_settings(check).defaults(), type_id=type_id_of(check))
        return self.load_folder(instance.source, instance=instance)

    # ---- Build Package (U2, targets iCUE 5 / iCUE Apps since U2.2) -----------------------------------------
    def open_package_dialog(self, settings=None, path_env: str | None = None, target: str = TARGET_ICUE5) -> PackageDialog | None:
        """Build Package for `target` from the source folder of the ACTIVE instance. Touches no instance, setting or viewport."""
        active = self.active
        if active is None or not active.check.ok:
            self._set_state(self.state, "Open a valid widget first: Build Package uses the active instance's source folder.")
            return None
        dialog = PackageDialog(active.check.folder, settings=settings, parent=self, path_env=path_env, target=target)
        dialog.show()
        self._package_dialog = dialog
        return dialog

    # ---- import from iCUE (E2, read-only) -----------------------------------------------------------------
    def open_import_dialog(self) -> ImportDialog:
        dialog = ImportDialog(self.library_panel.entries, self)
        dialog.device = self.device

        def run(candidate, entry, report, personalization) -> None:
            instance = self.import_icue_instance(candidate, entry, report=report, apply_personalization=personalization)
            dialog.status.setText("Imported as a new instance, size " + (instance.size or "n/a") + (f". NOTE: {self.import_notice}" if self.import_notice else "")
                                  if instance is not None else "Import failed: the library widget is invalid.")
        dialog.importRequested.connect(run)
        dialog.show()
        self._import_dialog = dialog
        return dialog

    def import_icue_instance(self, candidate, entry, *, report=None, apply_personalization: bool = False) -> WidgetInstance | None:
        """Create a NEW instance (new id, own storage; never an existing one) from an iCUE widget: the stored values exactly as stored, shown with
        the library widget `entry`. Nothing of iCUE is written; no browser storage/cookies are copied. Returns the instance, or None when the
        widget folder is invalid. Values are never logged here."""
        check = check_widget_folder(entry.folder)
        if not check.ok:
            self._set_state(State.INVALID, f"Invalid widget: {check.errors[0]}")
            return None
        instance = build_instance(candidate, entry)  # raises ValueError for a blocked candidate
        if apply_personalization and report is not None:
            values, key = personalization_values(report, candidate), self._personalization_key()
            for scope in ("device", "screen"):
                for field_name, value in values[scope].items():
                    self.personalization.set_value(scope, key, field_name, value)
            self.personalization.set_theme_by_screen(key, report.theme_by_screen.get(candidate.device_key, False))
        loaded = self.load_folder(entry.folder, instance=instance, follow_source=False)
        self.import_notice = self.size_notice(instance) if loaded else ""  # the stored size is the instance's own; say when the device cannot show it
        return instance if loaded else None

    def save_instances(self, path: str | Path) -> int:
        self.store.save(path)
        return len(self.store)

    def open_instances(self, path: str | Path) -> int:
        """Replace the store by a saved one (restore: nothing is added). A running instance whose id is not in the file stays in the
        store; one with the same id: the file wins in the store, apply it with Open."""
        loaded = InstanceStore.load(path)
        for runtime in self.runtimes.values():
            if runtime.instance.id not in loaded:
                loaded.add(runtime.instance)
        self.store = loaded
        self._refresh_instance_list()
        return len(loaded)

    def choose_save_instances(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save instances", "instances.json", "JSON (*.json)")
        if path:
            self.save_instances(path)

    def choose_open_instances(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open instances", "", "JSON (*.json)")
        if path:
            self.open_instances(path)

    # ---- workspace persistence (D7b) ----------------------------------------------------------------
    def _changed(self) -> None:
        if self.persist and not self._restoring and not self._shut_down:
            self.save_workspace()

    def save_workspace(self, path: str | Path | None = None) -> int:
        """InstanceStore (ids, settings, sources) + which instances are open, the active one, and the device choice."""
        target = Path(path) if path else self.workspace_path
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = {"version": WORKSPACE_VERSION, "store": json.loads(self.store.to_json()), "open": list(self.runtimes),
               "active": self._active_id, "recent": [i for i in self._recent if i in self.store],
               "device": {"id": self.device.id if self.device else None, "size": self._default_size,
                          "orientation": self._default_orientation, "mode": self.device_mode},
               "pendingStorageDeletes": [list(k) for k in self.pending_storage_deletes]}
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        return len(self.store)

    def restore_workspace(self, path: str | Path | None = None) -> int:
        """Load a saved workspace: the store as saved (no default added), the same ids (so the same storage), the saved device.
        Returns the number of instances opened. Instances whose folder is gone stay in the store, closed."""
        source = Path(path) if path else self.workspace_path
        doc = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("version") != WORKSPACE_VERSION:
            raise ValueError("unsupported workspace file")
        self._restoring = True
        try:
            for instance_id in list(self.runtimes):
                self._close_runtime(instance_id)
            self.store = InstanceStore.from_json(json.dumps(doc["store"]))
            self.pending_storage_deletes = [tuple(k) for k in doc.get("pendingStorageDeletes", [])]
            for unique_id, type_id in list(self.pending_storage_deletes):
                self._delete_storage(unique_id, type_id)
            device = doc.get("device") or {}
            try:
                self.select_device(device.get("id"), device.get("size"), device.get("orientation"), device.get("mode") or MODE_NOMINAL)
            except ValueError:
                self.select_device(None)
            opened = 0
            for instance_id in doc.get("open", []):
                instance = self.store.get(instance_id)
                if instance is not None and instance.source and Path(instance.source).is_dir() \
                        and self.load_folder(instance.source, instance=instance, follow_source=False):
                    opened += 1
            self._recent = [i for i in doc.get("recent", []) if i in self.store]
            if doc.get("active") in self.runtimes:
                self.select_instance(doc["active"])
            if self._active_id is None:
                self._after_removal()
            self._refresh_instance_list()
        finally:
            self._restoring = False
        return opened

    def _on_console_added(self, runtime: InstanceRuntime, entry: ConsoleEntry) -> None:
        if runtime is self.active:
            self.console_view.appendPlainText(entry.format())
            self._refresh_console_count()

    # ---- QtWebEngine signals ---------------------------------------------
    def _set_state(self, state: State, text: str, runtime: InstanceRuntime | None = None) -> None:
        """Status of one instance; shown when it is the active one (or when nothing is active)."""
        runtime = runtime or self.active
        if runtime is not None:
            runtime.state, runtime.status_text = state, text
        else:
            self._idle_state = state
        if runtime is None or runtime is self.active:
            tone = STATE_TONES[state]
            self.status.setText(text)
            theme.set_tone(self.status, tone)
            self.status_icon.setPixmap(icons.pixmap("dot", theme.TONES[tone], 12))

    def _on_load_started(self, runtime: InstanceRuntime) -> None:
        if runtime.check.ok:
            self._set_state(State.LOADING, f"Loading {runtime.check.widget_name}...", runtime)

    def _on_load_progress(self, runtime: InstanceRuntime, percent: int) -> None:
        if runtime.state is State.LOADING and runtime is self.active:
            self.status.setText(f"Loading {runtime.check.widget_name}... {percent}%")

    def _on_load_finished(self, runtime: InstanceRuntime, ok: bool) -> None:
        is_active = runtime is self.active
        if runtime.reloading:
            runtime.reloading = False
            if ok:
                runtime.reload_count += 1
            if is_active:
                self._set_watch((f"Reloaded #{runtime.reload_count} | " + self._watching_text()) if ok else "Reload failed",
                                "success" if ok else "error")
        if ok:
            self._set_state(
                State.LOADED,
                f"Page loaded: {runtime.check.widget_name}. Runtime bridge installed. Not verified: widget logic, "
                "missing sub-resources, iCUE APIs.", runtime)
        else:
            self._set_state(State.LOAD_FAILED, f"Failed to load {runtime.check.index_url}", runtime)
        self.instanceSettled.emit(runtime.instance.id, ok)
        if is_active:
            self.loadSettled.emit(ok)
            if ok:
                self._schedule_measure()

    # ---- lifecycle --------------------------------------------------------
    def shutdown(self) -> None:
        """Close the tool: save the workspace, then release every instance's WebEngine stack (page before profile).
        The storage of the instances is KEPT: closing the tool is not removing an instance."""
        if self._shut_down:
            return
        if self.persist:
            self.save_workspace()
        self._shut_down = True
        for watcher in [*self._watchers.values(), self._null_watcher, *([self._invalid_watcher] if self._invalid_watcher else [])]:
            watcher.stop()
        self.settings_panel.clear()
        for instance_id in list(self.runtimes):
            self._close_runtime(instance_id)
        self.sensors_panel.shutdown()  # after the pages: back to the mock source, Hub connection closed
        self.viewport_frame.show_view(None)
        self.hide()  # a closed tool leaves no window behind (matters for test runs that never close their windows)
        if self._owned_tmp:
            shutil.rmtree(self._owned_tmp, ignore_errors=True)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        self.shutdown()
        super().closeEvent(event)