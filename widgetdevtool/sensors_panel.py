"""Sensors tab: the data source of window.plugins.Sensorsdataprovider for every widget instance.

- Mock sensors (default): the WidgetDevTool catalog. Editing a value / units / connected state notifies every open page
  (sensorValueChanged / sensorUnitsChanged / sensorDataChanged), as the real plugin would. None of these sensors exist on this PC.
- Live sensors (Olympus Hub): this PC's hardware sensors read from the Olympus Hub (olympus_hub.OlympusHubSource). Read-only here.
The choice applies to every open page at once (no reload); the status line always says which source is active."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget)

from . import theme
from .olympus_hub import MEASUREMENT_IMPERIAL, MEASUREMENT_METRIC, MEASUREMENT_SYSTEM, OlympusHubSource, hub_source, system_is_imperial
from .sensor_source import sources
from .sensors_mock import SensorCatalog, catalog
from .ui_kit import narrow_combo

MODE_MOCK, MODE_LIVE = "mock", "live"
STATE_TONES = {"disconnected": "neutral", "detecting": "info", "connecting": "info", "discovering": "info", "connected": "success",
               "error": "error"}


class SensorsPanel(QWidget):
    LIVE_REFRESH_MS = 1000  # the live table is redrawn from the cache at most once per second (about 60 value changes per second)

    def __init__(self, sensors: SensorCatalog | None = None, parent: QWidget | None = None,
                 hub: OlympusHubSource | None = None) -> None:
        super().__init__(parent)
        self.catalog = sensors or catalog()
        self._hub = hub
        self._live_rows: dict[str, int] = {}
        self._live_dirty = True
        self.value_edits: dict[str, QLineEdit] = {}
        self.unit_edits: dict[str, QLineEdit] = {}
        self.connected_checks: dict[str, QCheckBox] = {}
        notice = QLabel("MOCK sensors - not this PC's hardware. Values reach the widget as strings (iCUE's plugin sends QString). "
                        "Enter a value, -1 with units -1 (\"unavailable\"), or untick Connected.")
        notice.setWordWrap(True)
        theme.set_tone(notice, "warning", banner=True)
        rows = QVBoxLayout()  # one block per sensor (name, then value / units / connected): fits a narrow panel
        rows.setSpacing(theme.FIELD_GAP)
        for sensor in self.catalog.sensors.values():
            name = QLabel(f"{sensor.name} \u00b7 {sensor.device}\n{sensor.type}{' / ' + sensor.kind if sensor.kind else ''}")
            name.setWordWrap(True)
            name.setProperty("role", "muted")
            name.setToolTip(f"id: {sensor.id}\ntype: {sensor.type}\nkind: {sensor.kind or '(none)'}")
            value, units = QLineEdit(sensor.value), QLineEdit(sensor.units)
            value.setPlaceholderText("value")
            units.setPlaceholderText("units")
            value.setToolTip("Value sent to the widget (a string, as iCUE's plugin sends it). Enter to apply.")
            units.setToolTip("Units sent to the widget. Enter to apply.")
            for edit in (value, units):
                edit.setMinimumWidth(40)
            value.editingFinished.connect(lambda s=sensor.id, e=value: self.catalog.set_value(s, e.text().strip()))
            units.editingFinished.connect(lambda s=sensor.id, e=units: self.catalog.set_units(s, e.text().strip()))
            connected = QCheckBox("Connected")
            connected.setChecked(sensor.connected)
            connected.toggled.connect(lambda on, s=sensor.id: self.catalog.set_connected(s, on))
            line = QHBoxLayout()
            line.setSpacing(6)
            line.addWidget(value, 3)
            line.addWidget(units, 2)
            line.addWidget(connected)
            field = QVBoxLayout()  # sensor name -> its controls: LABEL_GAP
            field.setSpacing(theme.LABEL_GAP)
            field.addWidget(name)
            field.addLayout(line)
            rows.addLayout(field)
            self.value_edits[sensor.id], self.unit_edits[sensor.id], self.connected_checks[sensor.id] = value, units, connected
        self.service_check = QCheckBox("Sensor service ready")
        self.service_check.setToolTip("Untick: the plugin's sensor service (replica) is not initialized, every answer is empty - as in iCUE "
                                      "(SensorDataController::requestAsync).")
        self.service_check.setChecked(self.catalog.service_ready)
        self.service_check.toggled.connect(self.catalog.set_service_ready)
        self.reset_button = QPushButton("Reset mock values")
        self.reset_button.setToolTip("Restore the original mock values, units and connection states (every open page is notified)")
        self.reset_button.clicked.connect(self.reset)
        self.mock_box = QWidget()
        mock_layout = QVBoxLayout(self.mock_box)
        mock_layout.setContentsMargins(0, 0, 0, 0)
        mock_layout.setSpacing(theme.FIELD_GAP)
        mock_layout.addWidget(notice)
        mock_layout.addLayout(rows)
        mock_layout.addWidget(self.service_check)
        mock_layout.addWidget(self.reset_button)
        self._build_live_box()
        self.source_combo = narrow_combo(QComboBox(), 12)
        self.source_combo.addItem("Mock sensors (invented)", MODE_MOCK)
        self.source_combo.addItem("Live sensors (Olympus Hub)", MODE_LIVE)
        self.source_combo.setToolTip("Where window.plugins.Sensorsdataprovider gets its sensors, for every open widget instance")
        self.source_combo.activated.connect(lambda _i: self.set_mode(self.source_combo.currentData()))
        self.source_status = QLabel()
        self.source_status.setWordWrap(True)
        source_title = QLabel("Data source")
        source_title.setProperty("role", "muted")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*[theme.PANEL_MARGIN] * 4)
        layout.setSpacing(theme.FIELD_GAP)
        source_field = QVBoxLayout()
        source_field.setSpacing(theme.LABEL_GAP)
        source_field.addWidget(source_title)
        source_field.addWidget(self.source_combo)
        source_field.addWidget(self.source_status)
        layout.addLayout(source_field)
        layout.addWidget(self.mock_box)
        layout.addWidget(self.live_box, 1)
        layout.addStretch(0)
        self.catalog.valueChanged.connect(self._sync)
        self.catalog.unitsChanged.connect(self._sync)
        self.catalog.dataChanged.connect(self._sync)
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(self.LIVE_REFRESH_MS)
        self._live_timer.timeout.connect(self._refresh_live)
        sources().activeChanged.connect(self._on_active_changed)
        if self._hub is not None:
            self._connect_hub_signals(self._hub)
        self._apply_mode()

    # ---- data source -------------------------------------------------------------------------------------------------
    @property
    def hub(self) -> OlympusHubSource:
        if self._hub is None:
            self._hub = hub_source()
            self._connect_hub_signals(self._hub)
        return self._hub

    def _connect_hub_signals(self, hub: OlympusHubSource) -> None:
        hub.stateChanged.connect(self._on_hub_changed)
        hub.added.connect(self._mark_live_dirty)
        hub.removed.connect(self._mark_live_dirty)
        hub.message.connect(self._on_hub_message)

    def mode(self) -> str:
        return MODE_LIVE if sources().active() is self._hub and self._hub is not None else MODE_MOCK

    def set_mode(self, mode: str) -> bool:
        """Switch every open page to the mock catalog or to the live Hub source (connects when needed; the mock stops the Hub)."""
        if mode == MODE_LIVE:
            hub = self.hub
            sources().set_active(hub)
            if not hub.running:
                self.connect_hub()
        else:
            sources().set_active(self.catalog)
            if self._hub is not None and self._hub.running:
                self._hub.stop()
        self._apply_mode()
        return True

    def _on_active_changed(self, _old, _new) -> None:
        self._apply_mode()

    def _apply_mode(self) -> None:
        live = self.mode() == MODE_LIVE
        index = self.source_combo.findData(MODE_LIVE if live else MODE_MOCK)
        if self.source_combo.currentIndex() != index:
            self.source_combo.setCurrentIndex(index)
        self.mock_box.setVisible(not live)
        self.live_box.setVisible(live)
        if live:
            self._live_dirty = True
            self._live_timer.start()
            self._on_hub_changed()
        else:
            self._live_timer.stop()
            self.source_status.setText("Active: MOCK sensors (WidgetDevTool catalog, not this PC's hardware).")
            theme.set_tone(self.source_status, "warning")

    # ---- live ---------------------------------------------------------------------------------------------------------
    def _build_live_box(self) -> None:
        self.live_box = QWidget()
        notice = QLabel("LIVE sensors of this PC, read from the Olympus Hub (Device Manager). Names, types, units and values follow "
                        "what the Olympus Widget Builder plugin was observed to send on this PC (temperatures converted like the "
                        "plugin; see Units). Read-only.")
        notice.setWordWrap(True)
        theme.set_tone(notice, "info", banner=True)
        self.host_edit = QLineEdit("127.0.0.1")
        self.host_edit.setToolTip("Hub bus host")
        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("auto-detect")
        self.port_edit.setToolTip("Hub bus port. Empty: detected from the processes the Hub started (--hub-port), never assumed.")
        self.port_edit.returnPressed.connect(self.connect_hub)
        self.host_edit.returnPressed.connect(self.connect_hub)
        for edit in (self.host_edit, self.port_edit):
            edit.setMinimumWidth(40)
        endpoint = QHBoxLayout()
        endpoint.setSpacing(theme.SPACING)
        endpoint.addWidget(self.host_edit, 3)
        endpoint.addWidget(self.port_edit, 2)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setToolTip("(Re)connect with this host / port (port empty: auto-detect)")
        self.connect_button.clicked.connect(self.connect_hub)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setToolTip("Unsubscribe and close the Hub connection (pages see no sensors until Connect)")
        self.disconnect_button.clicked.connect(self.disconnect_hub)
        buttons = QHBoxLayout()
        buttons.setSpacing(theme.SPACING)
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.disconnect_button)
        self.units_combo = narrow_combo(QComboBox(), 10)
        self.units_combo.addItem("Units: like the plugin (system locale)", MEASUREMENT_SYSTEM)
        self.units_combo.addItem("Units: \u00b0C", MEASUREMENT_METRIC)
        self.units_combo.addItem("Units: \u00b0F", MEASUREMENT_IMPERIAL)
        self.units_combo.setToolTip("Temperature units sent to the widget. The plugin sent \u00b0F with converted values on the en-US test PC; "
                                    "that it follows the system locale is INFERRED. The Hub itself always reports \u00b0C.")
        self.units_combo.activated.connect(lambda _i: self.hub.set_measurement(self.units_combo.currentData()))
        self.live_status = QLabel()
        self.live_status.setWordWrap(True)
        self.live_summary = QLabel()
        self.live_summary.setWordWrap(True)
        self.live_summary.setProperty("role", "muted")
        self.live_filter = QLineEdit()
        self.live_filter.setPlaceholderText("Filter sensors (name, device, type)")
        self.live_filter.textChanged.connect(self._apply_live_filter)
        self.live_table = QTableWidget(0, 3)
        self.live_table.setHorizontalHeaderLabels(["Sensor", "Value", "Units"])
        self.live_table.verticalHeader().setVisible(False)
        self.live_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.live_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.live_table.setWordWrap(False)
        header = self.live_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # Value / Units are sized once per rebuild (_rebuild_live_table): ResizeToContents re-measures the whole column on every
        # setText and cost about 10 % of a core with 212 live sensors (measured).
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.live_table.setMinimumHeight(220)
        self.live_table.setToolTip("Sensors of the live source (hover a row: id, device, type / kind, raw Hub value and unit)")
        self.live_log = QLabel()
        self.live_log.setWordWrap(True)
        self.live_log.setProperty("role", "muted")
        self.live_log.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self.live_box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.FIELD_GAP)
        layout.addWidget(notice)
        layout.addLayout(endpoint)
        layout.addLayout(buttons)
        layout.addWidget(self.units_combo)
        layout.addWidget(self.live_status)
        layout.addWidget(self.live_summary)
        layout.addWidget(self.live_filter)
        layout.addWidget(self.live_table, 1)
        layout.addWidget(self.live_log)

    def connect_hub(self) -> bool:
        host = self.host_edit.text().strip() or "127.0.0.1"
        port_text = self.port_edit.text().strip()
        port = None
        if port_text:
            if not port_text.isdigit() or not 0 < int(port_text) < 65536:
                self.live_status.setText(f"Invalid port {port_text!r}: enter 1-65535, or leave it empty to auto-detect.")
                theme.set_tone(self.live_status, "error")
                return False
            port = int(port_text)
        self.hub.start(host, port)
        return True

    def disconnect_hub(self) -> None:
        self.hub.stop()

    def _mark_live_dirty(self, *_args) -> None:
        self._live_dirty = True

    def _on_hub_message(self, _level: str, _text: str) -> None:
        if self.mode() == MODE_LIVE:
            self._refresh_log()

    def _refresh_log(self) -> None:
        events = list(self.hub.events)[-6:]
        self.live_log.setText("\n".join(f"{t} {level.upper() if level != 'info' else ''} {text}".replace("  ", " ") for t, level, text in events))

    def _on_hub_changed(self) -> None:
        if self.mode() != MODE_LIVE:
            return
        hub = self.hub
        endpoint = f"{hub.host}:{hub.port}" if hub.port else hub.host
        texts = {
            hub.DISCONNECTED: "Disconnected. Press Connect.",
            hub.DETECTING: "Looking for the Hub port (processes started with --hub-port)...",
            hub.CONNECTING: f"Connecting to {endpoint}...",
            hub.DISCOVERING: hub.error or f"Connected to {endpoint}; discovering sensors...",
            hub.CONNECTED: f"Connected to {endpoint}" + (f" ({hub.port_origin})" if hub.port_origin else "") + f": {len(hub.sensors)} sensors.",
            hub.ERROR: hub.error,
        }
        self.live_status.setText(texts.get(hub.state, hub.state))
        theme.set_tone(self.live_status, STATE_TONES.get(hub.state, "neutral"))
        ready = hub.service_ready
        self.source_status.setText("Active: LIVE sensors from the Olympus Hub" + ("." if ready else
                                   " - not ready: widgets get empty answers (as iCUE while its sensor service is unavailable)."))
        theme.set_tone(self.source_status, "success" if ready else "warning")
        self.disconnect_button.setEnabled(hub.running)
        if hub.auto_port and hub.port and not self.port_edit.text().strip():
            self.port_edit.setPlaceholderText(f"auto-detect (found {hub.port})")
        index = self.units_combo.findData(hub.measurement)
        if self.units_combo.currentIndex() != index:
            self.units_combo.setCurrentIndex(index)
        self._live_dirty = True
        self._refresh_live()
        self._refresh_log()

    def _refresh_live(self) -> None:
        if self.mode() != MODE_LIVE or not self.isVisible():  # hidden tab: redrawn when shown (showEvent)
            return
        hub = self.hub
        ids = hub.ids()
        if self._live_dirty or len(ids) != len(self._live_rows):
            self._rebuild_live_table(ids)
        for sensor_id, row in self._live_rows.items():
            sensor = hub.get(sensor_id)
            if sensor is None:
                continue
            for column, text in ((1, sensor.value), (2, sensor.units)):
                item = self.live_table.item(row, column)
                if item is not None and item.text() != text:
                    item.setText(text)
        system = "\u00b0F" if system_is_imperial() else "\u00b0C"
        parts = [f"{len(ids)} sensors among {hub.device_count} devices" if ids else "no sensors",
                 f"value updates: {hub.value_updates} ({hub.refetches} re-read after a notification without value)"]
        if hub.discovery_seconds and ids:
            parts.append(f"discovery {hub.discovery_seconds:.1f} s")
        if hub.measurement == MEASUREMENT_SYSTEM:
            parts.append(f"temperatures in {system} (system locale)")
        if hub.unverified_unit_types:
            parts.append("units never observed on the real plugin, sent as '': " + ", ".join(sorted(hub.unverified_unit_types)))
        self.live_summary.setText(" \u00b7 ".join(parts))

    def _rebuild_live_table(self, ids: list[str]) -> None:
        hub = self.hub
        self._live_dirty = False
        self.live_table.setUpdatesEnabled(False)
        self.live_table.setRowCount(len(ids))
        self._live_rows = {}
        for row, sensor_id in enumerate(ids):
            sensor = hub.get(sensor_id)
            if sensor is None:
                continue
            tip = (f"{sensor.name}\nid: {sensor.id}\ndevice: {sensor.device}\ntype / kind: {sensor.type} / {sensor.kind}\n"
                   f"Hub: {sensor.raw_value} {sensor.raw_unit if sensor.raw_unit is not None else '(no cue.sensor_unit)'} "
                   f"({sensor.dm_type} / {sensor.dm_kind})")
            items = (QTableWidgetItem(f"{sensor.name} \u00b7 {sensor.device}"), QTableWidgetItem(sensor.value), QTableWidgetItem(sensor.units))
            for column, item in enumerate(items):
                item.setToolTip(tip)
                item.setData(Qt.ItemDataRole.UserRole, sensor.id)
                self.live_table.setItem(row, column, item)
            self._live_rows[sensor_id] = row
        self.live_table.resizeColumnToContents(1)
        self.live_table.resizeColumnToContents(2)
        self.live_table.setUpdatesEnabled(True)
        self._apply_live_filter()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if self.mode() == MODE_LIVE:
            self._refresh_live()

    def _apply_live_filter(self, *_args) -> None:
        needle = self.live_filter.text().strip().lower()
        hub = self._hub
        for sensor_id, row in self._live_rows.items():
            sensor = hub.get(sensor_id) if hub is not None else None
            haystack = f"{sensor.name} {sensor.device} {sensor.type} {sensor.kind} {sensor.id}".lower() if sensor else ""
            self.live_table.setRowHidden(row, bool(needle) and needle not in haystack)

    def shutdown(self) -> None:
        """Window closing: back to the mock source, Hub connection closed without waiting for the event loop."""
        self._live_timer.stop()
        try:
            sources().activeChanged.disconnect(self._on_active_changed)
        except (RuntimeError, TypeError):
            pass
        if sources().active() is not self.catalog and sources().active() is self._hub:
            sources().set_active(self.catalog)
        if self._hub is not None:
            self._hub.shutdown()

    def _sync(self, sensor_id: str, *_args) -> None:
        sensor = self.catalog.get(sensor_id)
        if sensor is None or sensor_id not in self.value_edits:
            return
        for edit, text in ((self.value_edits[sensor_id], sensor.value), (self.unit_edits[sensor_id], sensor.units)):
            if edit.text() != text:
                edit.setText(text)
        check = self.connected_checks[sensor_id]
        if check.isChecked() != sensor.connected:
            check.blockSignals(True)
            check.setChecked(sensor.connected)
            check.blockSignals(False)

    def reset(self) -> None:
        self.catalog.reset()
        self.service_check.setChecked(True)
        for sensor_id in self.value_edits:
            self._sync(sensor_id)
