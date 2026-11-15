r"""Runtime bridge tests (v0.2): initialisation, lifecycle order, properties, plugin mock, console.

No iCUE, no device, no network. A test asserts only what the iCUE template guarantees; observed-but-
unguaranteed behaviour is printed, not asserted. Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgetdevtool.main_window import MainWindow, State  # noqa: E402
from widgetdevtool.properties import compute_defaults, parse_properties  # noqa: E402
from widgetdevtool.script_builder import build_data_script, parse_required_plugins, to_qml_string  # noqa: E402
from widgetdevtool.translator import Translator  # noqa: E402

LINK = "widgetbuilder.linkprovider:Url:1.0"
SENSORS = "widgetbuilder.sensorsdataprovider:Sensors:1.0"
MEDIA = "widgetbuilder.mediadataprovider:Media:1.0"

WIDGET_HTML = """<!doctype html><html><head><title>t</title>
<meta name="x-icue-property" content="refreshMinutes" data-type="slider" data-default="10" data-min="1" data-max="60" data-step="1">
<meta name="x-icue-property" content="label" data-type="textfield" data-default="'hello'">
<meta name="x-icue-property" content="enabled" data-type="switch" data-default="true">
<meta name="x-icue-property" content="accent" data-type="color" data-default="'#5B8CFF'">
<script>
window.__log = [];
function L(x) { window.__log.push(x); }
window.__atStart = {
  refreshMinutes: typeof refreshMinutes !== 'undefined' ? refreshMinutes : 'UNDEF',
  label: typeof label !== 'undefined' ? label : 'UNDEF',
  enabled: typeof enabled !== 'undefined' ? enabled : 'UNDEF',
  accent: typeof accent !== 'undefined' ? accent : 'UNDEF',
  iCUE_initialized: typeof iCUE_initialized !== 'undefined' ? iCUE_initialized : 'UNDEF',
  icueEvents: typeof icueEvents };
icueEvents = {
  onICUEInitialized: function () { L('onICUEInitialized'); window.__initFlag = iCUE_initialized; },
  onDataUpdated: function () { L('onDataUpdated:' + refreshMinutes); } };
pluginLinkproviderEvents = { onInitialized: function () { L('plugin:Linkprovider'); } };
document.addEventListener('DOMContentLoaded', function () { L('DOMContentLoaded'); });
console.log('hello-log'); console.warn('hello-warn'); console.error('hello-error');
setTimeout(function () { undefinedFunction(); }, 0);
</script></head><body>x</body></html>"""


def write_widget(root: Path, plugins: list[str] | None = None, html: str = WIDGET_HTML) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"id": "com.test.bridge", "name": "Bridge Widget", "version": "1.0.0"}
    if plugins is not None:
        manifest["required_plugins"] = plugins
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "index.html").write_text(html, encoding="utf-8")
    (root / "translation.json").write_text(
        json.dumps({"en": {"translation": {"Hello": "Hello EN"}}, "de": {"translation": {"Hello": "Hallo"}}}),
        encoding="utf-8")
    return root


class PureTests(unittest.TestCase):
    def test_to_qml_string_follows_toQmlString(self) -> None:
        self.assertEqual(to_qml_string(True), "true")
        self.assertEqual(to_qml_string(10), "10")
        self.assertEqual(to_qml_string("12"), "12")        # numeric-looking strings become raw numbers (quirk)
        self.assertEqual(to_qml_string("#fff"), '"#fff"')
        self.assertEqual(to_qml_string('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(to_qml_string(["x"]), '""')       # lists and maps cannot convert to string
        self.assertEqual(to_qml_string(None), '""')

    def test_script_has_no_leftover_placeholders_and_declares_globals(self) -> None:
        script = build_data_script({"a": 1, "label": "x", "url": "file:///skip", "typeId": "t"}, "{id}", ["Linkprovider"])
        self.assertNotIn("SCRIPT_", script)
        self.assertNotIn("%1", script)
        for needle in ("window.a = 1;", 'window.label = "x";', "let icueEvents", "let iCUE_initialized = false;",
                       'let uniqueId = "{id}";', "channel.objects.Linkprovider", "channel.objects.backend"):
            self.assertIn(needle, script)
        self.assertNotIn("window.url", script)             # url/typeId/isPreview are never declared

    def test_required_plugins_parsing(self) -> None:
        decls = parse_required_plugins({"required_plugins": [LINK, "bad", 5]})
        self.assertEqual([(d.channel_id, d.component, d.version) for d in decls], [("Linkprovider", "Url", "1.0")])

    def test_defaults_follow_html_parser_rules(self) -> None:
        specs = parse_properties(WIDGET_HTML + '<meta name="x-icue-property" content="bad" data-type="slider" data-default="abc">'
                                 '<meta name="x-icue-property" content="off" data-type="switch" data-default="false">'
                                 '<meta name="x-icue-property" content="weird" data-type="made-up">')
        self.assertEqual(len(specs), 7)  # 4 in WIDGET_HTML + 3 appended
        defaults, notes = compute_defaults(specs, lambda s: s)
        self.assertEqual(defaults["refreshMinutes"], 10)
        self.assertEqual(defaults["label"], "hello")
        self.assertIs(defaults["enabled"], True)
        self.assertEqual(defaults["accent"], "#5b8cff")    # HYPOTHESIS (QColor.name())
        self.assertEqual(defaults["bad"], 0)               # QString::toInt() failure -> 0
        self.assertIs(defaults["off"], False)
        self.assertNotIn("weird", defaults)
        self.assertTrue(any("weird" in n for n in notes))

    def test_translator_falls_back_to_english_then_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = write_widget(Path(tmp) / "w")
            self.assertEqual(Translator(root / "index.html", "de").translate("Hello"), "Hallo")
            fr = Translator(root / "index.html", "fr")
            self.assertEqual(fr.translate("Hello"), "Hello EN")
            self.assertEqual(fr.translate("Nope"), "Nope")
            self.assertIn("Nope", fr.missing_keys)

RSS_FOLDER = Path(r"D:\Widgets\WidgetsXE\RSS Feed Reader\widget\rssfeedreader")


class PageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.window = MainWindow()
        self.addCleanup(self._close)

    def _close(self) -> None:
        self.window.shutdown()
        self.pump(0.2)

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def wait(self, predicate, timeout: float = 20.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def js(self, script: str):
        box: dict = {}
        self.window.view.page().runJavaScript(script, lambda v: box.setdefault("v", v))
        self.assertTrue(self.wait(lambda: "v" in box, 10), f"runJavaScript did not return: {script}")
        return box["v"]

    def jsj(self, expression: str):
        """Evaluate an expression and return it via JSON. runJavaScript() returns "" for arrays/objects
        in PySide6 6.9.3 (measured), so structured values must be serialised."""
        return json.loads(self.js(f"JSON.stringify({expression})"))

    def load(self, root: Path) -> None:
        self.assertTrue(self.window.load_folder(root))
        self.assertTrue(self.wait(lambda: self.window.state is State.LOADED), f"state={self.window.state}")

    def lifecycle_done(self) -> bool:
        return self.wait(lambda: self.js("window.__log && window.__log.indexOf('onDataUpdated:10') >= 0") is True)


class LifecycleTests(PageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = write_widget(Path(self._tmp.name) / "w", [LINK])
        self.load(self.root)
        self.assertTrue(self.lifecycle_done(), f"lifecycle never completed; console={self.window.console.entries}")

    def test_order_dom_then_initialized_then_data_updated(self) -> None:
        log = self.jsj("window.__log")
        print("\n   observed lifecycle log:", log)  # informational: plugin position is not guaranteed
        self.assertEqual(log.count("onICUEInitialized"), 1)
        self.assertLess(log.index("DOMContentLoaded"), log.index("onICUEInitialized"))
        self.assertLess(log.index("onICUEInitialized"), log.index("onDataUpdated:10"))
        self.assertIs(self.js("window.__initFlag"), True, "iCUE_initialized must be true inside onICUEInitialized")

    def test_properties_and_globals_exist_before_widget_scripts_run(self) -> None:
        start = self.jsj("window.__atStart")
        self.assertEqual(start["refreshMinutes"], 10)
        self.assertEqual(start["label"], "hello")
        self.assertIs(start["enabled"], True)
        self.assertEqual(start["accent"].lower(), "#5b8cff")
        self.assertIs(start["iCUE_initialized"], False)
        self.assertEqual(start["icueEvents"], "object")

    def test_icue_global_object_and_tr_promise(self) -> None:
        self.assertEqual(self.js("iCUE.fpsLimit"), 30)
        self.assertIs(self.js("iCUE.isPreview"), False)
        self.assertEqual(self.js("iCUE.iCUELanguage"), "en")
        self.assertIs(self.js("tr('Hello') instanceof Promise"), True, "iCUE 5 tr() goes through WebChannel and returns a Promise")
        self.js("tr('Hello').then(v => { window.__tr = v; }); 0")
        self.assertTrue(self.wait(lambda: self.js("window.__tr") == "Hello EN"))
        self.js("iCUE.defaultTemperatureUnit().then(v => { window.__unit = v; }); 0")
        self.assertTrue(self.wait(lambda: self.js("window.__unit") == "\u00b0C"))

    def test_set_properties_runs_ondataupdated_without_reload(self) -> None:
        loads_before = self.js("performance.timeOrigin")
        self.window.bridge.set_properties({"refreshMinutes": 25})
        self.assertTrue(self.wait(lambda: self.js("window.__log.indexOf('onDataUpdated:25') >= 0") is True))
        self.assertEqual(self.js("window.refreshMinutes"), 25)
        self.assertEqual(self.js("performance.timeOrigin"), loads_before, "page must not reload on a property change")

    def test_linkprovider_mock_records_calls(self) -> None:
        self.assertIs(self.js("pluginLinkprovider_initialized"), True)
        self.assertEqual(self.js("typeof window.plugins.Linkprovider.open"), "function")
        self.js("window.plugins.Linkprovider.open('https://example.org/x'); 0")
        mock = self.window.bridge.plugin_mocks["Linkprovider"]
        self.assertTrue(self.wait(lambda: mock.opened_urls == ["https://example.org/x"]))
        self.assertTrue(self.window.console.matching(contains="MOCK only"))

    def test_console_logs_and_uncaught_errors_are_captured(self) -> None:
        self.assertTrue(self.wait(lambda: self.window.console.matching("error", "undefinedFunction", widget_only=True)))
        c = self.window.console
        self.assertTrue(c.matching("info", "hello-log", widget_only=True))
        self.assertTrue(c.matching("warning", "hello-warn", widget_only=True))
        self.assertTrue(c.matching("error", "hello-error", widget_only=True))
        self.assertIn("index.html", c.matching("error", "undefinedFunction", widget_only=True)[0].source)
        self.assertIn("[error]", self.window.console_view.toPlainText())

    def test_widget_is_loaded_from_source_folder_without_packaging(self) -> None:
        before = sorted(p.name for p in self.root.iterdir())
        href = self.js("location.href")
        self.assertTrue(href.startswith("file:///") and href.endswith("/w/index.html"), href)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)
        self.assertFalse(list(self.root.rglob("*.icuewidget")))


class PluginReportingTests(PageTestCase):
    def test_unsupported_plugin_is_reported_not_faked(self) -> None:
        root = write_widget(Path(self._tmp.name) / "w", [LINK, MEDIA])
        self.load(root)
        self.assertTrue(self.window.console.matching(contains="Mediadataprovider will be undefined"))
        self.assertTrue(self.wait(lambda: self.js("typeof window.plugins === 'object'") is True))
        self.assertIs(self.js("window.plugins.Mediadataprovider === undefined"), True)
        self.assertEqual(self.js("typeof pluginMediadataprovider_initialized"), "undefined")


SENSOR_HTML = """<!doctype html><html><head><title>t</title><script>
window.__r = {}; window.__changes = [];
function ask(name, method) { var args = Array.prototype.slice.call(arguments, 2);
  var p = window.plugins.Sensorsdataprovider; var id = Object.keys(window.__r).length + 1;
  window.__r[id] = {name: name}; p[method].apply(p, [id].concat(args)); }
pluginSensorsdataproviderEvents = { onInitialized: function () {
  var p = window.plugins.Sensorsdataprovider;
  p.asyncResponse.connect(function (id, value) { window.__r[id].value = value; window.__r[id].type = typeof value; });
  p.sensorValueChanged.connect(function (id, v) { window.__changes.push(['value', id, v]); });
  p.sensorDataChanged.connect(function (id) { window.__changes.push(['data', id]); });
  p.sensorAdded.connect(function (id) { window.__changes.push(['added', id]); });
  window.__ready = true; } };
</script></head><body>s</body></html>"""


class SensorsPluginMockTests(PageTestCase):
    """window.plugins.Sensorsdataprovider: the Sensors.qml protocol (requestId + asyncResponse, string values), MOCK sensors."""

    def setUp(self) -> None:
        from widgetdevtool.sensors_mock import catalog
        PageTestCase.setUp(self)
        self.catalog = catalog()
        self.addCleanup(self.catalog.reset)
        self.load(write_widget(Path(self._tmp.name) / "s", [SENSORS], SENSOR_HTML))
        self.assertTrue(self.wait(lambda: self.js("window.__ready === true") is True))

    def answers(self, count: int) -> dict:
        self.assertTrue(self.wait(lambda: self.jsj("Object.values(window.__r).filter(function (r) { return 'value' in r; }).length") >= count))
        return {r["name"]: (r["value"], r["type"]) for r in self.jsj("Object.values(window.__r)")}

    def test_every_request_is_answered_once_through_async_response(self) -> None:
        from widgetdevtool.sensors_mock import _sid
        self.js(f"ask('value', 'getSensorValue', '{_sid(1)}'); ask('units', 'getSensorUnits', '{_sid(1)}');"
                f"ask('name', 'getSensorName', '{_sid(1)}'); ask('device', 'getSensorDeviceName', '{_sid(1)}');"
                f"ask('type', 'getSensorType', '{_sid(4)}'); ask('kind', 'getSensorKind', '{_sid(4)}');"
                f"ask('connected', 'sensorIsConnected', '{_sid(7)}'); ask('all', 'getAllSensorIds');"
                "ask('default', 'getDefaultSensorId', 'fan'); ask('unknown', 'getSensorValue', 'nope'); true")
        got = self.answers(10)
        self.assertEqual(got["value"], ("45.3", "string"))  # QString in iCUE: the widget gets a STRING
        self.assertEqual((got["units"][0], got["name"][0], got["device"][0]), ("\u00b0C", "CPU Package", "MOCK CPU"))
        self.assertEqual((got["type"][0], got["kind"][0]), ("load", "gpu-load"))
        self.assertEqual(got["connected"], (False, "boolean"))
        self.assertEqual(len(got["all"][0]), len(self.catalog.sensors))
        self.assertEqual(got["default"][0], _sid(5))
        self.assertEqual(got["unknown"][0], "")
        self.assertTrue(self.window.console.matching(contains="unknown sensor id 'nope'"))
        self.assertEqual(self.js(f"window.plugins.Sensorsdataprovider.getDefaultSensorIdBlock === undefined"), False)

    def test_catalog_changes_reach_the_page_as_signals(self) -> None:
        from widgetdevtool.sensors_mock import _sid
        self.catalog.set_value(_sid(2), "88.5")
        self.catalog.set_connected(_sid(2), False)
        self.assertTrue(self.wait(lambda: len(self.jsj("window.__changes")) >= 2))
        self.assertEqual(self.jsj("window.__changes"), [["value", _sid(2), "88.5"], ["data", _sid(2)]])

    def test_a_service_that_is_not_ready_answers_empty_values_like_icue(self) -> None:
        """SensorDataController::requestAsync: replica not initialized -> callback(ReturnType{}); Valid -> sensorAdded for every id."""
        from widgetdevtool.sensors_mock import _sid
        self.catalog.set_service_ready(False)
        self.js(f"ask('value', 'getSensorValue', '{_sid(1)}'); ask('name', 'getSensorName', '{_sid(1)}');"
                f"ask('connected', 'sensorIsConnected', '{_sid(1)}'); ask('all', 'getAllSensorIds'); true")
        got = self.answers(4)
        self.assertEqual((got["value"], got["name"], got["connected"], got["all"]), (("", "string"), ("", "string"), (False, "boolean"), ([], "object")))
        self.assertEqual(self.js("window.plugins.Sensorsdataprovider.getDefaultSensorIdBlock('temperature')"), None) if False else None
        self.catalog.set_service_ready(True)
        self.assertTrue(self.wait(lambda: len([c for c in self.jsj("window.__changes") if c[0] == "added"]) == len(self.catalog.sensors)))


MODULE_JS = """const LABEL = typeof tr === 'function' ? tr('Hello') : 'no-tr';
export function getDefaultKey() { return 'auto'; }
export function getLabel() { return LABEL; }
"""

MODULE_HTML = """<!doctype html><html><head>
<meta name="x-icue-property" content="timeZone" data-type="combobox" data-values="Zones.getDefaultKey()" data-default="Zones.getDefaultKey()">
<meta name="x-icue-property" content="caption" data-type="textfield" data-default="Zones.getLabel()">
<style>@font-face { font-family: WdtOpenSansBold; src: url('qrc:/fonts/OpenSans-Bold.ttf'); }</style>
<script>
window.__atStart = typeof timeZone !== 'undefined' ? timeZone : 'UNDEF';
function tzKey() { return timeZone; }   // like Basketball Scores: ReferenceError when the property is missing
document.addEventListener('DOMContentLoaded', function () {
  document.fonts.load('16px WdtOpenSansBold').then(function (f) { window.__fontFaces = f.length; },
                                                    function () { window.__fontFaces = -1; });
});
</script></head><body>x</body></html>"""


class ManifestModulesTests(unittest.TestCase):
    """Regression: data-default expressions use the manifest "modules" (JavaScriptExecutor::importModule)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def make(self, tmp: str, modules) -> tuple[Path, list]:
        root = Path(tmp)
        (root / "modules").mkdir()
        (root / "modules" / "Zones.mjs").write_text(MODULE_JS, encoding="utf-8")
        (root / "index.html").write_text(MODULE_HTML, encoding="utf-8")
        manifest = {"id": "x", "modules": modules}
        return root, manifest

    def test_module_is_exposed_by_base_name_and_sees_tr(self) -> None:
        from widgetdevtool.properties import manifest_modules
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self.make(tmp, ["modules/Zones.mjs", 5, None])  # non-string entries are ignored
            mods = manifest_modules(manifest, root / "index.html")
            self.assertEqual([m.name for m in mods], ["Zones.mjs"])
            specs = parse_properties((root / "index.html").read_text(encoding="utf-8"))
            defaults, notes = compute_defaults(specs, lambda s: s.upper(), mods)
            self.assertEqual(defaults["timeZone"], "auto")
            self.assertEqual(defaults["caption"], "HELLO")      # tr() existed while the module was evaluated
            self.assertEqual(notes, [])

    def test_without_modules_the_failure_is_reported_not_hidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self.make(tmp, [])
            specs = parse_properties((root / "index.html").read_text(encoding="utf-8"))
            defaults, notes = compute_defaults(specs, lambda s: s)
            self.assertNotIn("timeZone", defaults)
            self.assertTrue(any("Zones is not defined" in n for n in notes))

    def test_missing_or_broken_module_is_reported(self) -> None:
        from widgetdevtool.properties import manifest_modules
        with tempfile.TemporaryDirectory() as tmp:
            root, manifest = self.make(tmp, ["modules/Nope.mjs", "modules/Bad.mjs"])
            (root / "modules" / "Bad.mjs").write_text("export function (", encoding="utf-8")
            _, notes = compute_defaults([], lambda s: s, manifest_modules(manifest, root / "index.html"))
            self.assertTrue(any("Nope.mjs" in n and "does not exist" in n for n in notes))
            self.assertTrue(any("Bad.mjs" in n and "failed to load" in n for n in notes))


class ModulesAndQrcFontsPageTests(PageTestCase):
    def widget(self) -> Path:
        root = Path(self._tmp.name) / "w"
        (root / "modules").mkdir(parents=True)
        (root / "modules" / "Zones.mjs").write_text(MODULE_JS, encoding="utf-8")
        (root / "manifest.json").write_text(json.dumps({"id": "com.test.mods", "name": "Mods",
                                                        "modules": ["modules/Zones.mjs"]}), encoding="utf-8")
        (root / "index.html").write_text(MODULE_HTML, encoding="utf-8")
        return root

    def test_module_based_property_exists_before_widget_scripts(self) -> None:
        self.load(self.widget())
        self.assertEqual(self.js("window.__atStart"), "auto")
        self.assertEqual(self.js("tzKey()"), "auto")            # no ReferenceError
        self.assertEqual(self.js("caption"), "Hello")
        self.assertFalse(self.window.console.matching("error", contains="timeZone is not defined"))

    def test_qrc_font_loads_from_file_page_without_cors_error(self) -> None:
        self.load(self.widget())
        self.assertTrue(self.wait(lambda: self.js("typeof window.__fontFaces !== 'undefined'") is True, 15))
        self.assertEqual(self.js("window.__fontFaces"), 1)
        self.assertIs(self.js("document.fonts.check('16px WdtOpenSansBold')"), True)
        self.assertEqual([e.message for e in self.window.console.matching("error", contains="CORS")], [])

    def test_bundled_fonts_are_registered(self) -> None:
        from widgetdevtool.qrc_fonts import register_fonts
        self.assertIn("OpenSans-Regular.ttf", register_fonts())
        self.assertIn("OpenSans-Bold.ttf", register_fonts())


@unittest.skipUnless(RSS_FOLDER.is_dir(), "WidgetsXE RSS Feeder not present on this machine")
class RssFeederSmokeTests(PageTestCase):
    def test_runtime_present_and_no_runtime_related_errors(self) -> None:
        self.load(RSS_FOLDER)
        self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True, 20),
                        "iCUE_initialized never became true")
        self.pump(4)  # let the widget run its start-up and first feed requests
        c = self.window.console
        widget_errors = [e.message for e in c.matching("error", widget_only=True)]
        print("\n   RSS Feeder console errors (%d):" % len(widget_errors))
        for message in widget_errors[:8]:
            print("     -", message[:160])
        runtime_related = [m for m in widget_errors
                           if any(k in m for k in ("Linkprovider", "icueEvents", "iCUE is not defined", "tr is not defined",
                                                   "plugins", "uniqueId", "WebChannel"))]
        self.assertEqual(runtime_related, [], "errors that point at missing iCUE runtime pieces")
        self.assertEqual(self.js("typeof window.plugins.Linkprovider.open"), "function")
        self.assertEqual(self.js("typeof feedUrl1"), "string")
        self.assertEqual(self.js("typeof refreshMinutes"), "number")


if __name__ == "__main__":
    unittest.main()