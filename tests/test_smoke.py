r"""Smoke tests (v0.1 behaviour, still valid in v0.2). No iCUE, no device and no network needed.

Run:  .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Headless: no window is shown, no GPU is required.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgetdevtool.main_window import MainWindow, State  # noqa: E402
from widgetdevtool.widget_loader import check_widget_folder  # noqa: E402

MANIFEST = {"id": "com.test.smoke", "name": "Smoke Widget", "version": "1.0.0"}


def write_widget(root: Path, manifest: object = MANIFEST, index: str | None = "<html></html>") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        text = manifest if isinstance(manifest, str) else json.dumps(manifest)
        (root / "manifest.json").write_text(text, encoding="utf-8")
    if index is not None:
        (root / "index.html").write_text(index, encoding="utf-8")
    return root


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def test_valid_widget(self) -> None:
        check = check_widget_folder(write_widget(self.base / "ok"))
        self.assertTrue(check.ok)
        self.assertEqual(check.widget_name, "Smoke Widget")
        self.assertTrue(check.index_url.startswith("file:///"))

    def test_missing_manifest(self) -> None:
        check = check_widget_folder(write_widget(self.base / "w", manifest=None))
        self.assertFalse(check.ok)
        self.assertIn("manifest.json not found", check.errors[0])

    def test_invalid_json(self) -> None:
        check = check_widget_folder(write_widget(self.base / "w", manifest="{ nope"))
        self.assertFalse(check.ok)
        self.assertIn("not valid JSON", check.errors[0])

    def test_manifest_not_object(self) -> None:
        check = check_widget_folder(write_widget(self.base / "w", manifest="[1, 2]"))
        self.assertIn("JSON object", check.errors[0])

    def test_missing_and_empty_index(self) -> None:
        self.assertIn("index.html not found", check_widget_folder(write_widget(self.base / "a", index=None)).errors[0])
        self.assertIn("index.html is empty", check_widget_folder(write_widget(self.base / "b", index="")).errors[0])

    def test_not_a_folder(self) -> None:
        self.assertFalse(check_widget_folder(self.base / "missing").ok)

    def test_warnings_do_not_block(self) -> None:
        manifest = {**MANIFEST, "required_plugins": ["widgetbuilder.sensorsdataprovider:Sensors:1.0"]}
        check = check_widget_folder(write_widget(self.base / "w", manifest=manifest))
        self.assertTrue(check.ok)
        self.assertTrue(any("required_plugins" in w for w in check.warnings))


class RenderTests(unittest.TestCase):
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
        self._pump(0.2)

    def _pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def _wait(self, predicate, timeout: float = 20.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def _eval(self, script: str):
        box: dict = {}
        self.window.view.page().runJavaScript(script, lambda value: box.setdefault("v", value))
        self.assertTrue(self._wait(lambda: "v" in box), "runJavaScript did not return")
        return box["v"]

    def test_relative_css_and_js_resolve_via_file_url(self) -> None:
        # Folder name has a space on purpose: file:// URL encoding must still work.
        root = write_widget(
            Path(self._tmp.name) / "My Widget",
            index='<!doctype html><html><head><title>t</title>'
                  '<link rel="stylesheet" href="styles/style.css"></head>'
                  '<body><script src="scripts/widget.js"></script></body></html>',
        )
        (root / "styles").mkdir()
        (root / "scripts").mkdir()
        (root / "styles" / "style.css").write_text("body{background:rgb(1,2,3)}", encoding="utf-8")
        (root / "scripts" / "widget.js").write_text("window.__widgetScriptRan = 'yes';", encoding="utf-8")

        self.assertTrue(self.window.load_folder(root))
        self.assertTrue(self._wait(lambda: self.window.state is State.LOADED), f"state={self.window.state}")
        self.assertEqual(self._eval("getComputedStyle(document.body).backgroundColor"), "rgb(1, 2, 3)")
        self.assertEqual(self._eval("window.__widgetScriptRan"), "yes")
        self.assertEqual(self._eval("typeof iCUE_initialized"), "boolean",
                         "bridge must declare iCUE_initialized before widget scripts run")

    def test_invalid_folder_is_reported_and_not_loaded(self) -> None:
        root = write_widget(Path(self._tmp.name) / "bad", manifest="{ nope")
        self.assertFalse(self.window.load_folder(root))
        self.assertIs(self.window.state, State.INVALID)
        self.assertIn("not valid JSON", self.window.status.text())
        self.assertFalse(self.window.problems.isHidden())


if __name__ == "__main__":
    unittest.main()