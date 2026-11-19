r"""Widget library and thumbnail inspector tests (D7a).

Expected behaviour comes from HtmlParser.cpp, HtmlTypeInterfacesImpl.cpp, WidgetGroupsModel.cpp, HtmlWidgetDeviceUtils.cpp and the
QML files WidgetPreviewPanel / AssetIcon / WidgetTypePreviewLayoutBase / WidgetImageTypePreviewView (iCUE v5.55, not verified against the
release). The tests prove that those rules are applied; they do NOT prove that a thumbnail looks like in iCUE 5, and a dark SVG
in them does NOT show that the Basketball Scores ticket is reproduced or fixed.
Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)
from PySide6.QtWidgets import QApplication  # noqa: E402

from test_runtime_bridge import PageTestCase  # noqa: E402
from test_settings_panel import BASKETBALL, RSS, SMOKECLOCK, classify_console_error  # noqa: E402
from widgetdevtool.device_profiles import load_profiles  # noqa: E402
from widgetdevtool.viewport import NATIVE  # noqa: E402
from widgetdevtool.paths import ICUE_DIR, WIDGETS_DIR  # noqa: E402
from widgetdevtool.icue_info_panel import inspect, summary  # noqa: E402
from widgetdevtool.widgets_folder import detect_root  # noqa: E402
from widgetdevtool.icue_theme import DEFAULT_ICON_FILE, PALETTE_FILE, PALETTE_SOURCE, load_palette  # noqa: E402
from widgetdevtool.thumbnails import analyze, render_preview_tile, render_type_tile  # noqa: E402
from widgetdevtool.widget_library import (  # noqa: E402
    SRC_DEFAULT, SRC_ICON, SRC_NONE, SRC_PREVIEW, build_groups, load_entry, parse_head, scan_library, support_reason)

WIDGETS = WIDGETS_DIR
ICUE = ICUE_DIR
PROFILES = {p.id: p for p in load_profiles()}
EDGE, LINK, PUMP, KEYBOARD = (PROFILES[k] for k in ("dash_accessory_lcd", "link_generic_lcd", "cooler_pump_lcd", "vanguard_96"))
PAL = {k: QColor(v) for k, v in load_palette().items()}


def svg(fill: str, size: int = 64, background: str | None = None) -> str:
    bg = f'<rect width="{size}" height="{size}" fill="{background}"/>' if background else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">{bg}'
            f'<circle cx="{size / 2}" cy="{size / 2}" r="{size / 3}" fill="{fill}"/></svg>')


def png(path: Path, w: int, h: int, color: str, alpha: int = 255) -> None:
    image = QImage(w, h, QImage.Format.Format_ARGB32)
    c = QColor(color)
    c.setAlpha(alpha)
    image.fill(c)
    assert image.save(str(path))


def make_widget(root: Path, folder: str, *, title: str = "Widget", wid: str | None = None, icon: str | None = None,
                preview: str | None = None, group: str | None = None, devices=(("dashboard_lcd", ()),), files: dict | None = None,
                manifest_preview_icon: str = "preview.svg", extra_head: str = "") -> Path:
    path = root / folder
    path.mkdir(parents=True, exist_ok=True)
    manifest = {"id": wid or f"com.test.{folder}", "name": title, "version": "1.0.0", "preview_icon": manifest_preview_icon,
                "supported_devices": [{"type": t, "features": list(f)} for t, f in devices]}
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    head = f"<title>{title}</title>"
    head += f'<link rel="icon" type="image/svg+xml" href="{icon}">' if icon is not None else ""
    head += f'<meta name="x-icue-widget-preview" content="{preview}">' if preview is not None else ""
    head += f'<meta name="x-icue-widget-group" content="{group}">' if group is not None else ""
    head += ('<meta name="x-icue-property" content="level" data-type="slider" data-default="5" data-min="0" data-max="10" '
             'data-step="1">' + extra_head)
    (path / "index.html").write_text(f"<!doctype html><html><head>{head}<script>icueEvents = {{ onICUEInitialized() {{}}, "
                                     f"onDataUpdated() {{}} }};</script></head><body>{title}</body></html>", encoding="utf-8")
    for name, content in (files or {}).items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            content(target)
    return path


class QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)


class HeadAndFallbackTests(QtCase):
    def test_library_scan_finds_widgets_from_their_files_only(self) -> None:
        make_widget(self.root, "a/one")
        make_widget(self.root, "b/c/two")
        (self.root / "empty").mkdir()
        (self.root / "only_manifest").mkdir()
        (self.root / "only_manifest" / "manifest.json").write_text("{}", encoding="utf-8")
        make_widget(self.root / "a" / "one", "nested_inside_a_widget")  # a widget folder is not searched further
        found = scan_library(self.root)
        self.assertEqual(sorted(e.folder.name for e in found), ["one", "two"])
        self.assertEqual([e.folder.name for e in scan_library(self.root / "a" / "one")], ["one"])  # the root itself
        self.assertEqual(scan_library(self.root / "missing"), [])

    def test_fallback_order_preview_then_icon_then_default_and_manifest_preview_icon_is_not_used(self) -> None:
        files = {"icon.svg": svg("#fff"), "pre.png": lambda p: png(p, 128, 56, "#336699")}
        both = load_entry(make_widget(self.root, "both", icon="icon.svg", preview="pre.png", files=files))
        only_icon = load_entry(make_widget(self.root, "icon", icon="icon.svg", files=files))
        neither = load_entry(make_widget(self.root, "none", manifest_preview_icon="icon.svg", files=files))
        self.assertEqual((both.preview_image().source, both.type_image().source), (SRC_PREVIEW, SRC_ICON))
        self.assertEqual((only_icon.preview_image().source, only_icon.type_image().source), (SRC_ICON, SRC_ICON))
        self.assertIn("no x-icue-widget-preview", only_icon.preview_image().reason)
        self.assertEqual((neither.preview_image().source, neither.type_image().source), (SRC_DEFAULT, SRC_DEFAULT))
        self.assertEqual(neither.manifest_preview_icon, "icon.svg")  # present and valid, but no thumbnail is taken from it
        self.assertTrue(neither.preview_image().usable_path == DEFAULT_ICON_FILE and DEFAULT_ICON_FILE.is_file())

    def test_a_missing_file_is_reported_and_never_replaced_by_a_fallback(self) -> None:
        entry = load_entry(make_widget(self.root, "m", icon="icon.svg", preview="gone.png", files={"icon.svg": svg("#fff")}))
        preview = entry.preview_image()
        self.assertEqual((preview.source, preview.exists, preview.usable_path), (SRC_PREVIEW, False, None))
        self.assertIn("does NOT fall back", preview.reason)
        lost_icon = load_entry(make_widget(self.root, "m2", icon="nowhere.svg"))
        self.assertEqual((lost_icon.type_image().source, lost_icon.type_image().exists), (SRC_ICON, False))
        self.assertIn("does NOT fall back to the default", lost_icon.type_image().reason)

    def test_html_parser_rules_first_icon_last_preview_exact_rel(self) -> None:
        head = parse_head('<html><head><link rel="shortcut icon" href="no.svg"><link rel="icon" href="first.svg">'
                          '<link rel="icon" href="second.svg"><meta name="x-icue-widget-preview" content="a.png">'
                          '<meta name="x-icue-widget-preview" content=""><meta name="x-icue-widget-preview" content="b.png">'
                          '<meta name="x-icue-widget-group" content="g1"><meta name="x-icue-widget-group" content="g2"></head></html>')
        self.assertEqual((head.icon_href, head.preview, head.group), ("first.svg", "b.png", "g2"))
        empty_href = parse_head('<link rel="icon">')
        self.assertEqual(empty_href.icon_href, "")  # still "declared": iCUE uses the widget folder as the path
        self.assertIsNone(parse_head("<title>x</title>").icon_href)
        entry = load_entry(make_widget(self.root, "e", icon=""))
        self.assertEqual((entry.type_image().source, entry.type_image().exists), (SRC_ICON, False))

    def test_group_name_is_a_js_expression_else_the_widget_name_and_nothing_is_invented(self) -> None:
        clock = load_entry(make_widget(self.root, "c", title="Clock A", group="tr('Clock Face')"))
        plain = load_entry(make_widget(self.root, "p", title="Plain Widget"))
        blank = load_entry(make_widget(self.root, "b", title="Blank Group", group=""))
        self.assertEqual((clock.group_name, clock.group_declared), ("Clock Face", True))
        self.assertEqual((plain.group_name, plain.group_declared, plain.group_expr), ("Plain Widget", False, None))
        self.assertEqual((blank.group_name, blank.group_declared), ("Blank Group", True))  # an empty group falls back to the name

    def test_the_group_image_has_no_default_in_the_widgets_pipeline_but_has_one_in_the_dash_pipeline(self) -> None:
        entry = load_entry(make_widget(self.root, "n"))  # no icon at all
        self.assertEqual(entry.group_image_widgets_pipeline().source, SRC_NONE)
        self.assertEqual(entry.group_icon_dash_pipeline().source, SRC_DEFAULT)
        with_icon = load_entry(make_widget(self.root, "i", icon="i.svg", files={"i.svg": svg("#fff")}))
        self.assertEqual((with_icon.group_image_widgets_pipeline().source, with_icon.group_icon_dash_pipeline().source), (SRC_ICON, SRC_ICON))


class GroupingTests(QtCase):
    def library(self):
        make_widget(self.root, "clock1", title="Clock One", wid="t.clock1", group="'Clock Face'", icon="i.svg", preview="p.png",
                    files={"i.svg": svg("#fff"), "p.png": lambda p: png(p, 128, 56, "#336699")})
        make_widget(self.root, "clock2", title="Clock Two", wid="t.clock2", group="'Clock Face'", icon="i.svg",
                    files={"i.svg": svg("#fff")})
        make_widget(self.root, "solo", title="Solo", wid="t.solo", devices=(("dashboard_lcd", ("sensor-screen",)),))
        make_widget(self.root, "pump", title="Pump Only", wid="t.pump", devices=(("pump_lcd", ()),))
        make_widget(self.root, "open", title="Anywhere", wid="t.open", devices=())  # no restrictions
        broken = make_widget(self.root, "broken", title="Broken")
        (broken / "index.html").unlink()
        (broken / "manifest.json").write_text("{}", encoding="utf-8")
        (broken / "index.html").write_text("", encoding="utf-8")
        return scan_library(self.root)

    def test_widgets_sharing_a_group_get_a_preview_panel_and_a_single_widget_does_not(self) -> None:
        groups, _ = build_groups(self.library(), None)
        by = {g.name: g for g in groups}
        self.assertEqual([w.name for w in by["Clock Face"].widgets], ["Clock One", "Clock Two"])
        self.assertTrue(by["Clock Face"].preview_panel and by["Clock Face"].declared)
        self.assertFalse(by["Solo"].preview_panel)
        self.assertFalse(by["Solo"].declared)  # implicit group named after the widget
        self.assertIn("no preview panel", by["Solo"].preview_reason)
        self.assertEqual(by["Clock Face"].image_widgets.source, SRC_ICON)

    def test_unlisted_widgets_carry_the_reason(self) -> None:
        entries = self.library()
        _, nowhere = build_groups(entries, None)
        self.assertEqual({u.entry.name for u in nowhere}, {"broken"})  # no name in its manifest: the folder name  # no device: only the invalid widget is out
        self.assertIn("invalid widget", nowhere[0].reason)
        groups, gone = build_groups(entries, PUMP)
        self.assertEqual({g.name for g in groups}, {"Pump Only", "Anywhere"})
        self.assertTrue(any("offers ['PumpLCD']" in u.reason for u in gone if u.entry.name == "Solo"))

    def test_sensor_screen_needs_the_touch_setup_notification_of_the_device(self) -> None:
        solo = next(e for e in self.library() if e.name == "Solo")
        self.assertIsNone(support_reason(solo, EDGE))
        self.assertIn("sensor-screen", support_reason(solo, LINK))
        self.assertIn("offers", support_reason(solo, KEYBOARD))
        self.assertIsNone(support_reason(solo, None))
        self.assertTrue(EDGE.touch_setup and not LINK.touch_setup)


class ThumbnailTests(QtCase):
    def entry(self, name="x", **kw):
        return load_entry(make_widget(self.root, name, **kw))

    def test_preview_row_is_138x66_with_a_128x56_icon_that_crops_and_sits_on_layer1(self) -> None:
        wide = self.entry("wide", preview="w.png", files={"w.png": lambda p: png(p, 400, 100, "#ff0000")})
        tile = render_preview_tile(wide.preview_image())
        self.assertEqual((tile.width(), tile.height()), (138, 66))
        self.assertEqual(tile.pixelColor(69, 33).name(), "#ff0000")
        self.assertEqual(tile.pixelColor(15, 15).name(), "#ff0000")  # PreserveAspectCrop: the image fills the rectangle (no letterbox)
        self.assertEqual(tile.pixelColor(122, 50).name(), "#ff0000")
        self.assertEqual(tile.pixelColor(2, 2).alpha(), 0)  # outside the 5 px margin: nothing (the panel colour is not simulated)
        self.assertEqual(tile.pixelColor(5, 5).alpha(), 0)  # the radius-4 corner is clipped

    def test_transparent_image_shows_the_layer1_background(self) -> None:
        clear = self.entry("clear", preview="c.png", files={"c.png": lambda p: png(p, 128, 56, "#ffffff", alpha=0)})
        tile = render_preview_tile(clear.preview_image())
        self.assertEqual(tile.pixelColor(69, 33).name(), PAL["layer1"].name())
        self.assertEqual(PAL["layer1"].name(), "#000000")
        half = self.entry("half", preview="h.png", files={"h.png": lambda p: png(p, 128, 56, "#ffffff", alpha=128)})
        self.assertNotIn(render_preview_tile(half.preview_image()).pixelColor(69, 33).name(), ("#000000", "#ffffff"))

    def test_a_black_svg_is_invisible_on_the_preview_row_but_visible_in_the_type_tile(self) -> None:
        black = self.entry("black", icon="b.svg", files={"b.svg": svg("#000000")})
        white = self.entry("white", icon="w.svg", files={"w.svg": svg("#ffffff")})
        for entry, visible in ((black, False), (white, True)):
            row = render_preview_tile(entry.preview_image())
            self.assertEqual(row.pixelColor(69, 33).name() == "#000000", not visible, entry.name)
            tile = render_type_tile(entry.type_image())
            self.assertEqual(tile.pixelColor(35, 20).name(), PAL["layer8"].name(), entry.name)  # recoloured: the SVG colour is gone
        self.assertTrue(analyze(black.preview_image()).dark_on_layer1)
        self.assertFalse(analyze(white.preview_image()).dark_on_layer1)

    def test_type_tile_keeps_only_the_alpha_of_the_image(self) -> None:
        opaque = self.entry("op", icon="o.png", files={"o.png": lambda p: png(p, 64, 64, "#00ff00")})
        tile = render_type_tile(opaque.type_image())
        self.assertEqual((tile.width(), tile.height()), (70, 40))
        self.assertEqual(tile.pixelColor(35, 20).name(), PAL["layer8"].name())  # an opaque square becomes a solid layer8 square
        self.assertEqual(tile.pixelColor(35 + 7, 20 + 7).name(), PAL["layer8"].name())
        self.assertEqual(tile.pixelColor(35 + 12, 20).name(), PAL["layer5"].name())  # beyond 16 x 16: the tile background
        selected = render_type_tile(opaque.type_image(), selected=True)
        self.assertEqual(selected.pixelColor(3, 3).name(), PAL["layer9"].name())  # selected: layer9 tile, layer1 overlay
        self.assertEqual(selected.pixelColor(35, 20).name(), PAL["layer1"].name())
        ring = self.entry("ring", icon="r.svg", files={"r.svg": svg("#ff0000")})
        self.assertEqual(render_type_tile(ring.type_image()).pixelColor(35, 20).name(), PAL["layer8"].name())
        self.assertEqual(render_type_tile(ring.type_image()).pixelColor(35 + 7, 20 - 7).name(), PAL["layer5"].name())  # transparent corner
        self.assertGreater(analyze(opaque.type_image()).overlay_coverage, 0.95)

    def test_png_svg_and_the_default_icon_render_and_a_missing_or_broken_file_does_not(self) -> None:
        default = self.entry("d")
        self.assertEqual(default.type_image().source, SRC_DEFAULT)
        self.assertTrue(analyze(default.type_image()).readable)
        self.assertLess(render_type_tile(default.type_image()).pixelColor(0, 0).alpha(), 64)  # the radius-4 corner of the tile
        broken = self.entry("broken", icon="x.svg", preview="x.png", files={"x.svg": "<not svg", "x.png": "not a png"})
        self.assertFalse(analyze(broken.type_image()).readable)
        self.assertFalse(analyze(broken.preview_image()).readable)
        tile = render_preview_tile(broken.preview_image())  # still a black layer1 rectangle, like an Image that failed to load
        self.assertEqual(tile.pixelColor(69, 33).name(), "#000000")

    def test_current_widget_gets_the_layer9_border(self) -> None:
        entry = self.entry("c")
        self.assertEqual(render_preview_tile(entry.preview_image(), current=True).pixelColor(69, 1).name(), PAL["layer9"].name())
        self.assertEqual(render_preview_tile(entry.preview_image(), current=False).pixelColor(69, 1).alpha(), 0)


@unittest.skipUnless((ICUE / PALETTE_SOURCE).is_file(), "iCUE source tree not present")
class ThemeSourceTests(unittest.TestCase):
    def test_palette_file_matches_cuepalette_cpp(self) -> None:
        doc = json.loads(PALETTE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(doc["sha256"], hashlib.sha256((ICUE / PALETTE_SOURCE).read_bytes()).hexdigest(), "palette.json is stale: regenerate")
        self.assertEqual({k: doc["colors"][k] for k in ("layer1", "layer5", "layer8", "layer9")},
                         {"layer1": "#000000", "layer5": "#2b2b2b", "layer8": "#c0c0c0", "layer9": "#ffffff"})
        self.assertEqual(doc["default_icon"]["url"], "qrc:///cue-icons/4.0/screeneffects/web-url.svg")
        icon = ICUE / doc["default_icon"]["source"]
        self.assertEqual(hashlib.sha256(icon.read_bytes()).hexdigest(), hashlib.sha256(DEFAULT_ICON_FILE.read_bytes()).hexdigest())


class LibraryUiTests(PageTestCase):
    """The library panel, the main window and the settings of the widgets that are opened one after the other."""

    def setUp(self) -> None:
        super().setUp()
        self.lib = Path(self._tmp.name) / "lib"
        files = {"i.svg": svg("#ffffff"), "p.png": lambda p: png(p, 128, 56, "#336699")}
        self.a = make_widget(self.lib, "clock_a", title="Clock A", wid="t.a", group="'Clock Face'", icon="i.svg", preview="p.png", files=files)
        self.b = make_widget(self.lib, "clock_b", title="Clock B", wid="t.b", group="'Clock Face'", icon="i.svg", files=files)
        self.solo = make_widget(self.lib, "solo", title="Solo", wid="t.solo", icon="i.svg", files=files,
                                devices=(("dashboard_lcd", ("sensor-screen",)),))
        self.pump_widget = make_widget(self.lib, "pump", title="Pump Only", wid="t.pump", devices=(("pump_lcd", ()),))
        self.lost = make_widget(self.lib, "lost", title="Lost Preview", wid="t.lost", icon="i.svg", preview="gone.png", files=files)
        self.folder = self.window.widgets_folder
        self.assertEqual(self.window.set_widgets_folder(self.lib), 5)
        self.settled = 0
        self.window.loadSettled.connect(lambda ok: setattr(self, "settled", self.settled + 1))

    def group_names(self) -> list[str]:
        groups, gone = build_groups(self.folder.entries, self.window.device)
        return [g.name for g in groups] + ([f"Not listed ({len(gone)})"] if gone else [])

    def open_and_wait(self, folder: Path) -> None:
        count = self.settled
        shown = self.window.pick_instance(folder)
        reused = shown is not None and shown.id in self.window.runtimes  # A1: a running instance is selected, not reloaded
        self.assertTrue(self.window.open_folder(folder))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED" and (reused or self.settled > count)))
        self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True))
        self.pump(0.2)

    def test_the_groups_follow_the_selected_device_and_explain_what_is_missing(self) -> None:
        self.assertEqual(self.group_names(), ["Clock Face", "Lost Preview", "Pump Only", "Solo"])  # no device: every valid widget
        self.window.select_device("dash_accessory_lcd")
        self.assertEqual(self.folder.device.id, "dash_accessory_lcd")
        self.assertEqual(self.group_names(), ["Clock Face", "Lost Preview", "Solo", "Not listed (1)"])  # EDGE: "Pump Only" supports PumpLCD only
        self.assertIn("offers ['DashboardLCD']", self.report(self.pump_widget))
        self.window.select_device("link_generic_lcd")
        listed = {w.name for g in build_groups(self.folder.entries, self.window.device)[0] for w in g.widgets}
        self.assertNotIn("Solo", listed)  # sensor-screen needs the touch setup of XENEON EDGE
        self.assertIn("sensor-screen", self.report(self.solo))
        self.assertIn("NOT in a panel for iCUE LINK GENERIC LCD", self.report(self.solo))
        self.window.select_device(None)
        self.assertIn("Solo", self.group_names())

    def report(self, folder: Path) -> str:
        return inspect(folder, self.folder.entries, self.window.device).report

    def test_the_report_names_the_image_the_source_and_the_reason(self) -> None:
        text = self.report(self.a)
        self.assertIn("widget preview: x-icue-widget-preview", text)
        self.assertIn("declared: p.png", text)
        self.assertIn("(exists)", text)
        self.assertIn("type image: link rel=icon", text)
        self.assertIn("required by the validator, NOT used", text)
        self.assertIn("preview panel", text)
        self.assertIn("no x-icue-widget-preview: fall back to the type image", self.report(self.b))
        lost = inspect(self.lost, self.folder.entries)
        self.assertIn("MISSING", lost.report)
        self.assertIn("does NOT fall back", lost.report)
        self.assertEqual(summary(lost.findings)[0], "error")
        self.assertIn("Preview file missing: gone.png", [f.text for f in lost.findings][0])

    def test_open_widget_fills_the_icue_tab_with_the_group_of_the_library(self) -> None:
        info = self.window.icue_info_panel
        self.assertIsNone(info.inspection)
        self.assertTrue(self.window.open_folder(self.a))  # Open Widget path, not the library
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.assertEqual(info.inspection.entry.type_id, "t.a")
        self.assertTrue(info.inspection.in_library)
        self.assertEqual(len(info.inspection.group.widgets), 2)  # Clock A + Clock B share 'Clock Face'
        self.assertTrue(info.inspection.group.preview_panel)
        self.assertIn("widget preview: x-icue-widget-preview", info.report.toPlainText())
        self.assertFalse(info.preview_tile.pixmap().isNull())
        self.assertGreater(info.type_tile.pixmap().width(), 0)
        alone = inspect(self.a)  # no library: only this widget is known, and the panel says so
        self.assertFalse(alone.in_library)
        self.assertEqual(len(alone.group.widgets), 1)
        self.assertTrue(any("only this widget is known" in f.text for f in alone.findings))

    def test_the_widgets_folder_is_detected_from_the_first_widget_and_remembered(self) -> None:
        self.assertFalse(hasattr(self.window, "library_button") or hasattr(self.window, "library_dock"))  # no Widget Library UI any more
        self.window.set_widgets_folder(None)
        self.assertIsNone(self.folder.root)
        self.open_and_wait(self.a)
        self.assertEqual(self.folder.root, self.lib.resolve())  # the nearest parent that holds several widgets
        self.assertEqual(len(self.window.icue_info_panel.inspection.group.widgets), 2)
        self.assertIn(str(self.lib.resolve()), self.window.icue_info_panel.folder_label.text())
        path = Path(self._tmp.name) / "workspace.json"
        self.window.save_workspace(path)
        self.window.set_widgets_folder(None)
        self.window.restore_workspace(path)
        self.assertEqual(self.folder.root, self.lib.resolve())  # remembered: not asked again
        self.assertEqual(len(self.folder.entries), 5)

    def test_a_widget_without_sibling_widgets_sets_no_widgets_folder(self) -> None:
        self.window.set_widgets_folder(None)
        own = tempfile.TemporaryDirectory()  # its own temp folder: nothing next to it but this one widget
        self.addCleanup(own.cleanup)
        alone = make_widget(Path(own.name), "only", title="Only", wid="t.only")
        self.assertIsNone(detect_root(alone))  # its parent holds one widget; the temp folder above is never scanned
        self.assertTrue(self.window.open_folder(alone))
        self.assertIsNone(self.folder.root)
        self.assertFalse(self.window.icue_info_panel.inspection.in_library)

    def test_opening_a_widget_loads_it_in_the_renderer_and_settings_editor(self) -> None:
        self.assertTrue(self.window.open_folder(self.b))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.assertEqual(self.window.current.folder, self.b)
        self.assertIn("level", self.window.settings_panel.editors)

    def test_choosing_widgets_one_after_the_other_loses_no_settings(self) -> None:
        self.window.resize(1500, 900)
        self.window.show()
        self.open_and_wait(self.a)
        inst_a = self.window.bridge.instance
        self.window.settings_panel.editors["level"].control.setValue(9)
        self.assertTrue(self.wait(lambda: self.js("level") == 9))
        self.window.select_device("dash_accessory_lcd", "SizeL", "portrait")
        self.window.viewport_frame.set_scale(NATIVE)
        bridge, settled = self.window.bridge, self.settled
        self.assertTrue(self.window.open_folder(self.a))  # the shown widget again: nothing happens
        self.pump(0.3)
        self.assertEqual((self.window.bridge is bridge, self.settled, self.window.reload_count), (True, settled, 0))
        self.open_and_wait(self.b)
        inst_b = self.window.bridge.instance
        self.assertIsNot(inst_a, inst_b)
        self.assertEqual(self.js("level"), 5)  # B starts from its own default
        self.window.settings_panel.editors["level"].control.setValue(2)
        self.assertTrue(self.wait(lambda: self.js("level") == 2))
        self.open_and_wait(self.a)
        self.assertIs(self.window.bridge.instance, inst_a)
        self.assertEqual((self.js("level"), inst_a.data["level"], inst_b.data["level"]), (9, 9.0, 2.0))
        self.assertEqual(self.window.settings_panel.editors["level"].control.value(), 9)
        self.assertEqual(len(self.window.store), 2)
        self.assertEqual((self.window.device.id, self.window.device_size, self.window.device_orientation), ("dash_accessory_lcd", "SizeL", "portrait"))
        self.assertTrue(self.wait(lambda: self.jsj("[innerWidth, innerHeight]") == list(self.window.viewport.size)))

    def test_live_reload_still_follows_the_source_after_library_switches(self) -> None:
        self.open_and_wait(self.a)
        self.window.settings_panel.editors["level"].control.setValue(7)
        self.open_and_wait(self.b)
        self.open_and_wait(self.a)
        index = self.a / "index.html"
        index.write_text(index.read_text(encoding="utf-8").replace('data-default="5"', 'data-default="6"'), encoding="utf-8")
        count = self.window.reload_count
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: self.window.reload_count == count + 1 and not self.window._reloading))
        self.pump(0.3)
        self.assertEqual(self.js("level"), 7)  # the user's value is kept, as in any live reload
        self.assertIn("level", self.window.bridge.instance.edited)


@unittest.skipUnless(BASKETBALL.is_dir() and SMOKECLOCK.is_dir() and RSS.is_dir(), "WidgetsXE widgets not present")
class RealLibraryTests(PageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.folder = self.window.widgets_folder
        self.count = self.window.set_widgets_folder(WIDGETS)

    def entry(self, name: str):
        return next(e for e in self.folder.entries if e.name == name)

    def runtime_errors(self, folder: Path | None = None) -> list[str]:
        """Runtime-class console errors of the widget in `folder` (a late error of the page that was just left is not counted)."""
        prefix = (folder or self.window.current.folder).resolve().as_uri().lower()
        return sorted({e.message[:140] for e in self.window.console.matching("error", widget_only=True)
                       if classify_console_error(e.message) == "runtime" and (not e.source or e.source.lower().startswith(prefix))})

    def test_metadata_of_the_real_widgets(self) -> None:
        self.assertGreaterEqual(self.count, 25)
        basketball, smoke = self.entry("Basketball Scores"), self.entry("Smoke Clock")
        self.assertEqual((basketball.icon_href, basketball.preview_expr, basketball.group_declared),
                         ("resources/basketball.svg", "resources/preview.png", False))
        self.assertEqual((basketball.preview_image().source, basketball.preview_image().exists, basketball.type_image().source,
                          basketball.type_image().exists, basketball.group_name), (SRC_PREVIEW, True, SRC_ICON, True, "Basketball Scores"))
        self.assertEqual(analyze(basketball.preview_image()).natural, (128, 56))
        self.assertEqual((smoke.group_name, smoke.preview_image().source, smoke.preview_image().exists), ("Clock Face", SRC_PREVIEW, True))
        self.assertTrue(basketball.manifest_preview_icon)  # present in the manifest, not used for any thumbnail
        self.assertEqual(analyze(smoke.preview_image()).natural, (128, 56))

    def test_clock_face_group_and_basketball_group(self) -> None:
        groups, unlisted = build_groups(self.folder.entries, EDGE)
        by = {g.name: g for g in groups}
        # every valid widget is listed for XENEON EDGE unless its manifest names other device types (e.g. keyboard/pump only)
        self.assertTrue(all(u.reason.startswith("not supported by the device") and "offers ['DashboardLCD']" in u.reason for u in unlisted),
                        [u.reason for u in unlisted])
        self.assertNotIn("Basketball Scores", {u.entry.name for u in unlisted})
        self.assertTrue({"Smoke Clock", "Robex Tourbillon", "MoonPhase Clock"} <= {w.name for w in by["Clock Face"].widgets})
        self.assertTrue(by["Clock Face"].preview_panel)
        self.assertEqual([w.name for w in by["Basketball Scores"].widgets], ["Basketball Scores"])
        self.assertFalse(by["Basketball Scores"].preview_panel)
        self.assertIn("no preview panel", by["Basketball Scores"].preview_reason)
        links, gone = build_groups(self.folder.entries, LINK)
        self.assertIn("Basketball Scores", {u.entry.name for u in gone})
        self.assertTrue(all("sensor-screen" in u.reason or "offers" in u.reason for u in gone))

    def test_a_dark_svg_alone_does_not_make_the_ticket(self) -> None:
        """Basketball Scores' SVG icon is black on transparent: invisible on the black AssetIcon background, visible in the 16x16 type
        tile. The widget now declares x-icue-widget-preview (a 128x56 PNG), so the preview row no longer draws the SVG at all; and the
        preview-panel row exists only for a group with more than one widget, while Basketball Scores is alone in its group."""
        entry = self.entry("Basketball Scores")
        self.assertTrue(analyze(entry.type_image()).dark_on_layer1)
        self.assertEqual(entry.preview_image().source, SRC_PREVIEW)
        self.assertFalse(analyze(entry.preview_image()).dark_on_layer1)  # the PNG preview is not the dark SVG
        row = render_preview_tile(entry.type_image())  # what the row would show if the icon were the preview (the old state)
        self.assertEqual(row.pixelColor(69, 33).name(), "#000000")
        tile = render_type_tile(entry.type_image())
        self.assertTrue(any(tile.pixelColor(x, y).name() == PAL["layer8"].name() for x in range(27, 43) for y in range(12, 28)))
        groups, _ = build_groups(self.folder.entries, EDGE)
        group = next(g for g in groups if entry in g.widgets)
        self.assertFalse(group.preview_panel)  # so this redraw is not the panel the ticket shows: which panel and condition is still open

    def test_every_declared_image_is_reported_consistently(self) -> None:
        for entry in self.folder.entries:
            for resolved in (entry.preview_image(), entry.type_image()):
                self.assertEqual(resolved.usable_path is None, (not resolved.exists) and resolved.source != SRC_DEFAULT, entry.name)
                self.assertTrue(render_preview_tile(resolved).size().width() == 138)

    def test_basketball_smokeclock_and_rss_through_the_library_keep_settings_and_stay_clean(self) -> None:
        self.window.resize(1500, 900)
        results = {}
        for name, folder in (("Basketball Scores", BASKETBALL), ("Smoke Clock", SMOKECLOCK), ("RSS Feed Reader", RSS)):
            self.assertTrue(self.window.open_folder(folder))
            self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED", 30))
            self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True, 30))
            self.pump(1.0)
            results[name] = (self.window.bridge.instance, list(self.window.settings_panel.editors), self.runtime_errors(folder))
            self.assertTrue(results[name][1], name)
        key = next(k for k, e in self.window.settings_panel.editors.items() if e.supported and e.param.data_type == "textfield")
        editor = self.window.settings_panel.editors[key]
        reloads = self.window.reload_count
        editor.control.setText("kept")
        editor.control.editingFinished.emit()
        self.pump(0.3)
        rss_instance = self.window.bridge.instance
        self.assertEqual(rss_instance.data[key], "kept")
        self.assertTrue(self.window.open_folder(BASKETBALL))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED" and self.window.bridge.instance is results["Basketball Scores"][0], 30))
        self.assertTrue(self.window.open_folder(RSS))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED" and self.window.bridge.instance is rss_instance, 30))
        self.pump(1.0)
        self.assertEqual(rss_instance.data[key], "kept")
        self.assertEqual(len(self.window.store), 3)
        # the widgets call the network on their own (Basketball Scores logs "Failed to fetch scores"): the errors of the second load must be
        # the ones of the first load of the same widget, nothing new
        self.assertEqual(self.runtime_errors(RSS), results["RSS Feed Reader"][2])
        self.assertTrue(self.window.open_folder(BASKETBALL))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED", 30))
        self.pump(1.5)
        self.assertEqual(self.runtime_errors(BASKETBALL), results["Basketball Scores"][2])
        self.assertEqual(reloads, self.window.reload_count)  # no live reload was caused by choosing widgets


if __name__ == "__main__":
    unittest.main()