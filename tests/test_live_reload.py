r"""Live reload tests (v0.3). Run: .venv\Scripts\python.exe -m unittest discover -s tests -v"""
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

from widgetdevtool.live_reload import FolderWatcher, snapshot  # noqa: E402
from widgetdevtool.main_window import MainWindow, State  # noqa: E402

HTML = """<!doctype html><html><head><link rel="stylesheet" href="css/style.css">
<meta name="x-icue-property" content="speed" data-type="slider" data-default="10" data-min="1" data-max="60" data-step="1">
<script>window.__log=[]; window.__start = typeof speed !== 'undefined' ? speed : 'UNDEF';
icueEvents = { onICUEInitialized: function(){ window.__log.push('init'); } };</script>
<script src="js/app.js"></script></head><body>x</body></html>"""


def write_widget(root: Path, color: str = "red", tag: str = "v1", html: str = HTML) -> Path:
    (root / "css").mkdir(parents=True, exist_ok=True)
    (root / "js").mkdir(exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({"id": "com.test.live", "name": "Live"}), encoding="utf-8")
    (root / "index.html").write_text(html, encoding="utf-8")
    (root / "css" / "style.css").write_text("body{color:%s}" % color, encoding="utf-8")
    (root / "js" / "app.js").write_text(
        "window.__tag='%s'; localStorage.setItem('n', String((+localStorage.getItem('n')||0)+1));" % tag, encoding="utf-8")
    return root


class PureTests(unittest.TestCase):
    def test_snapshot_is_recursive_and_ignores_editor_noise(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = write_widget(Path(t))
            (root / "assets" / "img").mkdir(parents=True)
            (root / "assets" / "img" / "a.png").write_bytes(b"1")
            (root / ".git").mkdir()
            (root / ".git" / "HEAD").write_text("x")
            (root / "index.html.swp").write_text("x")
            (root / "x.tmp").write_text("x")
            (root / "index.html~").write_text("x")
            self.assertEqual(set(snapshot(root)), {"manifest.json", "index.html", "css/style.css", "js/app.js",
                                                   "assets/img/a.png"})

    def test_debounce_gives_one_event_per_burst(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            root = write_widget(Path(t))
            w = FolderWatcher(interval_ms=100, debounce_ms=250)
            events: list = []
            w.changed.connect(events.append)
            w.start(root)
            w.set_enabled(False)  # drive poll() by hand with a fake clock
            (root / "css" / "style.css").write_text("body{color:blue}")
            w.poll(0.0); w.poll(0.1)
            (root / "js" / "app.js").write_text("window.__tag='v2';// longer")  # second file of the same Save
            w.poll(0.2); w.poll(0.4)
            self.assertEqual(events, [])           # window restarted by the second write
            w.poll(0.46)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0], ["css/style.css", "js/app.js"])
            w.poll(5.0); w.poll(6.0)
            self.assertEqual(len(events), 1)       # nothing new -> no repeat
            w.stop()


class PageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = write_widget(Path(self._tmp.name))
        self.w = MainWindow()
        self.settled = 0
        self.w.loadSettled.connect(lambda ok: setattr(self, "settled", self.settled + 1))
        self.assertTrue(self.w.load_folder(self.root))
        self.view, self.page, self.profile = self.w.view, self.w._page, self.w._profile  # created by the load (one stack per instance)
        self.pump(lambda: self.settled >= 1)

    def tearDown(self) -> None:
        self.w.shutdown()
        self.w.deleteLater()
        self._tmp.cleanup()

    def pump(self, cond=None, seconds: float = 15.0) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if cond and cond():
                return True
            time.sleep(0.01)
        return cond is None

    def js(self, code: str):
        box: dict = {}
        self.w._page.runJavaScript(code, lambda v: box.setdefault("v", v))
        self.pump(lambda: "v" in box, 10)
        return box.get("v")

    def wait_reloads(self, n: int) -> bool:
        return self.pump(lambda: self.w.reload_count >= n and not self.w._reloading)


class LiveReloadTests(PageTestCase):
    def test_css_and_js_edit_reload_once_with_same_view_page_profile(self) -> None:
        self.assertEqual(self.js("getComputedStyle(document.body).color"), "rgb(255, 0, 0)")
        write_widget(self.root, "blue", "v2")
        self.assertTrue(self.wait_reloads(1))
        self.pump(None, 1.0)                                   # a second, unwanted reload would show up here
        self.assertEqual(self.w.reload_count, 1)
        self.assertEqual(self.js("getComputedStyle(document.body).color"), "rgb(0, 0, 255)")
        self.assertEqual(self.js("window.__tag"), "v2")
        self.assertIs(self.w.view, self.view)
        self.assertIs(self.w._page, self.page)
        self.assertIs(self.w._profile, self.profile)
        self.assertIn("Reloaded #1", self.w.watch_label.text())

    def test_idle_watching_never_reloads(self) -> None:
        self.pump(None, 1.5)
        self.assertEqual(self.w.reload_count, 0)
        self.assertIn("Watching", self.w.watch_label.text())

    def test_lifecycle_is_reinitialised_after_each_reload(self) -> None:
        for i in (1, 2, 3):
            write_widget(self.root, "red", "v%d" % (i + 1))
            self.assertTrue(self.wait_reloads(i))
            self.pump(None, 0.3)
            self.assertEqual(self.js("window.__start"), 10)      # globals exist before page scripts run
            self.assertEqual(self.js("window.__log.filter(x => x === 'init').length"), 1)
            self.assertEqual(self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized"), True)

    def test_property_values_and_localstorage_survive_reload(self) -> None:
        self.w.bridge.set_properties({"speed": 42})
        self.pump(None, 0.3)
        n_before = int(self.js("localStorage.getItem('n')"))
        write_widget(self.root, "red", "v2")
        self.assertTrue(self.wait_reloads(1))
        self.pump(None, 0.3)
        self.assertEqual(self.js("window.__start"), 42)           # user value kept, not the default 10
        self.assertEqual(int(self.js("localStorage.getItem('n')")), n_before + 1)  # not cleared

    def test_removed_property_override_is_dropped_new_default_applies(self) -> None:
        self.w.bridge.set_properties({"speed": 42})
        write_widget(self.root, html=HTML.replace('content="speed"', 'content="other"'))
        self.assertTrue(self.wait_reloads(1))
        self.assertNotIn("speed", self.w.bridge.instance.edited)
        self.assertNotIn("speed", self.w.bridge.instance.data)

    def test_auto_reload_off_then_on_reloads_missed_edit(self) -> None:
        self.w.auto_reload.setChecked(False)
        write_widget(self.root, "blue", "v2")
        self.pump(None, 1.0)
        self.assertEqual(self.w.reload_count, 0)
        self.assertIn("OFF", self.w.watch_label.text())
        self.w.auto_reload.setChecked(True)
        self.assertTrue(self.wait_reloads(1))
        self.assertEqual(self.js("window.__tag"), "v2")

    def test_manual_reload_works_while_auto_is_off(self) -> None:
        self.w.auto_reload.setChecked(False)
        write_widget(self.root, "blue", "v2")
        self.w.reload_now()
        self.assertTrue(self.wait_reloads(1))
        self.assertEqual(self.js("window.__tag"), "v2")
        self.pump(None, 1.0)
        self.assertEqual(self.w.reload_count, 1)

    def test_broken_manifest_keeps_page_then_recovers(self) -> None:
        (self.root / "manifest.json").write_text("{ not json", encoding="utf-8")
        self.pump(lambda: "Invalid" in self.w.watch_label.text(), 5)
        self.assertIn("Invalid widget", self.w.watch_label.text())
        self.assertEqual(self.w.reload_count, 0)
        self.assertEqual(self.js("window.__tag"), "v1")           # previous page still shown
        write_widget(self.root, "red", "v3")
        self.assertTrue(self.wait_reloads(1))
        self.assertEqual(self.js("window.__tag"), "v3")

    def test_console_keeps_history_and_marks_reload(self) -> None:
        write_widget(self.root, "red", "v2")
        self.assertTrue(self.wait_reloads(1))
        self.assertTrue(any("--- reload #1" in e.message for e in self.w.console.entries))
        self.assertIs(self.w.state, State.LOADED)


if __name__ == "__main__":
    unittest.main()