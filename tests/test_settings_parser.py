r"""Settings parser tests (D1): data-type -> stored default, metadata, groups.

Expected values come from the iCUE source (HtmlParser.cpp parse<Type>, EditorSettingsParameters.h toSetting()).
Cases that rest on Qt API semantics instead of iCUE code say so in their name or comment.
Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
from html import escape
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgetdevtool.properties import (  # noqa: E402
    compute_defaults, manifest_modules, parse_list, parse_map_or_list, parse_properties, parse_settings)


def page(*metas: str, title: str = "Demo", groups: str = "") -> str:
    script = f'<script type="application/json" id="x-icue-groups">{groups}</script>' if groups else ""
    return f"<html><head><title>{title}</title>{''.join(metas)}{script}</head><body></body></html>"


def meta(name: str, data_type: str, **attrs: str) -> str:
    extra = "".join(f' {k.replace("_", "-")}="{escape(v)}"' for k, v in attrs.items())
    return f'<meta name="x-icue-property" content="{name}" data-type="{data_type}"{extra}>'


class ParserTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def one(self, data_type: str, translate=lambda s: s, **attrs: str):
        settings = parse_settings(page(meta("p", data_type, **attrs)), translate)
        param = settings.parameter("p")
        self.assertIsNotNone(param, settings.notes)
        return param


class ValueTypeTests(ParserTestCase):
    def test_slider_defaults_and_metadata(self) -> None:
        p = self.one("slider", data_label="'Speed'", data_default="10", data_min="1", data_max="60", data_step="5",
                     data_unit_label="' min'")
        self.assertEqual((p.has_value, p.kind, p.default), (True, "value", 10.0))
        self.assertIsInstance(p.default, float)  # SliderEditorSettingParameter::value is a double
        self.assertEqual((p.meta["min"], p.meta["max"], p.meta["step"], p.meta["unitLabel"]), (1.0, 60.0, 5.0, " min"))
        self.assertEqual(p.label, "Speed")

    def test_slider_uses_qstring_toint_and_is_never_clamped(self) -> None:
        for raw, expected in (("abc", 0), ("10.5", 0), ("+5", 5), (" 7 ", 7), ("", 0), ("99999999999", 0), ("-3", -3)):
            with self.subTest(raw=raw):
                self.assertEqual(self.one("slider", data_default=raw, data_min="0", data_max="10").default, expected)
        p = self.one("slider", data_default="500", data_min="0", data_max="10", data_step="1")
        self.assertEqual(p.default, 500.0)  # outside [min, max]: iCUE keeps it (only import validation complains)

    def test_switch_is_case_sensitive(self) -> None:
        self.assertIs(self.one("switch", data_default="true").default, True)
        self.assertIs(self.one("switch", data_default="True").default, False)
        self.assertIs(self.one("switch", data_default="false").default, False)

    def test_textfield_evaluates_the_js_default(self) -> None:
        self.assertEqual(self.one("textfield", data_default="'hello'").default, "hello")
        self.assertEqual(self.one("textfield", data_default="'a' + 'b'").default, "ab")
        self.assertEqual(self.one("textfield").default, "")  # missing attribute -> undefined -> ""

    def test_non_string_js_results_become_empty_qt_semantics(self) -> None:
        # toJsFormat() ends in QJsonValue::toString(): "" for numbers, booleans and null (Qt semantics).
        for expr in ("10", "true", "null", "undefined"):
            with self.subTest(expr=expr):
                self.assertEqual(self.one("textfield", data_default=expr).default, "")

    def test_js_error_is_reported_and_no_default_is_emitted(self) -> None:
        settings = parse_settings(page(meta("p", "textfield", data_default="nope()")))
        p = settings.parameter("p")
        self.assertFalse(p.default_valid)
        self.assertNotIn("p", settings.defaults())
        self.assertTrue(any("nope is not defined" in n for n in settings.notes))

    def test_color_is_a_normalised_name_and_invalid_colors_are_reported(self) -> None:
        self.assertEqual(self.one("color", data_default="'#5B8CFF'").default, "#5b8cff")
        bad = parse_settings(page(meta("p", "color", data_default="'not-a-color'")))
        self.assertFalse(bad.parameter("p").default_valid)
        self.assertNotIn("p", bad.defaults())
        self.assertTrue(any("not a valid color" in n for n in bad.notes))

    def test_colors_keeps_one_entry_per_value(self) -> None:
        p = self.one("colors", data_values='["#ff0000","#00ff00","bogus"]')
        self.assertEqual(p.default, ["#ff0000", "#00ff00", None])  # an invalid QColor is still pushed
        self.assertEqual(self.one("colors", data_values="[]").default, [])

    def test_media_selector_default_is_a_map_and_filters_are_kept(self) -> None:
        p = self.one("media-selector", data_filters='["image","video","gif"]')
        self.assertEqual(p.default, {"pathToAsset": "", "scale": 1.0, "positionX": 0.0, "positionY": 0.0, "angle": 0})
        self.assertEqual(p.meta["filters"], ["image", "video", "gif"])
        self.assertEqual(self.one("media-selector", data_filters="[]").meta["filters"], [])  # <= 2 chars: ignored

    def test_time_format_reads_two_bools(self) -> None:
        p = self.one("time-format", data_default='JSON.stringify({time12hourFormat:true,amPmEnable:false})')
        self.assertEqual(p.default, {"time12hourFormat": True, "amPmEnable": False})
        self.assertTrue(p.meta["amPmControlShow"])
        self.assertFalse(self.one("time-format", data_default="'{}'", data_one_more_name="FALSE").meta["amPmControlShow"])
        self.assertEqual(self.one("time-format", data_default="'oops'").default,
                         {"time12hourFormat": False, "amPmEnable": False})

    def test_remaining_value_types_store_their_empty_defaults(self) -> None:
        self.assertEqual(self.one("sensors-combobox", data_default="'cpu/temp'").default, "cpu/temp")
        p = self.one("sensors-factory", data_default="'cpu'")
        self.assertEqual((p.default, p.meta["defaultSensorId"]), ([], "cpu"))  # "default" is the id of Add Sensor
        self.assertEqual(self.one("accounts-list").default, [])
        self.assertEqual(self.one("stream-deck-virtual-device").default, "")
        for t in ("sensors-combobox", "sensors-factory", "accounts-list", "stream-deck-virtual-device"):
            self.assertTrue(self.one(t, data_default="'x'").has_value, t)

    def test_search_combobox_keeps_search_metadata(self) -> None:
        p = self.one("search-combobox", data_values="Provider.search", data_placeholder="'Find...'", data_default="'x'")
        self.assertEqual((p.default, p.unresolved), ("", False))
        self.assertEqual((p.meta["placeholder"], p.meta["searchFunc"], p.meta["displayValueKey"]),
                         ("Find...", "Provider.search", "pDisplayValue"))

    def test_search_combobox_provider_default_is_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp) / "Finder.mjs"
            mod.write_text("export function first() { return 'a'; }", encoding="utf-8")
            settings = parse_settings(page(meta("p", "search-combobox", data_values="Finder.first", data_default="Finder.first")),
                                      modules=[mod])
            p = settings.parameter("p")
            self.assertTrue(p.unresolved)
            self.assertIn("p", settings.defaults())
            self.assertTrue(any("provider call" in n for n in settings.notes))


class PluginDefaultTests(ParserTestCase):
    """JavaScriptExecutor::importPlugin: a REQUIRED plugin exists as plugins.<Name> while the settings are parsed (Sensor Bar's
    `plugins.Sensorsdataprovider.getDefaultSensorIdBlock('temperature')`). The values come from the WidgetDevTool MOCK catalog."""

    SENSOR_META = ('<meta name="x-icue-property" content="s" data-type="sensors-factory" '
                   'data-default="plugins.Sensorsdataprovider.getDefaultSensorIdBlock({args})">')

    def parse(self, args: str, plugins=None):
        from widgetdevtool.sensors_mock import PARSE_STUBS
        html = f"<html><head><title>t</title>{self.SENSOR_META.format(args=args)}</head></html>"
        return parse_settings(html, plugins=PARSE_STUBS if plugins is None else plugins).parameter("s")

    def test_without_the_plugin_the_default_fails_as_before(self) -> None:
        p = self.parse("'temperature'", plugins={})
        self.assertIn("ReferenceError: plugins is not defined", p.meta["evalErrors"]["data-default"])
        self.assertEqual((p.meta["defaultSensorId"], p.default, p.default_valid), ("", [], False))

    def test_the_mock_plugin_answers_the_default_sensor_id(self) -> None:
        from widgetdevtool.sensors_mock import _sid
        self.assertEqual(self.parse("'temperature'").meta["defaultSensorId"], _sid(1))
        self.assertEqual(self.parse("'load', 'gpu-load'").meta["defaultSensorId"], _sid(4))  # preferred kind first
        self.assertEqual(self.parse("'fan'").meta["defaultSensorId"], _sid(5))
        self.assertEqual(self.parse("'no-such-type'").meta["defaultSensorId"], "")
        p = self.parse("'temperature'")
        self.assertEqual((p.default, p.default_valid), ([], True))  # the stored value is still the EMPTY list (toSetting = values)


class OptionTests(ParserTestCase):
    def test_combobox_accepts_list_map_and_pairs(self) -> None:
        lst = self.one("combobox", data_values='["b","a"]', data_default="'a'")
        self.assertEqual(lst.meta["options"], [{"key": "b", "value": "b"}, {"key": "a", "value": "a"}])
        self.assertEqual(lst.default, "a")
        mp = self.one("combobox", data_values="""'{"z":"Zed","a":"Ay"}'""", data_default="'a'")
        self.assertEqual([o["key"] for o in mp.meta["options"]], ["a", "z"])  # QVariantMap: sorted by key
        pairs = self.one("combobox", data_values='[{"key":"k2","value":"Two"},{"key":"k1","value":"One"}]')
        self.assertEqual(pairs.meta["options"], [{"key": "k2", "value": "Two"}, {"key": "k1", "value": "One"}])
        self.assertEqual(pairs.meta["optionsForm"], "pairs")

    def test_object_literal_values_are_a_js_syntax_error_like_in_icue(self) -> None:
        # evalJsExpressionProperty() evaluates the text without parentheses: "{...}" parses as a block.
        settings = parse_settings(page(meta("p", "combobox", data_values='{"a":"A"}', data_default="'a'")))
        p = settings.parameter("p")
        self.assertEqual((p.meta["options"], p.default, p.default_valid), ([], "a", True))
        self.assertTrue(any("data-values failed to evaluate" in n for n in settings.notes))

    def test_combobox_default_is_not_checked_against_the_options(self) -> None:
        p = self.one("combobox", data_values='["a","b"]', data_default="'zzz'")
        self.assertEqual(p.default, "zzz")  # iCUE stores it; the ComboBox then has currentIndex -1

    def test_values_that_are_not_a_json_array_or_object_give_no_options(self) -> None:
        for raw in ("auto", "[]", "{}", "5"):
            with self.subTest(raw=raw):
                self.assertEqual(self.one("combobox", data_values=f"'{raw}'", data_default="'x'").meta["options"], [])

    def test_option_quirks_of_parsList_are_reproduced(self) -> None:
        self.assertEqual(parse_list('["a",1,{"b":2}]'), ["a", "{}", '{"b":2}'])  # toObject() of a number is {}
        self.assertEqual(parse_map_or_list('[1,2]'), ("list", []))
        self.assertEqual(parse_map_or_list('{"a":1,"b":{"c":3}}'), ("map", {"a": "{}", "b": '{"c":3}'}))
        self.assertIsNone(parse_list("[]"))
        self.assertEqual(parse_map_or_list("not json"), ("raw", "not json"))

    def test_each_type_only_takes_the_option_forms_iCUE_reads(self) -> None:
        values = '{"a":"A"}'
        self.assertEqual(self.one("tab-buttons", data_values=values, data_default="'a'").meta["options"], [])
        self.assertEqual(self.one("slider-enum", data_values='[{"key":"a","value":"A"}]', data_default="'a'").meta["options"], [])
        self.assertEqual(self.one("tab-buttons", data_values='[{"key":"a","value":"A"}]', data_default="'a'").meta["options"],
                         [{"key": "a", "value": "A"}])
        se = self.one("slider-enum", data_values='["slow","fast"]', data_default="'slow'")
        self.assertEqual((se.default, [o["key"] for o in se.meta["options"]]), ("slow", ["slow", "fast"]))


class StatusAndUnsupportedTests(ParserTestCase):
    def test_app_status_is_display_only_and_creates_no_value(self) -> None:
        settings = parse_settings(page(meta("conn", "app-status", application="stream-deck"), meta("n", "textfield",
                                                                                                  data_default="'x'")))
        p = settings.parameter("conn")
        self.assertEqual((p.has_value, p.kind), (False, "status"))
        self.assertEqual(p.meta["application"], "stream-deck")
        self.assertTrue(p.meta["storesNullKey"])  # iCUE creates a null key; this tool generates no value
        self.assertEqual(settings.defaults(), {"n": "x"})

    def test_media_session_and_unknown_types_create_no_parameter(self) -> None:
        settings = parse_settings(page(meta("m", "media-session"), meta("u", "made-up"), meta("ok", "switch",
                                                                                               data_default="true")))
        self.assertEqual([p.name for p in settings.parameters], ["ok"])
        self.assertEqual({s["name"] for s in settings.skipped}, {"m", "u"})
        self.assertEqual(len(settings.specs), 3)
        self.assertTrue(any("'made-up'" in n for n in settings.notes))

    def test_info_meta_is_parsed_like_a_property(self) -> None:
        html = page('<meta name="x-icue-info" content="hint" data-type="textfield" data-default="\'x\'">')
        self.assertEqual([s.name for s in parse_properties(html)], ["hint"])
        self.assertEqual(parse_settings(html).defaults(), {"hint": "x"})


class GroupAndMetadataTests(ParserTestCase):
    GROUPS = ('[{"title":"tr(\'Appearance\')","properties":["color1","size"],"info":"\'Look\'","description":"\'How it looks\'"},'
              '{"title":"Extra","properties":["missing","other"]}]')

    def settings(self):
        html = page(meta("size", "slider", data_label="tr('Size')", data_default="3", data_min="1", data_max="9", data_step="1"),
                    meta("color1", "color", data_default="'#ffffff'"),
                    meta("other", "switch", data_default="true"),
                    meta("lonely", "textfield", data_default="'x'"),
                    title="My Widget", groups=self.GROUPS)
        return parse_settings(html, lambda s: {"Appearance": "Aspecto", "Size": "Tamano"}.get(s, s))

    def test_groups_follow_the_json_and_keep_their_texts(self) -> None:
        g = self.settings().groups
        self.assertEqual([x.title for x in g], ["Aspecto", "Extra", "My Widget"])
        self.assertEqual((g[0].info, g[0].description), ("Look", "How it looks"))
        self.assertEqual([p.name for p in g[0].parameters], ["color1", "size"])  # order of the JSON, not of the HTML
        self.assertEqual([p.name for p in g[1].parameters], ["other"])  # unknown property names are ignored
        self.assertEqual([p.name for p in g[2].parameters], ["lonely"])  # leftovers -> group named like <title>

    def test_parameters_keep_label_group_attrs_and_limits(self) -> None:
        s = self.settings()
        size = s.parameter("size")
        self.assertEqual((size.label, size.group, size.meta["min"], size.meta["max"]), ("Tamano", "Aspecto", 1.0, 9.0))
        self.assertEqual(size.attrs["data-step"], "1")
        self.assertEqual(s.parameter("lonely").group, "My Widget")

    def test_leftovers_join_the_group_whose_title_equals_the_widget_title(self) -> None:
        html = page(meta("a", "switch", data_default="true"), meta("b", "switch", data_default="false"), title="Demo",
                    groups='[{"title":"Demo","properties":["a"]}]')
        groups = parse_settings(html).groups
        self.assertEqual(len(groups), 1)
        self.assertEqual([p.name for p in groups[0].parameters], ["a", "b"])

    def test_no_groups_json_gives_one_default_group_and_bad_json_is_ignored(self) -> None:
        for groups in ("", "not json", "[]", '{"title":"x"}'):
            with self.subTest(groups=groups):
                s = parse_settings(page(meta("a", "switch", data_default="true"), title="T", groups=groups))
                self.assertEqual([(g.title, [p.name for p in g.parameters]) for g in s.groups], [("T", ["a"])])

    def test_a_property_listed_in_two_groups_stays_in_the_first(self) -> None:
        html = page(meta("a", "switch", data_default="true"),
                    groups='[{"title":"One","properties":["a"]},{"title":"Two","properties":["a"]}]')
        g = parse_settings(html).groups
        self.assertEqual([len(x.parameters) for x in g], [1, 0])

    def test_manifest_modules_feed_defaults_and_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m.mjs").write_text("export const key = () => 'auto'; export const label = () => tr('Hi');", encoding="utf-8")
            mods = manifest_modules({"modules": ["m.mjs"]}, root / "index.html")
            s = parse_settings(page(meta("t", "textfield", data_default="m.key()", data_label="m.label()")),
                               lambda x: x.upper(), mods)
            self.assertEqual((s.parameter("t").default, s.parameter("t").label), ("auto", "HI"))


class CompatibilityTests(ParserTestCase):
    def test_compute_defaults_still_returns_defaults_and_notes(self) -> None:
        specs = parse_properties(page(meta("a", "slider", data_default="4", data_min="0", data_max="9", data_step="1"),
                                      meta("b", "app-status"), meta("c", "zzz")))
        defaults, notes = compute_defaults(specs, lambda s: s)
        self.assertEqual(defaults, {"a": 4})
        self.assertTrue(any("'zzz'" in n for n in notes))


if __name__ == "__main__":
    unittest.main()