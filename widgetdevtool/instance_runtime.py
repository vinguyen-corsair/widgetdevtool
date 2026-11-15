"""One widget instance running in its own WebEngine stack (D7b): profile + page + view + runtime bridge + console.

Why one stack per instance: iCUE gives every instance its own storage profile (storage_name), so two instances of the same
widget never share localStorage. The host (MainWindow) owns the list; this class owns the lifetime of the WebEngine objects.
"""
from __future__ import annotations

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .console_log import ConsoleLog, ConsolePage
from .instance_storage import StorageLayout, storage_name
from .instance_store import WidgetInstance
from .runtime_bridge import RuntimeBridge, parse_widget_settings
from .widget_loader import WidgetCheck


def type_id_of(check: WidgetCheck) -> str:
    """The type id the runtime bridge gives an instance: the manifest id, else the folder name."""
    return str((check.manifest or {}).get("id", check.folder.name))


def new_instance_for(check: WidgetCheck) -> WidgetInstance:
    """A NEW instance (random id) holding the defaults of the widget HTML (WidgetInstance.create)."""
    return WidgetInstance.create(type_id_of(check), parse_widget_settings(check).defaults(), name=check.widget_name)


class InstanceRuntime(QObject):
    loadStarted = Signal(object)
    loadProgress = Signal(object, int)
    loadFinished = Signal(object, bool)

    def __init__(self, check: WidgetCheck, instance: WidgetInstance, storage: StorageLayout, *,
                 follows_source: bool, is_preview: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.check = check
        self.instance = instance
        self.follows_source = follows_source
        self.is_preview = is_preview
        self.state = None  # set by the host (main_window.State); it is the host's vocabulary
        self.status_text = ""
        self.reload_count = 0
        self.reloading = False
        self.bridge: RuntimeBridge | None = None
        self.storage_name = storage_name(instance.unique_id(), instance.type_id, is_preview)
        self.console = ConsoleLog(self)
        self.profile = storage.make_profile(self.storage_name, is_preview, self)
        self.page = ConsolePage(self.profile, self.console, self)
        settings = self.page.settings()  # mirrors HtmlWidget.qml: remote content from local pages, no scroll bars
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
        self.view = QWebEngineView()
        self.view.setPage(self.page)
        self.view.loadStarted.connect(lambda: self.loadStarted.emit(self))
        self.view.loadProgress.connect(lambda percent: self.loadProgress.emit(self, percent))
        self.view.loadFinished.connect(lambda ok: self.loadFinished.emit(self, ok))

    @property
    def alive(self) -> bool:
        return self.page is not None

    def install_bridge(self, check: WidgetCheck, refresh_defaults: bool, personalization) -> RuntimeBridge:
        """New bridge for the (re)load that follows; the previous one is disposed first."""
        self.drop_bridge()
        self.check = check
        self.bridge = RuntimeBridge(self.page, check, self.console, instance=self.instance,
                                    refresh_defaults=refresh_defaults, personalization=personalization)
        return self.bridge

    def drop_bridge(self) -> None:
        if self.bridge is not None:
            self.bridge.dispose()
            self.bridge = None

    def dispose(self) -> None:
        """Release the WebEngine objects in the order QtWebEngine needs: bridge, view/page link, page, then profile.
        The storage on disk is NOT touched (closing is not removing)."""
        if self.page is None:
            return
        self.drop_bridge()
        self.view.setPage(None)
        self.view.setParent(None)
        shiboken6.delete(self.page)
        self.page = None
        shiboken6.delete(self.profile)  # the page is gone, so QtWebEngine accepts the release (measured: no warning)
        self.profile = None
        self.view.deleteLater()
        self.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)  # also without a running event loop (tests)