r"""iCUE instance importer tests (E2). Fixtures are generated here with FAKE data only (fake tokens, `.invalid` URLs): no real iCUE data is stored.

The fixture builder mirrors the cereal XML layout read from the iCUE v5.55 serializers (DashLCDStorage_s, DeviceSettings_s, ScreenSettings_s, Widget_s,
WidgetBuilderWidgetAttachment_s, HtmlSettingsData_s, SettingsData_s) and checked against a real 5.52.93 file (structure only).
Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import base64
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QByteArray, QDataStream, QIODevice  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtQml import QJSEngine  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)
from PySide6.QtWidgets import QApplication  # noqa: E402

from test_runtime_bridge import PageTestCase  # noqa: E402
from widgetdevtool import icue_import as ii  # noqa: E402
from widgetdevtool.instance_store import InstanceStore  # noqa: E402
from widgetdevtool.main_window import MainWindow, State  # noqa: E402
from widgetdevtool.widget_library import load_entry  # noqa: E402

SECRET_URL = "https://private.example.invalid/feed?token=SECRETTOKEN123"
SECRET_TOKEN = "FAKE-TOKEN-0123456789abcdef"
SECRETS = ("SECRETTOKEN123", "FAKE-TOKEN-0123456789abcdef", "private.example.invalid")
DEVICE, SCREEN_A, SCREEN_B = "{aaaaaaaa-0000-4000-8000-000000000001}", "{bbbbbbbb-0000-4000-8000-00000000000a}", "{bbbbbbbb-0000-4000-8000-00000000000b}"


def enc(value) -> str:
    """Python value -> cereal text of `QDataStream << QVariant` (standard base64, no padding)."""
    if isinstance(value, bytes):
        raw = value
    else:
        ba = QByteArray()
        stream = QDataStream(ba, QIODevice.OpenModeFlag.WriteOnly)
        stream.setVersion(QDataStream.Version.Qt_6_5)
        stream.writeQVariant(value)
        raw = bytes(ba)
    return base64.b64encode(raw).decode().rstrip("=")


NULL_VARIANT = b"\x00\x00\x00\x00\x01"
UNKNOWN_TYPE = b"\x00\x00\xff\xf0\x00\x01\x02\x03"


def ptr(inner: str, pid: int, poly: str = "", poly_id: int | None = None, name: str | None = None) -> str:
    head = f"<polymorphic_id>{poly_id if poly_id is not None else 1073741824}</polymorphic_id>"
    if name:
        head += f"<polymorphic_name>{name}</polymorphic_name>"
    return head + f"<ptr_wrapper><id>{pid}</id><data>{inner}</data></ptr_wrapper>"


def settings_xml(settings_id: str, type_id: str, values: dict, version: int, first: bool, location="UserStorage", path="com/test/rss/index.html") -> str:
    items = "".join(f"<value{i}><key>{k}</key><value>{enc(v)}</value></value{i}>" for i, (k, v) in enumerate(values.items()))
    ver = f"<cereal_class_version>{version}</cereal_class_version>" if first else ""
    return (ver + f"<base><cereal_class_version>500</cereal_class_version><id>{settings_id}</id><typeId>{type_id}</typeId></base><name></name>"
            f"<pathData><location>{location}</location><path>{path}</path></pathData><data size=\"dynamic\">{items}</data>")


def widget_xml(widget_id, slot, size, attach_id, settings: str | None, ids: dict, first_widget: bool, kind="builder", html_ids=(0, 0)) -> str:
    n = ids["n"]
    ids["n"] += 3
    if kind == "builder":
        first = ids.get("builder") is None
        ids.setdefault("builder", 1)
        poly = ptr(f"<base><id>{attach_id}</id></base><settings>{settings}</settings>", n + 1, poly_id=(2147483649 if first else 1),
                   name="cue::dashlcd::WidgetBuilderWidgetAttachment" if first else None)
    else:
        first = ids.get("default") is None
        ids["default"] = 3
        poly = ptr(f"<base><id>{attach_id}</id></base>", n + 1, poly_id=(2147483651 if first else 3),
                   name="cue::dashlcd::DefaultWidgetAttachment" if first else None)
    ver = "<cereal_class_version>501</cereal_class_version>" if first_widget else ""
    return ptr(f"{ver}<id>{widget_id}</id><slotIndex>{slot}</slotIndex><size>{size}</size><attachment>{poly}</attachment>", n)


def personalization_xml(text="rgb(1 1 1)", accent="rgb(0.941176 0.556863 0.2)", transparency=80) -> str:
    return (f"<imageSource></imageSource><backgroundColor>rgb(0 0 0)</backgroundColor><textColor>{text}</textColor><accentColor>{accent}</accentColor>"
            f"<widgetBackgroundColor>rgb(0 0 0)</widgetBackgroundColor><widgetTransparency>{transparency}</widgetTransparency><typeIndex>0</typeIndex>")


BASE_VALUES = {"label": SECRET_URL, "apiToken": SECRET_TOKEN, "level": 7.0, "accent": QColor("#ff8800"), "ghost": QColor(0x10, 0x20, 0x30, 0x80),
               "flag": True, "count": 3, "nothing": NULL_VARIANT, "broken": UNKNOWN_TYPE, "media": {"angle": 0, "scale": 1.0, "pathToAsset": "C:\\private\\pic.png"},
               "widgetPersonalizationCustomStyleEnabled": True, "widgetPersonalizationCustomStylePresent": True}


def build_storage(html_version: int = 505, device_text="rgb(0.0705882 0.141176 0.2)") -> str:
    ids = {"n": 2147483700}
    w1 = widget_xml("{c0000001-0000-4000-8000-000000000001}", 0, 2, "{d0000001-0000-4000-8000-000000000001}",
                    ptr(settings_xml("{d0000001-0000-4000-8000-000000000001}", "com.test.rss", BASE_VALUES, html_version, True), 2147483800,
                        poly_id=2147483650, name="cue::widgets::data::HtmlSettingsData"), ids, True)
    ids["settings_seen"] = True
    w2 = widget_xml("{c0000002-0000-4000-8000-000000000002}", 1, 6, "{d0000002-0000-4000-8000-000000000002}",
                    ptr(settings_xml("{d0000002-0000-4000-8000-000000000002}", "com.test.rss", {**BASE_VALUES, "level": 3.0}, html_version, False), 2147483801, poly_id=2),
                    ids, False)
    w3 = widget_xml("{c0000003-0000-4000-8000-000000000003}", 2, 1, "{d0000003-0000-4000-8000-000000000003}",
                    ptr(settings_xml("{d0000003-0000-4000-8000-000000000003}", "com.test.unknown", {"x": 1.0}, html_version, False,
                                     path="com/test/unknown/index.html"), 2147483802, poly_id=2), ids, False)
    w4 = widget_xml("{c0000004-0000-4000-8000-000000000004}", 4, 1, "{d0000004-0000-4000-8000-000000000004}", None, ids, False, kind="default")
    shared = "<polymorphic_id>1073741824</polymorphic_id><ptr_wrapper><id>2147483777</id></ptr_wrapper>"  # repeated pointer: no <data>

    def screen(screen_id, widgets, pers, pid):
        return ptr(f"<widgets size=\"dynamic\">" + "".join(f"<value{i}>{w}</value{i}>" for i, w in enumerate(widgets)) + f"</widgets><screenId>{screen_id}</screenId>"
                   f"<name></name><personalizationSettings>{ptr(personalization_xml(*pers), pid + 1)}</personalizationSettings>", pid)
    screens = [screen(SCREEN_A, [w1, w3, w4], ("rgb(1 1 1)", "rgb(1 0 0)", 70), 2147483900), screen(SCREEN_B, [w2], ("rgb(0 1 0)", "rgb(0 0 1)", 55), 2147483910)]
    device = ptr("<cereal_class_version>501</cereal_class_version><screenCollection>" + ptr(
        "<cereal_class_version>501</cereal_class_version><dashLCDScreens size=\"dynamic\">"
        + "".join(f"<value{i}>{s}</value{i}>" for i, s in enumerate(screens)) + f"<value2>{shared}</value2></dashLCDScreens><activeScreenId>{SCREEN_B}</activeScreenId>",
        2147483890) + "</screenCollection><devicePersonalizationSettings>" + ptr(personalization_xml(device_text, "rgb(0.941176 0.556863 0.2)", 80), 2147483895)
        + "</devicePersonalizationSettings><themeByScreenEnabled>true</themeByScreenEnabled>", 2147483880)
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<cereal><value0><cereal_class_version>500</cereal_class_version><devices size=\"dynamic\">"
            f"<value0><key>{DEVICE}</key><value>{device}</value></value0></devices></value0></cereal>")


LIB_HTML = """<!doctype html><html><head><title>Rss</title>
<meta name="x-icue-property" content="label" data-type="textfield" data-default="'default label'">
<meta name="x-icue-property" content="level" data-type="slider" data-default="5" data-min="0" data-max="10" data-step="1">
<meta name="x-icue-property" content="accent" data-type="color" data-default="'#112233'">
<meta name="x-icue-property" content="extra" data-type="textfield" data-default="'new in this version'">
<script>window.__log = []; icueEvents = { onICUEInitialized: function () { window.__log.push('init'); }, onDataUpdated: function () { window.__log.push('upd'); } };</script>
</head><body>probe</body></html>"""


def write_library_widget(root: Path, type_id="com.test.rss", version="2.0.5") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps({"id": type_id, "name": "Test Rss", "version": version}), encoding="utf-8")
    (root / "index.html").write_text(LIB_HTML, encoding="utf-8")
    return root

class DecoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def decode(self, value):
        return ii.decode_value("k", enc(value))

    def test_every_supported_type_round_trips_with_its_qt_type(self) -> None:
        for value, expected, qtype in ((True, True, 1), (7, 7, 2), (3.5, 3.5, 6), ("hi", "hi", 10), ("", "", 10), ([1, "a"], [1, "a"], 9),
                                       ({"a": 1.0, "b": "x"}, {"a": 1.0, "b": "x"}, 8)):
            with self.subTest(value=value):
                got = self.decode(value)
                self.assertEqual((got.status, got.value, got.qtype), ("ok", expected, qtype))
        self.assertEqual(type(self.decode(7.0).value), float)  # doubles stay doubles (the real file stores refreshMinutes as 10.0)

    def test_colors_become_the_text_the_widget_gets_and_match_qt(self) -> None:
        engine = QJSEngine()
        for color in (QColor("#5b8cff"), QColor(0x10, 0x20, 0x30, 0x80), QColor("#000000"), QColor(255, 255, 255, 1)):
            with self.subTest(color=color.name(QColor.NameFormat.HexArgb)):
                got = self.decode(color)
                self.assertEqual(got.status, "ok")
                self.assertEqual(got.value, engine.toScriptValue(color).toString())  # toQmlString -> QVariant::toString() for a QColor
        self.assertEqual(self.decode(QColor("#5b8cff")).value, "#5b8cff")
        self.assertEqual(self.decode(QColor(0x10, 0x20, 0x30, 0x80)).value, "#80102030")

    def test_null_undecodable_and_missing_are_three_different_things(self) -> None:
        null = ii.decode_value("k", enc(NULL_VARIANT))
        self.assertEqual((null.status, null.value, null.qtype), ("null", None, 0))
        for text, why in ((enc(UNKNOWN_TYPE), "type"), ("", "empty"), ("not base64 !!", "b64"), (enc(b"\x00\x00"), "short"), (None, "none"),
                          (enc(b"\x00\x00\x00\x01\x00\x01\x07"), "unread bytes after the value")):
            with self.subTest(why=why):
                got = ii.decode_value("k", text)
                self.assertEqual((got.status, got.value), ("undecodable", None), got.note)
        self.assertNotEqual(self.decode(""), ii.decode_value("k", None))  # an empty string is a value, a missing <value> is not

    def test_padding_and_url_safe_alphabet_are_accepted(self) -> None:
        text = enc("hello world")
        for variant in (text, text + "=" * (-len(text) % 4), text.replace("+", "-").replace("/", "_")):
            self.assertEqual(ii.decode_value("k", variant).value, "hello world")

    def test_rgb_text_parsing(self) -> None:
        self.assertEqual(ii.parse_rgb("rgb(0.941176 0.556863 0.2)"), "#f08e33")
        self.assertEqual(ii.parse_rgb("rgb(1 1 1)"), "#ffffff")
        self.assertEqual(ii.parse_rgb("#ABCDEF"), "#abcdef")
        self.assertIsNone(ii.parse_rgb("blue"))
        self.assertIsNone(ii.parse_rgb(""))


class StorageParsingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "storage"
        self.path.write_text(build_storage(), encoding="utf-8")

    def test_widgets_screens_ids_sizes_and_skipped_attachments(self) -> None:
        report = ii.read_storage(self.path)
        self.assertEqual([c.type_id for c in report.candidates], ["com.test.rss", "com.test.unknown", "com.test.rss"])
        first = report.candidates[0]
        self.assertEqual((first.device_key, first.screen_id, first.slot, first.size, first.size_name), (DEVICE, SCREEN_A, 0, 2, "SizeM"))
        self.assertEqual((first.widget_id, first.attachment_id, first.settings_id),
                         ("{c0000001-0000-4000-8000-000000000001}", "{d0000001-0000-4000-8000-000000000001}", "{d0000001-0000-4000-8000-000000000001}"))
        self.assertNotEqual(first.widget_id, first.settings_id)  # the Widget (slot) id is NOT the settings id that names the browser storage
        self.assertEqual([c.size_name for c in report.candidates], ["SizeM", "SizeS", "SizeXL"])
        self.assertEqual(len(report.skipped), 1)
        self.assertIn("DefaultWidgetAttachment", report.skipped[0])
        self.assertEqual((first.location, first.path), ("UserStorage", "com/test/rss/index.html"))
        self.assertEqual(report.versions["HtmlSettingsData"], 505)

    def test_values_keep_their_types_and_the_three_statuses(self) -> None:
        values = ii.read_storage(self.path).candidates[0].values
        self.assertEqual({k: v.status for k, v in values.items() if v.status != "ok"}, {"nothing": "null", "broken": "undecodable"})
        self.assertEqual((values["level"].value, values["accent"].value, values["ghost"].value, values["flag"].value, values["count"].value),
                         (7.0, "#ff8800", "#80102030", True, 3))
        self.assertEqual(values["media"].value, {"angle": 0, "scale": 1.0, "pathToAsset": "C:\\private\\pic.png"})
        self.assertIs(values["widgetPersonalizationCustomStyleEnabled"].value, True)
        self.assertEqual(ii.read_storage(self.path).candidates[2].values["level"].value, 3.0)  # the second instance has its own values

    def test_device_screen_personalization_and_theme_by_screen(self) -> None:
        report = ii.read_storage(self.path)
        self.assertEqual(report.device_personalization[DEVICE], {"textColor": "#122433", "accentColor": "#f08e33", "backgroundColor": "#000000",
                                                                 "widgetBackgroundColor": "#000000", "widgetTransparency": 80})
        self.assertEqual(report.screen_personalization[SCREEN_B]["widgetTransparency"], 55)
        self.assertIs(report.theme_by_screen[DEVICE], True)
        values = ii.personalization_values(report, report.candidates[2])
        self.assertEqual((values["device"]["widget_transparency"], values["screen"]["widget_transparency"], values["screen"]["text_color"]), (80, 55, "#00ff00"))

    def test_read_only_the_file_is_neither_changed_nor_needs_write_access(self) -> None:
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns)
        os.chmod(self.path, stat.S_IREAD)
        try:
            ii.read_storage(self.path)
        finally:
            os.chmod(self.path, stat.S_IREAD | stat.S_IWRITE)
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["storage"])  # no sidecar, no temp file

    def test_bad_files_are_rejected_with_a_message_not_guessed(self) -> None:
        for text in ("not xml at all", "<cereal><value0></value0></cereal>"):
            bad = self.path.with_name("bad")
            bad.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                ii.read_storage(bad)

    def test_newer_and_older_storage_versions(self) -> None:
        newer = self.path.with_name("newer")
        newer.write_text(build_storage(html_version=506), encoding="utf-8")
        candidate = ii.read_storage(newer).candidates[0]
        self.assertIn("newer iCUE", candidate.blocked)
        older = self.path.with_name("older")
        older.write_text(build_storage(html_version=503), encoding="utf-8")
        candidate = ii.read_storage(older).candidates[0]
        self.assertEqual(candidate.blocked, "")
        self.assertTrue(any("older storage format" in w and "NOT apply" in w for w in candidate.warnings))
        self.assertTrue(any("cannot be decoded" in w and "broken" in w for w in candidate.warnings))

    def test_masking_hides_urls_tokens_paths_and_long_text_but_not_numbers_or_colors(self) -> None:
        candidate = ii.read_storage(self.path).candidates[0]
        text = "\n".join(ii.preview_lines(candidate))
        for secret in (*SECRETS, "C:\\private", "pic.png"):
            self.assertNotIn(secret, text)
        for shown in ("level = 7.0", "accent = #ff8800", "ghost = #80102030", "flag = True", "count = 3", "nothing = null", "broken = UNDECODABLE"):
            self.assertIn(shown, text)
        self.assertEqual(ii.mask_value("title", "Basketball"), "'Basketball'")
        self.assertEqual(ii.mask_value("apiToken", "abc"), "<text 3 chars>")  # by key name
        self.assertEqual(ii.mask_value("x", "name@example.invalid"), "<text 20 chars>")
        self.assertEqual(ii.mask_value("x", "a1b2c3d4e5f6g7h8i9"), "<text 18 chars>")  # token-like


class LibraryMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "storage"
        self.path.write_text(build_storage(), encoding="utf-8")
        self.report = ii.read_storage(self.path)

    def test_match_is_by_type_id_only_and_never_guessed(self) -> None:
        rss = load_entry(write_library_widget(self.tmp / "lib" / "rss"))
        other = load_entry(write_library_widget(self.tmp / "lib" / "other", type_id="com.test.other"))
        self.assertEqual(ii.matching_entries(self.report.candidates[0], [rss, other]), [rss])
        self.assertEqual(ii.matching_entries(self.report.candidates[1], [rss, other]), [])  # com.test.unknown: no match, not "the closest"
        copy = load_entry(write_library_widget(self.tmp / "lib2" / "rss_copy"))
        self.assertEqual(len(ii.matching_entries(self.report.candidates[0], [rss, copy])), 2)

    def test_the_preview_says_no_match_instead_of_guessing(self) -> None:
        text = ii.preview_text(self.report, self.report.candidates[1], [], None, library_size=5)
        self.assertIn("NO MATCH", text)
        self.assertIn("com.test.unknown", text)
        self.assertIn("5 widgets", text)

    def test_versions_missing_and_undeclared_properties_are_reported(self) -> None:
        rss = load_entry(write_library_widget(self.tmp / "lib" / "rss", version="2.0.5"))
        installed = self.tmp / "installed" / "com" / "test" / "rss"
        installed.mkdir(parents=True)
        (installed / "manifest.json").write_text(json.dumps({"id": "com.test.rss", "version": "2.0.4"}), encoding="utf-8")
        candidate = self.report.candidates[0]
        version = ii.installed_manifest_version(candidate, {"UserStorage": self.tmp / "installed"})
        self.assertEqual(version, "2.0.4")
        info = ii.comparison(candidate, rss, version)
        self.assertTrue(info["version_differs"])
        self.assertEqual(info["declared_missing_in_icue"], ["extra"])  # declared by this source, absent in iCUE's data
        self.assertIn("apiToken", info["stored_not_declared"])
        self.assertEqual(info["undecodable"], ["broken"])
        text = ii.preview_text(self.report, candidate, [rss], version, 1)
        self.assertIn("VERSIONS DIFFER", text)
        self.assertIn("extra", text)
        self.assertIsNone(ii.installed_manifest_version(candidate, {"UserStorage": self.tmp / "nowhere"}))  # unknown, not guessed
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        self.assertIn("The device is not selected by the import", text)

class ImportWindowTests(PageTestCase):
    """Import -> Settings Editor -> Runtime -> Live Reload -> Restart, on fixtures with fake data."""

    def setUp(self) -> None:
        super().setUp()
        self.storage_file = Path(self._tmp.name) / "storage"
        self.storage_file.write_text(build_storage(), encoding="utf-8")
        self.lib = write_library_widget(Path(self._tmp.name) / "lib" / "rss")
        self.entry = load_entry(self.lib)
        self.report = ii.read_storage(self.storage_file)

    def import_one(self, index: int = 0, window: MainWindow | None = None, **kwargs):
        window = window or self.window
        instance = window.import_icue_instance(self.report.candidates[index], self.entry, report=self.report, **kwargs)
        self.assertIsNotNone(instance)
        runtime = window.runtimes[instance.id]
        self.assertTrue(self.wait(lambda: runtime.state is State.LOADED))
        self.pump(0.4)
        return instance, runtime

    def rjs(self, runtime, script: str):
        box: dict = {}
        runtime.page.runJavaScript(script, lambda v: box.setdefault("v", v))
        self.assertTrue(self.wait(lambda: "v" in box, 10), script)
        return box["v"]

    def test_import_creates_a_new_instance_with_the_stored_values_and_nothing_else(self) -> None:
        candidate = self.report.candidates[0]
        instance, runtime = self.import_one()
        self.assertNotIn(instance.id, {candidate.widget_id, candidate.attachment_id, candidate.settings_id}.union(
            {c.strip("{}") for c in (candidate.widget_id, candidate.attachment_id, candidate.settings_id)}))
        self.assertIs(self.window.store.get(instance.id), instance)
        expected = {k: v.value for k, v in candidate.values.items() if v.status != "undecodable"}
        self.assertEqual(instance.data, expected)
        self.assertEqual((instance.data["level"], instance.data["accent"], instance.data["ghost"], instance.data["nothing"]), (7.0, "#ff8800", "#80102030", None))
        self.assertNotIn("broken", instance.data)  # undecodable: left out, not turned into null or ""
        self.assertNotIn("extra", instance.data)  # declared by this source version, absent in iCUE: no default is added (restore semantics)
        self.assertEqual((self.rjs(runtime, "level"), self.rjs(runtime, "accent"), self.rjs(runtime, "typeof extra"), self.rjs(runtime, "typeof broken")),
                         (7, "#ff8800", "undefined", "undefined"))
        self.assertEqual(self.rjs(runtime, "nothing"), "")  # a stored null exists as a key (toQmlString(null) -> "")
        self.assertIs(self.rjs(runtime, "widgetPersonalizationCustomStyleEnabled"), True)
        self.assertEqual(self.window.settings_panel.editors["level"].control.value(), 7)
        self.assertEqual((instance.origin["sizeName"], instance.origin["undecodableKeys"], instance.origin["nullKeys"], instance.origin["icueSettingsId"]),
                         ("SizeM", ["broken"], ["nothing"], candidate.settings_id))
        from widgetdevtool.instance_storage import storage_name
        self.assertNotEqual(runtime.storage_name, storage_name(candidate.settings_id, candidate.type_id))  # its own storage, not iCUE's name
        self.assertEqual(self.window.storage.names_on_disk(), [runtime.storage_name])
        blob = json.dumps(instance.origin) + " ".join(e.message for e in self.window.console.entries)
        for secret in (*SECRETS, "C:\\private"):
            self.assertNotIn(secret, blob)  # no value reaches the origin metadata or the console

    def test_importing_never_overwrites_an_existing_instance(self) -> None:
        first, _ = self.import_one(0)
        before = json.dumps(first.to_record(), sort_keys=True)
        second, runtime2 = self.import_one(0)
        third, _ = self.import_one(2)
        self.assertEqual(len({first.id, second.id, third.id}), 3)
        self.assertEqual(json.dumps(first.to_record(), sort_keys=True), before)
        self.assertEqual((len(self.window.store), len(self.window.runtimes), self.window.store.get(third.id).data["level"]), (3, 3, 3.0))
        self.window.select_instance(second.id)
        self.window._on_setting_committed("level", 1.0)
        self.assertEqual((first.data["level"], second.data["level"]), (7.0, 1.0))
        self.assertEqual(len({r.storage_name for r in self.window.runtimes.values()}), 3)

    def test_a_blocked_or_invalid_import_creates_nothing(self) -> None:
        newer = Path(self._tmp.name) / "newer"
        newer.write_text(build_storage(html_version=506), encoding="utf-8")
        report = ii.read_storage(newer)
        with self.assertRaises(ValueError):
            self.window.import_icue_instance(report.candidates[0], self.entry, report=report)
        (self.lib / "index.html").unlink()
        self.assertIsNone(self.window.import_icue_instance(self.report.candidates[0], self.entry, report=self.report))
        self.assertEqual((len(self.window.store), len(self.window.runtimes)), (0, 0))

    def test_live_reload_keeps_the_imported_values_and_adds_no_defaults(self) -> None:
        instance, runtime = self.import_one()
        before = json.dumps(instance.data, sort_keys=True)
        html = (self.lib / "index.html").read_text(encoding="utf-8").replace("<body>probe</body>", "<body>edited</body>")
        (self.lib / "index.html").write_text(html, encoding="utf-8")
        self.assertTrue(self.wait(lambda: runtime.reload_count == 1 and not runtime.reloading, 20))
        self.pump(0.8)
        self.assertEqual((runtime.reload_count, json.dumps(instance.data, sort_keys=True)), (1, before))
        self.assertEqual((self.rjs(runtime, "document.body.textContent"), self.rjs(runtime, "level"), self.rjs(runtime, "typeof extra")), ("edited", 7, "undefined"))

    def test_settings_edit_then_restart_keeps_the_imported_instance(self) -> None:
        data = Path(self._tmp.name) / "data"
        window = MainWindow(data_dir=data)
        self.addCleanup(lambda: (window.shutdown(), self.pump(0.3)))
        instance, runtime = self.import_one(window=window)
        window._on_setting_committed("level", 9.0)
        self.assertTrue(self.wait(lambda: self.rjs(runtime, "level") == 9))
        instance_id, snapshot = instance.id, json.dumps(instance.to_record(), sort_keys=True)
        window.shutdown()
        self.pump(0.4)
        again = MainWindow(data_dir=data)
        self.addCleanup(lambda: (again.shutdown(), self.pump(0.3)))
        self.assertEqual(again.restore_workspace(), 1)
        restored = again.store.get(instance_id)
        self.assertEqual(json.dumps(restored.to_record(), sort_keys=True), snapshot)  # data, edited keys and origin survived
        self.assertEqual(restored.origin["sizeName"], "SizeM")
        runtime2 = again.runtimes[instance_id]
        self.assertTrue(self.wait(lambda: runtime2.state is State.LOADED))
        self.pump(0.3)
        self.assertEqual((self.rjs(runtime2, "level"), self.rjs(runtime2, "typeof extra")), (9, "undefined"))

    def test_the_stored_size_is_the_instances_own_and_personalization_is_applied_only_when_asked(self) -> None:
        self.window.select_device("dash_accessory_lcd", "SizeS", "landscape")
        first, rt1 = self.import_one(0)  # stored SizeM
        self.assertEqual((first.size, self.window.device_size, self.window.viewport.size), ("SizeM", "SizeM", (315, 261)))  # no checkbox needed
        self.assertEqual(self.window.personalization.values("device", "dash_accessory_lcd").widget_transparency, 80)  # tool default, untouched
        second, rt2 = self.import_one(2, apply_personalization=True)  # stored SizeXL
        self.assertEqual((second.size, self.window.device_size), ("SizeXL", "SizeXL"))
        self.assertEqual(self.window.instance_viewport(first).size, (315, 261))  # the first instance did not move
        device, screen = (self.window.personalization.values(s, "dash_accessory_lcd") for s in ("device", "screen"))
        self.assertEqual((device.text_color, device.widget_transparency, screen.text_color, screen.widget_transparency), ("#122433", 80, "#00ff00", 55))
        self.assertTrue(self.window.personalization.theme_by_screen("dash_accessory_lcd"))
        self.assertEqual(self.rjs(rt2, "[innerWidth, innerHeight].join('x')"), "951x261")
        self.assertEqual(self.rjs(rt1, "[innerWidth, innerHeight].join('x')"), "315x261")

    def test_a_device_that_does_not_offer_the_stored_size_is_reported_and_nothing_is_lost(self) -> None:
        self.window.select_device("link_generic_lcd", "SizeM", "landscape")
        link = next(p for p in self.window.profiles if p.id == "link_generic_lcd")
        stored = next(c for c in self.report.candidates if c.size_name not in link.supported_sizes())
        index = self.report.candidates.index(stored)
        instance, runtime = self.import_one(index)
        self.assertEqual(instance.size, stored.size_name)  # kept
        self.assertIn(f"stored size {stored.size_name} is not offered by", self.window.import_notice)
        self.assertIn("NOTE", self.window.device_info.text())
        self.assertIn(self.window.device_size, link.supported_sizes())
    def test_the_dialog_previews_masked_data_and_imports_only_after_confirmation(self) -> None:
        from widgetdevtool.import_dialog import ImportDialog
        copy = write_library_widget(Path(self._tmp.name) / "lib" / "rss_copy")
        entries = [self.entry, load_entry(copy), load_entry(write_library_widget(Path(self._tmp.name) / "lib" / "o", type_id="com.test.other"))]
        dialog = ImportDialog(entries)
        self.addCleanup(dialog.close)
        got: list = []
        dialog.importRequested.connect(lambda c, e, r, pers: got.append((c, e)))
        self.assertTrue(dialog.load(self.storage_file))
        self.assertEqual(dialog.listing.count(), 3)
        self.assertEqual(self.window.store.all(), [])  # reading and previewing import nothing
        dialog.listing.setCurrentRow(0)
        self.assertEqual(dialog.match_combo.count(), 2)  # two library widgets with this typeId: the user must choose
        self.assertFalse(dialog.import_button.isEnabled())
        for secret in (*SECRETS, "C:\\private"):
            self.assertNotIn(secret, dialog.preview.toPlainText())
        dialog.match_combo.setCurrentIndex(1)
        self.assertTrue(dialog.import_button.isEnabled())
        dialog.import_button.click()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][1].folder, copy)
        dialog.listing.setCurrentRow(1)  # com.test.unknown
        self.assertIn("NO MATCH", dialog.preview.toPlainText())
        self.assertFalse(dialog.import_button.isEnabled())
        self.assertFalse(dialog.load(Path(self._tmp.name) / "missing"))
        self.assertIn("Cannot read", dialog.status.text())


if __name__ == "__main__":
    unittest.main()