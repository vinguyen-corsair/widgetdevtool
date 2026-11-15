"""Sensor data sources behind window.plugins.Sensorsdataprovider (Live Sensors, phase 2).

A SensorSource is what the Sensorsdataprovider bridge (sensors_mock.SensorsdataproviderMock), the Sensors tab and the
sensors-factory editor read. Two implementations:
- MockSensorSource (sensors_mock.SensorCatalog): the invented WidgetDevTool catalog. Default; behaviour unchanged.
- OlympusHubSource (olympus_hub.py): the real sensors of this PC, read from the Olympus Hub (Device Manager).
Exactly one source is ACTIVE (SensorSources.active); every widget instance reads it, so there is one Hub connection for all pages.
Switching the source never falls back silently: the Sensors tab always shows which source is active and its state.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class SensorSource(QObject):
    """Common interface. `get()` returns an object with id, name, device, type, kind, value, units, connected (strings / bool,
    exactly what the widget receives) or None for an unknown id."""

    valueChanged = Signal(str, str)
    unitsChanged = Signal(str, str)
    dataChanged = Signal(str)
    added = Signal(str)
    removed = Signal(str)
    stateChanged = Signal()

    label = "sensors"
    is_live = False  # True: the bridge forwards only what a page asked for, coalesced (see SensorsdataproviderMock)
    service_ready = True  # False: every async answer is empty, as iCUE's SensorDataController while its replica is not initialized

    def ids(self) -> list[str]:
        raise NotImplementedError

    def get(self, sensor_id: str):
        raise NotImplementedError

    def default_id(self, sensor_type: str, kind: str = "") -> str:
        raise NotImplementedError

    def unknown_answer(self, attribute: str):
        """The answer for an id this source does not know, or None to let the bridge give its own (the mock's "" + console notice)."""
        return None


class SensorSources(QObject):
    """The active source. activeChanged(old, new) lets every bridge, the Sensors tab and the editors follow a switch."""

    activeChanged = Signal(object, object)

    def __init__(self, default: SensorSource) -> None:
        super().__init__()
        self._active = default

    def active(self) -> SensorSource:
        return self._active

    def set_active(self, source: SensorSource) -> None:
        if source is self._active:
            return
        old, self._active = self._active, source
        self.activeChanged.emit(old, source)


_SOURCES: SensorSources | None = None


def sources() -> SensorSources:
    global _SOURCES
    if _SOURCES is None:
        from .sensors_mock import catalog
        _SOURCES = SensorSources(catalog())
    return _SOURCES


def active_source() -> SensorSource:
    return sources().active()
