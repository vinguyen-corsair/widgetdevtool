r"""Open Package tests: a downloaded .icuewidget becomes source in the widgets folder (never overwriting) and is opened.

Run: .venv\Scripts\python.exe -m unittest discover -s tests -p test_package_import.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)

from test_runtime_bridge import PageTestCase  # noqa: E402
from widgetdevtool.package_import import PackageError, destination, extract, find_existing, read_package, safe_name  # noqa: E402
from widgetdevtool.widget_library import scan_library  # noqa: E402

INDEX = """<!DOCTYPE html><html><head><meta charset="utf-8"/><title>{title}</title>
<link rel="icon" href="resources/icon.svg"/></head><body>hello</body></html>"""
SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="#fff"/></svg>'


def manifest(wid: str, name: str, version: str = "1.2.0") -> str:
    return json.dumps({"author": "Someone", "id": wid, "name": name, "description": "d", "version": version,
                       "preview_icon": "resources/icon.svg", "min_framework_version": "1.0.0", "os": [{"platform": "windows"}],
                       "supported_devices": [{"type": "dashboard_lcd"}]})


def make_package(path: Path, wid: str = "com.test.weather", name: str = "Fancy Weather", version: str = "1.2.0", prefix: str = "",
                 extra: dict[str, str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    files = {"manifest.json": manifest(wid, name, version), "index.html": INDEX.format(title=name), "resources/icon.svg": SVG,
             **(extra or {})}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, text in files.items():
            z.writestr(prefix + rel, text)
    return path


def make_source(root: Path, folder: str, wid: str, name: str, version: str = "1.0.0") -> Path:
    target = root / name / "widget" / folder
    (target / "resources").mkdir(parents=True)
    (target / "manifest.json").write_text(manifest(wid, name, version), encoding="utf-8")
    (target / "index.html").write_text(INDEX.format(title=name), encoding="utf-8")
    (target / "resources" / "icon.svg").write_text(SVG, encoding="utf-8")
    return target


class PackageFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wdt pkg ü ")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.root = self.dir / "WidgetsXE"
        self.root.mkdir()

    def test_a_package_is_read_without_extracting_anything(self) -> None:
        info = read_package(make_package(self.dir / "dl" / "fancyweather-1.2.0.icuewidget"))
        self.assertEqual((info.type_id, info.name, info.version, info.prefix), ("com.test.weather", "Fancy Weather", "1.2.0", ""))
        self.assertEqual(info.base_name, "fancyweather")  # the -<version> suffix is dropped
        self.assertEqual(info.project_name, "Fancy Weather")
        self.assertEqual(list(self.root.iterdir()), [])
        market = read_package(make_package(self.dir / "dl" / "Daily Quote-1.1.1-windows.icuewidget", wid="com.corsair.widget.daily-quote",
                                           name="Daily quote", version="1.1.1"))
        self.assertEqual(market.base_name, "daily-quote")  # marketplace name: spaces + platform suffix -> the typeId's last part

    def test_extraction_follows_the_widgetsxe_layout_and_keeps_the_package(self) -> None:
        pkg = make_package(self.dir / "dl" / "fancyweather-1.2.0.icuewidget")
        target = extract(read_package(pkg), self.root)
        self.assertEqual(target, self.root / "Fancy Weather" / "widget" / "fancyweather")
        self.assertTrue((target / "manifest.json").is_file() and (target / "index.html").is_file())
        self.assertTrue((target / "resources" / "icon.svg").is_file())
        self.assertTrue((self.root / "Fancy Weather" / pkg.name).is_file())
        self.assertEqual([e.type_id for e in scan_library(self.root)], ["com.test.weather"])
        self.assertFalse(list(target.parent.glob(".wdt-extract-*")))  # no staging folder left

    def test_one_wrapping_top_folder_is_stripped(self) -> None:
        info = read_package(make_package(self.dir / "wrapped.icuewidget", prefix="wrapped/"))
        self.assertEqual(info.prefix, "wrapped/")
        target = extract(info, self.root)
        self.assertTrue((target / "manifest.json").is_file())

    def test_nothing_is_ever_overwritten(self) -> None:
        info = read_package(make_package(self.dir / "fancyweather-1.2.0.icuewidget"))
        target = extract(info, self.root)
        (target / "index.html").write_text("my edits", encoding="utf-8")
        with self.assertRaises(PackageError):
            extract(info, self.root)
        self.assertEqual((target / "index.html").read_text(encoding="utf-8"), "my edits")

    def test_bad_archives_are_refused_before_anything_is_written(self) -> None:
        cases = {"not a zip": None, "no manifest": {"index.html": "x"}, "no index": {"manifest.json": manifest("a.b", "A")},
                 "zip slip": {"manifest.json": manifest("a.b", "A"), "index.html": "x", "../evil.txt": "x"},
                 "absolute": {"manifest.json": manifest("a.b", "A"), "index.html": "x", "C:/evil.txt": "x"},
                 "no id": {"manifest.json": json.dumps({"name": "A"}), "index.html": "x"}}
        for label, files in cases.items():
            path = self.dir / f"{label}.icuewidget"
            if files is None:
                path.write_text("plain text", encoding="utf-8")
            else:
                with zipfile.ZipFile(path, "w") as z:
                    for rel, text in files.items():
                        z.writestr(rel, text)
            with self.assertRaises(PackageError, msg=label):
                read_package(path)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse((self.dir / "evil.txt").exists())

    def test_names_are_safe_windows_folder_names(self) -> None:
        self.assertEqual(safe_name('My: "Widget"?'), "My Widget")
        self.assertEqual(safe_name("con"), "")
        info = read_package(make_package(self.dir / "x.icuewidget", name='Bad/Name*'))
        project, target = destination(info, self.root)
        self.assertEqual(project.name, "BadName")

    def test_an_existing_typeid_is_found(self) -> None:
        make_source(self.root, "fancyweather", "com.test.weather", "Fancy Weather")
        info = read_package(make_package(self.dir / "fancyweather-1.2.0.icuewidget"))
        self.assertEqual(len(find_existing(info, scan_library(self.root))), 1)


class OpenPackageWindowTests(PageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.dir = Path(self._tmp.name)
        self.root = self.dir / "WidgetsXE"
        make_source(self.root, "clock", "com.test.clock", "Clock")
        make_source(self.root, "calendar", "com.test.calendar", "Calendar")
        self.window.set_widgets_folder(self.root)

    def test_a_new_package_is_extracted_opened_and_shown_in_the_icue_tab(self) -> None:
        self.assertIs(self.window.open_package_button.parentWidget(), self.window.top_bar)
        pkg = make_package(self.dir / "Downloads" / "fancyweather-1.2.0.icuewidget")
        target = self.window.open_package(pkg, ask=False)
        self.assertEqual(target, self.root / "Fancy Weather" / "widget" / "fancyweather")
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.assertEqual(self.window.current.folder.resolve(), target.resolve())
        self.assertIs(self.window.inspector.currentWidget(), self.window.icue_info_panel)
        self.assertEqual(self.window.icue_info_panel.inspection.entry.type_id, "com.test.weather")
        self.assertTrue(self.window.icue_info_panel.inspection.in_library)
        self.assertEqual(len(self.window.widgets_folder.entries), 3)

    def test_a_package_already_in_the_source_opens_the_source_and_extracts_nothing(self) -> None:
        source = make_source(self.root, "fancyweather", "com.test.weather", "Fancy Weather", version="1.0.0")
        before = sorted(str(p) for p in self.root.rglob("*"))
        pkg = make_package(self.dir / "Downloads" / "fancyweather-1.2.0.icuewidget")
        opened = self.window.open_package(pkg, ask=False)
        self.assertEqual(opened.resolve(), source.resolve())
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)  # nothing written
        self.assertIn("already in the widgets folder", self.window.status.text())

    def test_a_bad_package_changes_nothing(self) -> None:
        bad = self.dir / "Downloads" / "broken.icuewidget"
        bad.parent.mkdir(parents=True)
        bad.write_text("not a zip", encoding="utf-8")
        before = sorted(str(p) for p in self.root.rglob("*"))
        self.assertIsNone(self.window.open_package(bad, ask=False))
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)
        self.assertIn("Cannot open broken.icuewidget", self.window.status.text())


if __name__ == "__main__":
    unittest.main()
