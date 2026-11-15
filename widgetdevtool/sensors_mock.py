r"""MOCK of the iCUE Sensors plugin (widgetbuilder.sensorsdataprovider:Sensors:1.0) for WidgetDevTool.

Interface copied from the iCUE source (branch v5.55, NOT verified against release 5.52.93):
- qml/widgetbuilder/sensordataprovider/qml/Sensors.qml: signals asyncResponse(int requestId, var value), sensorAdded(string),
  sensorRemoved(string), sensorDataChanged(string), sensorUnitsChanged(string, string), sensorValueChanged(string, string);
  methods getSensorValue/Units/Name/DeviceName/Type/Kind(requestId, sensorId), getAllSensorIds(requestId),
  sensorIsConnected(requestId, sensorId), getDefaultSensorId(requestId, type, kind=""), getDefaultSensorIdBlock(type, kind="").
- src/SensorDataProvider.cpp: every async method answers ONCE through asyncResponse(requestId, value); value, units, names,
  type and kind are QStrings (so a value reaches the widget as a STRING, e.g. "45.3"), getAllSensorIds a QStringList,
  sensorIsConnected a bool. getDefaultSensorIdBlock is synchronous and returns a QString.
- lib/widgets/htmlwidgetcore/src/JavaScriptExecutor.cpp: when the settings are parsed, every required plugin is created in the
  parser's JS engine as global `plugins.<Name>` (Name = capitalised last module segment), which is what a data-default like
  `plugins.Sensorsdataprovider.getDefaultSensorIdBlock('temperature')` needs.

NOT equivalent to iCUE (everything below is WidgetDevTool's own):
- The sensors are a fixed MOCK catalog (MOCK_SENSORS), not this PC's hardware; ids, names, values and units are invented.
- SensorDataController.cpp requestAsync(): while the QtRemoteObjects replica is NOT initialized every async call answers at once
  with ReturnType{} ("" / empty list / false); onStateChanged(Valid) emits sensorAdded for every id. `SensorCatalog.service_ready`
  = False reproduces that state (getDefaultSensorIdBlock: the real one first waits up to 5 s, the mock answers "" at once).
- The real plugin talks to a sensor service over QtRemoteObjects; reply timing, update rate, the answer for an unknown id and
  how getDefaultSensorId picks a sensor are not known. The mock answers on the next event-loop turn, never pushes values by
  itself, answers "" for an unknown id and picks the first catalog sensor of the requested type (preferred kind first).
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from .console_log import ConsoleLog
from .sensor_source import SensorSource, active_source, sources

DEFAULT_SENSOR_COLOR = "#f08e33"  # SensorsFactoryParametersModel.cpp cDefaultSensorColor = "#F08E33"; QColor::name() is lower case
UNAVAILABLE = "-1"  # a value AND units of "-1": what Sensor Bar treats as "sensor unavailable" (widget code, not an iCUE fact)


@dataclass
class MockSensor:
    id: str
    name: str
    device: str
    type: str
    kind: str
    value: str
    units: str
    connected: bool = True


def _sid(n: int) -> str:
    return f"00000000-0000-4000-8000-mock{n:08d}"


MOCK_SENSORS = (
    MockSensor(_sid(1), "CPU Package", "MOCK CPU", "temperature", "", "45.3", "\u00b0C"),
    MockSensor(_sid(2), "GPU Temperature", "MOCK GPU", "temperature", "", "52.0", "\u00b0C"),
    MockSensor(_sid(3), "CPU Load", "MOCK CPU", "load", "", "23.0", "%"),
    MockSensor(_sid(4), "GPU Load", "MOCK GPU", "load", "gpu-load", "61.0", "%"),
    MockSensor(_sid(5), "Fan #1", "MOCK Commander", "fan", "", "1180", "RPM"),
    MockSensor(_sid(6), "Pump", "MOCK AIO", "pump", "", "2400", "RPM"),
    MockSensor(_sid(7), "Disconnected Sensor", "MOCK Device", "temperature", "", "30.0", "\u00b0C", connected=False),
    MockSensor(_sid(8), "Unavailable Sensor", "MOCK Device", "temperature", "", UNAVAILABLE, UNAVAILABLE),
)


class SensorCatalog(SensorSource):
    """The mock 'PC' (MockSensorSource): one set of sensors shared by every widget instance. Changing a sensor notifies every page."""

    label = "mock catalog"

    def __init__(self, sensors=MOCK_SENSORS) -> None:
        super().__init__()
        self.sensors: dict[str, MockSensor] = {s.id: MockSensor(**vars(s)) for s in sensors}
        self.service_ready = True  # False: the replica is not initialized (every async answer is empty, see the module doc)

    def set_service_ready(self, ready: bool) -> None:
        """Replica state. Becoming ready emits sensorAdded for every sensor (SensorDataController::onStateChanged(Valid))."""
        if bool(ready) == self.service_ready:
            return
        self.service_ready = bool(ready)
        if self.service_ready:
            for sensor_id in self.sensors:
                self.added.emit(sensor_id)

    def ids(self) -> list[str]:
        return list(self.sensors)

    def get(self, sensor_id: str) -> MockSensor | None:
        return self.sensors.get(str(sensor_id).strip("{}"))

    def default_id(self, sensor_type: str, kind: str = "") -> str:
        if not self.service_ready:
            return ""
        candidates = [s for s in self.sensors.values() if s.type == sensor_type]
        preferred = [s for s in candidates if kind and s.kind == kind]
        chosen = (preferred or candidates or [None])[0]
        return chosen.id if chosen else ""

    def set_value(self, sensor_id: str, value: str) -> None:
        sensor = self.get(sensor_id)
        if sensor is not None and sensor.value != str(value):
            sensor.value = str(value)
            self.valueChanged.emit(sensor.id, sensor.value)

    def set_units(self, sensor_id: str, units: str) -> None:
        sensor = self.get(sensor_id)
        if sensor is not None and sensor.units != str(units):
            sensor.units = str(units)
            self.unitsChanged.emit(sensor.id, sensor.units)

    def set_connected(self, sensor_id: str, connected: bool) -> None:
        sensor = self.get(sensor_id)
        if sensor is not None and sensor.connected != bool(connected):
            sensor.connected = bool(connected)
            self.dataChanged.emit(sensor.id)

    def reset(self) -> None:
        self.set_service_ready(True)
        for original in MOCK_SENSORS:
            sensor = self.sensors[original.id]
            changed = (sensor.value, sensor.units, sensor.connected, sensor.name) != (original.value, original.units, original.connected,
                                                                                    original.name)
            sensor.value, sensor.units, sensor.connected, sensor.name = original.value, original.units, original.connected, original.name
            if changed:
                self.dataChanged.emit(sensor.id)


MockSensorSource = SensorCatalog

_CATALOG: SensorCatalog | None = None


def catalog() -> SensorCatalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = SensorCatalog()
    return _CATALOG


class SensorsParseStub(QObject):
    """`plugins.Sensorsdataprovider` in the SETTINGS PARSER's JS engine (only the synchronous call a data-default can use)."""

    @Slot(str, result=str)
    @Slot(str, str, result=str)
    def getDefaultSensorIdBlock(self, sensor_type: str, preferred_kind: str = "") -> str:  # noqa: N802 (iCUE naming)
        return active_source().default_id(sensor_type, preferred_kind)  # from the cache: never waits for the network


PARSE_STUBS = {"Sensorsdataprovider": SensorsParseStub}


class SensorsdataproviderMock(QObject):
    """window.plugins.Sensorsdataprovider in the widget page (registered on the QWebChannel as "Sensorsdataprovider").

    Reads a SensorSource: the MOCK catalog (default) or the live Olympus Hub source (Sensors tab > Data Source). Without an explicit
    `sensors` it follows the ACTIVE source, also when it is switched while the page is open (retarget(): sensorDataChanged for every
    id the page asked about, then sensorAdded for every id of the new source, as SensorDataController::onStateChanged(Valid)).
    Mock: every signal is forwarded at once (unchanged behaviour). Live (about 60 value changes per second from 212 sensors on the
    test PC): only the sensors this page asked about are forwarded (getAllSensorIds = all of them) and value changes are coalesced
    per sensor (the latest value wins, flushed after LIVE_FLUSH_MS), so no sensor state is lost."""

    LIVE_FLUSH_MS = 100
    # Never connect a signal to a Python METHOD of this object: PySide6 then adds a dynamic slot to its QMetaObject after the
    # QWebChannel has introspected it, and the page stops receiving every signal (asyncResponse included; measured). Connections
    # use lambdas (kept for disconnect) or signal-to-signal.

    asyncResponse = Signal(int, "QVariant")
    sensorAdded = Signal(str)
    sensorRemoved = Signal(str)
    sensorDataChanged = Signal(str)
    sensorUnitsChanged = Signal(str, str)
    sensorValueChanged = Signal(str, str)

    def __init__(self, log: ConsoleLog, sensors: SensorSource | None = None) -> None:
        super().__init__()
        self._log = log
        self._follow = sensors is None
        self.catalog: SensorSource = sensors or active_source()
        self.requests: list[tuple[str, tuple]] = []  # every call, for tests and the console
        self._unknown: set[str] = set()
        self._connections: list[tuple] = []
        self._interest: set[str] = set()  # live: ids this page asked about
        self._interest_all = False  # live: the page asked getAllSensorIds
        self._pending_values: dict[str, str] = {}
        self._flush = QTimer(self)
        self._flush.setSingleShot(True)
        self._flush.setInterval(self.LIVE_FLUSH_MS)
        self._flush_slot = lambda: self._flush_values()  # disconnected in detach(): the closure would keep this object alive
        self._flush.timeout.connect(self._flush_slot)
        self._on_switch = lambda _old, new: self.retarget(new)
        self._attach(self.catalog)
        if self._follow:
            sources().activeChanged.connect(self._on_switch)

    def _attach(self, source: SensorSource) -> None:
        self.catalog = source
        if source.is_live:
            pairs = [(source.valueChanged, lambda s, v: self._on_live_value(s, v)),
                     (source.unitsChanged, lambda s, u: self._on_live_units(s, u)),
                     (source.dataChanged, lambda s: self._on_live_data(s)), (source.added, self.sensorAdded),
                     (source.removed, lambda s: self._on_live_removed(s))]
        else:
            pairs = [(source.valueChanged, self.sensorValueChanged), (source.unitsChanged, self.sensorUnitsChanged),
                     (source.dataChanged, self.sensorDataChanged), (source.added, self.sensorAdded), (source.removed, self.sensorRemoved)]
        for signal, slot in pairs:
            signal.connect(slot)
        self._connections = pairs

    def _disconnect(self) -> None:
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connections = []
        self._flush.stop()
        self._pending_values.clear()

    def detach(self) -> None:
        self._disconnect()
        try:
            self._flush.timeout.disconnect(self._flush_slot)
        except (RuntimeError, TypeError):
            pass
        if self._follow:
            try:
                sources().activeChanged.disconnect(self._on_switch)
            except (RuntimeError, TypeError):
                pass
            self._follow = False

    def retarget(self, source: SensorSource) -> None:
        """The page keeps running on another source: tell it to re-read what it showed, then announce the new sensors."""
        if source is self.catalog:
            return
        asked = sorted(self._interest)
        self._disconnect()
        self._interest.clear()
        self._interest_all = False
        self._unknown.clear()
        self._attach(source)
        self._log.notice(f"[Sensorsdataprovider] data source switched to the {source.label}", "info")
        for sensor_id in asked:
            self.sensorDataChanged.emit(sensor_id)
        if source.service_ready:
            for sensor_id in source.ids():
                self.sensorAdded.emit(sensor_id)

    # ---- live forwarding (only what this page asked for, coalesced) --------------------------------
    def _wanted(self, sensor_id: str) -> bool:
        return self._interest_all or sensor_id in self._interest

    def _on_live_value(self, sensor_id: str, value: str) -> None:
        if self._wanted(sensor_id):
            self._pending_values[sensor_id] = value
            if not self._flush.isActive():
                self._flush.start()

    def _flush_values(self) -> None:
        pending, self._pending_values = self._pending_values, {}
        for sensor_id, value in pending.items():
            self.sensorValueChanged.emit(sensor_id, value)

    def _on_live_units(self, sensor_id: str, units: str) -> None:
        if self._wanted(sensor_id):
            self.sensorUnitsChanged.emit(sensor_id, units)

    def _on_live_data(self, sensor_id: str) -> None:
        if self._wanted(sensor_id):
            self._pending_values.pop(sensor_id, None)  # the page re-reads everything, the latest value included
            self.sensorDataChanged.emit(sensor_id)

    def _on_live_removed(self, sensor_id: str) -> None:
        self._pending_values.pop(sensor_id, None)
        if self._wanted(sensor_id):
            self.sensorRemoved.emit(sensor_id)

    def _reply(self, request_id: int, value) -> None:
        QTimer.singleShot(0, lambda: self.asyncResponse.emit(request_id, value))

    def _field(self, method: str, request_id: int, sensor_id: str, attribute: str) -> None:
        self.requests.append((method, (request_id, sensor_id)))
        self._interest.add(str(sensor_id))
        if not self.catalog.service_ready:
            self._reply(request_id, "")  # requestAsync: callback(QString{}) while the replica is not initialized
            return
        sensor = self.catalog.get(sensor_id)
        if sensor is None:
            answer = self.catalog.unknown_answer(attribute)
            if answer is not None:
                self._reply(request_id, answer)
                return
        if sensor is None and sensor_id not in self._unknown:
            self._unknown.add(sensor_id)
            self._log.notice(f"[mock Sensorsdataprovider] unknown sensor id {sensor_id!r}: answering '' "
                             "(the real answer for an unknown id is UNVERIFIED)")
        self._reply(request_id, getattr(sensor, attribute) if sensor is not None else "")

    @Slot(int, str)
    def getSensorValue(self, request_id: int, sensor_id: str) -> None:  # noqa: N802 (iCUE naming)
        self._field("getSensorValue", request_id, sensor_id, "value")

    @Slot(int, str)
    def getSensorUnits(self, request_id: int, sensor_id: str) -> None:  # noqa: N802
        self._field("getSensorUnits", request_id, sensor_id, "units")

    @Slot(int, str)
    def getSensorName(self, request_id: int, sensor_id: str) -> None:  # noqa: N802
        self._field("getSensorName", request_id, sensor_id, "name")

    @Slot(int, str)
    def getSensorDeviceName(self, request_id: int, sensor_id: str) -> None:  # noqa: N802
        self._field("getSensorDeviceName", request_id, sensor_id, "device")

    @Slot(int, str)
    def getSensorType(self, request_id: int, sensor_id: str) -> None:  # noqa: N802
        self._field("getSensorType", request_id, sensor_id, "type")

    @Slot(int, str)
    def getSensorKind(self, request_id: int, sensor_id: str) -> None:  # noqa: N802
        self._field("getSensorKind", request_id, sensor_id, "kind")

    @Slot(int)
    def getAllSensorIds(self, request_id: int) -> None:  # noqa: N802
        self.requests.append(("getAllSensorIds", (request_id,)))
        self._interest_all = True
        self._reply(request_id, self.catalog.ids() if self.catalog.service_ready else [])

    @Slot(int, str)
    def sensorIsConnected(self, request_id: int, sensor_id: str) -> None:  # noqa: N802
        self.requests.append(("sensorIsConnected", (request_id, sensor_id)))
        self._interest.add(str(sensor_id))
        sensor = self.catalog.get(sensor_id) if self.catalog.service_ready else None
        self._reply(request_id, bool(sensor and sensor.connected))

    @Slot(int, str)
    @Slot(int, str, str)
    def getDefaultSensorId(self, request_id: int, sensor_type: str, preferred_kind: str = "") -> None:  # noqa: N802
        self.requests.append(("getDefaultSensorId", (request_id, sensor_type, preferred_kind)))
        self._reply(request_id, self.catalog.default_id(sensor_type, preferred_kind))

    @Slot(str, result=str)
    @Slot(str, str, result=str)
    def getDefaultSensorIdBlock(self, sensor_type: str, preferred_kind: str = "") -> str:  # noqa: N802
        self.requests.append(("getDefaultSensorIdBlock", (sensor_type, preferred_kind)))
        return self.catalog.default_id(sensor_type, preferred_kind)