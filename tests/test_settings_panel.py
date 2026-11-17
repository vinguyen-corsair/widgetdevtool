r"""Settings editor tests (D3): parser -> generated UI -> instance -> runtime bridge -> page.

Editors are driven through their real Qt widgets (click, setValue, activated, key events). The behaviour they are
compared with comes from the iCUE source (EditorSettingsModels.cpp, *EditorSetting.qml, HtmlSettingsImpl.cpp).
Tests on real widgets (Basketball Scores, SmokeClock, RSS Feeder) are skipped when the folders are not present.
Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from html import escape
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from test_runtime_bridge import WIDGET_HTML, PageTestCase, write_widget  # noqa: E402
from widgetdevtool.instance_store import WidgetInstance  # noqa: E402
from widgetdevtool.main_window import MainWindow  # noqa: E402
from widgetdevtool.paths import WIDGETS_DIR  # noqa: E402
from widgetdevtool.properties import parse_settings  # noqa: E402
from widgetdevtool.settings_editors import EDITABLE_TYPES  # noqa: E402
from widgetdevtool.settings_panel import SettingsPanel  # noqa: E402

WIDGETS = WIDGETS_DIR
BASKETBALL = WIDGETS / "Basketball Scores" / "widget" / "basketballscores"
SMOKECLOCK = WIDGETS / "SmokeClock" / "widget" / "smokeclock"
RSS = WIDGETS / "RSS Feed Reader" / "widget" / "rssfeedreader"


def meta(name: str, data_type: str, **attrs: str) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{escape(v)}"' for k, v in attrs.items())
    return f'<meta name="x-icue-property" content="{name}" data-type="{data_type}"{extra}>'


def page(*metas: str, title: str = "Demo", groups: str = "") -> str:
    script = f'<script type="application/json" id="x-icue-groups">{groups}</script>' if groups else ""
    return f"<html><head><title>{title}</title>{''.join(metas)}{script}</head><body></body></html>"


ALL = page(
    meta("sw", "switch", data_label="'Switch label'", data_default="true"),
    meta("sl", "slider", data_label="'Slider label'", data_default="5", data_min="0", data_max="10", data_step="1",
         data_unit_label="' px'"),
    meta("cb", "combobox", data_label="'Combo label'", data_values='[{key:"a",value:"Alpha"},{key:"b",value:"Beta"}]',
         data_default="'a'"),
    meta("tb", "tab-buttons", data_label="'Tabs'", data_values='["one","two","three"]', data_default="'two'"),
    meta("se", "slider-enum", data_label="'Enum'", data_values='["slow","medium","fast"]', data_default="'medium'"),
    meta("tx", "textfield", data_label="'Text'", data_default="'hello'"),
    meta("co", "color", data_label="'Color'", data_default="'#112233'"),
    meta("tf", "time-format", data_label="'Time'", data_default="'{\"time12hourFormat\":true,\"amPmEnable\":false}'"),
    meta("textColor", "color", data_label="'Text Color'", data_default="'#ffffff'"),
    meta("media", "media-selector", data_label="'Media'", data_filters='["image","video","gif"]'),
    meta("conn", "app-status", data_label="'Status'", application="stream-deck"),
    meta("zz", "made-up"),
    title="Demo Widget",
    groups='[{"title":"First","properties":["sw","sl","cb"],"info":"\'Info text\'","description":"\'Description text\'"},'
           '{"title":"Second","properties":["tb","se"]}]')


_FORMAT_BOUND = re.compile(r"^\s*([A-Za-z][A-Za-z0-9+.-]*://|[A-Za-z]:[\\/]|/|[{\[])|^[^\s@]+@[^\s@]+$")


_FORMAT_NAME = re.compile(r"url|uri|path|file|font|mail|link|address|host", re.IGNORECASE)


def is_format_bound(value: object, name: str = "") -> bool:
    """A textfield whose content has a structure of its own (URL, path, font name, JSON, e-mail): appending a character
    makes it invalid. Judged by the stored value, or by the property name when the value is empty (no widget is named)."""
    if isinstance(value, str) and _FORMAT_BOUND.search(value):
        return True
    return bool(name) and bool(_FORMAT_NAME.search(name))


def classify_console_error(message: str) -> str:
    """Where a console error comes from: "network" (outside the widget runtime: DNS, CORS, HTTP) or "runtime" (script)."""
    if message.startswith(("Access to fetch at", "Access to XMLHttpRequest at", "Failed to load resource", "net::ERR")):
        return "network"
    return "runtime"


class PanelTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def build(self, html: str = ALL, data: dict | None = None):
        settings = parse_settings(html)
        instance = (WidgetInstance.create("t", settings.defaults()) if data is None else
                    WidgetInstance.restore({"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "typeId": "t", "data": data}))
        panel = SettingsPanel()
        self.addCleanup(panel.deleteLater)
        panel.set_settings(settings, instance)
        commits: list[tuple[str, object]] = []
        panel.settingCommitted.connect(lambda k, v: commits.append((k, v)))
        return panel, instance, commits


class GeneratedStructureTests(PanelTestCase):
    def test_panel_is_generated_from_the_metadata(self) -> None:
        panel, instance, _ = self.build()
        self.assertEqual([b.title() for b in panel.group_boxes], ["First", "Second", "Demo Widget"])
        self.assertEqual(list(panel.editors), ["sw", "sl", "cb", "tb", "se", "tx", "co", "tf", "textColor", "media", "conn"])
        self.assertEqual(panel.editors["sw"].label.text(), "Switch label")
        texts = [lab.text() for lab in panel.group_boxes[0].findChildren(type(panel.header))]
        self.assertIn("Info text", texts)
        self.assertIn("Description text", texts)
        self.assertIn(instance.id, panel.header.text())

    def test_supported_and_unsupported_controls_are_told_apart(self) -> None:
        panel, _, _ = self.build()
        summary = panel.summary()
        self.assertEqual({t for _, t in summary["supported"]}, set(EDITABLE_TYPES) - {"sensors-factory"})  # not in ALL
        self.assertEqual({k for k, _, _ in summary["unsupported"]}, {"media", "conn"})
        media = panel.editors["media"]
        self.assertIn("Unsupported", media.status.text())
        self.assertIn("media assets controller", media.status.text())
        self.assertIn("Unsupported", panel.editors["conn"].status.text())
        self.assertIn("pathToAsset", media.control.text())  # the stored value is shown, read only
        self.assertFalse(media.control.isEnabled())

    def test_controls_without_a_parameter_are_listed_not_hidden(self) -> None:
        panel, _, _ = self.build()
        labels = [w.text() for w in panel.findChildren(type(panel.header)) if "Not editable" in w.text()]
        self.assertEqual(len(labels), 1)
        self.assertIn("zz (made-up)", labels[0])

    def test_options_whose_evaluation_failed_are_unsupported_not_invented(self) -> None:
        html = page(meta("cb", "combobox", data_label="'X'", data_values="Provider.search()", data_default="'a'"))
        panel, _, commits = self.build(html)
        editor = panel.editors["cb"]
        self.assertFalse(editor.supported)
        self.assertIn("could not be evaluated", editor.reason)
        self.assertIn("Provider is not defined", editor.reason)

    def test_personalization_keys_carry_a_note(self) -> None:
        panel, _, _ = self.build()
        self.assertIn("Personalization key", panel.editors["textColor"].status.text())
        self.assertNotIn("Personalization key", panel.editors["sw"].status.text())


class EditorBehaviourTests(PanelTestCase):
    def test_switch_commits_only_user_toggles(self) -> None:
        panel, instance, commits = self.build()
        box = panel.editors["sw"].control
        self.assertTrue(box.isChecked())
        instance.set_value("sw", False)  # a change from elsewhere refreshes the control without a commit
        self.assertFalse(box.isChecked())
        self.assertEqual(commits, [])
        box.click()
        self.assertEqual(commits, [("sw", True)])

    def test_slider_shows_min_max_step_unit_and_commits_numbers(self) -> None:
        panel, _, commits = self.build()
        editor = panel.editors["sl"]
        slider = editor.control
        self.assertEqual((slider.minimum(), slider.maximum(), slider.singleStep(), slider.value()), (0, 10, 1, 5))
        self.assertEqual(editor.findChildren(type(panel.header))[-2].text(), "5 px")
        slider.setValue(8)
        self.assertEqual(commits, [("sl", 8.0)])
        self.assertIsInstance(commits[0][1], float)

    def test_slider_value_outside_the_range_is_kept_and_only_flagged(self) -> None:
        panel, instance, commits = self.build(data={"sl": 500.0})
        editor = panel.editors["sl"]
        self.assertEqual(editor.control.value(), 10)  # only what is drawn is limited
        self.assertEqual(instance.data["sl"], 500.0)  # the stored value is untouched
        self.assertEqual(commits, [])
        self.assertIn("outside [0, 10]", editor.status.text())
        self.assertIn("500", editor.findChildren(type(panel.header))[-2].text())

    def test_combobox_options_selection_and_unknown_stored_value(self) -> None:
        panel, instance, commits = self.build()
        combo = panel.editors["cb"].control
        self.assertEqual([(combo.itemText(i), combo.itemData(i)) for i in range(combo.count())],
                         [("Alpha", "a"), ("Beta", "b")])
        self.assertEqual(combo.currentIndex(), 0)
        combo.setCurrentIndex(1)
        combo.activated.emit(1)
        self.assertEqual(commits, [("cb", "b")])
        instance.set_value("cb", "zzz")  # not among the options: iCUE shows no selection and keeps the value
        self.assertEqual((combo.currentIndex(), instance.data["cb"]), (-1, "zzz"))
        self.assertIn("not among the options", panel.editors["cb"].status.text())
        self.assertEqual(len(commits), 1)

    def test_tab_buttons_show_the_stored_key_and_commit_clicks(self) -> None:
        panel, instance, commits = self.build()
        editor = panel.editors["tb"]
        self.assertEqual([k for k, b in editor.buttons.items() if b.isChecked()], ["two"])
        editor.buttons["three"].click()
        self.assertEqual(commits, [("tb", "three")])
        instance.set_value("tb", "nope")
        self.assertEqual([k for k, b in editor.buttons.items() if b.isChecked()], [])

    def test_slider_enum_walks_the_options_and_commits_their_keys(self) -> None:
        panel, _, commits = self.build()
        slider = panel.editors["se"].control
        self.assertEqual((slider.minimum(), slider.maximum(), slider.value()), (0, 2, 1))
        slider.setValue(2)
        self.assertEqual(commits, [("se", "fast")])

    def test_textfield_commits_on_enter_and_on_focus_loss_and_esc_reverts(self) -> None:
        panel, instance, commits = self.build()
        edit = panel.editors["tx"].control
        self.assertEqual(edit.text(), "hello")
        QTest.keyClicks(edit, "A")
        self.assertEqual(commits, [])  # nothing while typing
        QTest.keyClick(edit, Qt.Key.Key_Return)
        self.assertEqual(commits, [("tx", "helloA")])
        instance.set_value("tx", "helloA")
        QTest.keyClicks(edit, "BC")
        QTest.keyClick(edit, Qt.Key.Key_Escape)
        self.assertEqual((edit.text(), len(commits)), ("helloA", 1))  # reverted, not committed
        QTest.keyClicks(edit, "D")
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QFocusEvent
        QApplication.sendEvent(edit, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason))
        self.assertEqual(commits[-1], ("tx", "helloAD"))

    def test_color_shows_the_stored_color_and_commits_a_name(self) -> None:
        panel, instance, commits = self.build()
        editor = panel.editors["co"]
        self.assertEqual(editor.control.text(), "#112233")
        editor.pick_color(QColor("#FF8800"))
        self.assertEqual(commits, [("co", "#ff8800")])
        instance.set_value("co", "not-a-color")
        self.assertEqual(editor.control.text(), "(invalid color)")

    def test_time_format_commits_the_whole_map(self) -> None:
        panel, instance, commits = self.build()
        editor = panel.editors["tf"]
        self.assertEqual({k: b.isChecked() for k, b in editor.checks.items()}, {"time12hourFormat": True, "amPmEnable": False})
        editor.checks["amPmEnable"].click()
        self.assertEqual(commits, [("tf", {"time12hourFormat": True, "amPmEnable": True})])
        hidden = self.build(page(meta("tf", "time-format", data_default="'{}'", data_one_more_name="false")))[0]
        self.assertTrue(hidden.editors["tf"].checks["amPmEnable"].isHidden())

    def test_unsupported_controls_never_commit(self) -> None:
        panel, _, commits = self.build()
        for key in ("media", "conn"):
            self.assertFalse(panel.editors[key].supported)
        self.assertEqual(commits, [])

    def test_a_missing_key_shows_the_empty_value_and_is_created_by_the_first_edit(self) -> None:
        panel, instance, commits = self.build(data={"tx": "x"})
        slider, switch = panel.editors["sl"], panel.editors["sw"]
        self.assertEqual((slider.control.value(), switch.control.isChecked()), (0, False))
        self.assertIn("Key not stored", slider.status.text())
        self.assertNotIn("sl", instance.data)
        slider.control.setValue(4)
        self.assertEqual(commits, [("sl", 4.0)])


class SensorsFactoryEditorTests(PanelTestCase):
    HTML = ('<html><head><title>t</title>' + meta("s", "sensors-factory", data_label="'Add Sensor'", data_default="'SENSOR-DEFAULT'")
            + '</head></html>')

    def test_add_pick_and_remove_commit_the_whole_list_like_icue(self) -> None:
        from widgetdevtool.sensors_mock import _sid
        panel, instance, commits = self.build(self.HTML)
        editor = panel.editors["s"]
        self.assertTrue(editor.supported)
        self.assertIn("MOCK sensors", editor.status.text())
        editor.add_sensor_button.click()  # SensorsFactoryParametersModel::addSensorParameter: data-default id + #f08e33
        self.assertEqual(commits[-1], ("s", [{"sensorId": "SENSOR-DEFAULT", "color": "#f08e33"}]))
        editor.set_value(True, commits[-1][1])
        combo = editor.sensor_rows[0][0]
        self.assertIn("not in the mock catalog", combo.placeholderText())
        combo.setCurrentIndex(combo.findData(_sid(3)))
        combo.activated.emit(combo.currentIndex())
        self.assertEqual(commits[-1], ("s", [{"sensorId": _sid(3), "color": "#f08e33"}]))
        editor.set_value(True, commits[-1][1])
        editor.sensor_rows[0][2].click()
        self.assertEqual(commits[-1], ("s", []))


class RuntimeFlowTests(PageTestCase):
    """Edits go: control -> WidgetInstance -> RuntimeBridge -> backend.dataChanged -> onDataUpdated. No page reload."""

    def widget(self, name: str = "w") -> Path:
        return write_widget(Path(self._tmp.name) / name)

    def setUp(self) -> None:
        super().setUp()
        self.settled = 0
        self.window.loadSettled.connect(lambda ok: setattr(self, "settled", self.settled + 1))

    def start(self, **kwargs) -> None:
        self.assertTrue(self.window.load_folder(self.widget(), **kwargs))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True))
        self.pump(0.3)
        self.js("window.__sentinel = 123; window.__log.length")

    def editors(self):
        return self.window.settings_panel.editors

    def log(self) -> list[str]:
        return self.jsj("window.__log")

    def test_panel_matches_the_loaded_widget(self) -> None:
        self.start()
        self.assertEqual(list(self.editors()), ["refreshMinutes", "label", "enabled", "accent"])
        self.assertEqual(self.window.settings_panel.summary()["unsupported"], [])
        self.assertEqual(self.editors()["refreshMinutes"].control.value(), 10)
        self.assertEqual(self.editors()["label"].control.text(), "hello")

    def test_a_change_reaches_the_widget_once_without_reloading_the_page(self) -> None:
        self.start()
        before = self.log().count("onDataUpdated:25")
        settled = self.settled
        self.editors()["refreshMinutes"].control.setValue(25)
        self.assertTrue(self.wait(lambda: self.js("refreshMinutes") == 25))
        self.pump(0.3)
        self.assertEqual(self.window.bridge.instance.data["refreshMinutes"], 25)
        self.assertEqual(self.log().count("onDataUpdated:25") - before, 1)
        self.assertEqual((self.js("window.__sentinel"), self.window.reload_count, self.settled), (123, 0, settled))

    def test_every_supported_control_updates_its_runtime_variable(self) -> None:
        self.start()
        ed = self.editors()
        ed["enabled"].control.click()
        ed["label"].control.setText("changed")
        QTest.keyClick(ed["label"].control, Qt.Key.Key_Return)
        ed["accent"].pick_color(QColor("#ff0000"))
        for key, expected in (("enabled", False), ("label", "changed"), ("accent", "#ff0000")):
            self.assertTrue(self.wait(lambda: self.jsj(f"window[{key!r}]") == expected), key)
        self.assertEqual(self.window.reload_count, 0)

    def test_an_equal_value_does_not_call_onDataUpdated(self) -> None:
        self.start()
        n = len(self.log())
        edit = self.editors()["label"].control
        QTest.keyClicks(edit, "x")
        edit.setText("hello")  # back to the stored text, then Enter
        QTest.keyClick(edit, Qt.Key.Key_Return)
        self.pump(0.4)
        self.assertEqual(len(self.log()), n)

    def test_esc_in_the_textfield_leaves_widget_and_instance_untouched(self) -> None:
        self.start()
        n = len(self.log())
        edit = self.editors()["label"].control
        QTest.keyClicks(edit, "zzz")
        QTest.keyClick(edit, Qt.Key.Key_Escape)
        self.pump(0.4)
        self.assertEqual((edit.text(), self.js("label"), self.window.bridge.instance.data["label"]), ("hello", "hello", "hello"))
        self.assertEqual(len(self.log()), n)

    def test_values_outside_the_range_are_not_clamped_on_their_way_to_the_widget(self) -> None:
        self.start()
        self.window.bridge.set_properties({"refreshMinutes": 500})  # not through the slider: no check anywhere in iCUE
        self.assertTrue(self.wait(lambda: self.js("refreshMinutes") == 500))
        self.assertEqual(self.editors()["refreshMinutes"].control.value(), 60)  # drawing only
        self.assertEqual(self.window.bridge.instance.data["refreshMinutes"], 500)

    def test_settings_survive_a_live_reload(self) -> None:
        self.start()
        self.editors()["refreshMinutes"].control.setValue(33)
        edit = self.editors()["label"].control
        edit.setText("kept")
        QTest.keyClick(edit, Qt.Key.Key_Return)
        self.assertTrue(self.wait(lambda: self.js("label") == "kept"))
        instance_id = self.window.bridge.instance.id
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: self.window.reload_count == 1 and not self.window._reloading))
        self.pump(0.3)
        self.assertEqual((self.js("refreshMinutes"), self.js("label")), (33, "kept"))
        self.assertEqual(self.window.bridge.instance.id, instance_id)
        self.assertEqual((self.editors()["refreshMinutes"].control.value(), self.editors()["label"].control.text()), (33, "kept"))
        self.assertIs(self.window.store.get(instance_id), self.window.bridge.instance)

    def test_editing_a_missing_key_of_a_restored_instance_creates_it_in_the_widget(self) -> None:
        old = WidgetInstance.restore({"id": "66666666-6666-4666-8666-666666666666", "typeId": "t", "data": {"label": "stored"}})
        self.start(instance=old)
        self.assertEqual(self.js("typeof refreshMinutes"), "undefined")
        self.assertIn("Key not stored", self.editors()["refreshMinutes"].status.text())
        self.editors()["refreshMinutes"].control.setValue(12)
        self.assertTrue(self.wait(lambda: self.js("typeof refreshMinutes") == "number"))
        self.assertEqual((self.js("refreshMinutes"), old.data["refreshMinutes"]), (12, 12.0))


class InstancePersistenceTests(PageTestCase):
    def widget(self, name: str = "w") -> Path:
        return write_widget(Path(self._tmp.name) / name)

    def open(self, window: MainWindow, root: Path, **kwargs) -> None:
        self.assertTrue(window.load_folder(root, **kwargs))
        self.assertTrue(self.wait(lambda: window.state.name == "LOADED"))
        self.pump(0.4)

    def value(self, window: MainWindow, script: str):
        box: dict = {}
        window.view.page().runJavaScript(script, lambda v: box.setdefault("v", v))
        self.assertTrue(self.wait(lambda: "v" in box, 10), script)
        return box["v"]

    def second_window(self) -> MainWindow:
        window = MainWindow()
        self.addCleanup(lambda: (window.shutdown(), self.pump(0.2)))
        return window

    def test_two_instances_of_one_widget_keep_separate_settings(self) -> None:
        root = self.widget()
        self.open(self.window, root)
        first = self.window.bridge.instance
        self.window.settings_panel.editors["refreshMinutes"].control.setValue(30)
        self.assertTrue(self.window.new_instance())
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.pump(0.4)
        second = self.window.bridge.instance
        self.assertNotEqual(first.id, second.id)
        self.assertEqual((first.data["refreshMinutes"], second.data["refreshMinutes"]), (30, 10.0))
        self.window.settings_panel.editors["label"].control.setText("second")
        QTest.keyClick(self.window.settings_panel.editors["label"].control, Qt.Key.Key_Return)
        self.assertEqual((first.data["label"], second.data["label"]), ("hello", "second"))
        self.assertEqual([i.id for i in self.window.store.of_type(first.type_id)], [first.id, second.id])
        self.assertEqual(self.window.instance_list.count(), 2)
        self.assertTrue(self.window.open_instance(first.id, "restore"))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.pump(0.4)
        self.assertEqual((self.js("refreshMinutes"), self.js("label"), self.window.bridge.instance.id), (30, "hello", first.id))

    def test_save_and_open_in_another_session_restore_and_merge(self) -> None:
        root = self.widget()
        self.open(self.window, root)
        instance = self.window.bridge.instance
        self.window.settings_panel.editors["refreshMinutes"].control.setValue(44)
        self.window.settings_panel.editors["enabled"].control.click()
        self.pump(0.3)
        path = Path(self._tmp.name) / "instances.json"
        self.assertEqual(self.window.save_instances(path), 1)
        # an instance saved before the property "accent" existed
        record = json.loads(path.read_text(encoding="utf-8"))
        del record["instances"][0]["data"]["accent"]
        path.write_text(json.dumps(record), encoding="utf-8")

        other = self.second_window()
        self.assertEqual(other.open_instances(path), 1)
        self.assertEqual(other.instance_list.count(), 1)
        self.assertTrue(other.open_instance(instance.id, "restore"))
        self.assertTrue(self.wait(lambda: other.state.name == "LOADED"))
        self.pump(0.4)
        self.assertEqual((self.value(other, "refreshMinutes"), self.value(other, "enabled"), self.value(other, "uniqueId")),
                         (44, False, "{" + instance.id + "}"))
        self.assertEqual(self.value(other, "typeof accent"), "undefined")  # restore adds no default
        self.assertEqual(other.settings_panel.editors["refreshMinutes"].control.value(), 44)
        self.assertIn("Key not stored", other.settings_panel.editors["accent"].status.text())

        self.assertTrue(other.open_instance(instance.id, "merge"))
        self.assertTrue(self.wait(lambda: other.state.name == "LOADED"))
        self.pump(0.4)
        self.assertEqual((self.value(other, "accent"), self.value(other, "refreshMinutes"), self.value(other, "enabled")),
                         ("#5b8cff", 44, False))  # new default added, stored values win
        self.assertEqual(other.bridge.instance.id, instance.id)

    def test_open_instance_reports_a_missing_widget_folder(self) -> None:
        root = self.widget()
        self.open(self.window, root)
        self.window.bridge.instance.source = str(Path(self._tmp.name) / "gone")
        self.assertFalse(self.window.open_instance(self.window.bridge.instance.id, "restore"))
        self.assertIn("not available", self.window.status.text())

    def test_unknown_open_mode_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.window.open_instance("x", "bogus")

    def test_local_storage_is_isolated_per_instance_since_d7b(self) -> None:
        """Each instance has its own profile: another instance, even of another widget type, never sees the value."""
        root = self.widget("a")
        self.open(self.window, root)
        first_id = self.window.bridge.instance.id
        self.js("localStorage.setItem('wdt_shared', 'written-by-instance-1'); 1")
        self.assertTrue(self.window.new_instance())
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.pump(0.3)
        self.assertNotEqual(self.window.bridge.instance.id, first_id)
        self.assertIn(self.js("localStorage.getItem('wdt_shared')"), (None, ""))
        other = self.widget("b")
        (other / "manifest.json").write_text(json.dumps({"id": "com.test.other", "name": "Other"}), encoding="utf-8")
        self.load(other)  # a different widget type
        self.assertIn(self.js("localStorage.getItem('wdt_shared')"), (None, ""))
        self.assertTrue(self.window.select_instance(first_id))
        self.assertEqual(self.js("localStorage.getItem('wdt_shared')"), "written-by-instance-1")

@unittest.skipUnless(BASKETBALL.is_dir() and SMOKECLOCK.is_dir() and RSS.is_dir(), "WidgetsXE widgets not present")
class RealWidgetTests(PageTestCase):
    """Data driven: every editable control of the real widgets is exercised; nothing here names a property."""

    def open_real(self, folder: Path) -> None:
        self.assertTrue(self.window.load_folder(folder))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED", 30))
        self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True, 30))
        self.pump(1.0)

    def new_value(self, editor):
        t, param = editor.param.data_type, editor.param
        current = self.window.bridge.instance.data.get(param.name)
        if t == "switch":
            return not bool(current), lambda: editor.control.click()
        if t == "slider":
            lo, hi = int(param.meta["min"]), int(param.meta["max"])
            target = lo if int(round(current or 0)) != lo else hi
            return float(target), lambda: editor.control.setValue(target)
        if t in ("combobox", "tab-buttons", "slider-enum"):
            keys = [o["key"] for o in param.meta["options"]]
            other = next((k for k in keys if k != current), None)
            if other is None:
                return None, None
            if t == "combobox":
                index = keys.index(other)
                return other, lambda: (editor.control.setCurrentIndex(index), editor.control.activated.emit(index))
            if t == "tab-buttons":
                return other, lambda: editor.buttons[other].click()
            return other, lambda: editor.control.setValue(keys.index(other))
        if t == "textfield":
            if is_format_bound(current, param.name):
                return None, None  # URL/path/JSON: no generic edit is valid; covered by a dedicated fixture test
            text = (current if isinstance(current, str) else "") + "x"
            return text, lambda: (editor.control.setText(text), QTest.keyClick(editor.control, Qt.Key.Key_Return))
        if t == "color":
            target = "#123456" if current != "#123456" else "#654321"
            return target, lambda: editor.pick_color(QColor(target))
        if t == "time-format":
            flipped = {k: not b.isChecked() for k, b in editor.checks.items()}
            return flipped, lambda: [b.click() for b in editor.checks.values()]
        return None, None

    def exercise(self, folder: Path) -> dict:
        self.open_real(folder)
        panel = self.window.settings_panel
        settings = self.window.bridge.settings_spec
        self.assertEqual(set(panel.editors), {p.name for p in settings.parameters})  # generated from metadata only
        for key, data_type, reason in panel.summary()["unsupported"]:
            self.assertTrue(reason, key)
        baseline = [e.message[:160] for e in self.window.console.matching("error", widget_only=True)]
        reloads, settled = self.window.reload_count, []
        self.window.loadSettled.connect(lambda ok: settled.append(ok))
        if panel.custom_style_check is not None:
            # D4: with Custom Style off iCUE hides the personalization editors and replaces their values for the widget; a user
            # edits them after switching Custom Style on. (Off-state behaviour is tested in test_personalization.py.)
            self.assertTrue(self.window.set_custom_style(True))
            self.pump(0.3)
        done, skipped = [], []
        for key, editor in panel.editors.items():
            if not editor.supported:
                continue
            expected, act = self.new_value(editor)
            if act is None:
                skipped.append((key, editor.param.data_type))
                continue
            act()
            done.append((key, editor.param.data_type))
            instance_value = self.window.bridge.instance.data.get(key)
            if editor.param.data_type == "time-format":
                self.assertEqual(instance_value, expected | {} if isinstance(expected, dict) else expected, key)
            else:
                self.assertEqual(instance_value, expected, key)
            self.assertTrue(self.wait(lambda: self.jsj(f"window[{key!r}]") == (expected if editor.param.data_type != "slider"
                                                                                 else expected), 8) or
                            editor.param.data_type in ("time-format",), key)
        self.pump(0.5)
        self.assertEqual((self.window.reload_count, settled), (reloads, []), "a setting change must not reload the page")
        # No error is ignored. Errors present right after load are environment/widget facts (reported, classified);
        # anything that appears after the edits was caused by them and fails the test.
        after = [e.message[:160] for e in self.window.console.matching("error", widget_only=True)]
        new = [(classify_console_error(m), m) for m in after if m not in baseline]
        self.assertEqual(new, [], "console errors caused by the edits")
        runtime_baseline = [m for m in baseline if classify_console_error(m) == "runtime"]
        self.assertEqual(runtime_baseline, [], "widget runtime errors at load")
        return {"done": done, "skipped": skipped, "unsupported": panel.summary()["unsupported"],
                "baseline": [(classify_console_error(m), m) for m in baseline]}

    def test_basketball_scores(self) -> None:
        result = self.exercise(BASKETBALL)
        types = {t for _, t in result["done"]}
        self.assertTrue({"combobox", "switch", "slider", "color"} <= types, result["done"])
        self.assertEqual({t for _, t, _ in result["unsupported"]}, {"media-selector"})

    def test_smokeclock(self) -> None:
        result = self.exercise(SMOKECLOCK)
        types = {t for _, t in result["done"]}
        self.assertTrue({"tab-buttons", "switch", "combobox", "slider", "color"} <= types, result["done"])

    def test_rss_feeder_regression_and_live_reload(self) -> None:
        result = self.exercise(RSS)
        self.assertTrue({"slider", "color"} <= {t for _, t in result["done"]}, result["done"])
        self.assertTrue(any(t == "textfield" for _, t in result["skipped"]), "the URL field is format-bound and skipped")
        keys = [k for k, t in result["done"] if t == "textfield"]
        expected = {k: self.window.bridge.instance.data[k] for k in keys}
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: self.window.reload_count == 1 and not self.window._reloading, 30))
        self.pump(1.5)
        for key, value in expected.items():
            self.assertEqual(self.js(f"window[{key!r}]"), value)
            self.assertEqual(self.window.settings_panel.editors[key].control.text(), value)
        after = [e.message[:160] for e in self.window.console.matching("error", widget_only=True)]
        baseline = {m for _, m in result["baseline"]}
        self.assertEqual([m for m in after if m not in baseline], [], "errors added by the edits or the reload")


URL_HTML = """<!doctype html><html><head><title>Feed</title>
<meta name="x-icue-property" content="feedUrl" data-type="textfield" data-default="'https://feeds.example.test/world.xml'">
<meta name="x-icue-property" content="title" data-type="textfield" data-default="'News'">
<script>
window.__log = [];
icueEvents = { onICUEInitialized: function () {}, onDataUpdated: function () { window.__log.push('feedUrl=' + feedUrl); } };
</script></head><body>feed</body></html>"""


SENSORBAR = WIDGETS / "Sensor Bar" / "widget" / "sensorbar"
SENSOR_ROWS = """JSON.stringify([...document.querySelectorAll('.sensor-row')].map(function (row) {
  var a = getComputedStyle(row.querySelector('.sensor-meta'), '::after');
  return {label: row.querySelector('.sensor-label').textContent, value: row.querySelector('.sensor-value').textContent,
          bar: getComputedStyle(row.querySelector('.sensor-bar')).display !== 'none',
          leader: a.content !== 'none' && a.borderTopStyle !== 'none'}; }))"""


@unittest.skipUnless(SENSORBAR.is_dir(), "WidgetsXE Sensor Bar not present")
class SensorBarMockTests(PageTestCase):
    """Ticket "Sensor Bar - a dash appears" (observation only, the widget is not changed): the MOCK Sensors provider renders rows;
    Show Bar / Show Row Background toggle what the ticket describes. Asserts only what the widget's own code does with the mock."""

    def setUp(self) -> None:
        from widgetdevtool.sensors_mock import catalog
        PageTestCase.setUp(self)
        self.addCleanup(catalog().reset)
        self.window.show()
        self.assertTrue(self.window.open_folder(SENSORBAR))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED", 30))
        self.window.select_device("dash_accessory_lcd", "SizeM", "landscape")

    def rows(self) -> list:
        return json.loads(self.js(SENSOR_ROWS))

    def test_add_sensor_uses_the_mock_default_and_rows_render_mock_values(self) -> None:
        from widgetdevtool.sensors_mock import _sid
        spec = self.window.bridge.settings_spec.parameter("sensorsFactoryValues")
        self.assertEqual((spec.meta["defaultSensorId"], spec.default, spec.default_valid), (_sid(1), [], True))  # no ReferenceError
        self.assertEqual(self.window.bridge.instance.data["sensorsFactoryValues"], [])  # a NEW instance has no sensor, as in iCUE
        editor = self.window.settings_panel.editors["sensorsFactoryValues"]
        editor.add_sensor_button.click()
        self.assertTrue(self.wait(lambda: [r["value"] for r in self.rows()] == ["45.3 \u00b0C"], 10), self.rows())
        self.assertEqual(self.rows()[0]["label"], "CPU Package")

    def test_show_bar_and_row_background_switches(self) -> None:
        from widgetdevtool.sensors_mock import _sid
        bridge = self.window.bridge
        bridge.set_properties({"sensorsFactoryValues": [{"sensorId": _sid(1), "color": "#f08e33"}, {"sensorId": _sid(7), "color": "#f08e33"}]})
        self.assertTrue(self.wait(lambda: len(self.rows()) == 2 and self.rows()[0]["value"] != "\u2014", 10))
        self.assertEqual(self.rows()[1]["value"], "\u2014")  # disconnected mock sensor: the widget's VALUE_PLACEHOLDER (em dash)
        self.assertTrue(all(r["bar"] and not r["leader"] for r in self.rows()))
        self.window.settings_panel.editors["showBar"].control.click()  # Show Bar OFF (the ticket step)
        self.assertTrue(self.wait(lambda: not any(r["bar"] for r in self.rows()), 10))
        self.assertFalse(any(r["leader"] for r in self.rows()))  # with Row Background ON: no leader line
        self.window.settings_panel.editors["showRowBackground"].control.click()
        # Both OFF: the widget USED TO draw a .sensor-meta::after leader line here (the dash of ticket XNCW-1). WidgetsXE commit 4809221
        # "Hide dash when Sensor Bar show bar is off" removed that CSS rule, so the current widget draws no line. Observation of the
        # widget source, not of WidgetDevTool: on a WidgetsXE checkout without that commit this assertion fails.
        self.pump(1.0)
        self.assertTrue(len(self.rows()) == 2 and not any(r["leader"] for r in self.rows()), self.rows())

    def test_sensor_service_not_ready_gives_dashes_until_it_becomes_ready(self) -> None:
        """Mock of the iCUE replica-not-initialized state: every answer is empty, Sensor Bar shows its em dash (label and value);
        when the service becomes ready (sensorAdded per id) the rows are fetched again."""
        from widgetdevtool.sensors_mock import _sid, catalog
        catalog().set_service_ready(False)
        self.window.bridge.set_properties({"sensorsFactoryValues": [{"sensorId": _sid(1), "color": "#f08e33"}]})
        self.assertTrue(self.wait(lambda: [(r["label"], r["value"]) for r in self.rows()] == [("\u2014", "\u2014")], 10), self.rows())
        catalog().set_service_ready(True)
        self.assertTrue(self.wait(lambda: [r["value"] for r in self.rows()] == ["45.3 \u00b0C"], 10), self.rows())


class FormatBoundTests(unittest.TestCase):
    def test_format_bound_values_are_recognised(self) -> None:
        for value in ("https://a.b/c.xml", "file:///x", "C:\\dir\\f.txt", "/usr/x", '{"a":1}', "[1]", "a@b.c"):
            self.assertTrue(is_format_bound(value), value)
        for value in ("News", "", "hello world", 5, None):
            self.assertFalse(is_format_bound(value), value)
        self.assertTrue(is_format_bound("", "feedUrl4"))   # empty, but the name says it holds a URL
        self.assertTrue(is_format_bound("", "widgetFont"))
        self.assertFalse(is_format_bound("", "title"))

    def test_console_errors_are_classified_by_origin(self) -> None:
        self.assertEqual(classify_console_error("Access to fetch at 'https://x' from origin 'file://' has been blocked"), "network")
        self.assertEqual(classify_console_error("Failed to load resource: net::ERR_NAME_NOT_RESOLVED"), "network")
        self.assertEqual(classify_console_error("Uncaught ReferenceError: timeZone is not defined"), "runtime")


class UrlFieldFlowTests(PageTestCase):
    """A URL textfield is edited with a valid URL (fixture), not with an appended character."""

    def test_url_edit_reaches_the_widget_without_reload_or_console_errors_and_survives_reload(self) -> None:
        root = write_widget(Path(self._tmp.name) / "feed", html=URL_HTML)
        self.assertTrue(self.window.load_folder(root))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.pump(0.4)
        editors = self.window.settings_panel.editors
        self.assertTrue(is_format_bound(editors["feedUrl"].control.text()))
        new_url = "https://feeds.example.test/technology.xml"
        reloads = self.window.reload_count
        before = self.jsj("window.__log")  # onDataUpdated also runs once during start-up
        editors["feedUrl"].control.setText(new_url)
        QTest.keyClick(editors["feedUrl"].control, Qt.Key.Key_Return)
        self.assertTrue(self.wait(lambda: self.js("feedUrl") == new_url))
        self.pump(0.3)
        self.assertEqual(self.window.bridge.instance.data["feedUrl"], new_url)
        self.assertEqual(self.jsj("window.__log")[len(before):], ["feedUrl=" + new_url])  # onDataUpdated ran once
        self.assertEqual(self.window.reload_count, reloads)
        self.assertEqual(self.window.console.matching("error", widget_only=True), [])
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: self.window.reload_count == reloads + 1 and not self.window._reloading))
        self.pump(0.4)
        self.assertEqual((self.js("feedUrl"), editors["feedUrl"].control.text()), (new_url, new_url))

if __name__ == "__main__":
    unittest.main()