r"""Live Sensors (Olympus Hub) tests: OlympusHubSource against a FAKE local Hub (QWebSocketServer speaking the bus protocol of
ipc-client / Devices.cidl), the Sensorsdataprovider bridge on a live source, and Sensor Bar with live sensors.

No real Hub needed (the fake one plays the Hub). The real-Hub checks are manual (see the Phase 2 report).
Run: .venv\Scripts\python.exe -m unittest discover -s tests -p test_live_sensors.py -v
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)
from PySide6.QtCore import QObject, QTimer, Signal  # noqa: E402
from PySide6.QtNetwork import QHostAddress  # noqa: E402
from PySide6.QtWebSockets import QWebSocketServer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgetdevtool.console_log import ConsoleLog  # noqa: E402
from widgetdevtool.paths import WIDGETS_DIR  # noqa: E402
from widgetdevtool.olympus_hub import (  # noqa: E402
    MEASUREMENT_IMPERIAL, MEASUREMENT_METRIC, OlympusHubSource, widget_kind, widget_type, widget_units, widget_value)
from widgetdevtool.sensor_source import sources  # noqa: E402
from widgetdevtool.sensors_mock import SensorsdataproviderMock, catalog  # noqa: E402

TEMP_ID, FAN_ID, LOAD_ID, CLOCK_ID, EFF_ID, NEW_ID = (f"0000000{n}-aaaa-4bbb-8ccc-00000000000{n}" for n in range(1, 7))
CPU_ID = "0000000c-aaaa-4bbb-8ccc-00000000000c"


def sensor_device(sid, name, dm_type, dm_kind, value, unit=None, owner=CPU_ID, source="Source Name"):
    props = {"cue.device_type": "Sensor", "cue.device_name": name, "cue.sensor_type": dm_type, "cue.sensor_kind": dm_kind,
             "cue.sensor_value": value, "cue.owner_device_id": "{" + owner + "}", "cue.sensor_source_name": source}
    if unit is not None:
        props["cue.sensor_unit"] = unit
    return {"id": "{" + sid.upper() + "}", "props": props}


def default_devices() -> dict:
    return {
        "dm/1": {"id": "{" + CPU_ID + "}", "props": {"cue.device_type": "CPU", "cue.device_name": "Test CPU"}},
        "dm/2": sensor_device(TEMP_ID, "Package", "Temperature", "Package", "38", "\u00b0C"),
        "dm/3": sensor_device(FAN_ID, "Fan", "Fan", "Default", "1206", owner="ffffffff-0000-0000-0000-000000000000",
                              source="iCUE LINK RX RGB"),
        "dm/4": sensor_device(LOAD_ID, "Available Capacity (c:)", "Load", "Default", "148.6", "GB"),
        "dm/5": sensor_device(CLOCK_ID, "P-Core #1", "ClockSpeed", "Default", "5287.07", "MHz"),
    }


class FakeHub(QObject):
    """A Hub bus on 127.0.0.1: cue.Bus.authenticate/subscribe/unsubscribe, devmgr/DeviceManager get_devices/get_device,
    cue.devmgr.Device get_id/get_property (NotSupported for a missing property). Records every request."""

    def __init__(self, devices: dict | None = None, port: int = 0) -> None:
        super().__init__()
        self.devices = devices if devices is not None else default_devices()
        self.requests: list[dict] = []
        self.subscriptions: set[tuple[str, str]] = set()
        self.silent_methods: set[str] = set()  # never answered (timeouts)
        self.clients: list = []
        self.server = QWebSocketServer("fake-hub", QWebSocketServer.SslMode.NonSecureMode)
        self.server.newConnection.connect(self._on_connection)
        assert self.server.listen(QHostAddress.SpecialAddress.LocalHost, port)
        self.port = self.server.serverPort()

    def close(self) -> None:
        for client in list(self.clients):
            client.abort()
        self.server.close()

    def drop_clients(self) -> None:
        for client in list(self.clients):
            client.close()

    def methods(self) -> list[str]:
        return [r.get("method") for r in self.requests]

    def _on_connection(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.clients.append(socket)
            socket.textMessageReceived.connect(lambda text, s=socket: self._on_text(s, text))
            socket.disconnected.connect(lambda s=socket: self.clients.remove(s) if s in self.clients else None)

    def notify(self, signal: str, source: str, params: dict) -> None:
        if (source, signal) not in self.subscriptions:
            return
        for client in self.clients:
            client.sendTextMessage(json.dumps({"signal": signal, "source": source, "params": params}))

    def value_changed(self, objid: str, value: str | None, inline: bool = True) -> None:
        self.devices[objid]["props"]["cue.sensor_value"] = value
        data = {"property_id": "cue.sensor_value", **({"value": value} if inline else {})}
        self.notify("cue.devmgr.Device.notification_received", objid, {"id": "cue.property_changed", "data": data})

    def _reply(self, socket, rid, result=None, error=None) -> None:
        socket.sendTextMessage(json.dumps({"id": rid, "error": error} if error else {"id": rid, "result": result}))

    def _on_text(self, socket, text: str) -> None:
        msg = json.loads(text)
        self.requests.append(msg)
        rid, target, method, params = msg["id"], msg["target"], msg["method"], msg.get("params") or {}
        if method in self.silent_methods:
            return
        if method == "cue.Bus.authenticate":
            return self._reply(socket, rid, {"objid_prefix": "#1/"})
        if method == "cue.Bus.subscribe":
            self.subscriptions.add((params["source"], params["signal"]))
            return self._reply(socket, rid, None)
        if method == "cue.Bus.unsubscribe":
            self.subscriptions.clear()
            return self._reply(socket, rid, None)
        if method == "cue.devmgr.DeviceManager.get_devices":
            return self._reply(socket, rid, list(self.devices))
        if method == "cue.devmgr.DeviceManager.get_device":
            objid = next((o for o, d in self.devices.items() if d["id"].strip("{}").lower() == params["device_id"].strip("{}").lower()), None)
            return self._reply(socket, rid, objid) if objid else self._reply(socket, rid, error={"code": "no-such-object"})
        device = self.devices.get(target)
        if device is None:
            return self._reply(socket, rid, error={"code": "no-such-object", "message": target})
        if method == "cue.devmgr.Device.get_id":
            return self._reply(socket, rid, device["id"])
        if method == "cue.devmgr.Device.get_property":
            if params["id"] not in device["props"]:
                return self._reply(socket, rid, error={"code": "not-supported", "message": "Property is not supported"})
            return self._reply(socket, rid, {"data": {"value": device["props"][params["id"]]}})
        self._reply(socket, rid, error={"code": "no-such-method"})


class FakeDetector(QObject):
    """Stands in for HubPortDetector (process command lines)."""

    finished = Signal(list, str)

    def __init__(self, ports: list | None = None) -> None:
        super().__init__()
        self.ports = ports or []
        self.starts = 0

    def start(self) -> None:
        self.starts += 1
        QTimer.singleShot(0, lambda: self.finished.emit(list(self.ports), ""))

    def cancel(self) -> None:
        pass


class Recorder:
    def __init__(self, source) -> None:
        self.events: list[tuple] = []
        source.added.connect(lambda s: self.events.append(("added", s)))
        source.removed.connect(lambda s: self.events.append(("removed", s)))
        source.dataChanged.connect(lambda s: self.events.append(("data", s)))
        source.valueChanged.connect(lambda s, v: self.events.append(("value", s, v)))
        source.unitsChanged.connect(lambda s, u: self.events.append(("units", s, u)))

    def of(self, kind: str) -> list[tuple]:
        return [e for e in self.events if e[0] == kind]


class HubTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate, timeout: float = 10.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)

    def make_hub(self, devices: dict | None = None, port: int = 0) -> FakeHub:
        hub = FakeHub(devices, port)
        self.addCleanup(hub.close)
        return hub

    def make_source(self, detector=None, timeout_ms: int = 2000) -> OlympusHubSource:
        source = OlympusHubSource(request_timeout_ms=timeout_ms, connect_timeout_ms=2000, detector=detector or FakeDetector())
        source.RECONNECT_DELAYS_MS = (50, 100, 200)
        source.measurement = MEASUREMENT_METRIC
        self.addCleanup(source.shutdown)
        return source

    def connected(self, hub: FakeHub, **kwargs) -> OlympusHubSource:
        source = self.make_source(**kwargs)
        source.start(port=hub.port)
        self.assertTrue(self.wait(lambda: source.state == source.CONNECTED), (source.state, source.error))
        return source

class MappingTests(unittest.TestCase):
    """Strings observed from the real plugin (probe in the Renderer, 212 sensors) and iCUE 5 HtmlSensorUtils.cpp."""

    def test_type_and_kind_in_kebab_case(self) -> None:
        for dm, expected in (("Temperature", "temperature"), ("ClockSpeed", "clock-speed"), ("CasLatency", "cas-latency"),
                             ("RasToCasDelay", "ras-to-cas-delay"), ("PerfLimiter", "perf-limiter"), ("Tras", "tras")):
            self.assertEqual(widget_type(dm), expected)
        for dm, expected in (("Default", "default"), ("Package", "package"), ("GpuTemp", "gpu-temp"), ("Voltage3_3", "voltage-3-3"),
                             ("Voltage12", "voltage-12"), ("VoltageCore", "voltage-core"), ("CurrentItem12V", "current-item-12v"),
                             ("Current12V", "current-12v"), ("CurrentItem12V2x6", "current-item-12v-2x6")):
            self.assertEqual(widget_kind(dm), expected)
        self.assertEqual((widget_type(None), widget_kind("")), ("", ""))

    def test_units_from_the_type_and_temperature_conversion(self) -> None:
        self.assertEqual(widget_units("temperature", True), ("\u00b0F", True))
        self.assertEqual(widget_units("temperature", False), ("\u00b0C", True))
        self.assertEqual(widget_units("fan", True), ("RPM", True))
        self.assertEqual(widget_units("load", True), ("%", True))
        self.assertEqual(widget_units("clock-speed", True), ("", True))  # the plugin sends "" although the Hub says MHz
        self.assertEqual(widget_units("efficiency", True), ("", False))  # never observed: not invented
        self.assertEqual(widget_value("45", "temperature", True), "113")
        self.assertEqual(widget_value("42.0313", "temperature", True), "107.65634")
        for celsius, plugin in (("42", "107.6"), ("37", "98.6"), ("62", "143.6")):  # observed plugin strings (* 1.8 adds float noise)
            self.assertEqual(widget_value(celsius, "temperature", True), plugin)
        self.assertEqual(widget_value("38", "temperature", False), "38")
        self.assertEqual(widget_value("12.192", "voltage", True), "12.192")  # raw string unchanged
        self.assertEqual(widget_value("n/a", "temperature", True), "n/a")


class HubSourceTests(HubTestCase):
    def test_discovery_metadata_units_and_raw_values(self) -> None:
        hub = self.make_hub()
        source = self.connected(hub)
        self.assertEqual(source.ids(), sorted([TEMP_ID, FAN_ID, LOAD_ID, CLOCK_ID]))  # bare lower-case ids, non-sensors skipped
        temp, fan, load, clock = (source.get(i) for i in (TEMP_ID, FAN_ID, LOAD_ID, CLOCK_ID))
        self.assertEqual((temp.name, temp.device, temp.type, temp.kind, temp.value, temp.units), ("Package", "Test CPU", "temperature",
                                                                                                    "package", "38", "\u00b0C"))
        self.assertEqual((temp.raw_value, temp.raw_unit, temp.dm_type), ("38", "\u00b0C", "Temperature"))
        self.assertEqual((fan.device, fan.units, fan.raw_unit), ("iCUE LINK RX RGB", "RPM", None))  # missing cue.sensor_unit
        self.assertEqual((load.units, load.raw_unit), ("%", "GB"))
        self.assertEqual((clock.units, clock.raw_unit, clock.value), ("", "MHz", "5287.07"))
        self.assertEqual(source.device_count, 5)
        self.assertIn(("dm/2", "cue.devmgr.Device.notification_received"), hub.subscriptions)
        self.assertNotIn(("dm/1", "cue.devmgr.Device.notification_received"), hub.subscriptions)
        self.assertEqual(hub.methods()[0], "cue.Bus.authenticate")
        source.set_measurement(MEASUREMENT_IMPERIAL)
        self.assertEqual((temp.value, temp.units, temp.raw_value), ("100.4", "\u00b0F", "38"))

    def test_value_updates_inline_and_refetched(self) -> None:
        hub = self.make_hub()
        source = self.connected(hub)
        rec = Recorder(source)
        hub.value_changed("dm/2", "41.5")
        self.assertTrue(self.wait(lambda: rec.of("value") == [("value", TEMP_ID, "41.5")]))
        before = source.refetches
        hub.value_changed("dm/3", "1300", inline=False)  # e.g. CdcsDevice: only property_id
        self.assertTrue(self.wait(lambda: ("value", FAN_ID, "1300") in rec.events))
        self.assertEqual(source.refetches, before + 1)
        self.assertEqual(source.get(FAN_ID).raw_value, "1300")
        hub.value_changed("dm/2", "41.5")  # unchanged: no signal
        self.pump(0.2)
        self.assertEqual(len(rec.of("value")), 2)

    def test_unobserved_type_without_unit_is_sent_empty_and_listed(self) -> None:
        devices = default_devices()
        devices["dm/6"] = sensor_device(EFF_ID, "PSU Efficiency", "Efficiency", "Default", "91")
        source = self.connected(self.make_hub(devices))
        self.assertEqual((source.get(EFF_ID).units, source.get(EFF_ID).units_verified), ("", False))
        self.assertEqual(source.unverified_unit_types, {"efficiency"})

    def test_default_id_and_unknown_answers(self) -> None:
        devices = default_devices()
        devices["dm/6"] = sensor_device("00000000-aaaa-4bbb-8ccc-000000000000", "CPU Core", "Temperature", "CpuTemp", "40", "\u00b0C")
        source = self.connected(self.make_hub(devices))
        self.assertEqual(source.default_id("temperature"), "00000000-aaaa-4bbb-8ccc-000000000000")  # lowest id
        self.assertEqual(source.default_id("Temperature", "package"), TEMP_ID)  # preferred kind, case-insensitive type
        self.assertEqual(source.default_id("pump"), "")
        self.assertIsNone(source.get("{" + TEMP_ID + "}"))  # braces: unknown, as the plugin
        self.assertEqual((source.unknown_answer("value"), source.unknown_answer("units"), source.unknown_answer("name")), ("-1", "-1", ""))

    def test_device_connected_and_disconnected(self) -> None:
        hub = self.make_hub()
        source = self.connected(hub)
        rec = Recorder(source)
        hub.devices["dm/9"] = sensor_device(NEW_ID, "Coolant Temp", "Temperature", "Default", "35.3")
        hub.notify("cue.devmgr.DeviceManager.device_connected", "devmgr/DeviceManager", {"device_id": "{" + NEW_ID + "}"})
        self.assertTrue(self.wait(lambda: ("added", NEW_ID) in rec.events))
        self.assertEqual(source.get(NEW_ID).name, "Coolant Temp")
        hub.value_changed("dm/9", "36")
        self.assertTrue(self.wait(lambda: ("value", NEW_ID, "36") in rec.events))
        hub.notify("cue.devmgr.DeviceManager.device_disconnected", "devmgr/DeviceManager", {"device_id": "{" + FAN_ID.upper() + "}"})
        self.assertTrue(self.wait(lambda: ("removed", FAN_ID) in rec.events))
        self.assertNotIn(FAN_ID, source.ids())

    def test_disconnect_marks_not_ready_then_reconnects(self) -> None:
        hub = self.make_hub()
        source = self.connected(hub)
        rec = Recorder(source)
        hub.drop_clients()
        self.assertTrue(self.wait(lambda: not source.service_ready))
        self.assertEqual(sorted(e[1] for e in rec.of("data")), source and sorted([TEMP_ID, FAN_ID, LOAD_ID, CLOCK_ID]))  # pages told once
        self.assertEqual((source.ids(), source.get(TEMP_ID), source.default_id("temperature")), ([], None, ""))
        self.assertTrue(self.wait(lambda: source.state == source.CONNECTED))  # reconnected and rediscovered
        self.assertEqual(len(source.ids()), 4)
        self.assertEqual(rec.of("added"), [])  # same sensors: dataChanged, not added
        hub.value_changed("dm/2", "50")
        self.assertTrue(self.wait(lambda: ("value", TEMP_ID, "50") in rec.events))

    def test_hub_restart_on_another_port_is_detected_again(self) -> None:
        hub = self.make_hub()
        detector = FakeDetector([(hub.port, "iCUE Device Manager.exe")])
        source = self.make_source(detector)
        source.start()  # auto port
        self.assertTrue(self.wait(lambda: source.state == source.CONNECTED))
        self.assertEqual((source.port, source.port_origin), (hub.port, "auto-detected from iCUE Device Manager.exe"))
        hub.close()
        hub2 = self.make_hub()
        detector.ports = [(hub2.port, "TrayManager.exe")]
        self.assertTrue(self.wait(lambda: source.state == source.CONNECTED and source.port == hub2.port, 15), (source.state, source.error))
        self.assertGreaterEqual(detector.starts, 2)

    def test_hub_not_running_gives_a_clear_error(self) -> None:
        source = self.make_source(FakeDetector([]))
        source.start()
        self.assertTrue(self.wait(lambda: source.state == source.ERROR))
        self.assertIn("Olympus Hub is not running", source.error)
        self.assertFalse(source.service_ready)

    def test_manual_port_without_hub(self) -> None:
        hub = self.make_hub()
        port = hub.port
        hub.close()
        source = self.make_source()
        source.start(port=port)
        self.assertTrue(self.wait(lambda: source.state == source.ERROR))
        self.assertIn(f"127.0.0.1:{port}", source.error)

    def test_request_timeout(self) -> None:
        hub = self.make_hub()
        hub.silent_methods.add("cue.Bus.authenticate")
        source = self.make_source(timeout_ms=300)
        source.start(port=hub.port)
        self.assertTrue(self.wait(lambda: source.state == source.ERROR))
        self.assertIn("timeout", source.error)

    def test_device_manager_restart(self) -> None:
        hub = self.make_hub()
        source = self.connected(hub)
        hub.notify("cue.Bus.known_object_unregistered", "cue/Bus", {"objname": "devmgr/DeviceManager"})
        self.assertTrue(self.wait(lambda: source.state == source.DISCOVERING and not source.service_ready))
        hub.notify("cue.Bus.known_object_registered", "cue/Bus", {"objname": "devmgr/DeviceManager"})
        self.assertTrue(self.wait(lambda: source.state == source.CONNECTED))
        self.assertEqual(len(source.ids()), 4)

    def test_stop_unsubscribes_then_closes(self) -> None:
        hub = self.make_hub()
        source = self.connected(hub)
        source.stop()
        self.assertTrue(self.wait(lambda: not hub.clients))
        self.assertEqual(hub.methods()[-1], "cue.Bus.unsubscribe")
        self.assertEqual((source.state, source.service_ready), (source.DISCONNECTED, False))

class BridgeLiveTests(HubTestCase):
    """window.plugins.Sensorsdataprovider on the live source (no page: the QObject the web channel exposes)."""

    def setUp(self) -> None:
        self.hub = self.make_hub()
        self.source = self.connected(self.hub)
        self.bridge = SensorsdataproviderMock(ConsoleLog(), self.source)
        self.addCleanup(self.bridge.detach)
        self.replies: dict[int, object] = {}
        self.values: list[tuple] = []
        self.bridge.asyncResponse.connect(lambda rid, v: self.replies.__setitem__(rid, v))
        self.bridge.sensorValueChanged.connect(lambda s, v: self.values.append((s, v)))

    def ask(self, method: str, *args) -> object:
        rid = 1000 + len(self.replies)
        getattr(self.bridge, method)(rid, *args)
        self.assertTrue(self.wait(lambda: rid in self.replies), method)
        return self.replies[rid]

    def test_answers_come_from_the_live_cache(self) -> None:
        self.assertEqual(self.ask("getSensorValue", TEMP_ID), "38")
        self.assertEqual(self.ask("getSensorUnits", FAN_ID), "RPM")
        self.assertEqual(self.ask("getSensorType", CLOCK_ID), "clock-speed")
        self.assertEqual(self.ask("getSensorDeviceName", TEMP_ID), "Test CPU")
        self.assertIs(self.ask("sensorIsConnected", TEMP_ID), True)
        self.assertEqual(self.ask("getAllSensorIds"), sorted([TEMP_ID, FAN_ID, LOAD_ID, CLOCK_ID]))
        self.assertEqual(self.bridge.getDefaultSensorIdBlock("temperature"), TEMP_ID)  # synchronous, from the cache
        self.assertEqual((self.ask("getSensorValue", "{" + TEMP_ID + "}"), self.ask("getSensorName", "nope")), ("-1", ""))

    def test_only_asked_sensors_are_forwarded_and_values_are_coalesced(self) -> None:
        self.ask("getSensorValue", TEMP_ID)
        for value in ("39", "40", "41"):
            self.hub.value_changed("dm/2", value)
        self.hub.value_changed("dm/3", "1500")  # never asked by this page
        self.assertTrue(self.wait(lambda: self.values))
        self.pump(SensorsdataproviderMock.LIVE_FLUSH_MS / 1000 + 0.2)
        self.assertEqual(self.values, [(TEMP_ID, "41")])  # the latest value, once; FAN not forwarded
        self.ask("getAllSensorIds")  # the page now wants every sensor
        self.hub.value_changed("dm/3", "1600")
        self.assertTrue(self.wait(lambda: (FAN_ID, "1600") in self.values))

    def test_not_ready_gives_empty_answers_and_tells_the_page(self) -> None:
        data: list[str] = []
        self.bridge.sensorDataChanged.connect(data.append)
        self.ask("getSensorValue", TEMP_ID)
        self.source.RECONNECT_DELAYS_MS = (60000,)
        self.hub.drop_clients()
        self.assertTrue(self.wait(lambda: not self.source.service_ready))
        self.assertTrue(self.wait(lambda: TEMP_ID in data))
        self.assertEqual(self.ask("getSensorValue", TEMP_ID), "")  # replica not initialized: QString{}
        self.assertEqual(self.ask("getAllSensorIds"), [])
        self.assertEqual(self.bridge.getDefaultSensorIdBlock("temperature"), "")

    def test_detached_bridges_are_released(self) -> None:
        """A lambda connected to a child QTimer once kept every reloaded bridge alive (cycle through C++, invisible to gc)."""
        import gc
        import weakref
        refs = []
        for source in (None, self.source):  # following the active source, and fixed on the live source
            bridge = SensorsdataproviderMock(ConsoleLog(), source)
            bridge.getSensorValue(1, TEMP_ID)
            bridge.detach()
            refs.append(weakref.ref(bridge))
            del bridge
        self.pump(0.1)
        gc.collect()
        self.assertEqual([r() for r in refs], [None, None])

    def test_following_bridge_retargets_on_a_source_switch(self) -> None:
        self.addCleanup(sources().set_active, catalog())
        follower = SensorsdataproviderMock(ConsoleLog())
        self.addCleanup(follower.detach)
        events: list[tuple] = []
        follower.sensorDataChanged.connect(lambda s: events.append(("data", s)))
        follower.sensorAdded.connect(lambda s: events.append(("added", s)))
        mock_id = next(iter(catalog().sensors))
        follower.getSensorValue(1, mock_id)
        sources().set_active(self.source)
        self.assertIs(follower.catalog, self.source)
        self.assertEqual(events[0], ("data", mock_id))  # the page re-reads what it showed
        self.assertEqual(sorted(e[1] for e in events if e[0] == "added"), sorted(self.source.ids()))
        sources().set_active(catalog())
        self.assertIs(follower.catalog, catalog())


class PanelLiveTests(HubTestCase):
    def setUp(self) -> None:
        from widgetdevtool.sensors_panel import MODE_LIVE, MODE_MOCK, SensorsPanel
        self.MODE_LIVE, self.MODE_MOCK = MODE_LIVE, MODE_MOCK
        self.hub = self.make_hub()
        self.source = self.make_source()
        self.panel = SensorsPanel(hub=self.source)
        self.panel.show()  # the live table is only redrawn while the panel is visible
        self.addCleanup(self.panel.close)
        self.addCleanup(self.panel.shutdown)
        self.addCleanup(sources().set_active, catalog())

    def test_live_mode_connects_lists_sensors_and_mock_mode_stops(self) -> None:
        self.panel.port_edit.setText(str(self.hub.port))
        self.panel.set_mode(self.MODE_LIVE)
        self.assertIs(sources().active(), self.source)
        self.assertTrue(self.wait(lambda: self.source.state == self.source.CONNECTED))
        self.panel._refresh_live()
        self.assertEqual(self.panel.live_table.rowCount(), 4)
        self.assertIn("Connected to 127.0.0.1", self.panel.live_status.text())
        self.assertTrue(self.panel.source_status.text().startswith("Active: LIVE"))
        self.assertTrue(self.panel.mock_box.isHidden() and not self.panel.live_box.isHidden())
        self.panel.live_filter.setText("fan")
        visible = [r for r in range(self.panel.live_table.rowCount()) if not self.panel.live_table.isRowHidden(r)]
        self.assertEqual([self.panel.live_table.item(r, 0).text() for r in visible], ["Fan \u00b7 iCUE LINK RX RGB"])
        self.panel.set_mode(self.MODE_MOCK)
        self.assertIs(sources().active(), catalog())
        self.assertTrue(self.wait(lambda: not self.hub.clients))
        self.assertEqual(self.source.state, self.source.DISCONNECTED)
        self.assertTrue(self.panel.source_status.text().startswith("Active: MOCK"))

    def test_invalid_port_is_rejected_without_connecting(self) -> None:
        sources().set_active(self.source)
        self.panel.port_edit.setText("70000")
        self.assertFalse(self.panel.connect_hub())
        self.assertIn("Invalid port", self.panel.live_status.text())
        self.assertFalse(self.source.running)


def _sensorbar_folder() -> Path:
    return WIDGETS_DIR / "Sensor Bar" / "widget" / "sensorbar"


@unittest.skipUnless(_sensorbar_folder().is_dir(), "WidgetsXE Sensor Bar not present")
class SensorBarLiveTests(HubTestCase):
    """Sensor Bar (unchanged) rendering LIVE sensors from the fake Hub: discovery, value updates, Hub lost, reconnect."""

    def setUp(self) -> None:
        from test_runtime_bridge import PageTestCase  # noqa: F401  (same helpers as the other page tests)
        from test_settings_panel import SENSOR_ROWS
        from widgetdevtool.main_window import MainWindow, State
        from widgetdevtool.sensors_panel import MODE_LIVE
        self.SENSOR_ROWS = SENSOR_ROWS
        self.hub = self.make_hub()
        self.window = MainWindow()
        self.addCleanup(self._close)
        self.window.show()
        self.assertTrue(self.window.open_folder(_sensorbar_folder()))
        self.assertTrue(self.wait(lambda: self.window.state is State.LOADED, 30))
        self.window.select_device("dash_accessory_lcd", "SizeM", "landscape")
        panel = self.window.sensors_panel
        panel.port_edit.setText(str(self.hub.port))
        panel.set_mode(MODE_LIVE)
        self.source = panel.hub
        self.source.RECONNECT_DELAYS_MS = (100, 200, 400)
        self.source.set_measurement(MEASUREMENT_METRIC)
        self.assertTrue(self.wait(lambda: self.source.state == self.source.CONNECTED), (self.source.state, self.source.error))

    def _close(self) -> None:
        self.window.shutdown()
        self.pump(0.2)

    def rows(self) -> list:
        box: dict = {}
        self.window.view.page().runJavaScript(self.SENSOR_ROWS, lambda v: box.setdefault("v", v))
        self.assertTrue(self.wait(lambda: "v" in box, 10))
        return json.loads(box["v"])

    def test_rows_show_live_values_updates_and_hub_loss(self) -> None:
        self.window.bridge.set_properties({"sensorsFactoryValues": [{"sensorId": TEMP_ID, "color": "#f08e33"},
                                                                    {"sensorId": FAN_ID, "color": "#f08e33"}]})
        self.assertTrue(self.wait(lambda: [r["label"] for r in self.rows()] == ["Package", "Fan"]
                                  and all(r["value"] != "\u2014" for r in self.rows()), 15), self.rows())
        first = [r["value"] for r in self.rows()]
        print("\n   Sensor Bar live rows:", first)
        self.assertTrue(first[0].startswith("38") and "\u00b0C" in first[0], first)
        self.assertTrue(first[1].startswith("1206") and "RPM" in first[1], first)
        self.hub.value_changed("dm/2", "41.5")  # live update
        self.assertTrue(self.wait(lambda: self.rows()[0]["value"].startswith("41.5"), 10), self.rows())
        self.hub.value_changed("dm/3", "1300", inline=False)  # notification without value: re-read
        self.assertTrue(self.wait(lambda: self.rows()[1]["value"].startswith("1300"), 10), self.rows())
        self.source.RECONNECT_DELAYS_MS = (1500,)
        self.hub.drop_clients()  # Hub lost: the widget stops showing stale values
        self.assertTrue(self.wait(lambda: all(r["value"] == "\u2014" for r in self.rows()), 10), self.rows())
        print("   after the Hub was lost:", [r["value"] for r in self.rows()])
        self.assertTrue(self.wait(lambda: self.source.state == self.source.CONNECTED, 15))  # reconnected
        self.assertTrue(self.wait(lambda: self.rows()[0]["value"].startswith("41.5"), 15), self.rows())

    def test_add_sensor_uses_the_live_default(self) -> None:
        editor = self.window.settings_panel.editors["sensorsFactoryValues"]
        self.assertTrue(self.wait(lambda: editor.sensor_rows == [] and "LIVE sensors" in editor.status.text(), 5), editor.status.text())
        editor.add_sensor_button.click()
        stored = self.window.bridge.instance.data["sensorsFactoryValues"]
        self.assertEqual(stored, [{"sensorId": self.source.default_id("temperature"), "color": "#f08e33"}])
        self.assertTrue(self.wait(lambda: [r["label"] for r in self.rows()] == ["Package"], 15), self.rows())
        self.assertTrue(self.wait(lambda: editor.sensor_rows and editor.sensor_rows[0][0].count() == 4, 5))  # live sensors in the combo