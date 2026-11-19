r"""Device profiles (D5) and device viewport (D6) tests.

Pure tests read the real iCUE manifests (skipped when the iCUE source, widgetdevtool.paths.ICUE_DIR, is absent). Page tests measure the viewport through
window.innerWidth / innerHeight in a real QtWebEngine page. Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)

from test_runtime_bridge import PageTestCase, write_widget  # noqa: E402
from test_settings_panel import BASKETBALL, RSS, SMOKECLOCK, classify_console_error  # noqa: E402
from widgetdevtool.device_profiles import (  # noqa: E402
    KIND_DASHBOARD, KIND_KEYBOARD, KIND_PUMP, LANDSCAPE, PORTRAIT, PROFILES_FILE, DeviceProfile, generate_profiles,
    import_manifest, load_profiles)
from widgetdevtool.viewport import FIT, MODE_NOMINAL, MODE_PHYSICAL, NATIVE, compute_viewport, exact_zooms, fit_scale  # noqa: E402
from widgetdevtool.paths import ICUE_DIR  # noqa: E402

ICUE = ICUE_DIR
HAVE_ICUE = (ICUE / "Application" / "src" / "mod" / "devices" / "manifests").is_dir()
PROFILES = load_profiles()
BY_ID = {p.id: p for p in PROFILES}
DASHBOARDS = [p for p in PROFILES if p.kind == KIND_DASHBOARD]
FIXED = [p for p in PROFILES if p.kind != KIND_DASHBOARD]


def manifest(name: str, caps: dict | None = None, html=("DashboardLCD",), extra: dict | None = None) -> str:
    common = {"UserModelName": name, "SupportedHtmlWidgetDeviceTypes": list(html), **(extra or {})}
    if caps:
        common["WidgetScreenCapabilities"] = caps
    return json.dumps({"common": common})


def caps(mimic=(100, 50), physical=(400, 200)) -> dict:
    wh = lambda w, h: {"width": w, "height": h}  # noqa: E731
    return {"mimicScreenSize": wh(*mimic), "physicalScreenSize": wh(*physical),
            "widgetSizeMaps": {"landscapeRelativeSizes": {"SizeS": wh(0.5, 0.5), "SizeXL": wh(1.0, 1.0)},
                               "portraitRelativeSizes": {"SizeS": wh(0.5, 0.5), "SizeXL": wh(1.0, 1.0)}},
            "landscapeGrid": [{"x": 0, "y": 0}], "portraitGrid": [{"x": 0, "y": 0}]}


class ProfileFromManifestTests(unittest.TestCase):
    """Nothing is read by device name: any manifest with the same structure becomes a profile."""

    def write(self, text: str, name: str = "new_device_manifest.cuemsf") -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_an_unknown_device_is_imported_from_its_structure_only(self) -> None:
        profile, reason = import_manifest(self.write(manifest("Totally New Device", caps())))
        self.assertEqual(reason, "")
        self.assertEqual((profile.kind, profile.id, profile.mimic, profile.physical), (KIND_DASHBOARD, "new_device", (100, 50), (400, 200)))
        self.assertEqual(profile.supported_sizes(), ["SizeS", "SizeXL"])
        self.assertEqual(profile.widget_size("SizeS", LANDSCAPE), (50.0, 25.0))
        self.assertEqual(profile.widget_size("SizeS", PORTRAIT), (25.0, 50.0))  # portrait: width x mimic.height, height x mimic.width

    def test_a_size_in_one_orientation_only_is_not_offered(self) -> None:
        c = caps()
        del c["widgetSizeMaps"]["portraitRelativeSizes"]["SizeXL"]
        profile, _ = import_manifest(self.write(manifest("X", c)))
        self.assertEqual(profile.supported_sizes(), ["SizeS"])
        self.assertTrue(any("SizeXL" in n for n in profile.notes))
        with self.assertRaises(ValueError):
            profile.widget_size("SizeXL", LANDSCAPE)

    def test_fixed_size_sources_follow_the_widget_size_watcher_order(self) -> None:
        kb = {"ScreenModuleCapabilities": {"screenGeometry": {"width": 200, "height": 100}},
              "WidgetsRects": {"HardwareWidgetSetup": {"width": 9, "height": 9, "x": 0, "y": 0}}}
        profile, _ = import_manifest(self.write(manifest("K", html=("KeyboardLCD",), extra=kb)))
        self.assertEqual((profile.kind, profile.fixed), (KIND_KEYBOARD, (200, 100)))  # keyboard before pump
        pump = {"WidgetsRects": {"HardwareWidgetSetup": {"width": 300, "height": 300, "x": 0, "y": 0}}}
        profile, _ = import_manifest(self.write(manifest("P", html=("PumpLCD",), extra=pump)))
        self.assertEqual((profile.kind, profile.fixed, profile.orientations()), (KIND_PUMP, (300, 300), []))

    def test_a_widget_device_without_a_size_source_is_refused_not_guessed(self) -> None:
        profile, reason = import_manifest(self.write(manifest("Y", html=("PumpLCD",))))
        self.assertIsNone(profile)
        self.assertIn("LCDScreenSize", reason)
        self.assertEqual(import_manifest(self.write(json.dumps({"common": {"UserModelName": "Mouse"}})))[1], "not a widget device")


@unittest.skipUnless(HAVE_ICUE, "iCUE source tree not present")
class RealManifestTests(unittest.TestCase):
    def test_xeneon_edge_values_from_the_real_manifest(self) -> None:
        edge = BY_ID["dash_accessory_lcd"]
        self.assertEqual((edge.name, edge.mimic, edge.physical), ("XENEON EDGE", (960, 270), (2560, 720)))
        self.assertEqual(edge.supported_sizes(), ["SizeS", "SizeM", "SizeL", "SizeXL"])
        self.assertEqual(edge.slot_count(), 6)
        got = {s: compute_viewport(edge, s, LANDSCAPE).size for s in edge.supported_sizes()}
        self.assertEqual(got, {"SizeS": (315, 129), "SizeM": (315, 261), "SizeL": (633, 261), "SizeXL": (951, 261)})
        self.assertEqual(compute_viewport(edge, "SizeM", PORTRAIT).size, (261, 315))  # swapped, not rescaled

    def test_xeneon_edge_portrait_sizes_follow_the_portrait_map_not_a_swapped_landscape_size(self) -> None:
        """WidgetSizeUtils::size(): portraitRelativeSizes x (mimic.height, mimic.width). On the 270 x 960 portrait screen an S widget is a
        full-width row of 1/6 height (261 x 156, wider than tall); it is NOT the landscape S turned by 90 degrees (129 x 315)."""
        edge = BY_ID["dash_accessory_lcd"]
        caps = json.loads((ICUE / edge.source).read_text(encoding="utf-8-sig"))["common"]["WidgetScreenCapabilities"]
        mw, mh = caps["mimicScreenSize"]["width"], caps["mimicScreenSize"]["height"]
        for orientation, key, base in ((LANDSCAPE, "landscapeRelativeSizes", (mw, mh)), (PORTRAIT, "portraitRelativeSizes", (mh, mw))):
            for size, rel in caps["widgetSizeMaps"][key].items():
                with self.subTest(size=size, orientation=orientation):
                    self.assertEqual(compute_viewport(edge, size, orientation).size, (int(rel["width"] * base[0]), int(rel["height"] * base[1])))
        portrait = {s: compute_viewport(edge, s, PORTRAIT).size for s in edge.supported_sizes()}
        self.assertEqual(portrait, {"SizeS": (261, 156), "SizeM": (261, 315), "SizeL": (261, 633), "SizeXL": (261, 951)})
        self.assertNotEqual(portrait["SizeS"], compute_viewport(edge, "SizeS", LANDSCAPE).size[::-1])
        self.assertEqual(edge.screen_size(PORTRAIT), (720, 2560))  # the whole portrait screen (physical), not a widget tile

    def test_each_device_offers_only_the_sizes_its_manifest_has(self) -> None:
        self.assertEqual(BY_ID["link_generic_lcd"].supported_sizes(), ["SizeS", "SizeM", "SizeL"])
        self.assertEqual(BY_ID["taiga_lcd"].supported_sizes(), ["SizeS", "SizeM"])
        with self.assertRaises(ValueError):
            compute_viewport(BY_ID["taiga_lcd"], "SizeXL", LANDSCAPE)
        link, naut = BY_ID["link_generic_lcd"], BY_ID["taiga_lcd"]
        self.assertEqual(compute_viewport(link, "SizeS", LANDSCAPE).size, (234, 129))
        self.assertEqual(compute_viewport(link, "SizeL", PORTRAIT).size, (261, 471))
        self.assertEqual(compute_viewport(naut, "SizeM", LANDSCAPE).size, (315, 233))  # 315.7 x 233.7, truncated like screenSizeText

    def test_mimic_and_physical_are_different_things(self) -> None:
        edge = BY_ID["dash_accessory_lcd"]
        nominal = compute_viewport(edge, "SizeM", LANDSCAPE, MODE_NOMINAL)
        physical = compute_viewport(edge, "SizeM", LANDSCAPE, MODE_PHYSICAL)
        self.assertEqual((nominal.size, nominal.experimental), ((315, 261), False))
        self.assertEqual((physical.size, physical.experimental), ((840, 696), True))
        self.assertEqual(edge.screen_size(PORTRAIT), (720, 2560))

    def test_keyboard_and_pump_families_have_one_fixed_size_and_no_orientation(self) -> None:
        self.assertEqual(compute_viewport(BY_ID["vanguard_96"], None, None).size, (248, 170))
        self.assertEqual(compute_viewport(BY_ID["cooler_pump_lcd"], None, None).size, (472, 472))
        self.assertEqual(BY_ID["link_cooler_pump_lcd_screen"].fixed, (472, 472))  # HardwareWidgetSetup, not WidgetSetup (480)
        for p in FIXED:
            self.assertEqual((p.orientations(), p.supported_sizes()), ([], []), p.id)
            with self.assertRaises(ValueError):
                compute_viewport(p, None, None, MODE_PHYSICAL)

    def test_families_requested_are_all_present_and_test_devices_are_not(self) -> None:
        names = {p.name for p in PROFILES}
        for needed in ("XENEON EDGE", "iCUE LINK GENERIC LCD", "NAUTILUS II LCD", "VANGUARD 96", "Elite LCD"):
            self.assertIn(needed, names)
        self.assertEqual({p.kind for p in PROFILES}, {KIND_DASHBOARD, KIND_KEYBOARD, KIND_PUMP})
        self.assertFalse([p.id for p in PROFILES if p.id.startswith(("dummy_", "fake_", "demo_"))])

    def test_the_shipped_profiles_file_matches_the_manifests_it_was_generated_from(self) -> None:
        fresh = generate_profiles(ICUE)
        shipped = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
        self.assertEqual(shipped["profiles"], fresh["profiles"], "device_profiles.json is stale: run the generate command")
        self.assertEqual(shipped["skipped"], fresh["skipped"])
        self.assertEqual({s["id"] for s in shipped["skipped"]}, {"link_cooler_pump_lcd", "xd_elite_lcd"})

    def test_profiles_survive_a_round_trip(self) -> None:
        for p in PROFILES:
            self.assertEqual(DeviceProfile.from_dict(json.loads(json.dumps(p.to_dict()))), p)


class FitScaleTests(unittest.TestCase):
    def test_fit_only_ever_gives_a_zoom_with_whole_pixels(self) -> None:
        s = fit_scale(315, 261, 800, 200)  # raw 0.766; gcd 9 -> exact zooms are multiples of 1/9: the largest one below it
        self.assertTrue(math.isclose(315 * s, round(315 * s)) and math.isclose(261 * s, round(261 * s)), s)
        self.assertAlmostEqual(s, 6 / 9)
        self.assertEqual(fit_scale(315, 261, 5000, 5000), 1.0)
        self.assertGreaterEqual(fit_scale(2560, 720, 100, 100), 0.25)

    def test_fit_is_exact_for_every_viewport_of_every_profile_in_every_area(self) -> None:
        count = 0
        for profile in PROFILES:
            for size in profile.supported_sizes() or [None]:
                for orientation in profile.orientations() or [None]:
                    viewport = compute_viewport(profile, size, orientation)
                    for area in ((300, 200), (420, 300), (617, 266), (1300, 900), (100, 80)):
                        z = fit_scale(viewport.width, viewport.height, *area)
                        self.assertTrue(z == 1.0 or 0.25 <= z < 1.0, (profile.id, size, orientation, area, z))
                        for side in (viewport.width, viewport.height):
                            self.assertTrue(math.isclose(side * z, round(side * z), abs_tol=1e-9), (profile.id, size, orientation, area, z))
                        count += 1
        self.assertGreaterEqual(count, 150)
        self.assertEqual(fit_scale(261, 951, 300, 200), 1 / 3)  # XENEON EDGE XL portrait: the old 0.568 gave 260.64 px

    def test_exact_zooms_are_the_ones_with_whole_pixels(self) -> None:
        self.assertEqual(exact_zooms(248, 170), [1.0, 0.5])
        self.assertEqual(exact_zooms(315, 233), [1.0])  # gcd 1: no smaller exact preview exists
        self.assertEqual(len(exact_zooms(472, 472)), 472 - 117)  # every k/472 from 0.25 up
        for w, h in ((315, 261), (951, 261), (472, 472), (248, 170)):
            for z in exact_zooms(w, h):
                self.assertTrue(math.isclose(w * z, round(w * z), abs_tol=1e-9) and math.isclose(h * z, round(h * z), abs_tol=1e-9), (w, h, z))

RESP_HTML = """<!doctype html><html><head><title>Resp</title>
<meta name="x-icue-property" content="label" data-type="textfield" data-default="'hello'">
<meta name="x-icue-property" content="level" data-type="slider" data-default="5" data-min="0" data-max="10" data-step="1">
<style>html,body{margin:0;padding:0;overflow:hidden}#box{width:100vw;height:100vh;background:#223}
#wide{position:absolute;left:0;top:0;width:400px;height:20px;background:#a33}</style></head>
<body><div id="box"></div><div id="wide"></div>
<script>window.__resizes = 0; window.addEventListener('resize', function () { window.__resizes++; });
icueEvents = { onICUEInitialized: function () {}, onDataUpdated: function () { window.__updates = (window.__updates || 0) + 1; } };
</script></body></html>"""

SCROLL_HTML = """<!doctype html><html><head><title>Scroll</title></head><body style="margin:0">
<div style="width:400px;height:50px;background:#363"></div>
<script>icueEvents = { onICUEInitialized: function () {}, onDataUpdated: function () {} };</script></body></html>"""


class ViewportPageTests(PageTestCase):
    """The page really measures the viewport (window.innerWidth/innerHeight) for every choice the profiles offer."""

    def setUp(self) -> None:
        super().setUp()
        self.window.resize(1500, 900)
        self.window.show()
        self.settled = 0
        self.window.loadSettled.connect(lambda ok: setattr(self, "settled", self.settled + 1))

    def start(self, html: str = RESP_HTML, name: str = "w") -> None:
        self.assertTrue(self.window.load_folder(write_widget(Path(self._tmp.name) / name, html=html)))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True))
        self.pump(0.3)

    def measured(self) -> list:
        return self.jsj("[window.innerWidth, window.innerHeight]")

    def settle(self, expected, what: str = "") -> None:
        self.assertTrue(self.wait(lambda: self.measured() == list(expected), 10), f"{what}: page measures {self.measured()}, expected {list(expected)}")

    def choose(self, profile, size=None, orientation=None, mode=MODE_NOMINAL, scale=NATIVE):
        self.window.viewport_frame.set_scale(scale)
        viewport = self.window.select_device(profile.id, size, orientation, mode)
        self.window.viewport_frame.set_scale(scale)
        return viewport

    def test_every_supported_size_and_orientation_gives_the_exact_viewport(self) -> None:
        self.start()
        count = 0
        for profile in PROFILES:
            sizes = profile.supported_sizes() or [None]
            for size in sizes:
                for orientation in profile.orientations() or [None]:
                    with self.subTest(device=profile.id, size=size, orientation=orientation):
                        viewport = self.choose(profile, size, orientation)
                        self.settle(viewport.size, profile.id)
                        count += 1
        self.assertEqual(count, sum(max(1, len(p.supported_sizes())) * max(1, len(p.orientations())) for p in PROFILES))
        self.assertGreaterEqual(count, 18 + 12)  # 9 sizes x 2 orientations + 12 fixed devices

    def test_physical_mode_measures_physical_pixels_and_is_marked_experimental(self) -> None:
        self.start()
        for profile in DASHBOARDS:
            for size in profile.supported_sizes():
                for orientation in (LANDSCAPE, PORTRAIT):
                    with self.subTest(device=profile.id, size=size, orientation=orientation):
                        viewport = self.choose(profile, size, orientation, MODE_PHYSICAL)
                        self.assertTrue(viewport.experimental)
                        self.settle(viewport.size, profile.id)
        self.assertIn("EXPERIMENTAL", self.window.device_info.text())

    def test_the_preview_scale_does_not_change_what_the_page_measures(self) -> None:
        self.start()
        self.window.viewport_frame.setFixedSize(300, 200)  # smaller than any viewport tested: the EXPERIMENTAL zoom must zoom out
        self.pump(0.2)
        pump, keyboard, edge = BY_ID["cooler_pump_lcd"], BY_ID["vanguard_96"], BY_ID["dash_accessory_lcd"]
        for profile, size, scale in ((pump, None, 0.5), (keyboard, None, 0.5), (edge, "SizeM", FIT), (edge, "SizeM", 5 / 9)):
            with self.subTest(device=profile.id, scale=scale):
                viewport = self.choose(profile, size, LANDSCAPE if size else None, scale=scale)
                self.settle(viewport.size, f"{profile.id} scale {scale}")
                zoom = self.window.viewport_frame.zoom
                self.assertLess(zoom, 1.0)
                geometry = self.window.view.geometry()
                self.assertEqual((geometry.width(), geometry.height()), (round(viewport.width * zoom), round(viewport.height * zoom)))
                ratio = self.js("window.devicePixelRatio")
                self.assertAlmostEqual(ratio / zoom, round(ratio / zoom), places=1)  # the documented side effect: dpr scales with the zoom

    def test_the_scale_choices_are_the_exact_ones(self) -> None:
        self.start()
        w = self.window
        w.select_device("vanguard_96")
        self.assertEqual([w.scale_combo.itemText(i) for i in range(w.scale_combo.count())], ["Native 1:1", "Visual Fit (Experimental)", "50% zoom (Experimental)"])
        w.select_device("taiga_lcd", "SizeM", LANDSCAPE)
        self.assertEqual([w.scale_combo.itemText(i) for i in range(w.scale_combo.count())], ["Native 1:1", "Visual Fit (Experimental)"])
        w.select_device(None)
        self.assertFalse(w.scale_combo.isEnabled())

    def test_a_viewport_that_cannot_be_exact_is_reported_not_hidden(self) -> None:
        self.start()
        naut = BY_ID["taiga_lcd"]  # 315 x 233: gcd 1, so no zoom below 100 % gives whole pixels
        viewport = self.window.select_device(naut.id, "SizeM", LANDSCAPE)
        self.window.viewport_frame.set_scale(0.75)  # possible through the API only; the combo does not offer it
        self.pump(0.5)
        measured = self.measured()
        self.window.refresh_measurement()
        self.assertTrue(self.wait(lambda: "page:" in self.window.measured_label.text()), self.window.measured_label.text())
        flagged = "!= expected" in self.window.measured_label.text()
        self.assertEqual(flagged, measured != list(viewport.size), (measured, self.window.measured_label.text()))
        self.assertTrue(flagged or measured == list(viewport.size))

    def test_the_measurement_label_confirms_an_exact_viewport(self) -> None:
        self.start()
        viewport = self.choose(BY_ID["vanguard_96"], scale=0.5)
        self.settle(viewport.size)
        self.window.refresh_measurement()
        self.assertTrue(self.wait(lambda: "page: 248x170" in self.window.measured_label.text()), self.window.measured_label.text())
        self.assertNotIn("expected", self.window.measured_label.text())

    def test_changing_device_size_and_orientation_never_reloads_or_touches_the_instance(self) -> None:
        self.start()
        instance = self.window.bridge.instance
        self.window.settings_panel.editors["level"].control.setValue(8)
        self.assertTrue(self.wait(lambda: self.js("level") == 8))
        before = (dict(instance.data), set(instance.edited), self.window.reload_count, self.settled, self.window.bridge)
        self.js("window.__sentinel = 'kept'; 1")
        resizes = self.jsj("window.__resizes")
        edge, link = BY_ID["dash_accessory_lcd"], BY_ID["link_generic_lcd"]
        for profile, size, orientation in ((edge, "SizeS", LANDSCAPE), (edge, "SizeXL", PORTRAIT), (link, "SizeM", LANDSCAPE),
                                           (BY_ID["cooler_pump_lcd"], None, None), (None, None, None), (edge, "SizeM", LANDSCAPE)):
            viewport = self.window.select_device(profile.id if profile else None, size, orientation)
            self.window.viewport_frame.set_scale(NATIVE)
            self.pump(0.2)
            if viewport:
                self.settle(viewport.size)
        self.assertEqual((dict(instance.data), set(instance.edited), self.window.reload_count, self.settled, self.window.bridge), before)
        self.assertIs(self.window.bridge.instance, instance)
        self.assertEqual((self.js("window.__sentinel"), self.js("level")), ("kept", 8))
        self.assertGreater(self.jsj("window.__resizes"), resizes)  # the page was told, through a normal resize event
        self.assertEqual(self.window.console.matching("error", widget_only=True), [])

    def test_responsive_layout_clipping_and_scrolling_follow_the_viewport(self) -> None:
        self.start()
        narrow = "window.matchMedia('(max-width: 300px)').matches"
        link, edge = BY_ID["link_generic_lcd"], BY_ID["dash_accessory_lcd"]
        self.settle(self.choose(link, "SizeS", LANDSCAPE).size)  # 234 wide
        self.assertTrue(self.js(narrow))
        box = self.jsj("(function(){var r=document.getElementById('box').getBoundingClientRect();return [r.width,r.height]})()")
        self.assertEqual(box, [234, 129])  # 100vw x 100vh is the viewport
        self.assertEqual(self.js("document.documentElement.scrollWidth"), 400)  # the 400 px element does not fit: clipped (overflow hidden)
        self.settle(self.choose(edge, "SizeM", LANDSCAPE).size)  # 315 wide
        self.assertFalse(self.js(narrow))
        self.assertEqual(self.js("document.documentElement.scrollWidth"), 400)
        self.settle(self.choose(edge, "SizeXL", LANDSCAPE).size)  # 951 wide: it fits now
        self.assertEqual(self.js("document.documentElement.scrollWidth"), 951)

    def test_scrolling_is_possible_only_when_the_content_overflows_the_viewport(self) -> None:
        self.start(SCROLL_HTML, "scroll")
        edge = BY_ID["dash_accessory_lcd"]
        self.settle(self.choose(edge, "SizeM", LANDSCAPE).size)  # 315 < 400
        self.assertEqual(self.js("window.scrollTo(100, 0); window.scrollX"), 400 - 315)  # scrolls to the end of the overflow
        self.settle(self.choose(edge, "SizeXL", LANDSCAPE).size)  # 951 > 400
        self.assertEqual(self.js("window.scrollTo(100, 0); window.scrollX"), 0)

    def test_live_reload_and_the_settings_editor_work_after_a_device_change(self) -> None:
        self.start()
        edge = BY_ID["dash_accessory_lcd"]
        viewport = self.choose(edge, "SizeL", PORTRAIT)
        self.settle(viewport.size)
        editors = self.window.settings_panel.editors
        editors["level"].control.setValue(3)
        self.assertTrue(self.wait(lambda: self.js("level") == 3))
        count = self.window.reload_count
        write_widget(Path(self._tmp.name) / "w", html=RESP_HTML.replace("#223", "#322"))
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: self.window.reload_count == count + 1 and not self.window._reloading))
        self.pump(0.4)
        self.settle(viewport.size, "after live reload")
        self.assertEqual((self.window.device, self.window.device_size, self.window.device_orientation), (edge, "SizeL", PORTRAIT))
        self.assertEqual(self.js("level"), 3)  # user-edited value kept through the reload
        editors = self.window.settings_panel.editors
        self.assertEqual(editors["level"].control.value(), 3)
        editors["level"].control.setValue(9)
        editors["label"].control.setText("after")
        QTest.keyClick(editors["label"].control, Qt.Key.Key_Return)
        self.assertTrue(self.wait(lambda: self.js("level") == 9 and self.js("label") == "after"))
        self.assertEqual(self.window.reload_count, count + 1)
        self.settle(viewport.size, "after settings edit")

    def test_choices_the_device_does_not_offer_are_refused_and_the_controls_show_only_what_it_supports(self) -> None:
        self.start()
        w = self.window
        edge, naut, pump = BY_ID["dash_accessory_lcd"], BY_ID["taiga_lcd"], BY_ID["cooler_pump_lcd"]
        w.select_device(naut.id, "SizeS", LANDSCAPE)
        # S..XL are always shown (U1); what the device does not offer is disabled, with the reason as tooltip
        self.assertEqual([w.size_combo.itemText(i) for i in range(w.size_combo.count())], ["S", "M", "L", "XL"])
        self.assertEqual(w.size_combo.enabled_texts(), ["S", "M"])
        self.assertIn("not offered", w.size_combo.button(3).toolTip())
        self.assertEqual(w.orientation_combo.enabled_texts(), ["Landscape", "Portrait"])
        w.select_device(edge.id)
        self.assertEqual(w.size_combo.enabled_texts(), ["S", "M", "L", "XL"])
        state = (w.device, w.device_size, w.device_orientation, w.viewport)
        for args in ((naut.id, "SizeXL", LANDSCAPE), (edge.id, "SizeM", "sideways"), (pump.id, "SizeM", None), ("no_such_device",),
                     (pump.id, None, None, MODE_PHYSICAL)):
            with self.assertRaises(ValueError, msg=str(args)):
                w.select_device(*args)
        self.assertEqual((w.device, w.device_size, w.device_orientation, w.viewport), state)
        w.select_device(pump.id)
        self.assertFalse(w.size_combo.isEnabled() or w.orientation_combo.isEnabled() or w.mode_combo.isEnabled())
        w.select_device(edge.id)
        self.assertTrue(w.size_combo.isEnabled() and w.orientation_combo.isEnabled() and w.mode_combo.isEnabled())

    def test_the_combo_boxes_drive_the_same_selection(self) -> None:
        self.start()
        w = self.window
        w.device_combo.setCurrentIndex(w.device_combo.findData("link_generic_lcd"))
        w.device_combo.activated.emit(w.device_combo.currentIndex())
        w.size_combo.setCurrentIndex(w.size_combo.findData("SizeL"))
        w.size_combo.activated.emit(w.size_combo.currentIndex())
        w.orientation_combo.setCurrentIndex(w.orientation_combo.findData(PORTRAIT))
        w.orientation_combo.activated.emit(w.orientation_combo.currentIndex())
        self.assertEqual((w.device.id, w.device_size, w.device_orientation), ("link_generic_lcd", "SizeL", PORTRAIT))
        w.scale_combo.setCurrentIndex(0)
        w.scale_combo.activated.emit(0)
        self.settle(w.viewport.size)
        self.assertEqual(w.viewport.size, (261, 471))

    def test_free_size_gives_the_page_the_whole_preview_area_again(self) -> None:
        self.start()
        self.settle(self.choose(BY_ID["vanguard_96"]).size)
        self.window.select_device(None)
        self.assertIsNone(self.window.viewport_frame.viewport)
        frame = self.window.viewport_frame
        self.assertEqual((self.window.view.width(), self.window.view.height()), (frame.width(), frame.height()))
        self.settle((frame.width(), frame.height()))


class NativeScaleTests(PageTestCase):
    """A2: Native 1:1 keeps the page at the exact viewport and the display's devicePixelRatio; the page-zoom fit is EXPERIMENTAL."""

    def setUp(self) -> None:
        PageTestCase.setUp(self)
        self.window.resize(1500, 900)
        self.window.show()
        self.settled = 0
        self.window.loadSettled.connect(lambda ok: setattr(self, "settled", self.settled + 1))

    start = ViewportPageTests.start
    measured = ViewportPageTests.measured
    settle = ViewportPageTests.settle

    def metrics(self) -> list:
        return self.jsj("[innerWidth, innerHeight, devicePixelRatio, visualViewport.width, visualViewport.height, visualViewport.scale]")

    def display(self) -> float:
        return self.window.screen().devicePixelRatio()

    def test_native_is_exact_with_the_display_dpr_for_every_device_size_and_orientation_in_a_small_frame(self) -> None:
        self.start()
        self.window.viewport_frame.setFixedSize(300, 200)  # every viewport is larger: it must scroll, not shrink
        self.pump(0.2)
        count = 0
        for profile in PROFILES:
            for size in profile.supported_sizes() or [None]:
                for orientation in profile.orientations() or [None]:
                    with self.subTest(device=profile.id, size=size, orientation=orientation):
                        viewport = self.window.select_device(profile.id, size, orientation)
                        self.settle(viewport.size, profile.id)
                        width, height, ratio, vv_w, vv_h, vv_scale = self.metrics()
                        self.assertEqual((vv_w, vv_h, vv_scale), (viewport.width, viewport.height, 1))  # no fractional page size
                        self.assertAlmostEqual(ratio, self.display(), places=3)
                        self.assertEqual((self.window.viewport_frame.zoom, self.window.view.zoomFactor()), (1.0, 1.0))
                        self.assertEqual(self.window.view.size().toTuple(), viewport.size)
                        count += 1
        self.assertGreaterEqual(count, 30)
        self.assertEqual((self.window.reload_count, self.settled), (0, 1))

    def test_a_viewport_larger_than_the_window_scrolls_and_never_shrinks(self) -> None:
        self.start()
        frame = self.window.viewport_frame
        frame.setFixedSize(300, 200)
        self.pump(0.2)
        edge = BY_ID["dash_accessory_lcd"]
        viewport = self.window.select_device(edge.id, "SizeXL", PORTRAIT)
        self.settle(viewport.size)
        self.assertGreater(frame.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.window.view.size().toTuple(), viewport.size)
        before = self.window.view.mapTo(self.window, self.window.view.rect().topLeft())
        frame.scroll.verticalScrollBar().setValue(frame.scroll.verticalScrollBar().maximum())
        self.pump(0.2)
        self.assertNotEqual(self.window.view.mapTo(self.window, self.window.view.rect().topLeft()), before)
        self.settle(viewport.size)  # scrolling the canvas does not touch the page size
        viewport = self.window.select_device(edge.id, "SizeXL", LANDSCAPE)
        self.settle(viewport.size)
        self.assertGreater(frame.scroll.horizontalScrollBar().maximum(), 0)

    def test_the_experimental_fit_changes_dpr_and_is_labelled_but_native_gets_everything_back(self) -> None:
        self.start()
        w, frame = self.window, self.window.viewport_frame
        frame.setFixedSize(300, 200)
        self.pump(0.2)
        edge = BY_ID["dash_accessory_lcd"]
        for size, orientation in (("SizeM", LANDSCAPE), ("SizeL", PORTRAIT), ("SizeXL", PORTRAIT)):
            with self.subTest(size=size, orientation=orientation):
                viewport = w.select_device(edge.id, size, orientation)
                frame.set_scale(NATIVE)
                self.settle(viewport.size)
                native = self.metrics()
                frame.set_scale(FIT)
                self.pump(0.4)
                fitted = self.metrics()
                self.assertLess(frame.zoom, 1.0)
                self.assertNotAlmostEqual(fitted[2], native[2], places=2)  # the documented side effect of the page zoom
                w.refresh_measurement()
                self.assertTrue(self.wait(lambda: "EXPERIMENTAL scale" in w.measured_label.text(), 5), w.measured_label.text())
                frame.set_scale(NATIVE)
                self.pump(0.4)
                self.assertEqual(self.metrics(), native)  # DPR and exact size are back
                w.refresh_measurement()
                self.assertTrue(self.wait(lambda: "EXPERIMENTAL" not in w.measured_label.text() and "page:" in w.measured_label.text(), 5))
        self.assertEqual((w.reload_count, self.settled), (0, 1))  # only the preview changed: no reload

    def test_the_portrait_xl_deviation_is_gone_in_fit_too_only_the_dpr_stays_scaled(self) -> None:
        self.start()
        frame = self.window.viewport_frame
        frame.setFixedSize(300, 200)
        self.pump(0.2)
        edge = BY_ID["dash_accessory_lcd"]
        for size in ("SizeL", "SizeXL"):
            viewport = self.window.select_device(edge.id, size, PORTRAIT)
            frame.set_scale(NATIVE)
            self.settle(viewport.size)
            native = self.metrics()
            frame.set_scale(FIT)
            self.pump(0.5)
            fitted = self.metrics()
            self.assertEqual(fitted[:2] + fitted[3:5], list(viewport.size) + list(viewport.size))  # exact: 261x633 / 261x951, whole
            self.assertEqual(fitted[5], 1)  # visualViewport.scale
            self.assertAlmostEqual(fitted[2], self.display() * frame.zoom, places=3)  # the remaining (inherent) side effect
            self.assertEqual(self.window.view.size().toTuple(), (round(viewport.width * frame.zoom), round(viewport.height * frame.zoom)))
            frame.set_scale(NATIVE)
            self.settle(viewport.size)
            self.assertEqual(self.metrics(), native)

    def test_media_queries_clipping_and_scrolling_follow_the_native_viewport(self) -> None:
        self.start(SCROLL_HTML)
        edge = BY_ID["dash_accessory_lcd"]
        queries = "[matchMedia('(max-width: 300px)').matches, matchMedia('(orientation: portrait)').matches, matchMedia('(min-height: 600px)').matches]"
        for size, orientation, expected in (("SizeM", LANDSCAPE, [False, False, False]), ("SizeL", PORTRAIT, [True, True, True]),
                                            ("SizeM", PORTRAIT, [True, True, False])):
            with self.subTest(size=size, orientation=orientation):
                viewport = self.window.select_device(edge.id, size, orientation)
                self.settle(viewport.size)
                self.assertEqual(self.jsj(queries), expected)
        viewport = self.window.select_device(edge.id, "SizeM", LANDSCAPE)  # 315 wide, content 400 wide: clipped, scrollable
        self.settle(viewport.size)
        self.assertEqual(self.jsj("[document.documentElement.scrollWidth > innerWidth]"), [True])
        self.js("window.scrollTo(1000, 0); 1")
        self.pump(0.2)
        self.assertEqual(self.js("Math.round(window.scrollX)"), 400 - viewport.width)

    def test_native_dpr_follows_a_windows_display_scaling_other_than_100_percent(self) -> None:
        import subprocess
        code = r"""
import json, os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
sys.path.insert(0, sys.argv[1])
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication
app = QApplication([])
from widgetdevtool.main_window import MainWindow
from widgetdevtool.viewport import FIT, NATIVE
def wait(f, t=30):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        if f(): return True
        time.sleep(0.02)
def js(w, code):
    box = {}
    w.view.page().runJavaScript(code, lambda v: box.setdefault("v", v)); wait(lambda: "v" in box, 10)
    return json.loads(box["v"])
w = MainWindow(); w.resize(1300, 900); w.show()
w.load_folder(sys.argv[2]); wait(lambda: w.state.name == "LOADED"); time.sleep(0.5)
out = {"display": w.screen().devicePixelRatio()}
w.viewport_frame.setFixedSize(300, 200)
v = w.select_device("dash_accessory_lcd", "SizeL", "portrait"); w.viewport_frame.set_scale(NATIVE); time.sleep(0.6); wait(lambda: False, 0.4)
M = "JSON.stringify([innerWidth, innerHeight, devicePixelRatio])"
out["want"] = list(v.size); out["native"] = js(w, M)
w.viewport_frame.set_scale(FIT); wait(lambda: False, 0.6); out["fit"] = js(w, M); out["zoom"] = w.viewport_frame.zoom
w.viewport_frame.set_scale(NATIVE); wait(lambda: False, 0.6); out["native_again"] = js(w, M)
w.shutdown(); print("RESULT" + json.dumps(out))
"""
        folder = write_widget(Path(self._tmp.name) / "dpr", html=SCROLL_HTML)
        env = dict(os.environ, QT_SCREEN_SCALE_FACTORS="1.5")
        done = subprocess.run([sys.executable, "-c", code, str(Path(__file__).resolve().parents[1]), str(folder)], env=env,
                              capture_output=True, text=True, timeout=240)
        line = next((l for l in done.stdout.splitlines() if l.startswith("RESULT")), None)
        self.assertIsNotNone(line, done.stderr[-800:])
        out = json.loads(line[6:])
        self.assertEqual(out["display"], 1.5)  # the probe really ran at 150 % display scaling
        self.assertAlmostEqual(out["native"][2], 1.5, places=3)  # DPR is the display's, not hard-coded to 1
        self.assertLessEqual(abs(out["native"][0] - out["want"][0]) + abs(out["native"][1] - out["want"][1]), 2)  # Qt rounds logical -> physical
        self.assertAlmostEqual(out["fit"][2], 1.5 * out["zoom"], places=2)  # the experimental zoom multiplies it
        self.assertEqual(out["native_again"], out["native"])

class WindowSizeStabilityTests(PageTestCase):
    """The MainWindow keeps its size when the user changes Device / Size / Orientation / preview scale: only the Widget Viewport
    changes. (Bug found after A2: long unwrapped labels and combo boxes set the window's minimum size, so every longer text made
    the window grow and it never shrank back.) The window is shown but never resized by these tests after the first show."""

    def setUp(self) -> None:
        PageTestCase.setUp(self)
        self.window.show()  # default size, as the user starts the tool
        self.pump(0.4)
        self.assertTrue(self.window.load_folder(write_widget(Path(self._tmp.name) / "w", html=RESP_HTML)))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.pump(0.4)

    def snapshot(self) -> dict:
        w = self.window
        return {"window": w.size().toTuple(), "frame": w.viewport_frame.size().toTuple(), "min": w.minimumSizeHint().toTuple(),
                "minimum": (w.minimumWidth(), w.minimumHeight())}

    def every_choice(self):
        for profile in PROFILES:
            for size in profile.supported_sizes() or [None]:
                for orientation in profile.orientations() or [None]:
                    yield profile, size, orientation

    def test_cycling_every_device_size_and_orientation_never_changes_the_window_size(self) -> None:
        start = self.snapshot()
        for scale in (NATIVE, FIT):
            self.window.viewport_frame.set_scale(scale)
            for round_ in range(2):
                for profile, size, orientation in self.every_choice():
                    self.window.select_device(profile.id, size, orientation)
                    self.window.refresh_measurement()
                    self.pump(0.05)
                    self.assertEqual(self.snapshot(), start, f"{profile.id} {size} {orientation} scale {scale} round {round_}")
        self.pump(0.5)  # the measurement label is written asynchronously
        self.assertEqual(self.snapshot(), start)

    def test_s_xl_s_back_and_forth_gives_back_the_same_window_and_frame(self) -> None:
        edge = BY_ID["dash_accessory_lcd"]
        self.window.select_device(edge.id, "SizeS", LANDSCAPE)
        self.pump(0.3)
        start = self.snapshot()
        for _ in range(6):
            for size, orientation in (("SizeXL", PORTRAIT), ("SizeS", LANDSCAPE), ("SizeL", LANDSCAPE), ("SizeS", PORTRAIT), ("SizeS", LANDSCAPE)):
                self.window.select_device(edge.id, size, orientation)
                self.window.refresh_measurement()
                self.pump(0.1)
        self.assertEqual(self.snapshot(), start)

    def test_a_viewport_larger_than_the_preview_area_scrolls_instead_of_growing_the_window(self) -> None:
        w, frame = self.window, self.window.viewport_frame
        start = self.snapshot()
        w.select_device("dash_accessory_lcd", "SizeXL", PORTRAIT)
        self.pump(0.3)
        self.assertEqual(self.snapshot(), start)
        self.assertGreater(frame.canvas.height(), frame.height())  # the canvas is larger ...
        self.assertGreater(frame.scroll.verticalScrollBar().maximum(), 0)  # ... and scrolls
        self.assertEqual(w.view.size().toTuple(), w.viewport.size)  # the HTML viewport is the device's, untouched

    def inside(self, name: str) -> bool:
        widget = getattr(self.window, name)
        rect = widget.rect().translated(widget.mapTo(self.window, widget.rect().topLeft()))
        return widget.isVisible() and self.window.rect().contains(rect)

    def test_the_panels_stay_inside_the_window_and_changing_size_does_not_move_them(self) -> None:
        w = self.window
        names = ("inspector", "instances_panel", "console_view", "viewport_frame", "status")
        w.select_device("dash_accessory_lcd", "SizeXL", PORTRAIT)
        self.pump(0.4)
        self.assertEqual([n for n in names if not self.inside(n)], [])
        where = {n: getattr(w, n).geometry() for n in names}
        for size, orientation in (("SizeS", LANDSCAPE), ("SizeXL", PORTRAIT), ("SizeS", LANDSCAPE), ("SizeL", PORTRAIT)):
            w.select_device("dash_accessory_lcd", size, orientation)
            self.pump(0.2)
        self.assertEqual({n: getattr(w, n).geometry() for n in names}, where)  # only the viewport inside the frame changed

    def test_shutdown_hides_the_window_so_finished_test_windows_do_not_pile_up(self) -> None:
        self.assertTrue(self.window.isVisible())
        self.window.shutdown()
        self.assertFalse(self.window.isVisible())

@unittest.skipUnless(BASKETBALL.is_dir() and SMOKECLOCK.is_dir() and RSS.is_dir(), "WidgetsXE widgets not present")
class RealWidgetViewportTests(PageTestCase):
    """Basketball Scores, RSS Feeder, SmokeClock in several viewports: measured size, no reload, settings editor, live reload."""

    CASES = (("dash_accessory_lcd", "SizeM", LANDSCAPE), ("dash_accessory_lcd", "SizeL", PORTRAIT),
             ("link_generic_lcd", "SizeS", LANDSCAPE), ("taiga_lcd", "SizeM", PORTRAIT), ("vanguard_96", None, None),
             ("cooler_pump_lcd", None, None))

    def run_widget(self, folder: Path) -> dict:
        self.window.resize(1500, 900)
        self.window.show()
        self.assertTrue(self.window.load_folder(folder))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED", 30))
        self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True, 30))
        self.pump(1.0)
        baseline = [e.message[:160] for e in self.window.console.matching("error", widget_only=True)]
        self.assertEqual([m for m in baseline if classify_console_error(m) == "runtime"], [], "runtime errors at load")
        reloads, instance = self.window.reload_count, self.window.bridge.instance
        overflow = {}
        for device_id, size, orientation in self.CASES:
            viewport = self.window.select_device(device_id, size, orientation)
            self.window.viewport_frame.set_scale(NATIVE)
            self.assertTrue(self.wait(lambda: self.jsj("[innerWidth, innerHeight]") == list(viewport.size), 10), f"{device_id} {size}")
            self.pump(0.3)
            overflow[f"{device_id}/{size}/{orientation}"] = self.jsj("[document.documentElement.scrollWidth, document.documentElement.scrollHeight]")
        self.assertEqual((self.window.reload_count, self.window.bridge.instance), (reloads, instance))
        key = next(k for k, e in self.window.settings_panel.editors.items() if e.supported and e.param.data_type in ("switch", "slider"))
        editor = self.window.settings_panel.editors[key]
        before = self.jsj(f"window[{key!r}]")
        editor.control.click() if editor.param.data_type == "switch" else editor.control.setValue(int(editor.param.meta["max"]))
        self.assertTrue(self.wait(lambda: self.jsj(f"window[{key!r}]") != before, 8), key)
        self.assertEqual(self.window.reload_count, reloads)
        edited = self.jsj(f"window[{key!r}]")
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: self.window.reload_count == reloads + 1 and not self.window._reloading, 30))
        self.pump(1.5)
        self.assertEqual(self.jsj(f"window[{key!r}]"), edited)
        last = self.window.viewport.size
        self.assertTrue(self.wait(lambda: self.jsj("[innerWidth, innerHeight]") == list(last), 10))
        after = [e.message[:160] for e in self.window.console.matching("error", widget_only=True)]
        self.assertEqual([m for m in after if m not in baseline], [], "errors added by viewport changes, the edit or the reload")
        return {"baseline_errors": [(classify_console_error(m), m) for m in baseline], "overflow": overflow}

    def test_basketball_scores(self) -> None:
        self.run_widget(BASKETBALL)

    def test_rss_feeder(self) -> None:
        self.run_widget(RSS)

    def test_smokeclock(self) -> None:
        self.run_widget(SMOKECLOCK)


if __name__ == "__main__":
    unittest.main()