"""iCUE runtime bridge: the QWebChannel objects and scripts iCUE 5 gives every HTML widget.

Structure mirrors HtmlDataProvider.cpp / JavaScriptICUEGlobalObject.cpp / HtmlWidget.qml of the
iCUE source (branch v5.55). It has NOT been verified against the installed release 5.52.93.
Anything not faithfully reproduced is reported through ConsoleLog.notice(), never silently faked.
"""
from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import Property, QFile, QIODevice, QLocale, QObject, QTime, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from .console_log import ConsoleLog
from .instance_store import WidgetInstance
from .personalization import PersonalizationValues, effective_settings, sync_present
from .properties import manifest_modules, parse_settings
from .script_builder import DEFAULT_WEBCHANNEL_ID, EXCLUDED_KEYS, build_data_script, parse_required_plugins
from .sensor_source import active_source
from .sensors_mock import PARSE_STUBS, SensorsdataproviderMock
from .translator import Translator
from .widget_loader import WidgetCheck


class IcueGlobalObject(QObject):
    """window.iCUE (JavaScriptICUEGlobalObject). Properties are readable synchronously; methods return Promises."""

    fpsLimitChanged = Signal()
    isPreviewChanged = Signal()

    def __init__(self, log: ConsoleLog, language: str, is_preview: bool, fps_limit: int = 30) -> None:
        super().__init__()
        self._log, self._language, self._is_preview, self._fps = log, language, is_preview, fps_limit

    iCUELanguage = Property(str, lambda self: self._language.replace("_", "-"), constant=True)
    # The real value is a private API key; deliberately empty.
    ipRegistryApiKey = Property(str, lambda self: "", constant=True)
    fpsLimit = Property(int, lambda self: self._fps, notify=fpsLimitChanged)

    def _set_preview(self, value: bool) -> None:
        if value != self._is_preview:
            self._is_preview = value
            self.isPreviewChanged.emit()

    isPreview = Property(bool, lambda self: self._is_preview, _set_preview, notify=isPreviewChanged)

    def _unsupported(self, name: str, fallback: str) -> str:
        self._log.notice(f"iCUE.{name}() is NOT simulated; returning {fallback!r}")
        return fallback

    @Slot(result=str)
    def default24HourFormat(self) -> str:  # same logic as the C++ implementation
        sample = QTime(13, 0).toString(QLocale().timeFormat(QLocale.FormatType.ShortFormat))
        return "24h" if "13" in sample else "12h"

    @Slot(result=str)
    def defaultTemperatureUnit(self) -> str:
        return "\u00b0C"  # ASSUMES iCUE's MeasurementSystem setting is Metric (its default)

    @Slot(result=str)
    def defaultSpeedUnit(self) -> str:
        return "km/h"  # same assumption as above

    @Slot(result=str)
    def allTimeZones(self) -> str:
        return self._unsupported("allTimeZones", "[]")

    @Slot(result=str)
    def defaultTimeZone(self) -> str:
        return self._unsupported("defaultTimeZone", "")

    @Slot(str, str, result=str)
    def formatUserLocaleDate(self, time_zone: str = "Europe/London", language: str = "en") -> str:
        return self._unsupported("formatUserLocaleDate", "")

    @Slot(str, str, result=str)
    def formatUserLocaleTime(self, time_zone: str = "Europe/London", language: str = "en") -> str:
        return self._unsupported("formatUserLocaleTime", "")


class Backend(QObject):
    """The object registered as "backend" (HtmlDataProvider)."""

    dataChanged = Signal(dict)
    updateRequested = Signal()
    initialized = Signal()
    isPreviewChanged = Signal()

    def __init__(self, log, data, translator, global_object) -> None:
        super().__init__()
        self._log, self._data, self._translator, self._global = log, data, translator, global_object
        self._initialized = False
        global_object.isPreviewChanged.connect(self.isPreviewChanged)

    data = Property("QVariantMap", lambda self: self._data, notify=dataChanged)
    tick = Property("qlonglong", lambda self: int(time.time() * 1000), constant=True)  # semantics UNVERIFIED
    iCUEGlobalObject = Property(QObject, lambda self: self._global, constant=True)
    isPreview = Property(bool, lambda self: self._global._is_preview, notify=isPreviewChanged)
    # No device / permissions in the dev tool: window.device is therefore never set.
    deviceModel = Property(QObject, lambda self: None, constant=True)
    permissionsController = Property(QObject, lambda self: None, constant=True)

    def set_data(self, data: dict) -> None:
        self._data = data
        self.dataChanged.emit(data)

    def notify_initialized(self) -> None:
        self._initialized = True
        self.initialized.emit()

    @Slot(str, result=str)
    def tr(self, text: str) -> str:
        return self._translator.translate(text)

    @Slot()
    def requestUpdate(self) -> None:
        self.updateRequested.emit()

    @Slot(result=bool)
    def isInitialized(self) -> bool:
        return self._initialized

    @Slot(str, result=str)
    def pathToMacrosFormat(self, path: str) -> str:
        self._log.notice("pathToMacrosFormat() is NOT simulated; returning the path unchanged")
        return path

    @Slot(str, result=str)
    def resourcePath(self, path: str) -> str:
        self._log.notice("resourcePath() is NOT simulated; returning the path unchanged")
        return path


class LinkproviderMock(QObject):
    """Recording stand-in for widgetbuilder.linkprovider (Url.qml has a single open(link)).

    NOT equivalent to the real plugin: the real one calls Qt.openUrlExternally(); this only records.
    """

    def __init__(self, log: ConsoleLog, open_externally: bool = False) -> None:
        super().__init__()
        self._log, self._open_externally = log, open_externally
        self.opened_urls: list[str] = []

    @Slot(str)
    def open(self, link: str) -> None:
        self.opened_urls.append(link)
        if self._open_externally:
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(link))
        else:
            self._log.notice(f"[mock Linkprovider] open({link!r}) recorded; browser NOT opened", "info")


SUPPORTED_PLUGINS = {"Linkprovider": LinkproviderMock, "Sensorsdataprovider": SensorsdataproviderMock}
PLUGIN_NOTES = {"Linkprovider": "MOCK only (recording stand-in, not the real plugin)",
                "Sensorsdataprovider": "sensors of the source chosen in the Sensors tab (MOCK catalog by default, or LIVE Olympus Hub)"}

def _qwebchannel_js() -> str:
    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("qrc:/qtwebchannel/qwebchannel.js is not available")
    return bytes(f.readAll()).decode("utf-8")


def _script(name: str, source: str) -> QWebEngineScript:
    s = QWebEngineScript()
    s.setName(name)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)  # as in HtmlWidget.qml
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setSourceCode(source)
    return s


def parse_widget_settings(check: WidgetCheck, translator: Translator | None = None, language: str = "en"):
    """index.html of a checked widget folder -> ParsedSettings (manifest modules and tr() available)."""
    index = check.index_path
    translator = translator or Translator(index, language)
    plugins = {d.channel_id: PARSE_STUBS[d.channel_id] for d in parse_required_plugins(check.manifest or {}) if d.channel_id in PARSE_STUBS}
    return parse_settings(index.read_text(encoding="utf-8-sig", errors="replace"), translator.translate,
                          manifest_modules(check.manifest or {}, index), plugins)


class RuntimeBridge(QObject):
    """Installs the iCUE runtime into one QWebEnginePage for one widget. Create a new one per load."""

    def __init__(self, page: QWebEnginePage, check: WidgetCheck, log: ConsoleLog,
                 language: str = "en", is_preview: bool = False, open_links_externally: bool = False,
                 instance: WidgetInstance | None = None, refresh_defaults: bool = False,
                 personalization: Callable[[], PersonalizationValues | None] | None = None) -> None:
        super().__init__(page)
        self.page, self.check, self.log = page, check, log
        index = check.index_path
        manifest = check.manifest or {}

        self.translator = Translator(index, language)
        self.settings_spec = parse_widget_settings(check, self.translator)
        defaults = self.settings_spec.defaults()
        for note in [*self.translator.problems, *self.settings_spec.notes]:
            log.notice(note, "info")
        type_id = str(manifest.get("id", check.folder.name))
        # The WidgetInstance is the ONLY owner of the settings values; `data` below is derived from it.
        self.refresh_report: dict | None = None
        if instance is None:
            # NEW instance (random id): the defaults of the HTML.
            self.instance = WidgetInstance.create(type_id, defaults, name=check.widget_name)
        elif refresh_defaults:
            # Live reload of an instance that follows its source: new defaults, user-edited keys keep the user's value.
            self.instance = instance
            self.refresh_report = instance.refresh_from_source(defaults)
        else:
            # RESTORE: the stored data is used as it is; no default is added (iCUE: HtmlSettingsImpl(data)).
            self.instance = instance

        # D4: the widget receives the EFFECTIVE settings (personalization.effective_settings); the instance keeps the raw ones.
        # Without a source (direct users of the bridge) the widget gets the raw settings, as before D4.
        self._personalization = personalization
        if personalization is not None:
            sync_present(self.instance)  # what opening iCUE's settings panel does

        self._url = QUrl.fromLocalFile(str(index.resolve())).toString()
        self._type_id = type_id
        self.data: dict = self._payload()
        # One uniqueId per instance (it doubles as the localStorage key in widgets), never derived from the type id.
        self.unique_id = self.instance.unique_id(is_preview)

        self.global_object = IcueGlobalObject(log, language, is_preview)
        self.backend = Backend(log, dict(self.data), self.translator, self.global_object)
        self.plugin_mocks: dict[str, QObject] = {}
        self.plugin_channel_ids: list[str] = []
        for decl in parse_required_plugins(manifest):
            factory = SUPPORTED_PLUGINS.get(decl.channel_id)
            if factory is None:
                log.notice(f"plugin {decl.module_name}:{decl.component}:{decl.version} is declared but NOT simulated; "
                           f"window.plugins.{decl.channel_id} will be undefined")
                continue
            mock = factory(log, open_links_externally) if factory is LinkproviderMock else factory(log)
            self.plugin_mocks[decl.channel_id] = mock
            self.plugin_channel_ids.append(decl.channel_id)
            note = PLUGIN_NOTES.get(decl.channel_id, 'MOCK only')
            if decl.channel_id == "Sensorsdataprovider":
                note += f"; active now: {active_source().label}"
            log.notice(f"plugin {decl.channel_id}: {note}", "info")

        self.channel = QWebChannel(page)
        self.channel.registerObject(DEFAULT_WEBCHANNEL_ID, self.backend)
        for channel_id, mock in self.plugin_mocks.items():
            self.channel.registerObject(channel_id, mock)
        page.setWebChannel(self.channel)  # MainWorld, as in HtmlWidget.qml
        self._install_scripts()
        self.backend.notify_initialized()  # iCUE: dataScriptChanged -> notifyInitialized(), before the page loads

    def _payload(self) -> dict:
        """What the widget sees: the EFFECTIVE instance data plus the two keys the host adds (url, typeId)."""
        return {**self.effective_data(), "url": self._url, "typeId": self._type_id}

    def effective_data(self) -> dict:
        """The instance data after Widget Personalization (a new dict; the instance is not touched)."""
        active = self._personalization() if self._personalization is not None else None
        return effective_settings(self.instance.data, active)

    def refresh_effective(self) -> bool:
        """The personalization (or its source: device, screen, theme-by-screen) changed: recompute what the widget receives and
        tell it only when that differs (dataChanged -> window[key] + onDataUpdated). No page reload. Returns True when sent."""
        payload = self._payload()
        if payload == self.data:
            return False
        before = self.keys
        self.data = payload
        if self.keys != before:
            self._install_scripts()
            self.backend.notify_initialized()
        self.backend.set_data(dict(self.data))
        return True

    @property
    def keys(self) -> set[str]:
        return {k for k in self.data if k not in EXCLUDED_KEYS}

    def _install_scripts(self) -> None:
        self.script_source = build_data_script(self.data, self.unique_id, self.plugin_channel_ids)
        scripts = self.page.scripts()
        scripts.clear()
        scripts.insert(_script("qwebchannel", _qwebchannel_js()))
        scripts.insert(_script("icue-data", self.script_source))

    def set_properties(self, props: dict) -> None:
        """Change widget properties like the iCUE settings panel: dataChanged -> window[key] + onDataUpdated."""
        before = self.keys
        for key, value in props.items():
            self.instance.set_value(key, value)  # marks the key as user-edited
        self.data = self._payload()
        if self.keys != before:  # HtmlDataProvider::updateKeys -> updateScript -> notifyInitialized
            self._install_scripts()
            self.backend.notify_initialized()
        self.backend.set_data(dict(self.data))

    def dispose(self) -> None:
        for mock in self.plugin_mocks.values():
            if hasattr(mock, "detach"):
                mock.detach()
        self.page.setWebChannel(None)
        self.page.scripts().clear()
        self.deleteLater()