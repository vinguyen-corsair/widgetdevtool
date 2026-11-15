r"""Widget Personalization tests (D4): raw instance settings vs the effective settings the widget receives.

Expected behaviour comes from WidgetBuilderPersonalizationSettings.cpp settings(), ActivePersonalizationSettingsModel.cpp and
WidgetBuilderPanelSettingsModel.cpp (iCUE v5.55, not verified against the release). The tests prove the rules are applied; they
do NOT prove that a widget looks like it does in iCUE 5 (no visual comparison exists).
Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)

from test_runtime_bridge import PageTestCase, write_widget  # noqa: E402
from test_settings_panel import BASKETBALL, RSS, SMOKECLOCK, classify_console_error  # noqa: E402
from widgetdevtool.instance_store import (  # noqa: E402
    CUSTOM_STYLE_ENABLED_KEY as ENABLED, CUSTOM_STYLE_PRESENT_KEY as PRESENT, WidgetInstance)
from widgetdevtool.personalization import (  # noqa: E402
    FREE_KEY, WP_KEYS, PersonalizationModel, PersonalizationValues, effective_settings, has_personalization,
    override_report, personalization_applies, sync_present, to_bool)

ACTIVE = PersonalizationValues("#ffffff", "#f08e33", "#f08e33", "#000000", 80)
RAW = {"label": "x", "textColor": "#123456", "accentColor": "#654321", "backgroundColor": "#101010", "transparency": 100.0,
       "backgroundMedia": {"pathToAsset": "file:///a.png", "scale": 2.0, "positionX": 1.0, "positionY": 2.0, "angle": 90}}


class DefaultsAndModelTests(unittest.TestCase):
    def test_defaults_are_the_ones_of_personalization_settings_h(self) -> None:
        v = PersonalizationValues()
        self.assertEqual((v.text_color, v.accent_color, v.accent_color2, v.widget_background_color, v.widget_transparency),
                         ("#ffffff", "#f08e33", "#f08e33", "#000000", 80))
        self.assertIsInstance(v.widget_transparency, int)
        self.assertEqual(WP_KEYS, ("textColor", "accentColor", "accentColor2", "backgroundColor", "transparency", "backgroundMedia"))

    def test_device_is_active_until_theme_by_screen_is_enabled_and_each_device_has_its_own(self) -> None:
        m = PersonalizationModel()
        m.set_value("device", "edge", "text_color", "#FF0000")
        m.set_value("screen", "edge", "text_color", "#00ff00")
        self.assertEqual(m.active("edge").text_color, "#ff0000")  # stored lower case, like QColor::name()
        m.set_theme_by_screen("edge", True)
        self.assertEqual(m.active("edge").text_color, "#00ff00")
        self.assertEqual(m.active("link").text_color, "#ffffff")  # another device: untouched defaults, theme-by-screen off
        self.assertEqual(m.values("device", "edge").text_color, "#ff0000")  # the Device values are kept while Screen is active
        with self.assertRaises(ValueError):
            m.values("galaxy", "edge")
        with self.assertRaises(ValueError):
            m.set_value("device", "edge", "bogus", 1)

    def test_transparency_is_an_int(self) -> None:
        m = PersonalizationModel()
        m.set_value("device", FREE_KEY, "widget_transparency", 55.0)
        self.assertEqual(m.active(FREE_KEY).widget_transparency, 55)
        self.assertIsInstance(m.active(FREE_KEY).widget_transparency, int)


class EffectiveSettingsTests(unittest.TestCase):
    def test_custom_style_off_replaces_the_existing_personalization_keys_only(self) -> None:
        raw = copy.deepcopy(RAW)
        eff = effective_settings(raw, ACTIVE)
        self.assertEqual((eff["textColor"], eff["accentColor"], eff["backgroundColor"], eff["transparency"]),
                         ("#ffffff", "#f08e33", "#000000", 80))
        self.assertNotIn("accentColor2", eff)  # not in the instance: nothing is added
        self.assertEqual(eff["label"], "x")  # a non personalization key is never touched
        self.assertEqual(eff["backgroundMedia"], {"pathToAsset": "", "scale": 2.0, "positionX": 1.0, "positionY": 2.0, "angle": 90})
        self.assertEqual(raw, RAW)  # the raw settings are untouched

    def test_custom_style_on_or_present_false_gives_the_raw_map(self) -> None:
        for extra in ({ENABLED: True}, {PRESENT: False}, {ENABLED: "true"}, {ENABLED: 1}, {PRESENT: False, ENABLED: False}):
            raw = {**RAW, **extra}
            self.assertEqual(effective_settings(raw, ACTIVE), raw, extra)
            self.assertFalse(personalization_applies(raw), extra)

    def test_marker_defaults_present_defaults_to_true_enabled_to_false(self) -> None:
        self.assertTrue(personalization_applies(RAW))  # both missing
        self.assertTrue(personalization_applies({**RAW, PRESENT: True, ENABLED: False}))
        self.assertTrue(personalization_applies({**RAW, ENABLED: "false"}))
        self.assertEqual([to_bool(v) for v in (True, False, None, 0, 2, "", "0", "false", "False", "yes", {}, [])],
                         [True, False, False, False, True, False, False, False, False, True, False, False])

    def test_backgroundMedia_is_cleared_not_replaced_and_survives_a_bad_value(self) -> None:
        self.assertEqual(effective_settings({"backgroundMedia": "oops"}, ACTIVE), {"backgroundMedia": {"pathToAsset": ""}})
        eff = effective_settings(RAW, ACTIVE)
        eff["backgroundMedia"]["scale"] = 9.0
        self.assertEqual(RAW["backgroundMedia"]["scale"], 2.0)  # a copy

    def test_without_an_active_source_the_raw_map_is_returned_as_a_copy(self) -> None:
        eff = effective_settings(RAW, None)
        self.assertEqual(eff, RAW)
        eff["label"] = "changed"
        self.assertEqual(RAW["label"], "x")

    def test_report_tells_what_differs(self) -> None:
        report = override_report(RAW, effective_settings(RAW, ACTIVE))
        self.assertEqual({k for k, v in report.items() if v["overridden"]}, set(WP_KEYS) - {"accentColor2"})
        self.assertEqual(report["textColor"], {"stored": "#123456", "runtime": "#ffffff", "overridden": True})
        self.assertFalse(override_report(RAW, RAW)["textColor"]["overridden"])


class MarkerTests(unittest.TestCase):
    def test_present_is_written_only_for_an_instance_with_personalization_keys_and_is_not_a_user_edit(self) -> None:
        inst = WidgetInstance.create("t", RAW)
        self.assertTrue(has_personalization(inst.data))
        self.assertTrue(sync_present(inst))
        self.assertEqual((inst.data[PRESENT], inst.edited), (True, set()))
        self.assertFalse(sync_present(inst))  # idempotent
        plain = WidgetInstance.create("t", {"label": "x"})
        self.assertFalse(sync_present(plain))
        self.assertEqual(plain.data, {"label": "x"})  # no property the widget did not declare

    def test_markers_survive_a_live_reload_refresh_and_merge_carries_custom_style(self) -> None:
        inst = WidgetInstance.create("t", {"textColor": "#111111"})
        inst.set_value(ENABLED, True)
        sync_present(inst)
        report = inst.refresh_from_source({"textColor": "#222222"})
        self.assertEqual((inst.data[ENABLED], inst.data[PRESENT]), (True, True))
        self.assertEqual(report["dropped"], [])
        self.assertIn(ENABLED, inst.edited)
        merged = WidgetInstance.merge(inst, {"textColor": "#333333"})
        self.assertEqual(merged.data.get(ENABLED), True)  # update() carries the Custom Style switch over

    def test_the_raw_map_of_an_instance_is_not_changed_by_applying_personalization(self) -> None:
        inst = WidgetInstance.create("t", RAW)
        before = copy.deepcopy(inst.data)
        effective_settings(inst.data, ACTIVE)
        self.assertEqual(inst.data, before)


GROUPS = ('<script type="application/json" id="x-icue-groups">[{"title":"Setup","properties":["label"]},'
          '{"title":"Widget Personalization","properties":["backgroundMedia","glassBlur","textColor","accentColor",'
          '"backgroundColor","transparency"]}]</script>')


def meta(name: str, data_type: str, default: str = "", **attrs: str) -> str:
    extra = "".join(f' data-{k.replace("_", "-")}="{v}"' for k, v in attrs.items())
    d = f' data-default="{default}"' if default else ""
    return f'<meta name="x-icue-property" content="{name}" data-type="{data_type}"{d}{extra}>'


def page(*metas: str, groups: str = "") -> str:
    return ("<!doctype html><html><head><title>Pers</title>" + "".join(metas) + groups +
            "<script>window.__updates = 0; icueEvents = { onICUEInitialized: function () {}, "
            "onDataUpdated: function () { window.__updates++; } };</script></head><body>x</body></html>")


PERS_HTML = page(
    meta("label", "textfield", "'hello'", label="'Label'"),
    meta("textColor", "color", "'#123456'", label="'Text'"),
    meta("accentColor", "color", "'#654321'", label="'Accent'"),
    meta("backgroundColor", "color", "'#101010'", label="'Bg'"),
    meta("transparency", "slider", "100", label="'Transp'", min="0", max="100", step="1"),
    meta("backgroundMedia", "media-selector", filters="[&quot;image&quot;]", label="'Media'"),
    meta("glassBlur", "slider", "3", label="'Blur'", min="0", max="10", step="1"),
    groups=GROUPS)
PLAIN_HTML = page(meta("label", "textfield", "'hello'", label="'Label'"), meta("level", "slider", "5", label="'L'", min="0", max="10", step="1"))


class PersonalizationPageTests(PageTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.settled = 0
        self.window.loadSettled.connect(lambda ok: setattr(self, "settled", self.settled + 1))

    def start(self, html: str = PERS_HTML, name: str = "w", **kwargs) -> Path:
        root = write_widget(Path(self._tmp.name) / name, html=html)
        self.assertTrue(self.window.load_folder(root, **kwargs))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True))
        self.pump(0.3)
        return root

    def updates(self) -> int:
        return self.jsj("window.__updates")

    def runtime(self, *keys: str) -> list:
        return [self.jsj(f"typeof window[{k!r}] === 'undefined' ? '<undefined>' : window[{k!r}]") for k in keys]

    def wait_runtime(self, key: str, expected) -> None:
        self.assertTrue(self.wait(lambda: self.runtime(key) == [expected], 8), f"{key}: {self.runtime(key)} != {expected!r}")

    def reload(self, root: Path, html: str) -> None:
        write_widget(root, html=html)
        count = self.window.reload_count
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: self.window.reload_count == count + 1 and not self.window._reloading))
        self.pump(0.4)

    # ---- Custom Style ----
    def test_custom_style_off_the_widget_receives_the_device_personalization_and_the_instance_keeps_its_own(self) -> None:
        self.start()
        inst = self.window.bridge.instance
        self.assertEqual(self.runtime("textColor", "accentColor", "backgroundColor", "transparency"), ["#ffffff", "#f08e33", "#000000", 80])
        self.assertEqual(self.runtime("label", "glassBlur"), ["hello", 3])  # not personalization keys
        self.assertEqual((inst.data["textColor"], inst.data["accentColor"], inst.data["backgroundColor"], inst.data["transparency"]),
                         ("#123456", "#654321", "#101010", 100.0))  # raw untouched
        self.assertEqual(self.runtime(PRESENT, ENABLED), [True, "<undefined>"])
        self.assertTrue(inst.data[PRESENT] and ENABLED not in inst.data and inst.edited == set())

    def test_switching_custom_style_on_and_off_updates_the_widget_without_a_reload(self) -> None:
        self.start()
        inst, bridge = self.window.bridge.instance, self.window.bridge
        raw = copy.deepcopy(inst.data)
        self.js("window.__sentinel = 'kept'; 1")
        before, settled = self.updates(), self.settled
        self.assertTrue(self.window.set_custom_style(True))
        self.wait_runtime("textColor", "#123456")
        self.assertEqual(self.runtime("accentColor", "backgroundColor", "transparency", ENABLED), ["#654321", "#101010", 100, True])
        self.assertEqual(self.updates(), before + 1)
        self.assertTrue(self.window.set_custom_style(False))
        self.wait_runtime("textColor", "#ffffff")
        self.assertEqual(self.updates(), before + 2)
        self.assertEqual((self.js("window.__sentinel"), self.window.reload_count, self.settled, self.window.bridge), ("kept", 0, settled, bridge))
        self.assertEqual({k: v for k, v in inst.data.items() if k not in (ENABLED, PRESENT)}, {k: v for k, v in raw.items() if k not in (ENABLED, PRESENT)})
        self.assertIs(inst.data[ENABLED], False)

    def test_a_raw_edit_with_custom_style_off_is_stored_but_does_not_reach_the_widget(self) -> None:
        self.start()
        inst = self.window.bridge.instance
        editor = self.window.settings_panel.editors["textColor"]
        editor.pick_color(QColor("#ff8800"))
        self.pump(0.3)
        self.assertEqual(inst.data["textColor"], "#ff8800")
        self.assertEqual(self.runtime("textColor"), ["#ffffff"])  # the UI shows the stored value, the runtime the replacement
        self.assertEqual(editor.control.text(), "#ff8800")
        self.assertIn("Runtime receives #ffffff (stored: #ff8800)", editor.status.text())
        self.window.set_custom_style(True)
        self.wait_runtime("textColor", "#ff8800")
        self.assertNotIn("Runtime receives", editor.status.text())

    def test_only_keys_the_instance_declares_are_overridden(self) -> None:
        html = page(meta("textColor", "color", "'#123456'"), meta("label", "textfield", "'a'"))
        self.start(html)
        self.assertEqual(self.runtime("textColor", "accentColor", "accentColor2", "backgroundColor", "transparency", "backgroundMedia"),
                         ["#ffffff", "<undefined>", "<undefined>", "<undefined>", "<undefined>", "<undefined>"])
        self.window.set_personalization("device", "accent_color2", "#00ff00")
        self.assertEqual(self.runtime("accentColor2"), ["<undefined>"])
        self.assertNotIn("accentColor2", self.window.bridge.instance.data)

    # ---- widget without / with personalization keys ----
    def test_a_widget_without_personalization_properties_gets_nothing_added_and_no_switch(self) -> None:
        self.start(PLAIN_HTML, "plain")
        inst, panel = self.window.bridge.instance, self.window.settings_panel
        self.assertEqual(set(inst.data), {"label", "level"})  # no marker, no personalization key
        self.assertIsNone(panel.personalization_group)
        self.assertIsNone(panel.custom_style_check)
        self.assertEqual(self.runtime(PRESENT, ENABLED, "textColor"), ["<undefined>"] * 3)
        before = self.updates()
        self.assertFalse(self.window.set_personalization("device", "text_color", "#ff0000"))  # nothing to tell the widget
        self.assertFalse(self.window.set_theme_by_screen(True))
        self.assertFalse(self.window.set_custom_style(True))
        self.pump(0.3)
        self.assertEqual((self.updates(), set(inst.data), self.window.bridge.effective_data()), (before, {"label", "level"}, inst.data))

    def test_custom_style_switch_lives_in_the_last_group_and_hides_the_six_editors_while_off(self) -> None:
        self.start()
        panel = self.window.settings_panel
        self.assertIs(panel.personalization_group, panel.group_boxes[-1])
        self.assertEqual(panel.personalization_group.title(), "Widget Personalization")
        self.assertIsNotNone(panel.custom_style_check)
        self.assertFalse(panel.custom_style_check.isChecked())
        self.assertIn("Widget Personalization is disabled", panel.custom_style_info.text())
        hidden = {k for k in panel.editors if panel.editors[k].isHidden()}
        self.assertEqual(hidden, {"textColor", "accentColor", "backgroundColor", "transparency", "backgroundMedia"})
        self.assertFalse(panel.editors["glassBlur"].isHidden() or panel.editors["label"].isHidden())  # not personalization keys
        panel.custom_style_check.setChecked(True)  # a user action: goes through the window to the widget
        self.assertTrue(self.wait(lambda: self.window.bridge.instance.data.get(ENABLED) is True))
        self.assertEqual({k for k in panel.editors if panel.editors[k].isHidden()}, set())
        self.assertTrue(panel.custom_style_info.isHidden())
        self.wait_runtime("textColor", "#123456")

    def test_a_single_group_widget_has_the_switch_in_that_group(self) -> None:
        html = page(meta("textColor", "color", "'#123456'", label="'T'"), meta("label", "textfield", "'a'", label="'L'"))
        self.start(html)
        panel = self.window.settings_panel
        self.assertEqual(len(panel.group_boxes), 1)
        self.assertIs(panel.personalization_group, panel.group_boxes[0])
        self.assertTrue(panel.editors["textColor"].isHidden() and not panel.editors["label"].isHidden())

    def test_backgroundMedia_is_cleared_for_the_widget_while_custom_style_is_off_and_the_limit_is_stated(self) -> None:
        self.start()
        bridge = self.window.bridge
        bridge.instance.data["backgroundMedia"]["pathToAsset"] = "file:///pic.png"  # a stored value (the editor is unsupported)
        self.assertEqual(bridge.effective_data()["backgroundMedia"]["pathToAsset"], "")
        self.window.set_custom_style(True)
        self.assertEqual(bridge.effective_data()["backgroundMedia"]["pathToAsset"], "file:///pic.png")
        media = self.window.settings_panel.editors["backgroundMedia"]
        self.assertFalse(media.supported)
        self.assertIn("Unsupported", media.status.text())
        self.assertIn("pathToAsset", self.window.personalization_panel.note.text())
        self.assertIn("not simulated", self.window.personalization_panel.note.text())

    # ---- personalization source: device, screen, theme by screen ----
    def test_a_personalization_change_reaches_the_widget_once_without_reload_and_never_touches_the_instance(self) -> None:
        self.start()
        inst = self.window.bridge.instance
        raw = copy.deepcopy(inst.data)
        before, settled = self.updates(), self.settled
        panel = self.window.personalization_panel
        panel.pick_color("text_color", QColor("#ff0000"))  # a user action in the panel
        self.wait_runtime("textColor", "#ff0000")
        panel.transparency.setValue(55)
        self.wait_runtime("transparency", 55)
        self.assertEqual(self.updates(), before + 2)
        self.assertEqual((inst.data, self.window.reload_count, self.settled), (raw, 0, settled))
        self.window.set_custom_style(True)  # now the widget's own values count: a personalization change is invisible to it
        self.wait_runtime("textColor", "#123456")
        count = self.updates()
        self.assertFalse(self.window.set_personalization("device", "text_color", "#0000ff"))
        self.pump(0.3)
        self.assertEqual((self.updates(), self.runtime("textColor")), (count, ["#123456"]))

    def test_theme_by_screen_switches_the_active_values_and_keeps_both_sets(self) -> None:
        self.start()
        self.window.set_personalization("device", "text_color", "#ff0000")
        self.window.set_personalization("screen", "text_color", "#00ff00")
        self.wait_runtime("textColor", "#ff0000")  # the Screen values are not active yet
        panel = self.window.personalization_panel
        panel.theme_by_screen.setChecked(True)
        self.wait_runtime("textColor", "#00ff00")
        self.assertIn("Active for widgets: Screen", panel.active_label.text())
        self.window.set_theme_by_screen(False)
        self.wait_runtime("textColor", "#ff0000")
        self.assertEqual(self.window.personalization.values("screen", FREE_KEY).text_color, "#00ff00")

    def test_the_scope_combo_edits_the_screen_values_through_the_ui(self) -> None:
        self.start()
        panel = self.window.personalization_panel
        panel.scope_combo.setCurrentIndex(panel.scope_combo.findData("screen"))
        panel.scope_combo.activated.emit(panel.scope_combo.currentIndex())
        panel.pick_color("accent_color", QColor("#112233"))
        self.assertEqual(self.window.personalization.values("screen", FREE_KEY).accent_color, "#112233")
        self.assertEqual(self.window.personalization.values("device", FREE_KEY).accent_color, "#f08e33")
        self.assertEqual(self.runtime("accentColor"), ["#f08e33"])  # theme by screen is off: the Device values are active

    def test_personalization_is_per_device_and_a_device_change_loses_no_raw_setting(self) -> None:
        self.start()
        inst = self.window.bridge.instance
        self.window.set_custom_style(False)
        self.window.set_personalization("device", "text_color", "#ff0000")  # for "free size"
        self.window.select_device("dash_accessory_lcd")
        self.wait_runtime("textColor", "#ffffff")  # EDGE has its own (default) personalization
        self.window.set_personalization("device", "text_color", "#00ff00")
        self.wait_runtime("textColor", "#00ff00")
        raw, before, settled = copy.deepcopy(inst.data), self.updates(), self.settled
        self.window.select_device("link_generic_lcd")
        self.wait_runtime("textColor", "#ffffff")
        self.window.select_device("dash_accessory_lcd", "SizeL", "portrait")
        self.wait_runtime("textColor", "#00ff00")
        self.window.select_device(None)
        self.wait_runtime("textColor", "#ff0000")
        self.assertEqual((inst.data, self.window.reload_count, self.settled), (raw, 0, settled))
        self.assertEqual(self.updates(), before + 3)  # only the changes of what the widget receives were sent
        self.window.select_device("dash_accessory_lcd", "SizeS", "landscape")
        self.window.select_device("dash_accessory_lcd", "SizeM", "landscape")  # same active values: nothing to tell the widget
        self.pump(0.3)
        self.assertEqual(self.updates(), before + 4)

    # ---- live reload, restore, merge ----
    def test_live_reload_keeps_custom_style_and_user_values_and_applies_the_new_source_defaults(self) -> None:
        root = self.start()
        inst = self.window.bridge.instance
        self.window.settings_panel.editors["glassBlur"].control.setValue(7)  # a user edit of a non-personalization key
        self.window.set_custom_style(True)
        self.pump(0.3)
        self.reload(root, PERS_HTML.replace("'#123456'", "'#abcdef'").replace('data-default="3"', 'data-default="4"'))
        self.assertIs(self.window.bridge.instance, inst)
        self.assertEqual((inst.data[ENABLED], inst.data[PRESENT]), (True, True))
        self.assertEqual(self.runtime("textColor", "glassBlur"), ["#abcdef", 7])  # Custom Style on: new source default / user value
        self.assertEqual(inst.data["textColor"], "#abcdef")
        self.assertTrue(self.window.settings_panel.custom_style_check.isChecked())
        self.assertFalse(any("widgetPersonalization" in e.message for e in self.window.console.matching(contains="no longer declared")))
        self.window.set_custom_style(False)
        self.reload(root, PERS_HTML.replace("'#123456'", "'#fedcba'"))
        self.assertEqual(self.runtime("textColor"), ["#ffffff"])  # Custom Style off again: personalization wins
        self.assertEqual(inst.data["textColor"], "#fedcba")  # and the raw value followed the source default (it was not edited)

    def test_restore_does_not_add_defaults_and_merge_keeps_custom_style(self) -> None:
        old = WidgetInstance.restore({"id": "88888888-8888-4888-8888-888888888888", "typeId": "t",
                                      "data": {"label": "stored", "textColor": "#222222", ENABLED: True}})
        self.start(instance=old)
        self.assertEqual(self.runtime("label", "textColor", "accentColor", "transparency"), ["stored", "#222222", "<undefined>", "<undefined>"])
        self.assertTrue(old.data[PRESENT])  # the panel marker is written (the widget has personalization keys)
        self.assertTrue(self.window.settings_panel.custom_style_check.isChecked())
        merged = WidgetInstance.merge(old, {"label": "d", "textColor": "#123456", "accentColor": "#654321", "transparency": 100.0})
        self.assertTrue(self.window.store.merge(old.id, {"label": "d", "textColor": "#123456", "accentColor": "#654321", "transparency": 100.0}))
        self.assertTrue(self.window.open_instance(old.id, "restore"))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.pump(0.3)
        self.assertEqual(self.runtime("textColor", "accentColor"), ["#222222", "#654321"])  # Custom Style (carried) -> raw values
        self.assertEqual(merged.data[ENABLED], True)

    def test_restoring_an_old_instance_without_personalization_keys_applies_nothing(self) -> None:
        old = WidgetInstance.restore({"id": "99999999-9999-4999-8999-999999999999", "typeId": "t", "data": {"label": "stored"}})
        self.start(instance=old)
        self.assertEqual(old.data, {"label": "stored"})
        self.assertIsNone(self.window.settings_panel.custom_style_check)
        self.assertEqual(self.runtime("textColor", PRESENT), ["<undefined>", "<undefined>"])
        self.assertEqual(self.window.bridge.effective_data(), {"label": "stored"})

    def test_a_restored_instance_without_markers_gets_personalization_because_present_defaults_to_true(self) -> None:
        old = WidgetInstance.restore({"id": "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa", "typeId": "t",
                                      "data": {"textColor": "#222222", "transparency": 10.0}})
        self.assertTrue(personalization_applies(old.data))
        self.start(instance=old)
        self.assertEqual(self.runtime("textColor", "transparency"), ["#ffffff", 80])
        self.assertEqual((old.data["textColor"], old.data["transparency"]), ("#222222", 10.0))

    def test_the_saved_instance_keeps_the_raw_values_and_the_switch_not_the_replacement(self) -> None:
        self.start()
        self.window.set_custom_style(True)
        path = Path(self._tmp.name) / "inst.json"
        self.window.save_instances(path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("#123456", text)
        self.assertNotIn("#f08e33", text)
        self.assertIn(ENABLED, text)


@unittest.skipUnless(BASKETBALL.is_dir() and SMOKECLOCK.is_dir() and RSS.is_dir(), "WidgetsXE widgets not present")
class RealWidgetPersonalizationTests(PageTestCase):
    """Basketball Scores, RSS Feeder, SmokeClock: what the UI stores against what the page receives."""

    def run_widget(self, folder: Path) -> dict:
        w = self.window
        self.assertTrue(w.load_folder(folder))
        self.assertTrue(self.wait(lambda: w.state.name == "LOADED", 30))
        self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True, 30))
        self.pump(1.0)
        baseline = [e.message[:160] for e in w.console.matching("error", widget_only=True)]
        self.assertEqual([m for m in baseline if classify_console_error(m) == "runtime"], [], "runtime errors at load")
        inst, panel = w.bridge.instance, w.settings_panel
        raw = copy.deepcopy(inst.data)
        keys = [k for k in WP_KEYS if k in raw and k != "backgroundMedia"]
        self.assertTrue(panel.custom_style_check is not None and keys)
        # 1. Custom Style off: the page receives the active personalization; the UI shows the stored values; editors hidden
        active = w.personalization.active(w._personalization_key())
        expected_off = effective_settings(raw, active)
        for key in keys:
            self.assertEqual(self.jsj(f"window[{key!r}]"), expected_off[key], key)
        self.assertTrue(all(panel.editors[k].isHidden() for k in WP_KEYS if k in panel.editors))
        self.assertEqual({k: inst.data[k] for k in keys}, {k: raw[k] for k in keys})
        differing = [k for k in keys if raw[k] != expected_off[k]]
        self.assertTrue(differing, "the widget defaults happen to equal the personalization defaults")
        for k in differing:
            self.assertIn("Runtime receives", panel.editors[k].status.text())
        reloads = w.reload_count
        # 2. a device change and a personalization change: no reload, raw untouched, the page follows the effective values
        w.select_device("dash_accessory_lcd", "SizeM", "landscape")
        w.personalization_panel.pick_color("text_color", QColor("#ff0000")) if "textColor" in keys else None
        self.pump(0.5)
        if "textColor" in keys:
            self.assertEqual(self.jsj("window.textColor"), "#ff0000")
        w.select_device("link_generic_lcd", "SizeS", "landscape")  # its own personalization (defaults)
        self.pump(0.5)
        if "textColor" in keys:
            self.assertEqual(self.jsj("window.textColor"), "#ffffff")
        # 3. Custom Style on: the page receives the stored values
        self.assertTrue(w.set_custom_style(True))
        self.pump(0.5)
        for key in keys:
            self.assertEqual(self.jsj(f"window[{key!r}]"), raw[key], key)
        self.assertFalse(any(panel.editors[k].isHidden() for k in WP_KEYS if k in panel.editors))
        # 4. live reload keeps Custom Style and the stored values
        self.assertTrue(w.reload_now())
        self.assertTrue(self.wait(lambda: w.reload_count == reloads + 1 and not w._reloading, 30))
        self.pump(1.5)
        self.assertEqual((inst.data[ENABLED], inst.data[PRESENT]), (True, True))
        for key in keys:
            self.assertEqual(self.jsj(f"window[{key!r}]"), raw[key], key)
        after = [e.message[:160] for e in w.console.matching("error", widget_only=True)]
        self.assertEqual([m for m in after if m not in baseline], [], "console errors added by personalization changes")
        return {"keys": keys, "differing": differing, "reloads_caused": w.reload_count - reloads - 1,
                "baseline_errors": [(classify_console_error(m), m) for m in baseline]}

    def test_basketball_scores(self) -> None:
        self.assertEqual(self.run_widget(BASKETBALL)["reloads_caused"], 0)

    def test_rss_feeder(self) -> None:
        self.assertEqual(self.run_widget(RSS)["reloads_caused"], 0)

    def test_smokeclock(self) -> None:
        self.assertEqual(self.run_widget(SMOKECLOCK)["reloads_caused"], 0)


if __name__ == "__main__":
    unittest.main()