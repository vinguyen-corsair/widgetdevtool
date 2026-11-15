"""Live Sensors (phase 2): the real sensors of this PC, read from the Olympus Hub. Protocol proven by tools/hub_sensors_poc.py.

Hub bus (ui-npm-registry ipc-client + ipc-client-if protocol.ts, DeviceManagerInterfaces Devices.cidl): WebSocket ws://host:port/,
JSON text frames. request {"id","target","method","params"}, reply {"id","result"} / {"id","error":{"code","message"}},
notification {"signal","source","params"}. cue.Bus.authenticate first, cue.Bus.subscribe {source, signal} for every signal.
Sensors are Device Manager devices with cue.device_type == "Sensor" (widgets SensorsCollection.cpp); target "devmgr/DeviceManager".
A value change is notification_received(id="cue.property_changed", data={property_id: "cue.sensor_value"[, value]}): some devices
send the value, others only property_id (CdcsDevice.cpp) and the value is read again with get_property.

What a widget receives is what the Olympus Widget Builder plugin (widgetbuilder.sensorsdataprovider, Renderer 0.48.2) returns.
OBSERVED on the test PC by a probe widget run in "iCUE Widget Builder Renderer.exe" with the same pluginProviders config the
Launcher sends (212 sensors, 2026-11-15), NOT read from source (the WBF source is not available):
- ids without braces ("{...}" is an unknown id); getAllSensorIds sorted by id; unknown id: value "-1", units "-1", texts "",
  sensorIsConnected false.
- type / kind: the Device Manager enum names in kebab case ("ClockSpeed" -> "clock-speed", "Voltage3_3" -> "voltage-3-3"),
  the same strings as iCUE 5 HtmlSensorUtils.cpp sensorTypeKindToString for the types both know.
- units: derived from the type (cue.sensor_unit is ignored: "Available Capacity" has GB on the Hub, "%" from the plugin), see
  WBF_UNITS; temperatures in degrees F with the value converted on this en-US (Imperial) PC.
- values: the raw Hub string, unchanged; temperatures converted with celsius * 9 / 5 + 32 and written as the shortest round-trip
  number ("113", "107.65634"). Not celsius * 1.8 + 32 (iCUE 5 UnitsUtil::celciusToSystem): for 42 / 37 / 62 degrees C that gives
  "107.60000000000001" / "98.60000000000001" / "143.60000000000002" in double precision, the plugin sent "107.6" / "98.6" / "143.6".
- getDefaultSensorId(type, kind): case-insensitive type, the lowest id, a sensor of the preferred kind first.
"""
from __future__ import annotations

import itertools
import json
import math
import re
import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QLocale, QObject, QProcess, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket, QWebSocketProtocol

from .sensor_source import SensorSource

BUS = "cue/Bus"
DEVMGR = "devmgr/DeviceManager"
DEVICE_NOTIFICATION = "cue.devmgr.Device.notification_received"
PROPERTY_CHANGED = "cue.property_changed"
SENSOR_VALUE = "cue.sensor_value"
DEVICE_NAME = "cue.device_name"
SENSOR_PROPS = {"dm_type": "cue.sensor_type", "dm_kind": "cue.sensor_kind", "raw_unit": "cue.sensor_unit",
                "raw_value": SENSOR_VALUE, "owner_id": "cue.owner_device_id", "source_name": "cue.sensor_source_name"}
DEFAULT_HOST = "127.0.0.1"
CLIENT_NAME = "WidgetDevTool"

# ---- what the widget receives (see the module doc) ------------------------------------------------------------------------
_KEBAB = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])")
# iCUE 5 HtmlSensorUtils.cpp where its string is not the kebab-case name (none of these exist on the test PC: UNVERIFIED on WBF)
TYPE_EXCEPTIONS = {"DeviceBatteryLevel": "battery-charge", "DeviceBatteryStatus": "battery-status"}
KIND_EXCEPTIONS = {"CurrentItem12V2x6": "current-item-12v-2x6"}
# OBSERVED plugin units per widget type (probe, 212 sensors). Temperature: TEMPERATURE_UNITS. A type missing here was not seen:
# its units are "" and the source lists it in `unverified_unit_types` (no unit is invented).
WBF_UNITS = {"fan": "RPM", "pump": "RPM", "load": "%", "power": "W", "voltage": "V", "current": "A", "dram-frequency": "MHz",
             "bandwidth": "", "cas-latency": "", "clock-speed": "", "command-rate": "", "counter": "", "perf-limiter": "",
             "ras-precharge": "", "ras-to-cas-delay": "", "tras": ""}
TEMPERATURE_UNITS = {False: "\u00b0C", True: "\u00b0F"}

MEASUREMENT_SYSTEM, MEASUREMENT_METRIC, MEASUREMENT_IMPERIAL = "system", "metric", "imperial"


def kebab(name: str) -> str:
    return _KEBAB.sub("-", name).replace("_", "-").lower()


def widget_type(dm_type: str | None) -> str:
    return TYPE_EXCEPTIONS.get(dm_type, kebab(dm_type)) if dm_type else ""


def widget_kind(dm_kind: str | None) -> str:
    return KIND_EXCEPTIONS.get(dm_kind, kebab(dm_kind)) if dm_kind else ""


def widget_units(wtype: str, imperial: bool) -> tuple[str, bool]:
    """(units the widget receives, observed on the real plugin)."""
    if wtype == "temperature":
        return TEMPERATURE_UNITS[imperial], True
    if wtype in WBF_UNITS:
        return WBF_UNITS[wtype], True
    return "", False


def qt_number(x: float) -> str:
    """QString::number(x, 'g', QLocale::FloatingPointShortest): shortest round-trip text, no ".0" for whole numbers."""
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return repr(x)


def widget_value(raw, wtype: str, imperial: bool) -> str:
    raw = "" if raw is None else str(raw)
    if wtype == "temperature" and imperial:
        try:
            celsius = float(raw)
        except ValueError:
            return raw
        if math.isfinite(celsius):
            return qt_number(celsius * 9 / 5 + 32)  # matches the plugin's strings (module doc), * 1.8 does not
    return raw


def system_is_imperial() -> bool:
    """INFERRED: the plugin follows the system locale (en-US test PC: QLocale ImperialUSSystem and the plugin sends degrees F)."""
    return QLocale.system().measurementSystem() != QLocale.MeasurementSystem.MetricSystem


def plain_id(device_id) -> str:
    """Device Manager ids come as "{uuid}"; the plugin (and the widgets' stored settings) use the bare lower-case uuid."""
    return str(device_id or "").strip().strip("{}").lower()


def property_value(result):
    """cue.devmgr.Device.get_property -> Result {data: {value: ...}}."""
    if isinstance(result, dict):
        data = result.get("data") or {}
        return data.get("value") if isinstance(data, dict) else None
    return result


def fmt_error(error) -> str:
    if isinstance(error, dict):
        return f"{error.get('code')}: {error.get('message') or ''}".strip().rstrip(":")
    return str(error)


# ---- bus client ---------------------------------------------------------------------------------------------------------
class HubBusClient(QObject):
    """Minimal async bus client on the Qt event loop (never blocks): request/reply with per-request timeout, notifications."""

    connected = Signal()
    disconnected = Signal()
    notification = Signal(dict)

    def __init__(self, timeout_ms: int = 5000, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.timeout_ms = timeout_ms
        self.ws = QWebSocket("", QWebSocketProtocol.Version.VersionLatest, self)  # child: deleted with the client, never orphaned
        self.last_error = ""
        self.sent = 0
        self._ids = itertools.count(1)
        self._pending: dict[str, tuple] = {}
        self.ws.connected.connect(self.connected)
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.textMessageReceived.connect(self._on_text)
        self.ws.errorOccurred.connect(self._on_error)

    def open(self, host: str, port: int) -> None:
        self.last_error = ""
        self.ws.open(QUrl(f"ws://{host}:{port}/"))

    def is_connected(self) -> bool:
        return self.ws.state() == QAbstractSocket.SocketState.ConnectedState

    def is_idle(self) -> bool:
        return self.ws.state() == QAbstractSocket.SocketState.UnconnectedState

    def close(self) -> None:
        """Graceful close (close frame) when connected, otherwise abort the pending connection."""
        if self.is_connected():
            self.ws.close()
        else:
            self.ws.abort()

    def abort(self) -> None:
        self.ws.abort()

    def request(self, target: str, method: str, params: dict, callback, timeout_ms: int | None = None) -> None:
        if not self.is_connected():
            callback(None, {"code": "no-channel", "message": "not connected"})
            return
        rid = str(next(self._ids))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._on_timeout(rid))
        timer.start(timeout_ms or self.timeout_ms)
        self._pending[rid] = (callback, timer, method, target)
        self.sent += 1
        self.ws.sendTextMessage(json.dumps({"id": int(rid), "target": target, "method": method, "params": params}))

    def send_only(self, target: str, method: str, params: dict) -> None:
        """Fire and forget (shutdown: there is no event loop left to wait for the reply)."""
        if self.is_connected():
            self.ws.sendTextMessage(json.dumps({"id": int(next(self._ids)), "target": target, "method": method, "params": params}))
            self.ws.flush()

    def _on_timeout(self, rid: str) -> None:
        entry = self._pending.pop(rid, None)
        if entry:
            callback, timer, method, target = entry
            timer.deleteLater()
            callback(None, {"code": "timeout", "message": f"{method} on {target}: no reply in time"})

    def _on_error(self, _error) -> None:
        self.last_error = self.ws.errorString()

    def _on_text(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if "signal" in msg and "source" in msg:
            self.notification.emit(msg)
        elif "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
            entry = self._pending.pop(str(msg["id"]), None)
            if entry is not None:
                callback, timer, _method, _target = entry
                timer.stop()
                timer.deleteLater()
                callback(msg.get("result"), msg.get("error"))
        elif "method" in msg and "id" in msg:  # nothing is registered on the bus by this client
            self.ws.sendTextMessage(json.dumps({"id": msg["id"], "error": {"code": "no-such-object", "message": CLIENT_NAME}}))

    def _on_disconnected(self) -> None:
        pending, self._pending = self._pending, {}
        for callback, timer, method, target in pending.values():
            timer.stop()
            timer.deleteLater()
            callback(None, {"code": "disconnected", "message": f"{method} on {target}: connection closed"})
        self.disconnected.emit()


# ---- Hub port: from the command line the Hub gives its child processes -------------------------------------------------
_HUB_PORT_ARG = re.compile(r"--hub-port(?:=|\s+)(\d{1,5})")
_DETECT_COMMAND = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '--hub-port' } | "
                   "ForEach-Object { $_.Name + '|' + $_.CommandLine }")


def parse_hub_ports(lines) -> list[tuple[int, str]]:
    """"<process>|<command line>" lines -> [(port, process)] (OlympusHub.cpp starts Device Manager, Apps UI, TrayManager... with
    --hub-port=<port>). Only the process name and the port are kept."""
    found = []
    for line in lines:
        name, _, command = str(line).partition("|")
        match = _HUB_PORT_ARG.search(command)
        if match and 0 < int(match.group(1)) < 65536:
            found.append((int(match.group(1)), name.strip()))
    return found


class HubPortDetector(QObject):
    """Asynchronous (QProcess): never blocks the UI. finished(ports [(port, process)], error)."""

    finished = Signal(list, str)

    def __init__(self, parent: QObject | None = None, timeout_ms: int = 15000) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._on_timeout)

    def running(self) -> bool:
        return self._process is not None

    def start(self) -> None:
        if self._process is not None:
            return
        process = QProcess(self)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_process_error)
        self._process = process
        self._timer.start()
        process.start("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", _DETECT_COMMAND])

    def cancel(self) -> None:
        process, self._process = self._process, None
        self._timer.stop()
        if process is not None:
            process.blockSignals(True)
            process.kill()
            process.deleteLater()

    def _done(self, ports: list, error: str) -> None:
        self.cancel()
        self.finished.emit(ports, error)

    def _on_finished(self, *_args) -> None:
        if self._process is None:
            return
        text = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace")
        self._done(parse_hub_ports(text.splitlines()), "")

    def _on_process_error(self, _error) -> None:
        if self._process is not None and self._process.state() == QProcess.ProcessState.NotRunning:
            self._done([], f"process list unavailable: {self._process.errorString()}")

    def _on_timeout(self) -> None:
        self._done([], "process list timed out")


@dataclass
class LiveSensor:
    """One sensor: raw Hub data (raw_value, raw_unit, dm_type, dm_kind) kept exactly; value / units / type / kind are what the widget
    receives (see the module doc)."""

    id: str
    objid: str
    name: str = ""
    device: str = ""
    type: str = ""
    kind: str = ""
    value: str = ""
    units: str = ""
    connected: bool = True
    dm_type: str = ""
    dm_kind: str = ""
    raw_value: str = ""
    raw_unit: str | None = None
    owner_id: str = ""
    source_name: str = ""
    units_verified: bool = True
    updates: int = 0
    last_update: float = 0.0

class OlympusHubSource(SensorSource):
    """The live source: one Hub connection shared by every widget instance (hub_source()). Everything is asynchronous on the Qt event
    loop; the cache (sensors) answers the widgets at once, also the synchronous getDefaultSensorIdBlock.

    States: disconnected -> detecting (port from the process command lines) -> connecting -> discovering -> connected; error (with a
    message, retried with backoff). While not connected the source is "not ready": every async answer is empty, as iCUE's
    SensorDataController while its replica is not initialized, and every page is told once (sensorDataChanged per sensor) so it
    stops showing stale values. Reconnects after a lost connection, a Hub restart (the port is detected again when it was
    auto-detected) and a Device Manager restart (known_object_unregistered / registered). stop() unsubscribes, then closes."""

    DISCONNECTED, DETECTING, CONNECTING, DISCOVERING, CONNECTED, ERROR = (
        "disconnected", "detecting", "connecting", "discovering", "connected", "error")
    RECONNECT_DELAYS_MS = (1000, 2000, 4000, 8000, 16000, 30000)
    DISCOVERY_CONCURRENCY = 32
    DEVMGR_RETRY_MS = 3000

    label = "live Olympus Hub sensors"
    is_live = True
    message = Signal(str, str)  # level, text: connection / discovery / error events (Sensors tab)

    def __init__(self, request_timeout_ms: int = 5000, connect_timeout_ms: int = 5000, detector: QObject | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.request_timeout_ms, self.connect_timeout_ms = request_timeout_ms, connect_timeout_ms
        self.host = DEFAULT_HOST
        self.port: int | None = None
        self.port_origin = ""
        self.auto_port = True
        self.state = self.DISCONNECTED
        self.error = ""
        self.measurement = MEASUREMENT_SYSTEM
        self.sensors: dict[str, LiveSensor] = {}
        self.device_names: dict[str, str] = {}
        self.device_count = 0
        self.unverified_unit_types: set[str] = set()
        self.notifications = self.value_updates = self.refetches = 0
        self.discovery_seconds = 0.0
        self.events: deque = deque(maxlen=200)
        self._by_obj: dict[str, str] = {}
        self._wanted = False
        self._gen = 0
        self._failures = 0
        self._suppress = False
        # ONE client for the whole life of the source: a reconnect is abort() + open() on the same socket. Deleting a QWebSocket
        # that is still connecting crashed Qt in the tests (access violation); the generation counter drops stale callbacks.
        self._client = HubBusClient(request_timeout_ms, self)
        self._client.connected.connect(self._on_client_connected)
        self._client.disconnected.connect(self._on_client_disconnected)
        self._client.notification.connect(self._on_client_notification)
        self._detector = detector if detector is not None else HubPortDetector(self)
        self._detector.finished.connect(self._on_detected)
        self._reconnect = QTimer(self)
        self._reconnect.setSingleShot(True)
        self._reconnect.timeout.connect(self._attempt)
        self._connect_timer = QTimer(self)
        self._connect_timer.setSingleShot(True)
        self._connect_timer.timeout.connect(self._on_connect_timeout)

    # ---- SensorSource ------------------------------------------------------------------------------------------------
    @property
    def service_ready(self) -> bool:
        return self.state == self.CONNECTED

    @property
    def imperial(self) -> bool:
        return self.measurement == MEASUREMENT_IMPERIAL or (self.measurement == MEASUREMENT_SYSTEM and system_is_imperial())

    def ids(self) -> list[str]:
        return sorted(self.sensors) if self.service_ready else []

    def get(self, sensor_id: str) -> LiveSensor | None:
        return self.sensors.get(str(sensor_id)) if self.service_ready else None

    def default_id(self, sensor_type: str, kind: str = "") -> str:
        if not self.service_ready:
            return ""
        wanted_type, wanted_kind = str(sensor_type or "").lower(), str(kind or "").lower()
        candidates = sorted((s for s in self.sensors.values() if s.type == wanted_type), key=lambda s: s.id)
        preferred = [s for s in candidates if wanted_kind and s.kind == wanted_kind]
        chosen = (preferred or candidates or [None])[0]
        return chosen.id if chosen else ""

    def unknown_answer(self, attribute: str):
        return "-1" if attribute in ("value", "units") else ""  # observed plugin answer for an unknown id

    # ---- control -----------------------------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._wanted

    def start(self, host: str | None = None, port: int | None = None) -> None:
        """port None: detect it from the processes the Hub started (--hub-port)."""
        if self._wanted:
            self._halt()
        self._wanted = True
        self.host = (host or DEFAULT_HOST).strip() or DEFAULT_HOST
        self.auto_port = port is None
        self.port = port
        self.port_origin = "" if port is None else "entered manually"
        self._failures = 0
        self._attempt()

    def retry_now(self) -> None:
        if self._wanted:
            self._reconnect.stop()
            self._attempt()

    def stop(self) -> None:
        """Unsubscribe from every signal, then close. Pages are told the source is no longer ready."""
        was_wanted = self._wanted
        self._halt()
        self._set_not_ready(self.DISCONNECTED, "")
        if was_wanted:
            self._log("info", "disconnected from the Hub")

    def shutdown(self) -> None:
        """Application exit: no event loop left to wait for replies."""
        self._wanted = False
        self._reconnect.stop()
        self._connect_timer.stop()
        self._detector.cancel()
        self._gen += 1
        self._by_obj = {}
        self._client.send_only(BUS, "cue.Bus.unsubscribe", {})  # its disconnected signal is ignored: _wanted is False
        self._client.close()
        if self.state != self.DISCONNECTED:
            self._set_not_ready(self.DISCONNECTED, "")

    def set_measurement(self, mode: str) -> None:
        if mode == self.measurement:
            return
        self.measurement = mode
        imperial = self.imperial
        for sensor in self.sensors.values():
            if sensor.type != "temperature":
                continue
            units, value = widget_units(sensor.type, imperial)[0], widget_value(sensor.raw_value, sensor.type, imperial)
            units_changed, value_changed = units != sensor.units, value != sensor.value
            sensor.units, sensor.value = units, value
            if self.service_ready and units_changed:
                self.unitsChanged.emit(sensor.id, units)
            if self.service_ready and value_changed:
                self.valueChanged.emit(sensor.id, value)
        self.stateChanged.emit()

    # ---- internals: state ----------------------------------------------------------------------------------------------
    def _log(self, level: str, text: str) -> None:
        self.events.append((time.strftime("%H:%M:%S"), level, text))
        self.message.emit(level, text)

    def _set_state(self, state: str, error: str = "") -> None:
        self.state, self.error = state, error
        self.stateChanged.emit()

    def _set_not_ready(self, state: str, error: str) -> None:
        was_ready = self.service_ready
        self._set_state(state, error)
        if was_ready:
            for sensor_id in sorted(self.sensors):
                self.dataChanged.emit(sensor_id)

    def _halt(self) -> None:
        self._wanted = False
        self._reconnect.stop()
        self._detector.cancel()
        self._gen += 1
        gen = self._gen
        self._connect_timer.stop()
        self._by_obj = {}
        if self._client.is_connected():
            def unsubscribed(_result, _error) -> None:
                if gen == self._gen and not self._wanted:  # not reconnected meanwhile
                    self._client.close()
            self._client.request(BUS, "cue.Bus.unsubscribe", {}, unsubscribed, timeout_ms=1000)
        else:
            self._abort_client()

    def _abort_client(self) -> None:
        if not self._client.is_idle():
            self._suppress = True
            try:
                self._client.abort()
            finally:
                self._suppress = False

    def _dispose_client(self) -> None:
        self._gen += 1  # every callback of the old connection is ignored from now on
        self._connect_timer.stop()
        self._by_obj = {}
        self._abort_client()

    def _fail(self, text: str) -> None:
        self._dispose_client()
        self._failures += 1
        delay = self.RECONNECT_DELAYS_MS[min(self._failures, len(self.RECONNECT_DELAYS_MS)) - 1]
        self._log("error", text)
        self._set_not_ready(self.ERROR, f"{text} Retrying in {delay / 1000:g} s.")
        if self._wanted:
            self._reconnect.start(delay)

    # ---- internals: port, connection, authentication ----------------------------------------------------------------
    def _attempt(self) -> None:
        if not self._wanted:
            return
        if self.auto_port and (self.port is None or self._failures >= 2):
            self._set_state(self.DETECTING, self.error if self.state == self.ERROR else "")
            self._log("info", "looking for the Hub port in the command lines of running processes (--hub-port)")
            self._detector.start()
            return
        self._connect()

    def _on_detected(self, ports: list, error: str) -> None:
        if not self._wanted or not self.auto_port:
            return
        if not ports:
            self.port = None
            self._fail(error or "Olympus Hub is not running: no process was started with --hub-port. "
                                "Start iCUE Apps, or enter the Hub port manually.")
            return
        counts: dict[int, list[str]] = {}
        for port, name in ports:
            counts.setdefault(int(port), []).append(name)
        port = max(counts, key=lambda p: len(counts[p]))
        if len(counts) > 1:
            self._log("warning", f"several Hub ports in use: {sorted(counts)}; using {port}")
        self.port = port
        self.port_origin = "auto-detected from " + ", ".join(sorted(set(counts[port])))
        self._log("info", f"Hub port {port} {self.port_origin}")
        self._connect()

    def _connect(self) -> None:
        self._dispose_client()
        self._set_state(self.CONNECTING, "")
        self._log("info", f"connecting to ws://{self.host}:{self.port}/")
        self._connect_timer.start(self.connect_timeout_ms)
        self._client.open(self.host, int(self.port))

    def _on_client_connected(self) -> None:
        if self.state == self.CONNECTING:
            self._on_connected(self._gen)

    def _on_client_disconnected(self) -> None:
        if not self._suppress:
            self._on_disconnected(self._gen)

    def _on_client_notification(self, msg: dict) -> None:
        self._on_notification(self._gen, msg)

    def _on_connect_timeout(self) -> None:
        if self.state == self.CONNECTING:
            self._fail(f"No answer from the Hub at {self.host}:{self.port} within {self.connect_timeout_ms} ms.")

    def _on_disconnected(self, gen: int) -> None:
        if gen != self._gen or not self._wanted:
            return
        reason = self._client.last_error or "connection closed"
        if self.state in (self.DISCOVERING, self.CONNECTED):
            self._fail(f"Connection to the Hub at {self.host}:{self.port} lost ({reason}).")
        else:
            self._fail(f"Hub not reachable at {self.host}:{self.port} ({reason}). Is iCUE Apps running?")

    def _req(self, gen: int, target: str, method: str, params: dict, callback) -> None:
        if gen != self._gen:
            return
        self._client.request(target, method, params, lambda r, e: callback(r, e) if gen == self._gen else None)

    def _on_connected(self, gen: int) -> None:
        if gen != self._gen:
            return
        self._connect_timer.stop()
        self._log("info", "connected; cue.Bus.authenticate")
        self._req(gen, BUS, "cue.Bus.authenticate", {"name": CLIENT_NAME, "description": "WidgetDevTool Live Sensors"},
                  lambda r, e: self._on_authenticated(gen, r, e))

    def _on_authenticated(self, gen: int, result, error) -> None:
        if error:
            self._fail(f"Hub refused cue.Bus.authenticate ({fmt_error(error)}).")
            return
        prefix = result.get("objid_prefix", "") if isinstance(result, dict) else ""
        self._log("info", f"authenticated (objid prefix {prefix!r})")
        for source, signal in ((BUS, "cue.Bus.known_object_registered"), (BUS, "cue.Bus.known_object_unregistered"),
                               (DEVMGR, "cue.devmgr.DeviceManager.device_connected"),
                               (DEVMGR, "cue.devmgr.DeviceManager.device_disconnected")):
            self._subscribe(gen, source, signal)
        self._discover(gen)

    def _subscribe(self, gen: int, source: str, signal: str, done=None) -> None:
        def on_reply(_result, error) -> None:
            if error:
                self._log("warning", f"subscribe {signal} on {source} failed: {fmt_error(error)}")
            if done:
                done()
        self._req(gen, BUS, "cue.Bus.subscribe", {"source": source, "signal": signal}, on_reply)
    # ---- internals: discovery ----------------------------------------------------------------------------------------------
    def _run_jobs(self, gen: int, jobs: list, on_done) -> None:
        """At most DISCOVERY_CONCURRENCY requests chains in flight; on_done once every job has finished."""
        pending, state = list(jobs), {"inflight": 0, "done": False}

        def pump() -> None:
            while gen == self._gen and state["inflight"] < self.DISCOVERY_CONCURRENCY and pending:
                job = pending.pop(0)
                state["inflight"] += 1
                job(finished)
            if gen == self._gen and state["inflight"] == 0 and not pending and not state["done"]:
                state["done"] = True
                on_done()

        def finished() -> None:
            state["inflight"] -= 1
            QTimer.singleShot(0, pump)  # never recurse through hundreds of chained callbacks

        pump()

    def _discover(self, gen: int) -> None:
        if gen != self._gen:
            return
        self._set_not_ready(self.DISCOVERING, "")
        started = time.monotonic()
        self._log("info", f"{DEVMGR} get_devices")

        def on_devices(result, error) -> None:
            if error:
                text = f"Device Manager is not available on the Hub yet ({fmt_error(error)}); retrying."
                self._log("warning", text)
                self._set_state(self.DISCOVERING, text)
                QTimer.singleShot(self.DEVMGR_RETRY_MS, lambda: self._discover(gen) if self.state == self.DISCOVERING else None)
                return
            objids = [str(o) for o in (result or [])]
            found: dict = {"sensors": {}, "names": {}, "errors": 0}
            jobs = [lambda done, o=objid: self._load_device(gen, o, found, done) for objid in objids]
            self._run_jobs(gen, jobs, lambda: self._commit(gen, found, len(objids), started))
        self._req(gen, DEVMGR, "cue.devmgr.DeviceManager.get_devices", {}, on_devices)

    def _get_property(self, gen: int, objid: str, prop: str, callback) -> None:
        self._req(gen, objid, "cue.devmgr.Device.get_property", {"id": prop}, callback)

    def _load_device(self, gen: int, objid: str, found: dict, done) -> None:
        """type + id + name of one device; a Sensor also gets its sensor properties and a notification subscription."""
        record: dict = {}
        remaining = {"n": 3}

        def finish() -> None:
            remaining["n"] -= 1
            if remaining["n"]:
                return
            device_id = plain_id(record.get("id"))
            if device_id:
                found["names"][device_id] = str(record.get("name") or "")
            if record.get("type") == "Sensor" and device_id:
                self._load_sensor(gen, objid, device_id, str(record.get("name") or ""), found, done)
            else:
                done()

        def store(key: str, extract=property_value):
            def on_reply(result, error) -> None:
                if error:
                    found["errors"] += 1
                else:
                    record[key] = extract(result)
                finish()
            return on_reply
        self._get_property(gen, objid, "cue.device_type", store("type"))
        self._req(gen, objid, "cue.devmgr.Device.get_id", {}, store("id", lambda r: r))
        self._get_property(gen, objid, DEVICE_NAME, store("name"))

    def _load_sensor(self, gen: int, objid: str, sensor_id: str, name: str, found: dict, done) -> None:
        sensor = LiveSensor(id=sensor_id, objid=objid, name=name)
        remaining = {"n": len(SENSOR_PROPS)}

        def finish() -> None:
            remaining["n"] -= 1
            if remaining["n"] == 0:
                found["sensors"][sensor_id] = sensor
                self._subscribe(gen, objid, DEVICE_NOTIFICATION, done)

        for key, prop in SENSOR_PROPS.items():
            def on_reply(result, error, key=key) -> None:
                if not error:  # a missing property (e.g. cue.sensor_unit: NotSupported) keeps the default
                    value = property_value(result)
                    setattr(sensor, key, plain_id(value) if key == "owner_id" else ("" if value is None else str(value)))
                finish()
            self._get_property(gen, objid, prop, on_reply)

    def _finish_sensor(self, sensor: LiveSensor) -> None:
        """Raw Hub data -> what the widget receives."""
        imperial = self.imperial
        sensor.type, sensor.kind = widget_type(sensor.dm_type), widget_kind(sensor.dm_kind)
        sensor.device = self.device_names.get(sensor.owner_id) or sensor.source_name
        sensor.units, sensor.units_verified = widget_units(sensor.type, imperial)
        sensor.value = widget_value(sensor.raw_value, sensor.type, imperial)
        if not sensor.units_verified:
            self.unverified_unit_types.add(sensor.type)

    def _commit(self, gen: int, found: dict, device_count: int, started: float) -> None:
        if gen != self._gen:
            return
        previous = set(self.sensors)
        self.device_names = found["names"]
        self.device_count = device_count
        for sensor in found["sensors"].values():
            self._finish_sensor(sensor)
        self.sensors = found["sensors"]
        self._by_obj = {s.objid: s.id for s in self.sensors.values()}
        self.discovery_seconds = time.monotonic() - started
        self._failures = 0
        self._set_state(self.CONNECTED, "")
        errors = f", {found['errors']} property request(s) failed" if found["errors"] else ""
        self._log("info", f"{len(self.sensors)} sensors discovered among {device_count} devices in {self.discovery_seconds:.1f} s{errors}")
        if self.unverified_unit_types:
            self._log("warning", "units not observed on the real plugin, sent as '': " + ", ".join(sorted(self.unverified_unit_types)))
        for sensor_id in sorted(previous - set(self.sensors)):
            self.removed.emit(sensor_id)
        for sensor_id in sorted(self.sensors):
            (self.dataChanged if sensor_id in previous else self.added).emit(sensor_id)

    # ---- internals: notifications ----------------------------------------------------------------------------------------
    def _on_notification(self, gen: int, msg: dict) -> None:
        if gen != self._gen:
            return
        self.notifications += 1
        signal, source, params = msg.get("signal"), str(msg.get("source")), msg.get("params") or {}
        if not isinstance(params, (dict, str)):
            return
        if signal == DEVICE_NOTIFICATION:
            sensor_id = self._by_obj.get(source)
            if sensor_id is None or not isinstance(params, dict) or params.get("id") != PROPERTY_CHANGED:
                return
            data = params.get("data") if isinstance(params.get("data"), dict) else {}
            prop = data.get("property_id")
            if prop == SENSOR_VALUE:
                if "value" in data:
                    self._apply_raw(sensor_id, data["value"])
                else:  # e.g. CdcsDevice: only property_id
                    self.refetches += 1
                    self._get_property(gen, source, SENSOR_VALUE,
                                       lambda r, e, s=sensor_id: self._apply_raw(s, property_value(r)) if not e else None)
            elif prop == DEVICE_NAME:
                self._get_property(gen, source, DEVICE_NAME, lambda r, e, s=sensor_id: self._rename(s, property_value(r)) if not e else None)
        elif signal == "cue.devmgr.DeviceManager.device_connected" and isinstance(params, dict):
            self._on_device_connected(gen, params.get("device_id"))
        elif signal == "cue.devmgr.DeviceManager.device_disconnected" and isinstance(params, dict):
            self._on_device_disconnected(params.get("device_id"))
        elif signal in ("cue.Bus.known_object_unregistered", "cue.Bus.known_object_registered") and _objname(params) == DEVMGR:
            if signal.endswith("unregistered"):
                self._by_obj = {}
                self._log("warning", "Device Manager left the Hub; waiting for it to come back")
                self._set_not_ready(self.DISCOVERING, "Device Manager left the Hub; waiting for it to come back.")
            else:
                self._log("info", "Device Manager is back on the Hub; discovering again")
                for name in ("device_connected", "device_disconnected"):
                    self._subscribe(gen, DEVMGR, f"cue.devmgr.DeviceManager.{name}")
                self._discover(gen)

    def _apply_raw(self, sensor_id: str, raw) -> None:
        sensor = self.sensors.get(sensor_id)
        if sensor is None:
            return
        sensor.raw_value = "" if raw is None else str(raw)
        sensor.updates += 1
        sensor.last_update = time.monotonic()
        self.value_updates += 1
        value = widget_value(sensor.raw_value, sensor.type, self.imperial)
        if value != sensor.value:
            sensor.value = value
            if self.service_ready:
                self.valueChanged.emit(sensor_id, value)

    def _rename(self, sensor_id: str, name) -> None:
        sensor = self.sensors.get(sensor_id)
        if sensor is not None and str(name or "") != sensor.name:
            sensor.name = str(name or "")
            self.device_names[sensor_id] = sensor.name
            if self.service_ready:
                self.dataChanged.emit(sensor_id)

    def _on_device_connected(self, gen: int, device_id) -> None:
        if not self.service_ready or not device_id:
            return  # a running discovery sees it

        def on_objid(result, error) -> None:
            if error or not result:
                self._log("warning", f"device {device_id} connected but get_device failed: {fmt_error(error)}")
                return
            found: dict = {"sensors": {}, "names": {}, "errors": 0}
            self._load_device(gen, str(result), found, lambda: self._merge(found))
        self._req(gen, DEVMGR, "cue.devmgr.DeviceManager.get_device", {"device_id": device_id}, on_objid)

    def _merge(self, found: dict) -> None:
        self.device_names.update(found["names"])
        self.device_count += 1
        for sensor in found["sensors"].values():
            self._finish_sensor(sensor)
            existed = sensor.id in self.sensors
            self.sensors[sensor.id] = sensor
            self._by_obj[sensor.objid] = sensor.id
            self._log("info", f"sensor {'updated' if existed else 'added'}: {sensor.name} ({sensor.id})")
            (self.dataChanged if existed else self.added).emit(sensor.id)
        for owner_id, name in found["names"].items():  # a device that owns sensors came back: their device name
            for sensor in self.sensors.values():
                if sensor.owner_id == owner_id and sensor.device != (name or sensor.source_name):
                    sensor.device = name or sensor.source_name
                    self.dataChanged.emit(sensor.id)
        self.stateChanged.emit()

    def _on_device_disconnected(self, device_id) -> None:
        sensor_id = plain_id(device_id)
        sensor = self.sensors.pop(sensor_id, None)
        if sensor is None:
            return
        self._by_obj.pop(sensor.objid, None)
        self._log("info", f"sensor removed: {sensor.name} ({sensor_id})")
        self.removed.emit(sensor_id)
        self.stateChanged.emit()


def _objname(params) -> str:
    """cue.Bus.known_object_* carries the object name (BusKnownObjectName); accept {"objname": ...} or a bare string."""
    if isinstance(params, str):
        return params
    if isinstance(params, dict):
        if isinstance(params.get("objname"), str):
            return params["objname"]
        return next((v for v in params.values() if isinstance(v, str)), "")
    return ""


_HUB: OlympusHubSource | None = None


def hub_source() -> OlympusHubSource:
    """The one live source shared by every page and the Sensors tab."""
    global _HUB
    if _HUB is None:
        _HUB = OlympusHubSource()
    return _HUB