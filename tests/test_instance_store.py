r"""Instance store tests (D2): ids, create / restore / merge semantics, independence of instances, runtime identity.

Expected behaviour comes from HtmlSettingsImpl.cpp (createWithDefaultsAsync, update, clone, setValue) and
SettingsFactoryImpl.cpp (restore = the stored data as it is).
Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import copy
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)

from test_runtime_bridge import WIDGET_HTML, PageTestCase, write_widget  # noqa: E402
from widgetdevtool.instance_store import (  # noqa: E402
    CUSTOM_STYLE_ENABLED_KEY, InstanceStore, WidgetInstance, update_settings_maps)
from widgetdevtool.main_window import MainWindow  # noqa: E402
from widgetdevtool.properties import parse_settings  # noqa: E402

DEFAULTS = {"speed": 10.0, "label": "hello", "media": {"pathToAsset": "", "scale": 1.0}}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class InstanceTests(unittest.TestCase):
    def test_every_instance_has_its_own_uuid_and_the_unique_id_is_not_the_type_id(self) -> None:
        a, b = WidgetInstance.create("com.x.type"), WidgetInstance.create("com.x.type")
        self.assertRegex(a.id, UUID_RE)
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(a.unique_id(), "{" + a.id + "}")
        self.assertEqual(a.unique_id(True), "{" + a.id + "}-preview")
        self.assertNotIn("com.x.type", a.unique_id())

    def test_create_stores_defaults_only_for_missing_keys(self) -> None:
        inst = WidgetInstance.create("t", DEFAULTS, data={"label": "given"})
        self.assertEqual(inst.data, {"speed": 10.0, "label": "given", "media": {"pathToAsset": "", "scale": 1.0}})

    def test_instances_of_one_type_do_not_share_state(self) -> None:
        a, b = WidgetInstance.create("t", DEFAULTS), WidgetInstance.create("t", DEFAULTS)
        a.set_value("speed", 99.0)
        a.data["media"]["pathToAsset"] = "x.png"
        self.assertEqual(b.data["speed"], 10.0)
        self.assertEqual(b.data["media"]["pathToAsset"], "")
        self.assertEqual(DEFAULTS["media"]["pathToAsset"], "")  # the defaults dict is copied, never aliased

    def test_set_value_is_a_noop_when_equal_notifies_and_never_clamps(self) -> None:
        inst = WidgetInstance.create("t", {"speed": 10.0, "flag": False})
        seen: list[tuple[str, object]] = []
        inst.subscribe(lambda k, v: seen.append((k, v)))
        self.assertFalse(inst.set_value("speed", 10))        # 10 == 10.0
        self.assertTrue(inst.set_value("speed", 5000.0))     # no range check: iCUE has none here
        self.assertTrue(inst.set_value("flag", 1))           # a bool never equals a number
        self.assertEqual(seen, [("speed", 5000.0), ("flag", 1)])
        self.assertEqual(inst.data["speed"], 5000.0)

    def test_setting_a_null_value_creates_the_key_without_a_notification(self) -> None:
        inst = WidgetInstance.create("t")
        seen: list = []
        inst.subscribe(lambda k, v: seen.append(k))
        self.assertFalse(inst.set_value("status", None))     # QMap::operator[] creates it, QVariant == QVariant
        self.assertIn("status", inst.data)
        self.assertEqual(seen, [])

    def test_clear_notifies_every_key_with_an_empty_value(self) -> None:
        inst = WidgetInstance.create("t", {"a": 1, "b": 2})
        seen: list = []
        inst.subscribe(lambda k, v: seen.append((k, v)))
        inst.clear()
        self.assertEqual((inst.data, seen), ({}, [("a", None), ("b", None)]))

    def test_clone_copies_data_with_a_new_id(self) -> None:
        a = WidgetInstance.create("t", DEFAULTS, name="N")
        b = a.clone()
        self.assertNotEqual(a.id, b.id)
        self.assertEqual((b.type_id, b.name, b.data), ("t", "N", a.data))
        b.data["media"]["scale"] = 3.0
        self.assertEqual(a.data["media"]["scale"], 1.0)


class RestoreAndMergeTests(unittest.TestCase):
    def old_instance(self) -> WidgetInstance:
        """An instance saved before the property 'speed' existed (and with a property that was removed since)."""
        return WidgetInstance.restore({"id": "11111111-1111-4111-8111-111111111111", "typeId": "t", "name": "Old",
                                       "data": {"label": "mine", "removed": "x", "media": {"scale": 2.0}}})

    def test_restore_adds_no_default_and_keeps_the_stored_data_exactly(self) -> None:
        inst = self.old_instance()
        self.assertEqual(inst.data, {"label": "mine", "removed": "x", "media": {"scale": 2.0}})
        self.assertNotIn("speed", inst.data)  # the widget would get no window.speed
        self.assertEqual((inst.id, inst.type_id, inst.name), ("11111111-1111-4111-8111-111111111111", "t", "Old"))

    def test_merge_adds_new_defaults_and_old_values_win(self) -> None:
        merged = WidgetInstance.merge(self.old_instance(), DEFAULTS)
        self.assertEqual(merged.id, "11111111-1111-4111-8111-111111111111")
        self.assertEqual(merged.data["speed"], 10.0)       # new key -> default
        self.assertEqual(merged.data["label"], "mine")     # existing key -> old value, not the new default
        self.assertNotIn("removed", merged.data)           # keys that only exist in the old data are dropped
        self.assertEqual(merged.data["media"], {"pathToAsset": "", "scale": 2.0})  # nested maps are merged

    def test_merge_does_not_modify_the_old_instance_or_the_defaults(self) -> None:
        old, defaults = self.old_instance(), copy.deepcopy(DEFAULTS)
        before = copy.deepcopy(old.data)
        merged = WidgetInstance.merge(old, defaults)
        merged.data["media"]["scale"] = 9.0
        self.assertEqual((old.data, defaults), (before, DEFAULTS))

    def test_merge_carries_the_custom_style_switch_even_when_not_declared(self) -> None:
        old = WidgetInstance.restore({"id": "i", "typeId": "t", "data": {CUSTOM_STYLE_ENABLED_KEY: True}})
        self.assertIs(WidgetInstance.merge(old, DEFAULTS).data[CUSTOM_STYLE_ENABLED_KEY], True)
        self.assertEqual(update_settings_maps({"a": 1}, {"b": 2, CUSTOM_STYLE_ENABLED_KEY: False}),
                         {"a": 1, CUSTOM_STYLE_ENABLED_KEY: False})

    def test_merge_with_another_type_keeps_only_the_id(self) -> None:
        merged = WidgetInstance.merge(self.old_instance(), DEFAULTS, type_id="other")
        self.assertEqual((merged.id, merged.type_id), ("11111111-1111-4111-8111-111111111111", "other"))
        self.assertEqual(merged.data, DEFAULTS)            # old values ignored
        self.assertEqual(merged.name, "")                  # update() copies only id and data

    def test_type_change_of_a_value_kind_prefers_the_old_value_as_icue_does(self) -> None:
        # old value is a string, the new default is a number: updateSettingsMaps() still takes the old one.
        old = WidgetInstance.restore({"id": "i", "typeId": "t", "data": {"speed": "fast"}})
        self.assertEqual(WidgetInstance.merge(old, {"speed": 10.0}).data["speed"], "fast")

    def test_drop_keys_simulates_an_instance_saved_before_a_property_existed(self) -> None:
        inst = WidgetInstance.create("t", DEFAULTS)
        inst.drop_keys("speed", "does-not-exist")
        self.assertNotIn("speed", inst.data)
        self.assertEqual(WidgetInstance.merge(inst, DEFAULTS).data["speed"], 10.0)


class StoreTests(unittest.TestCase):
    def test_store_keeps_instances_of_the_same_type_apart(self) -> None:
        store = InstanceStore()
        a, b = store.create("t", DEFAULTS), store.create("t", DEFAULTS)
        other = store.create("u", {"z": 1})
        a.set_value("label", "A")
        self.assertEqual([i.id for i in store.of_type("t")], [a.id, b.id])
        self.assertEqual((store.get(b.id).data["label"], store.get(other.id).data), ("hello", {"z": 1}))
        self.assertEqual(len(store), 3)
        store.remove(a.id)
        self.assertNotIn(a.id, store)

    def test_duplicate_ids_are_rejected(self) -> None:
        store = InstanceStore()
        inst = store.create("t")
        with self.assertRaises(ValueError):
            store.add(WidgetInstance.restore(inst.to_record()))

    def test_store_merge_replaces_the_instance_under_the_same_id(self) -> None:
        store = InstanceStore()
        old = store.add(WidgetInstance.restore({"id": "i1", "typeId": "t", "data": {"label": "mine"}}))
        merged = store.merge("i1", DEFAULTS)
        self.assertIsNot(merged, old)
        self.assertEqual((store.get("i1").data["label"], store.get("i1").data["speed"]), ("mine", 10.0))

    def test_store_clone_gets_a_new_id(self) -> None:
        store = InstanceStore()
        a = store.create("t", DEFAULTS)
        b = store.clone(a.id)
        self.assertEqual((len(store), a.data == b.data, a.id != b.id), (2, True, True))

    def test_save_and_load_is_a_restore_nothing_is_added_back(self) -> None:
        store = InstanceStore()
        a = store.create("t", DEFAULTS, name="A")
        a.drop_keys("speed")
        store.create("t", DEFAULTS)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "instances.json"
            store.save(path)
            loaded = InstanceStore.load(path)
        self.assertEqual([i.to_record() for i in loaded.all()], [i.to_record() for i in store.all()])
        self.assertNotIn("speed", loaded.get(a.id).data)

    def test_unknown_file_version_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            InstanceStore.from_json('{"version": 99, "instances": []}')


class RuntimeIdentityTests(PageTestCase):
    """The runtime bridge uses the instance: own uniqueId, stored data, stable across live reloads."""

    def widget(self) -> Path:
        return write_widget(Path(self._tmp.name) / "w")

    def second_window(self) -> MainWindow:
        window = MainWindow()
        self.addCleanup(lambda: (window.shutdown(), self.pump(0.2)))
        return window

    def js_of(self, window: MainWindow, script: str):
        box: dict = {}
        window.view.page().runJavaScript(script, lambda v: box.setdefault("v", v))
        self.assertTrue(self.wait(lambda: "v" in box, 10), script)
        return box["v"]

    def load_in(self, window: MainWindow, root: Path, **kwargs) -> None:
        self.assertTrue(window.load_folder(root, **kwargs))
        self.assertTrue(self.wait(lambda: window.state.name == "LOADED"), window.state)
        self.assertTrue(self.wait(lambda: self.js_of(window, "typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True))

    def test_two_instances_of_the_same_widget_get_different_unique_ids_and_independent_settings(self) -> None:
        root = self.widget()
        second = self.second_window()
        self.load(root)
        self.load_in(second, root)
        uid_a, uid_b = self.js("uniqueId"), self.js_of(second, "uniqueId")
        self.assertNotEqual(uid_a, uid_b)
        self.assertEqual(uid_a, self.window.bridge.instance.unique_id())
        self.assertRegex(uid_a.strip("{}"), UUID_RE)
        self.assertNotIn("com.test.bridge", uid_a)  # not derived from the type id
        self.window.bridge.set_properties({"refreshMinutes": 42})
        self.assertTrue(self.wait(lambda: self.js("refreshMinutes") == 42))
        self.assertEqual(self.js_of(second, "refreshMinutes"), 10)
        self.assertEqual(second.bridge.instance.data["refreshMinutes"], 10.0)
        self.assertEqual(self.window.bridge.instance.data["refreshMinutes"], 42)

    def test_unique_id_and_values_are_stable_across_live_reloads(self) -> None:
        root = self.widget()
        self.load(root)
        uid = self.js("uniqueId")
        instance_id = self.window.bridge.instance.id
        self.window.bridge.set_properties({"refreshMinutes": 7})
        for n in (1, 2):
            self.assertTrue(self.window.reload_now())
            self.assertTrue(self.wait(lambda: self.window.reload_count >= n and not self.window._reloading))
            self.pump(0.3)
            self.assertEqual(self.js("uniqueId"), uid)
            self.assertEqual(self.window.bridge.instance.id, instance_id)
            self.assertEqual(self.js("refreshMinutes"), 7)

    def test_restore_of_an_old_instance_leaves_the_missing_property_undefined(self) -> None:
        root = self.widget()
        old = WidgetInstance.restore({"id": "22222222-2222-4222-8222-222222222222", "typeId": "com.test.bridge",
                                      "data": {"label": "stored", "enabled": False}})
        self.load_in(self.window, root, instance=old)
        self.assertEqual(self.js("uniqueId"), "{22222222-2222-4222-8222-222222222222}")
        self.assertEqual(self.js("label"), "stored")
        self.assertEqual(self.js("typeof refreshMinutes"), "undefined")   # no default added on restore
        self.assertEqual(self.js("typeof accent"), "undefined")
        self.assertEqual(set(old.data), {"label", "enabled"})             # the instance was not touched

    def test_merged_instance_gets_the_new_defaults_and_keeps_the_stored_values(self) -> None:
        root = self.widget()
        defaults = parse_settings(WIDGET_HTML).defaults()
        old = WidgetInstance.restore({"id": "33333333-3333-4333-8333-333333333333", "typeId": "com.test.bridge",
                                      "data": {"label": "stored"}})
        merged = WidgetInstance.merge(old, defaults)
        self.load_in(self.window, root, instance=merged)
        self.assertEqual(self.js("uniqueId"), "{33333333-3333-4333-8333-333333333333}")
        self.assertEqual(self.js("label"), "stored")
        self.assertEqual(self.js("refreshMinutes"), 10)
        self.assertEqual(self.js("accent"), "#5b8cff")

    def test_a_pinned_instance_survives_live_reloads_with_its_data(self) -> None:
        root = self.widget()
        pinned = WidgetInstance.restore({"id": "44444444-4444-4444-8444-444444444444", "typeId": "t",
                                         "data": {"label": "pinned"}})
        self.load_in(self.window, root, instance=pinned)
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: self.window.reload_count >= 1 and not self.window._reloading))
        self.pump(0.3)
        self.assertIs(self.window.bridge.instance, pinned)
        self.assertEqual((self.js("label"), self.js("typeof refreshMinutes")), ("pinned", "undefined"))

    def test_set_properties_on_a_restored_instance_writes_into_it(self) -> None:
        root = self.widget()
        inst = WidgetInstance.restore({"id": "55555555-5555-4555-8555-555555555555", "typeId": "t", "data": {"label": "a"}})
        self.load_in(self.window, root, instance=inst)
        self.window.bridge.set_properties({"label": "b"})
        self.assertEqual(inst.data["label"], "b")


SRC_HTML = """<!doctype html><html><head><title>Src</title>
<meta name="x-icue-property" content="speed" data-type="slider" data-default="{speed}" data-min="0" data-max="100" data-step="1">
<meta name="x-icue-property" content="label" data-type="textfield" data-default="'{label}'">
{extra}
<script>icueEvents = {{ onICUEInitialized: function () {{}}, onDataUpdated: function () {{}} }};</script></head><body>x</body></html>"""


def src_html(speed=10, label="hello", extra="") -> str:
    return SRC_HTML.format(speed=speed, label=label, extra=extra)


class EditedTrackingTests(unittest.TestCase):
    def test_set_value_marks_the_key_as_user_edited_defaults_are_not(self) -> None:
        inst = WidgetInstance.create("t", DEFAULTS)
        self.assertEqual(inst.edited, set())
        inst.set_value("speed", 55.0)
        self.assertEqual(inst.edited, {"speed"})
        inst.clear()
        self.assertEqual(inst.edited, set())

    def test_refresh_from_source_replaces_defaults_and_keeps_edited_values(self) -> None:
        inst = WidgetInstance.create("t", {"a": 1, "b": 2, "gone": 3})
        inst.set_value("a", 100)
        report = inst.refresh_from_source({"a": 1, "b": 20, "new": 4})
        self.assertEqual(inst.data, {"a": 100, "b": 20, "new": 4})
        self.assertEqual(report, {"added": ["new"], "dropped": ["gone"], "defaults_changed": ["b"], "user_values_kept": ["a"]})

    def test_an_edited_key_that_is_no_longer_declared_is_dropped_with_its_mark(self) -> None:
        inst = WidgetInstance.create("t", {"a": 1})
        inst.set_value("a", 5)
        inst.refresh_from_source({"b": 2})
        self.assertEqual((inst.data, inst.edited), ({"b": 2}, set()))

    def test_edited_survives_record_round_trip_clone_and_merge(self) -> None:
        inst = WidgetInstance.create("t", {"a": 1, "b": 2})
        inst.set_value("a", 9)
        self.assertEqual(WidgetInstance.restore(inst.to_record()).edited, {"a"})
        self.assertEqual(inst.clone().edited, {"a"})
        self.assertEqual(WidgetInstance.merge(inst, {"a": 1, "c": 3}).edited, {"a"})
        self.assertEqual(WidgetInstance.merge(inst, {"c": 3}).edited, set())
        old_file = {"id": "x", "typeId": "t", "data": {"a": 1}}  # a file written before `edited` existed
        self.assertEqual(WidgetInstance.restore(old_file).edited, set())

    def test_the_instance_data_is_not_shared_with_the_defaults_dict(self) -> None:
        defaults = {"m": {"x": 1}}
        inst = WidgetInstance.create("t", defaults)
        inst.refresh_from_source(defaults)
        inst.data["m"]["x"] = 2
        self.assertEqual(defaults, {"m": {"x": 1}})


class SourceOwnershipTests(PageTestCase):
    """One owner of the settings values (the WidgetInstance): the bridge payload, the panel and a live reload all follow it."""

    def root(self, **kw) -> Path:
        return write_widget(Path(self._tmp.name) / "w", html=src_html(**kw))

    def start(self, root: Path, **kwargs) -> None:
        self.assertTrue(self.window.load_folder(root, **kwargs))
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        self.assertTrue(self.wait(lambda: self.js("typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True))
        self.pump(0.3)

    def reload_with(self, root: Path, **kw) -> None:
        write_widget(root, html=src_html(**kw))
        count = self.window.reload_count
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: self.window.reload_count == count + 1 and not self.window._reloading))
        self.pump(0.3)

    def test_bridge_payload_is_derived_from_the_instance(self) -> None:
        root = self.root()
        self.start(root)
        bridge, inst = self.window.bridge, self.window.bridge.instance
        self.assertFalse(hasattr(bridge, "overrides"))
        self.assertEqual({k: v for k, v in bridge.data.items() if k not in ("url", "typeId")}, inst.data)
        bridge.set_properties({"speed": 70})
        self.assertEqual((inst.data["speed"], bridge.data["speed"]), (70, 70))
        inst.data["label"] = "direct"  # whatever the instance holds is what a later payload says
        self.assertEqual(bridge._payload()["label"], "direct")

    def test_live_reload_takes_new_source_defaults_but_keeps_user_edits_and_says_so(self) -> None:
        root = self.root(speed=10, label="hello")
        self.start(root)
        inst = self.window.bridge.instance
        self.window.settings_panel.editors["speed"].control.setValue(42)  # the user edits speed only
        self.assertTrue(self.wait(lambda: self.js("speed") == 42))
        self.reload_with(root, speed=20, label="changed in source")
        self.assertIs(self.window.bridge.instance, inst)
        self.assertEqual((self.js("speed"), self.js("label")), (42, "changed in source"))
        self.assertEqual((inst.data["speed"], inst.edited), (42.0, {"speed"}))
        self.assertEqual(self.window.settings_panel.editors["label"].control.text(), "changed in source")
        notes = [e.message for e in self.window.console.matching(contains="live reload -")]
        self.assertIn("live reload - new defaults from source: label", notes)
        self.assertIn("live reload - user-edited, kept: speed", notes)

    def test_a_property_added_or_removed_in_the_source_is_followed(self) -> None:
        root = self.root()
        self.start(root)
        self.window.bridge.set_properties({"label": "mine"})
        extra = '<meta name="x-icue-property" content="added" data-type="switch" data-default="true">'
        self.reload_with(root, extra=extra)
        self.assertEqual((self.js("added"), self.js("label")), (True, "mine"))
        self.reload_with(root)  # `added` is gone again
        self.assertEqual(self.js("typeof added"), "undefined")
        self.assertNotIn("added", self.window.bridge.instance.data)

    def test_restored_instance_is_not_refreshed_by_a_live_reload(self) -> None:
        root = self.root(speed=10)
        pinned = WidgetInstance.restore({"id": "77777777-7777-4777-8777-777777777777", "typeId": "t",
                                         "data": {"label": "stored"}})
        self.start(root, instance=pinned)
        self.reload_with(root, speed=99)
        self.assertEqual((self.js("label"), self.js("typeof speed")), ("stored", "undefined"))
        self.assertEqual(pinned.data, {"label": "stored"})

    def test_a_new_instance_gets_a_new_object_and_its_own_edit_marks(self) -> None:
        root = self.root()
        self.start(root)
        first = self.window.bridge.instance
        self.window.bridge.set_properties({"speed": 5})
        self.assertTrue(self.window.new_instance())
        self.assertTrue(self.wait(lambda: self.window.state.name == "LOADED"))
        second = self.window.bridge.instance
        self.assertIsNot(first, second)
        self.assertEqual((first.edited, second.edited), ({"speed"}, set()))
        self.assertEqual(self.window.store.get(second.id), second)

    def test_app_status_is_metadata_only_no_key_no_runtime_value(self) -> None:
        """storesNullKey stays metadata: how iCUE passes its null QVariant to the widget is not verified, so nothing is emitted."""
        status = '<meta name="x-icue-property" content="conn" data-type="app-status" data-label="\'S\'" application="stream-deck">'
        root = self.root(extra=status)
        self.start(root)
        param = next(p for p in self.window.bridge.settings_spec.parameters if p.name == "conn")
        self.assertTrue(param.meta["storesNullKey"])
        self.assertFalse(param.has_value)
        self.assertNotIn("conn", self.window.bridge.instance.data)
        self.assertEqual(self.js("typeof conn"), "undefined")
        self.reload_with(root, extra=status)
        self.assertEqual(self.js("typeof conn"), "undefined")
        self.assertNotIn("conn", self.window.bridge.instance.data)
        self.assertFalse(self.window.settings_panel.editors["conn"].supported)


if __name__ == "__main__":
    unittest.main()