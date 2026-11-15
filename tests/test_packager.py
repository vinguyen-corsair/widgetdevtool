r"""Build Package tests (U2; targets iCUE 5 / iCUE Apps since U2.2). The iCUE Apps flow is tested with a FAKE CLI
(`fixtures/fake_icuewidget/icuewidget.js`, run by the real node.exe through the same npm-shim layout the tool looks for). The fake only mimics
the interface (commands, -o, exit codes, stderr, the min_app_version / semver rules). `RealCliTests` run the REAL installed CLI on a compliant
fixture (skipped when the CLI is not installed). The iCUE 5 target needs no CLI. `RealSensorBarTests` build Sensor Bar (WidgetsXE, read-only)
for both targets into a temp folder.
Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)
from PySide6.QtWidgets import QApplication  # noqa: E402

from test_runtime_bridge import WIDGET_HTML, PageTestCase  # noqa: E402
from test_settings_panel import BASKETBALL, RSS, SENSORBAR, SMOKECLOCK  # noqa: E402
from widgetdevtool import legacy, packager  # noqa: E402
from widgetdevtool.main_window import State  # noqa: E402
from widgetdevtool.package_dialog import SETTINGS_KEY, TARGET_KEY, PackageDialog, default_output_dir  # noqa: E402
from widgetdevtool.packager import DOCUMENTED_MIN_APP_VERSION, TARGET_ICUE5, TARGET_ICUEAPPS  # noqa: E402

FAKE_JS = Path(__file__).resolve().parent / "fixtures" / "fake_icuewidget" / "icuewidget.js"
NODE = shutil.which("node")
FULL_MANIFEST = {"author": "WidgetDevTool tests", "id": "com.test.pkgwidget", "name": "Pkg Widget", "description": "U2 fixture", "version": "1.2.3",
                 "preview_icon": "resources/icon.svg", "min_framework_version": "1.0.0", "os": [{"platform": "windows"}],
                 "supported_devices": [{"type": "dashboard_lcd"}]}


def make_widget(root: Path, version: str = "1.2.3", manifest: dict | None = None, icon: bool = True, index: bool = True,
                min_app_version: str | None = None) -> Path:
    """A legacy-style widget (WidgetsXE: no min_app_version unless given) with a dot-file that must not be packaged."""
    root.mkdir(parents=True, exist_ok=True)
    data = dict(FULL_MANIFEST, version=version) if manifest is None else manifest
    if min_app_version is not None:
        data["min_app_version"] = min_app_version
    (root / "manifest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    if index:
        (root / "index.html").write_text(WIDGET_HTML, encoding="utf-8")
    if icon:
        (root / "resources").mkdir(exist_ok=True)
        (root / "resources" / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    (root / ".hidden.txt").write_text("not packaged", encoding="utf-8")
    return root


def tree_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def staging_dirs() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob(f"{legacy.STAGE_PREFIX}*")}


def wait_for(app, done: list, seconds: float = 60) -> None:
    end = time.monotonic() + seconds
    while not done and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


@unittest.skipUnless(NODE, "node.exe is needed to run the fake CLI")
class PackagerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wdt pkg ü ")  # spaces + Unicode in every path
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        bin_dir = self.tmp / "fake bin"
        script = bin_dir / "node_modules" / "icuewidget-cli" / "node-bin" / "icuewidget.js"
        script.parent.mkdir(parents=True)
        shutil.copy(FAKE_JS, script)
        (bin_dir / "icuewidget.cmd").write_text("@echo off\r\nnode \"%~dp0node_modules\\icuewidget-cli\\node-bin\\icuewidget.js\" %*\r\n", encoding="ascii")
        self.path_env = os.pathsep.join([str(bin_dir), str(Path(NODE).parent)])
        self.log = self.tmp / "cli-calls.log"
        os.environ["FAKE_ICUEWIDGET_LOG"] = str(self.log)
        os.environ.pop("FAKE_ICUEWIDGET_MODE", None)
        self.addCleanup(lambda: (os.environ.pop("FAKE_ICUEWIDGET_LOG", None), os.environ.pop("FAKE_ICUEWIDGET_MODE", None)))
        self.cli = packager.probe_cli(packager.find_cli(self.path_env))
        self.out = self.tmp / "Ausgabe Pakete ü"
        self.src = make_widget(self.tmp / "Widgets XE ü" / "My Widget" / "widget" / "pkgwidget")
        self.stages = staging_dirs()

    def calls(self) -> list[list[str]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def cli_runs(self) -> list[str]:
        return [c[0] for c in self.calls() if c[0] in ("validate", "package") and "--help" not in c]

    def manifest(self) -> dict:
        return json.loads((self.src / "manifest.json").read_text(encoding="utf-8"))

    def build(self, **kwargs):
        """iCUE Apps by default, with the documented min_app_version for a manifest that has none."""
        kwargs.setdefault("target", TARGET_ICUEAPPS)
        if kwargs["target"] == TARGET_ICUEAPPS:
            kwargs.setdefault("min_app_version", DOCUMENTED_MIN_APP_VERSION)
        return packager.build(self.cli, self.src, self.out, self.manifest(), **kwargs)

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.out.iterdir() if ".partial." in p.name) if self.out.exists() else []

    def assert_clean(self) -> None:
        self.assertEqual((self.leftovers(), staging_dirs() - self.stages), ([], set()))


class CliTests(PackagerCase):
    def test_cli_is_found_through_the_npm_shim_and_probed(self) -> None:
        self.assertEqual(self.cli.command[0], NODE if os.path.isabs(NODE) else self.cli.command[0])
        self.assertTrue(self.cli.command[1].endswith("icuewidget.js"))
        self.assertTrue(self.cli.location.lower().endswith("icuewidget.cmd"))
        self.assertEqual((self.cli.version, self.cli.output_flag), ("0.0.0-fake", "--output"))  # ANSI colour codes stripped

    def test_missing_cli_blocks_only_icue_apps_and_installs_nothing(self) -> None:
        self.assertIsNone(packager.find_cli(str(self.tmp / "empty")))
        result = packager.build(None, self.src, self.out, self.manifest(), target=TARGET_ICUEAPPS, min_app_version="5.47")
        self.assertEqual((result.ok, result.state), (False, "cli-missing"))
        self.assertIn("npm install -g icuewidget-cli", result.message)
        self.assertIn("does not install it", result.message)
        self.assertFalse(self.out.exists())
        self.assertTrue(packager.build(None, self.src, self.out, self.manifest(), target=TARGET_ICUE5).ok)  # iCUE 5 needs no CLI

    def test_a_cli_without_an_output_option_is_not_used_to_build_next_to_the_source(self) -> None:
        self.cli.output_flag = None
        result = self.build()
        self.assertFalse(result.ok)
        self.assertIn("does not list an output option", result.message)
        self.assertEqual(self.cli_runs(), ["validate"])
        self.assertFalse((self.src.parent / "default.icuewidget").exists())
        self.assert_clean()

    def test_cli_warnings_are_parsed_including_wrapped_lines(self) -> None:
        text = ("\u256d\u2500 Warnings \u2500\u256e\n\u2502 ! manifest.interactive is true, but   \u2502\n\u2502   iCUE only recognizes dashboard_lcd. \u2502\n"
                "\u2502 ! translation.json is missing.        \u2502\n\u2570\u2500\u256f\n")
        self.assertEqual(packager.cli_warnings(text), ["manifest.interactive is true, but iCUE only recognizes dashboard_lcd.",
                                                       "translation.json is missing."])


class IcueAppsBuildTests(PackagerCase):
    def test_validate_then_package_a_temporary_copy_with_min_app_version_added(self) -> None:
        before = tree_hashes(self.src)
        result = self.build()
        self.assertTrue(result.ok, result.log())
        self.assertEqual((result.target, result.output), (TARGET_ICUEAPPS, (self.out / "pkgwidget-1.2.3-icueapps.icuewidget").resolve()))
        commands = [c for c in self.calls() if c[0] in ("validate", "package") and "--help" not in c]
        self.assertEqual([c[0] for c in commands], ["validate", "package"])
        self.assertIn(legacy.STAGE_PREFIX, commands[0][1])  # the CLI ran on the temporary copy, never on the source
        self.assertEqual(Path(commands[0][1]).name, "pkgwidget")
        self.assertEqual(commands[1][2], "--output")
        self.assertTrue(Path(commands[1][3]).name.startswith(".pkgwidget-1.2.3-icueapps.") and Path(commands[1][3]).parent == self.out.resolve())
        with zipfile.ZipFile(result.output) as zf:
            self.assertEqual(sorted(zf.namelist()), ["index.html", "manifest.json", "resources/icon.svg"])  # dot-file excluded, as the CLI does
            self.assertEqual(zf.read("index.html"), (self.src / "index.html").read_bytes())
            packaged = json.loads(zf.read("manifest.json"))
        self.assertEqual(packaged, dict(self.manifest(), min_app_version=DOCUMENTED_MIN_APP_VERSION))
        self.assertEqual([c.file for c in result.changes], ["manifest.json"])
        self.assertEqual(result.differs_from_source, ["manifest.json"])
        self.assertTrue(result.archive_check["ok"])
        self.assertEqual(tree_hashes(self.src), before)  # source byte-identical
        self.assertNotIn("min_app_version", self.manifest())
        self.assert_clean()
        self.assertFalse(any(p.suffix == ".icuewidget" for p in self.src.parent.parent.rglob("*")))  # nothing written next to the source
        log = result.log()
        self.assertIn("Target: iCUE Apps", log)
        self.assertIn("build copy only", log)
        self.assertIn("exit code: 0", log)
        self.assertNotIn("\x1b[", log)

    def test_an_existing_valid_min_app_version_is_kept_byte_identical(self) -> None:
        shutil.rmtree(self.src)
        make_widget(self.src, min_app_version="5.52")
        result = self.build(min_app_version="5.47")  # ignored: the manifest has one
        self.assertTrue(result.ok, result.log())
        self.assertEqual((result.changes, result.differs_from_source), ([], []))
        with zipfile.ZipFile(result.output) as zf:
            self.assertEqual(zf.read("manifest.json"), (self.src / "manifest.json").read_bytes())

    def test_without_min_app_version_nothing_runs(self) -> None:
        result = self.build(min_app_version=None)
        self.assertFalse(result.ok)
        self.assertIn("min_app_version", result.message)
        self.assertEqual(self.cli_runs(), [])
        self.assert_clean()
        self.assertFalse(packager.build(self.cli, self.src, self.out, self.manifest(), target=TARGET_ICUEAPPS, min_app_version="5").ok)

    def test_validation_failures_are_reported_and_keep_the_old_package(self) -> None:
        self.assertTrue(self.build().ok)
        target = self.out / "pkgwidget-1.2.3-icueapps.icuewidget"
        old = target.read_bytes()
        cases = (("missing asset", lambda: (self.src / "resources" / "icon.svg").unlink(), "preview_icon"),
                 ("missing entry point", lambda: (self.src / "index.html").unlink(), "index.html"),
                 ("invalid version", lambda: make_widget(self.src, version="1.2"), "semantic version"),
                 ("invalid existing min_app_version (not changed)", lambda: make_widget(self.src, min_app_version="5"), "iCUE version format"))
        for name, breaker, error in cases:
            with self.subTest(case=name):
                shutil.rmtree(self.src)
                make_widget(self.src)
                breaker()
                before = tree_hashes(self.src)
                self.log.unlink(missing_ok=True)
                manifest = {"id": "com.test.pkgwidget", "version": "1.2.3"}
                result = packager.build(self.cli, self.src, self.out, manifest, target=TARGET_ICUEAPPS, min_app_version="5.47",
                                        confirm_overwrite=lambda p: True)
                self.assertEqual((result.ok, result.state), (False, "failed"))
                self.assertIn("Validation failed", result.message)
                self.assertEqual(self.cli_runs(), ["validate"])  # package was NOT run
                self.assertEqual(result.steps[0].exit_code, 1)
                self.assertIn(error, result.steps[0].stderr)
                self.assertIn(error, result.log())
                self.assertEqual(target.read_bytes(), old)
                self.assertEqual(tree_hashes(self.src), before)
                self.assert_clean()

    def test_an_invalid_manifest_is_refused_before_the_cli(self) -> None:
        (self.src / "manifest.json").write_text("{ not json", encoding="utf-8")
        result = packager.build(self.cli, self.src, self.out, {"id": "com.test.pkgwidget", "version": "1.2.3"}, target=TARGET_ICUEAPPS,
                                min_app_version="5.47")
        self.assertFalse(result.ok)
        self.assertIn("not valid JSON", result.message)
        self.assertEqual(self.cli_runs(), [])

    def test_icue_apps_requirements_are_checked_in_the_archive(self) -> None:
        manifest = dict(FULL_MANIFEST)
        del manifest["description"]
        manifest["supported_devices"] = [{"type": "watch_lcd"}]
        shutil.rmtree(self.src)
        make_widget(self.src, manifest=manifest)  # the fake CLI does not check these: the tool's own check must
        result = self.build()
        self.assertFalse(result.ok)
        self.assertIn("iCUE Apps requirements", result.message)
        self.assertTrue(any("manifest.description" in p for p in result.problems), result.problems)
        self.assertTrue(any("supported_devices" in p for p in result.problems), result.problems)
        self.assertFalse((self.out / "pkgwidget-1.2.3-icueapps.icuewidget").exists())
        self.assert_clean()

    def test_cli_warnings_and_a_low_min_app_version_are_reported_as_notes(self) -> None:
        os.environ["FAKE_ICUEWIDGET_MODE"] = "warn"
        result = self.build(min_app_version="5.45")
        self.assertTrue(result.ok, result.log())
        self.assertIn("CLI warning: fake warning: interactive widget on pump_lcd", result.notes)
        self.assertTrue(any("below the documented minimum 5.47" in n for n in result.notes), result.notes)

    def test_packaging_failures_leave_the_old_package_and_no_partial_file(self) -> None:
        self.assertTrue(self.build().ok)
        target = self.out / "pkgwidget-1.2.3-icueapps.icuewidget"
        old = target.read_bytes()
        for mode, expect in (("fail", "Packaging failed"), ("nozip", "no file was created"), ("badzip", "does not match the build copy")):
            with self.subTest(mode=mode):
                os.environ["FAKE_ICUEWIDGET_MODE"] = mode
                result = self.build(confirm_overwrite=lambda p: True)
                self.assertFalse(result.ok)
                self.assertIn(expect, result.message)
                self.assertEqual(target.read_bytes(), old)
                self.assert_clean()

    def test_an_existing_package_is_only_replaced_after_confirmation(self) -> None:
        self.assertTrue(self.build().ok)
        target = self.out / "pkgwidget-1.2.3-icueapps.icuewidget"
        old = target.read_bytes()
        (self.src / "index.html").write_text(WIDGET_HTML + "<!-- v2 -->", encoding="utf-8")
        asked: list = []
        result = self.build(confirm_overwrite=lambda p: asked.append(p) or False)
        self.assertEqual((result.state, asked, target.read_bytes()), ("cancelled", [target.resolve()], old))
        self.assertEqual(self.cli_runs(), ["validate", "package"])  # only the 1st build
        result = self.build(confirm_overwrite=lambda p: True)
        self.assertTrue(result.ok)
        with zipfile.ZipFile(target) as zf:
            self.assertIn(b"<!-- v2 -->", zf.read("index.html"))

    def test_a_source_changed_before_the_build_is_packaged_as_it_is_now(self) -> None:
        (self.src / "index.html").write_text(WIDGET_HTML + "<!-- just saved -->", encoding="utf-8")
        (self.src / "resources" / "new.png").write_bytes(b"\x89PNG fake")
        result = self.build()
        self.assertTrue(result.ok)
        with zipfile.ZipFile(result.output) as zf:
            self.assertIn(b"just saved", zf.read("index.html"))
            self.assertIn("resources/new.png", zf.namelist())

    def test_a_cli_writing_into_its_input_never_reaches_the_source(self) -> None:
        os.environ["FAKE_ICUEWIDGET_MODE"] = "touch"  # the fake appends to index.html of the folder it packages (the temporary copy)
        before = tree_hashes(self.src)
        result = self.build()
        self.assertEqual(tree_hashes(self.src), before)
        self.assertFalse(result.ok)  # the archive no longer matches the copy that was validated
        self.assertFalse((self.out / "pkgwidget-1.2.3-icueapps.icuewidget").exists())
        self.assert_clean()

    def test_a_source_changed_during_the_build_discards_the_package(self) -> None:
        real_run = packager.run

        def run_and_touch(command, args, *rest, **kwargs):
            if args and args[0] == "package":
                (self.src / "index.html").write_text(WIDGET_HTML + "<!-- saved while building -->", encoding="utf-8")
            return real_run(command, args, *rest, **kwargs)

        with mock.patch.object(packager, "run", run_and_touch):
            result = self.build()
        self.assertFalse(result.ok)
        self.assertIn("source changed during the build", result.message)
        self.assertFalse((self.out / "pkgwidget-1.2.3-icueapps.icuewidget").exists())
        self.assert_clean()

    def test_cancel_kills_the_cli_and_leaves_nothing(self) -> None:
        os.environ["FAKE_ICUEWIDGET_MODE"] = "slow"
        cancel = threading.Event()
        states: list = []
        threading.Timer(2.0, cancel.set).start()
        started = time.monotonic()
        result = self.build(cancel=cancel, progress=states.append)
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual((result.state, states), ("cancelled", ["staging", "validating", "packaging"]))
        self.assertFalse((self.out / "pkgwidget-1.2.3-icueapps.icuewidget").exists())
        self.assert_clean()

    def test_a_timeout_is_a_failure(self) -> None:
        os.environ["FAKE_ICUEWIDGET_MODE"] = "slow"
        result = self.build(timeout=2)
        self.assertFalse(result.ok)
        self.assertTrue(result.steps[-1].timed_out)
        self.assert_clean()


class Icue5BuildTests(PackagerCase):
    def build5(self, **kwargs):
        return self.build(target=TARGET_ICUE5, **kwargs)

    def test_icue5_packages_the_files_at_the_root_without_cli_and_without_changing_the_manifest(self) -> None:
        before = tree_hashes(self.src)
        states: list = []
        result = self.build5(progress=states.append)
        self.assertTrue(result.ok, result.log())
        self.assertEqual((result.target, result.output.name, states), (TARGET_ICUE5, "pkgwidget-1.2.3-icue5.icuewidget", ["checking", "packaging"]))
        self.assertEqual((self.cli_runs(), result.steps), ([], []))  # no icuewidget validate / package
        with zipfile.ZipFile(result.output) as zf:
            infos = zf.infolist()
            self.assertEqual(sorted(i.filename for i in infos), ["index.html", "manifest.json", "resources/icon.svg"])
            self.assertTrue(all(i.compress_type == zipfile.ZIP_DEFLATED and not i.is_dir() for i in infos))
            self.assertIsNone(zf.testzip())
            self.assertEqual(zf.read("manifest.json"), (self.src / "manifest.json").read_bytes())  # byte-identical, no min_app_version
        self.assertNotIn(b"min_app_version", (self.src / "manifest.json").read_bytes())
        self.assertEqual(tree_hashes(self.src), before)
        self.assertEqual(result.changes, [])
        self.assertTrue(result.archive_check["ok"])
        self.assertIn("Target: iCUE 5", result.log())
        self.assert_clean()

    def test_icue5_ignores_an_existing_min_app_version_and_does_not_need_one(self) -> None:
        shutil.rmtree(self.src)
        make_widget(self.src, min_app_version="5.52")
        result = self.build5()
        self.assertTrue(result.ok, result.log())
        with zipfile.ZipFile(result.output) as zf:
            self.assertEqual(zf.read("manifest.json"), (self.src / "manifest.json").read_bytes())

    def test_icue5_checks_refuse_a_broken_source_and_keep_the_old_package(self) -> None:
        self.assertTrue(self.build5().ok)
        target = self.out / "pkgwidget-1.2.3-icue5.icuewidget"
        old = target.read_bytes()
        cases = (("invalid manifest", lambda: (self.src / "manifest.json").write_text("{ not json", encoding="utf-8"), "not valid JSON"),
                 ("missing preview icon", lambda: (self.src / "resources" / "icon.svg").unlink(), "manifest.preview_icon does not exist"),
                 ("missing entry point", lambda: (self.src / "index.html").unlink(), "required file missing: index.html"),
                 ("missing asset", lambda: (self.src / "index.html").write_text(WIDGET_HTML.replace("<body>", '<body><img src="images/x.png">', 1),
                                                                                 encoding="utf-8"), "index.html asset images/x.png does not exist"),
                 ("asset outside the widget", lambda: (self.src / "index.html").write_text(WIDGET_HTML.replace("<body>", '<body><img src="../x.png">', 1),
                                                                                           encoding="utf-8"), "must stay inside"),
                 ("invalid version", lambda: make_widget(self.src, version="1.0"), "not a semantic version"),
                 ("bad translation.json", lambda: (self.src / "translation.json").write_text("{", encoding="utf-8"), "translation.json"))
        for name, breaker, problem in cases:
            with self.subTest(case=name):
                shutil.rmtree(self.src)
                make_widget(self.src)
                breaker()
                result = packager.build(None, self.src, self.out, {"id": "com.test.pkgwidget", "version": "1.2.3"}, target=TARGET_ICUE5,
                                        confirm_overwrite=lambda p: True)
                self.assertEqual((result.ok, result.state), (False, "failed"))
                self.assertIn("NOT built", result.message)
                self.assertTrue(any(problem in p for p in result.problems), result.problems)
                self.assertIn(problem, result.log())
                self.assertEqual(target.read_bytes(), old)
                self.assert_clean()

    def test_icue5_overwrite_needs_confirmation_and_a_failed_write_keeps_the_old_package(self) -> None:
        self.assertTrue(self.build5().ok)
        target = self.out / "pkgwidget-1.2.3-icue5.icuewidget"
        old = target.read_bytes()
        self.assertEqual(self.build5().state, "cancelled")
        with mock.patch.object(packager, "write_icue5_archive", side_effect=OSError("disk full")):
            result = self.build5(confirm_overwrite=lambda p: True)
        self.assertEqual((result.ok, target.read_bytes()), (False, old))
        self.assertIn("disk full", result.message)
        self.assert_clean()

    def test_icue5_source_changed_during_the_build_discards_the_package(self) -> None:
        real = packager.write_icue5_archive

        def write_and_touch(root, output):
            (self.src / "index.html").write_text(WIDGET_HTML + "<!-- saved while building -->", encoding="utf-8")
            return real(root, output)

        with mock.patch.object(packager, "write_icue5_archive", write_and_touch):
            result = self.build5()
        self.assertFalse(result.ok)
        self.assertIn("source changed during the build", result.message)
        self.assertFalse((self.out / "pkgwidget-1.2.3-icue5.icuewidget").exists())
        self.assert_clean()

    def test_both_targets_give_two_distinct_files(self) -> None:
        a, b = self.build5(), self.build()
        self.assertTrue(a.ok and b.ok, a.log() + b.log())
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["pkgwidget-1.2.3-icue5.icuewidget", "pkgwidget-1.2.3-icueapps.icuewidget"])
        with zipfile.ZipFile(a.output) as five, zipfile.ZipFile(b.output) as apps:
            self.assertNotIn("min_app_version", json.loads(five.read("manifest.json")))
            self.assertEqual(json.loads(apps.read("manifest.json"))["min_app_version"], DOCUMENTED_MIN_APP_VERSION)


class OutputFolderTests(PackagerCase):
    def test_invalid_or_forbidden_output_folders_are_refused(self) -> None:
        blocker = self.tmp / "a file"
        blocker.write_text("x")
        repo = self.tmp / "Widgets XE ü"
        (repo / ".git").mkdir()
        cases = {"": "no output folder", "relative/dir": "not an absolute path", str(self.src / "dist"): "inside the widget source",
                 str(self.src.parent): "widget project folder", str(repo / "Packages"): "repository", str(packager.TOOL_DIR / "out"): "WidgetDevTool installation",
                 str(blocker / "sub"): "cannot create"}
        for folder, why in cases.items():
            with self.subTest(folder=folder):
                problem = packager.check_output_folder(folder, self.src)
                self.assertIsNotNone(problem)
                self.assertIn(why, problem)
        self.assertIsNone(packager.check_output_folder(self.out, self.src))
        self.assertTrue(self.out.is_dir())
        self.assertEqual([p.name for p in self.out.iterdir()], [])  # the write test file is removed
        for target in (TARGET_ICUE5, TARGET_ICUEAPPS):
            result = packager.build(self.cli, self.src, str(self.src / "dist"), {"version": "1"}, target=target, min_app_version="5.47")
            self.assertIn("Output folder", result.message)
        self.assertEqual(self.calls(), [["--version"], ["package", "--help"]])  # no validate / package started

    def test_package_file_names(self) -> None:
        self.assertEqual(packager.package_file_name({"id": "com.shocksim.rssfeedreader", "version": "2.0.5"}, Path("x")), "rssfeedreader-2.0.5.icuewidget")
        self.assertEqual(packager.package_file_name({"id": "com.shocksim.sensorbar", "version": "1.0.6"}, Path("x"), TARGET_ICUE5),
                         "sensorbar-1.0.6-icue5.icuewidget")
        self.assertEqual(packager.package_file_name({"id": "com.shocksim.sensorbar", "version": "1.0.6"}, Path("x"), TARGET_ICUEAPPS),
                         "sensorbar-1.0.6-icueapps.icuewidget")
        self.assertEqual(packager.package_file_name({"name": "Mein Widget ü", "version": "1.0"}, Path("x")), "Mein Widget ü-1.0.icuewidget")
        self.assertEqual(packager.package_file_name({"name": 'a<b>:c', "version": ""}, Path("x")), "a_b_c.icuewidget")
        self.assertEqual(packager.package_file_name({}, Path("folder")), "folder.icuewidget")


class DialogTests(PackagerCase):
    def settings(self) -> QSettings:
        return QSettings(str(self.tmp / "settings.ini"), QSettings.Format.IniFormat)

    def run_dialog(self, dialog: PackageDialog) -> packager.BuildResult:
        done: list = []
        dialog.buildFinished.connect(done.append)
        self.assertTrue(dialog.start())
        wait_for(self.app, done)
        self.assertTrue(done, dialog.log.toPlainText())
        return done[0]

    def test_default_folder_is_documents_widgetdevtool_packages_and_folder_and_target_are_remembered(self) -> None:
        self.assertEqual(default_output_dir().parts[-2:], ("WidgetDevTool", "Packages"))
        dialog = PackageDialog(self.src, settings=self.settings(), path_env=self.path_env)
        self.addCleanup(dialog.close)
        self.assertEqual((dialog.target, dialog.target_combo.currentData()), (TARGET_ICUE5, TARGET_ICUE5))  # default target
        self.assertEqual(dialog.output_edit.text(), str(default_output_dir()))
        self.assertEqual((dialog.name_label.text(), dialog.version_label.text(), dialog.file_label.text(), dialog.source_label.text()),
                         ("Pkg Widget", "1.2.3", "pkgwidget-1.2.3-icue5.icuewidget", str(self.src.resolve())))
        self.assertIn("iCUE 5", dialog.windowTitle())
        self.assertEqual(dialog.build_button.text(), "Build for iCUE 5")
        self.assertFalse(dialog.apps_card.isVisibleTo(dialog) or dialog.cli_label.isVisibleTo(dialog))
        dialog.output_edit.setText(str(self.out))
        result = self.run_dialog(dialog)
        self.assertTrue(result.ok, dialog.log.toPlainText())
        self.assertTrue(dialog.open_button.isVisibleTo(dialog))
        self.assertTrue(dialog.status.text().startswith("Success") and "[iCUE 5]" in dialog.status.text())
        self.assertEqual((self.settings().value(SETTINGS_KEY), self.settings().value(TARGET_KEY)), (str(self.out), TARGET_ICUE5))
        again = PackageDialog(self.src, settings=self.settings(), path_env=self.path_env)
        self.addCleanup(again.close)
        self.assertEqual(again.output_edit.text(), str(self.out))

    def test_icue_apps_target_shows_cli_and_a_documented_editable_min_app_version(self) -> None:
        dialog = PackageDialog(self.src, settings=self.settings(), path_env=self.path_env, target=TARGET_ICUEAPPS)
        self.addCleanup(dialog.close)
        self.assertEqual((dialog.file_label.text(), dialog.build_button.text()), ("pkgwidget-1.2.3-icueapps.icuewidget", "Build for iCUE Apps"))
        self.assertTrue(dialog.apps_card.isVisibleTo(dialog) and dialog.cli_label.isVisibleTo(dialog))
        self.assertIn("0.0.0-fake", dialog.cli_label.text())
        self.assertEqual((dialog.min_version_edit.text(), dialog.min_version_edit.isReadOnly()), (DOCUMENTED_MIN_APP_VERSION, False))
        self.assertIn("docs.elgato.com/icue/widgets", dialog.min_version_source.text())
        self.assertIn("build copy only", dialog.min_version_source.text())
        for bad in ("", "5", "5.x", "latest"):
            dialog.min_version_edit.setText(bad)
            self.assertFalse(dialog.build_button.isEnabled(), bad)
            self.assertFalse(dialog.start())
        dialog.min_version_edit.setText("5.52")
        self.assertTrue(dialog.build_button.isEnabled())
        dialog.output_edit.setText(str(self.out))
        result = self.run_dialog(dialog)
        self.assertTrue(result.ok, dialog.log.toPlainText())
        with zipfile.ZipFile(result.output) as zf:
            self.assertEqual(json.loads(zf.read("manifest.json"))["min_app_version"], "5.52")
        self.assertNotIn("min_app_version", self.manifest())
        dialog.target_combo.setCurrentIndex(dialog.target_combo.findData(TARGET_ICUE5))  # switching target updates the dialog
        self.assertEqual((dialog.target, dialog.file_label.text()), (TARGET_ICUE5, "pkgwidget-1.2.3-icue5.icuewidget"))
        self.assertFalse(dialog.apps_card.isVisibleTo(dialog) or dialog.open_button.isVisibleTo(dialog))

    def test_an_existing_min_app_version_is_shown_read_only(self) -> None:
        for value, tone in (("5.52", "success"), ("5", "error")):
            with self.subTest(value=value):
                shutil.rmtree(self.src)
                make_widget(self.src, min_app_version=value)
                dialog = PackageDialog(self.src, settings=self.settings(), path_env=self.path_env, target=TARGET_ICUEAPPS)
                self.addCleanup(dialog.close)
                self.assertEqual((dialog.min_version_edit.text(), dialog.min_version_edit.isReadOnly()), (value, True))
                self.assertEqual(dialog.min_version_source.property("tone"), tone)
                self.assertTrue(dialog.build_button.isEnabled())  # an invalid value is reported by the validator, never rewritten

    def test_dialog_reports_a_bad_folder_and_a_missing_cli_only_for_icue_apps(self) -> None:
        dialog = PackageDialog(self.src, settings=self.settings(), path_env=self.path_env)
        self.addCleanup(dialog.close)
        dialog.output_edit.setText(str(self.src / "dist"))
        self.assertFalse(dialog.start())
        self.assertIn("Choose another folder", dialog.status.text())
        self.assertIsNone(self.settings().value(SETTINGS_KEY))  # a refused folder is not remembered
        missing = PackageDialog(self.src, settings=self.settings(), path_env=str(self.tmp / "empty"))
        self.addCleanup(missing.close)
        self.assertEqual(missing.log.toPlainText(), "")  # iCUE 5 needs no CLI
        missing.set_target(TARGET_ICUEAPPS)
        self.assertIn("Not found", missing.cli_label.text())
        self.assertIn("npm install -g icuewidget-cli", missing.log.toPlainText())
        missing.output_edit.setText(str(self.out))
        self.assertFalse(missing.start())
        self.assertTrue(missing.status.text().startswith("CLI missing"))

    def test_a_validation_error_is_shown_in_the_dialog(self) -> None:
        make_widget(self.src, version="1.2")
        dialog = PackageDialog(self.src, settings=self.settings(), path_env=self.path_env, target=TARGET_ICUEAPPS)
        self.addCleanup(dialog.close)
        dialog.output_edit.setText(str(self.out))
        result = self.run_dialog(dialog)
        self.assertFalse(result.ok)
        self.assertTrue(dialog.status.text().startswith("Failed: Validation failed"))
        self.assertIn("semantic version", dialog.log.toPlainText())
        self.assertFalse(dialog.open_button.isVisibleTo(dialog))


@unittest.skipUnless(NODE, "node.exe is needed to run the fake CLI")
class WindowTests(PageTestCase):
    def test_packaging_touches_no_instance_setting_viewport_or_live_reload(self) -> None:
        helper = PackagerCase("run")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        window = self.window
        self.assertEqual(window.package_button.text(), "Build Package")
        self.assertEqual([a.text() for a in window.package_menu.actions()], ["Build for iCUE 5", "Build for iCUE Apps"])
        window.select_device("dash_accessory_lcd", "SizeL", "portrait")
        self.assertTrue(window.load_folder(helper.src))
        self.assertTrue(self.wait(lambda: window.state is State.LOADED))
        window._on_setting_committed("label", "kept")
        self.pump(0.5)
        before = (json.dumps(window.store.get(window.active.instance.id).to_record(), sort_keys=True), window.active.reload_count,
                  window.viewport.size, window.size().toTuple(), len(window.runtimes), len(window._watchers))
        settings = QSettings(str(helper.tmp / "w.ini"), QSettings.Format.IniFormat)
        for target in (TARGET_ICUE5, TARGET_ICUEAPPS):
            with self.subTest(target=target):
                dialog = window.open_package_dialog(settings=settings, path_env=helper.path_env, target=target)
                self.addCleanup(dialog.close)
                self.assertEqual((dialog.source, dialog.target), (helper.src.resolve(), target))
                dialog.output_edit.setText(str(helper.out))
                done: list = []
                dialog.buildFinished.connect(done.append)
                self.assertTrue(dialog.start())
                self.assertTrue(self.wait(lambda: bool(done), 60))
                self.assertTrue(done[0].ok, dialog.log.toPlainText())
                dialog.close()
        self.pump(1.0)  # longer than the watcher debounce: a write into the source would have reloaded
        after = (json.dumps(window.store.get(window.active.instance.id).to_record(), sort_keys=True), window.active.reload_count,
                 window.viewport.size, window.size().toTuple(), len(window.runtimes), len(window._watchers))
        self.assertEqual(after, before)
        self.assertEqual(self.js("label"), "kept")
        self.assertEqual(sorted(p.name for p in helper.out.iterdir()), ["pkgwidget-1.2.3-icue5.icuewidget", "pkgwidget-1.2.3-icueapps.icuewidget"])

    def test_menu_actions_open_the_dialog_for_their_target(self) -> None:
        helper = PackagerCase("run")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.assertTrue(self.window.load_folder(helper.src))
        self.assertTrue(self.wait(lambda: self.window.state is State.LOADED))
        for action, target in ((self.window.package_icue5_action, TARGET_ICUE5), (self.window.package_icueapps_action, TARGET_ICUEAPPS)):
            action.trigger()
            dialog = self.window._package_dialog
            self.addCleanup(dialog.close)
            self.assertEqual(dialog.target, target)
            dialog.close()

    def test_no_widget_open_gives_no_dialog(self) -> None:
        self.assertIsNone(self.window.open_package_dialog())
        self.assertIsNone(self.window.open_package_dialog(target=TARGET_ICUEAPPS))


@unittest.skipUnless(BASKETBALL.is_dir() and SMOKECLOCK.is_dir() and RSS.is_dir(), "WidgetsXE widgets not present")
class RealWidgetSourceTests(unittest.TestCase):
    """Package root, file name and the expected archive content of the real widgets (read-only; the real CLI is not run here)."""

    def test_roots_names_and_expected_entries(self) -> None:
        for folder in (RSS, BASKETBALL, SMOKECLOCK):
            with self.subTest(widget=folder.name):
                root = packager.package_root(folder)
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
                for target in (TARGET_ICUE5, TARGET_ICUEAPPS):
                    self.assertTrue(packager.package_file_name(manifest, root, target).endswith(f"-{manifest['version']}-{target}.icuewidget"))
                entries = packager.expected_entries(root)
                self.assertIn("manifest.json", entries)
                self.assertIn("index.html", entries)
                self.assertEqual(packager.check_icue5_source(root), [])
                self.assertIsNotNone(packager.check_output_folder(root.parent, root))  # the CLI default location is refused
                repo = packager.repository_root(root)
                if repo is not None:
                    self.assertIn("repository", packager.check_output_folder(repo / "Packages", root))


REAL_CLI = packager.find_cli()


def make_compliant_widget(root: Path, manifest_extra: dict | None = None, drop: tuple = ()) -> Path:
    """A small widget that satisfies icuewidget-cli 0.5.1 (all required manifest keys, self-closed void elements in <head>)."""
    (root / "resources").mkdir(parents=True, exist_ok=True)
    manifest = {"author": "WidgetDevTool tests", "id": "com.widgetdevtool.realcliprobe", "name": "Real CLI Probe", "description": "U2 fixture",
                "version": "1.0.0", "preview_icon": "resources/icon.svg", "min_app_version": "5.45", "min_framework_version": "1.0.0",
                "os": [{"platform": "windows"}], "supported_devices": [{"type": "dashboard_lcd"}], **(manifest_extra or {})}
    for key in drop:
        manifest.pop(key, None)
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (root / "resources" / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><rect width="16" height="16"/></svg>', encoding="utf-8")
    (root / "translation.json").write_text(json.dumps({"en": {"translation": {"Probe": "Probe"}}}), encoding="utf-8")
    (root / "index.html").write_text('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8" />\n<title>tr(\'Probe\')</title>\n'
                                     '<link rel="icon" type="image/svg+xml" href="resources/icon.svg" />\n</head>\n<body>probe'
                                     '<script>icueEvents = { onICUEInitialized: function () {} };</script></body>\n</html>\n', encoding="utf-8")
    return root


@unittest.skipUnless(REAL_CLI, "the real icuewidget CLI is not installed (npm install -g icuewidget-cli)")
class RealCliTests(unittest.TestCase):
    """The REAL icuewidget CLI on a compliant fixture (not WidgetsXE): proves the success path, the real archive and the validate gate."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.cli = packager.probe_cli(packager.find_cli())

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wdt real cli ü ")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "Ausgabe ü"

    def test_the_installed_cli_reports_version_and_output_option(self) -> None:
        self.assertRegex(self.cli.version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(self.cli.output_flag, "--output")

    def test_dialog_builds_a_real_package_and_offers_the_output_folder(self) -> None:
        src = make_compliant_widget(self.tmp / "src ü" / "probe widget")
        settings = QSettings(str(self.tmp / "s.ini"), QSettings.Format.IniFormat)
        dialog = PackageDialog(src, settings=settings, target=TARGET_ICUEAPPS)
        self.addCleanup(dialog.close)
        dialog.output_edit.setText(str(self.out))
        done: list = []
        dialog.buildFinished.connect(done.append)
        self.assertTrue(dialog.start())
        wait_for(self.app, done, 120)
        result = done[0]
        self.assertTrue(result.ok, result.log())
        self.assertEqual(result.output.name, "realcliprobe-1.0.0-icueapps.icuewidget")
        self.assertEqual([s.name for s in result.steps], ["validate", "package"])
        self.assertIn("Widget is valid", result.steps[0].stdout)
        self.assertIn("Package created", result.steps[1].stdout)
        with zipfile.ZipFile(result.output) as zf:
            self.assertEqual(sorted(zf.namelist()), ["index.html", "manifest.json", "resources/icon.svg", "translation.json"])
            self.assertIsNone(zf.testzip())
            self.assertEqual(zf.read("manifest.json"), (src / "manifest.json").read_bytes())  # existing min_app_version kept as it is
        self.assertTrue(result.archive_check["ok"])
        self.assertTrue(dialog.open_button.isVisibleTo(dialog))
        self.assertFalse(any(p.suffix == ".icuewidget" for p in src.parent.rglob("*")))  # nothing next to the source

    def test_a_missing_min_app_version_is_added_to_the_copy_and_the_real_cli_accepts_it(self) -> None:
        src = make_compliant_widget(self.tmp / "nomin" / "probe", drop=("min_app_version",))
        before = (src / "manifest.json").read_bytes()
        result = packager.build(self.cli, src, self.out, json.loads(before), target=TARGET_ICUEAPPS, min_app_version=DOCUMENTED_MIN_APP_VERSION)
        self.assertTrue(result.ok, result.log())
        with zipfile.ZipFile(result.output) as zf:
            self.assertEqual(json.loads(zf.read("manifest.json")), dict(json.loads(before), min_app_version=DOCUMENTED_MIN_APP_VERSION))
        self.assertEqual((src / "manifest.json").read_bytes(), before)

    def test_a_real_validation_error_stops_before_package(self) -> None:
        src = make_compliant_widget(self.tmp / "bad" / "probe", manifest_extra={"min_app_version": "5"})
        result = packager.build(self.cli, src, self.out, json.loads((src / "manifest.json").read_text(encoding="utf-8")), target=TARGET_ICUEAPPS)
        self.assertEqual((result.ok, [s.name for s in result.steps], result.steps[0].exit_code), (False, ["validate"], 1))
        self.assertIn("min_app_version must use iCUE version format", result.steps[0].stderr)
        self.assertFalse(any(self.out.glob("*.icuewidget")))


@unittest.skipUnless(SENSORBAR.is_dir(), "WidgetsXE Sensor Bar not present")
class RealSensorBarTests(unittest.TestCase):
    """Sensor Bar (WidgetsXE, read-only) for both targets into a temp folder; the source must stay byte-identical."""
    REFERENCE = SENSORBAR.parents[1] / "sensorbar-1.0.6.icuewidget"  # the package that works in iCUE 5

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wdt sensorbar ")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "out"
        self.before = tree_hashes(SENSORBAR)
        self.manifest = json.loads((SENSORBAR / "manifest.json").read_text(encoding="utf-8-sig"))
        self.addCleanup(lambda: self.assertEqual(tree_hashes(SENSORBAR), self.before))

    def test_icue5_package_has_the_layout_of_the_working_package_and_the_original_manifest(self) -> None:
        result = packager.build(None, SENSORBAR, self.out, self.manifest, target=TARGET_ICUE5)
        self.assertTrue(result.ok, result.log())
        self.assertEqual(result.output.name, f"sensorbar-{self.manifest['version']}-icue5.icuewidget")
        with zipfile.ZipFile(result.output) as zf:
            infos = zf.infolist()
            self.assertEqual(sorted(i.filename for i in infos), packager.expected_entries(SENSORBAR))
            self.assertEqual(zf.read("manifest.json"), (SENSORBAR / "manifest.json").read_bytes())
            self.assertNotIn("min_app_version", json.loads(zf.read("manifest.json")))
            mine = {(i.compress_type, i.is_dir(), "/" in i.filename or i.filename in ("index.html", "manifest.json", "translation.json")) for i in infos}
        if self.REFERENCE.is_file():
            with zipfile.ZipFile(self.REFERENCE) as ref:
                reference = {(i.compress_type, i.is_dir(), "/" in i.filename or i.filename in ("index.html", "manifest.json", "translation.json"))
                             for i in ref.infolist()}
                self.assertEqual(mine, reference)  # Deflate, no directory entries, files at the root / in sub folders as in the working package
                self.assertTrue({"manifest.json", "index.html"} <= set(ref.namelist()))

    def test_icue_apps_runs_the_real_validator_on_a_copy_and_reports_its_error(self) -> None:
        if not REAL_CLI:
            self.skipTest("the real icuewidget CLI is not installed")
        cli = packager.probe_cli(packager.find_cli())
        stages = staging_dirs()
        for legacy_mode in (False, True):
            with self.subTest(legacy_mode=legacy_mode):
                result = packager.build(cli, SENSORBAR, self.out, self.manifest, target=TARGET_ICUEAPPS, min_app_version=DOCUMENTED_MIN_APP_VERSION,
                                        legacy_mode=legacy_mode)
                self.assertFalse(result.ok)
                self.assertEqual([s.name for s in result.steps], ["validate"])
                self.assertIn("index.html", result.steps[0].stderr)
                self.assertIn("min_app_version", [c.description.split('"')[1] for c in result.changes if c.file == "manifest.json"])
                self.assertFalse(any(self.out.glob("*.icuewidget")))
        self.assertEqual(staging_dirs(), stages)


LEGACY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>tr('Legacy Probe')</title>
    <!-- iCUE 5.41+ restrictions: JSON-based ONLY -->
    <!-- a comment with <link rel="x" data-note="kept"> stays as it is -->
    <link rel="icon" type="image/svg+xml" href="resources/icon.svg">
    <meta name="x-icue-property" content="label" data-label="tr('Label')" data-type="textfield" data-default="'hello'">
    <link rel="stylesheet" href="styles/main.css">
</head>
<body>
    BODY_MARKUP
    <img src="resources/icon.svg" alt="body img keeps its form">
    <script>icueEvents = { onICUEInitialized: function () {} };</script>
</body>
</html>
"""


def make_legacy_widget(root: Path, header: bool = False, min_app_version: str | None = None) -> Path:
    make_compliant_widget(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("min_app_version")
    if min_app_version:
        manifest["min_app_version"] = min_app_version
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (root / "styles").mkdir(exist_ok=True)
    (root / "styles" / "main.css").write_text("header { color: red; } .x { color: blue; }", encoding="utf-8")
    body = '<header class="top">title</header>' if header else '<div class="top">title</div>'
    (root / "index.html").write_text(LEGACY_HTML.replace("BODY_MARKUP", body), encoding="utf-8")
    return root


class LegacyAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wdt legacy ü ")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_analysis_lists_every_issue_and_marks_what_is_not_fixed(self) -> None:
        root = make_legacy_widget(self.tmp / "w", header=True)
        issues = {i.rule: i for i in legacy.analyze(root)}
        self.assertEqual(set(issues), {"min_app_version", "void-elements", "head-lookalike"})
        self.assertTrue(issues["min_app_version"].fixable and issues["void-elements"].fixable)
        self.assertFalse(issues["head-lookalike"].fixable)
        self.assertEqual(issues["void-elements"].lines, [4, 8, 9, 10])  # comment content (line 7) is not counted, as in the CLI
        self.assertEqual(legacy.analyze(make_compliant_widget(self.tmp / "ok")), [])

    def test_self_closing_touches_only_open_void_tags_in_head(self) -> None:
        html = LEGACY_HTML.replace("BODY_MARKUP", "<br>")
        fixed, count = legacy.self_close_head(html)
        self.assertEqual(count, 4)
        self.assertIn('<meta charset="UTF-8" />', fixed)
        self.assertIn('<link rel="stylesheet" href="styles/main.css" />', fixed)
        self.assertIn('<link rel="x" data-note="kept">', fixed)  # inside a comment: unchanged
        self.assertEqual(fixed.split("</head>")[1], html.split("</head>")[1])  # body byte-identical (its <br>/<img> untouched)
        self.assertEqual(legacy.self_close_head(fixed), (fixed, 0))  # idempotent
        self.assertEqual(len(fixed) - len(html), 4 * 2)  # only ">" -> " />" per tag

    def test_hints_come_from_the_widgets_own_files_and_are_never_applied(self) -> None:
        root = make_legacy_widget(self.tmp / "project" / "widget" / "legacy")
        (self.tmp / "project" / "listing.md").write_text("- **Compatibility**: iCUE 5.47 or higher\n", encoding="utf-8")
        hints = legacy.min_version_hints(root)
        self.assertEqual(sorted({h[0] for h in hints}), ["5.41", "5.47"])
        self.assertIn("listing.md", {Path(h[1]).name for h in hints})
        self.assertEqual(legacy.min_version_hints(make_compliant_widget(self.tmp / "clean" / "w")), [("5.45", "w\\manifest.json" if os.name == "nt" else "w/manifest.json", 8, '"min_app_version": "5.45",')])

    def test_staging_applies_only_the_two_changes_and_never_writes_the_source(self) -> None:
        root = make_legacy_widget(self.tmp / "src")
        before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        with self.assertRaises(ValueError):
            legacy.stage(root, None)  # no guessing
        with self.assertRaises(ValueError):
            legacy.stage(root, "5")
        staging = legacy.stage(root, "5.41")
        try:
            self.assertIn(legacy.STAGE_PREFIX, staging.temp_dir.name)
            self.assertEqual(staging.changed_files, ["index.html", "manifest.json"])
            self.assertEqual([c.file for c in staging.changes], ["manifest.json", "index.html"])
            manifest_old, manifest_new = (root / "manifest.json").read_text(encoding="utf-8"), (staging.root / "manifest.json").read_text(encoding="utf-8")
            self.assertEqual(manifest_new.replace('  "min_app_version": "5.41",\n', ""), manifest_old)  # only one line inserted
            self.assertEqual(json.loads(manifest_new)["min_app_version"], "5.41")
            self.assertIn('+  "min_app_version": "5.41",', staging.diff)
            self.assertIn('+    <link rel="stylesheet" href="styles/main.css" />', staging.diff)
            self.assertEqual((staging.root / "styles" / "main.css").read_bytes(), before["styles/main.css"])  # CSS untouched
        finally:
            staging.cleanup()
        self.assertFalse(staging.temp_dir.exists())
        self.assertEqual({p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}, before)

    def test_without_legacy_only_min_app_version_is_added(self) -> None:
        root = make_legacy_widget(self.tmp / "src")
        staging = legacy.stage(root, "5.47", self_close=False)
        try:
            self.assertEqual((staging.changed_files, [c.file for c in staging.changes]), (["manifest.json"], ["manifest.json"]))
            self.assertEqual((staging.root / "index.html").read_bytes(), (root / "index.html").read_bytes())
        finally:
            staging.cleanup()

    def test_an_existing_min_app_version_is_kept_and_no_value_is_needed(self) -> None:
        root = make_legacy_widget(self.tmp / "src", min_app_version="5.47")
        staging = legacy.stage(root, None)
        try:
            self.assertEqual(json.loads((staging.root / "manifest.json").read_text(encoding="utf-8"))["min_app_version"], "5.47")
            self.assertEqual(staging.changed_files, ["index.html"])
        finally:
            staging.cleanup()


class LegacyBuildFakeCliTests(PackagerCase):
    def test_legacy_build_stages_reports_differences_and_removes_the_staging_copy(self) -> None:
        src = make_legacy_widget(self.tmp / "lsrc" / "legacy")
        before_src = {p.relative_to(src).as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        result = packager.build(self.cli, src, self.out, manifest, target=TARGET_ICUEAPPS, legacy_mode=True, min_app_version="5.41")
        self.assertTrue(result.ok, result.log())
        self.assertEqual([c.file for c in result.changes], ["manifest.json", "index.html"])
        self.assertEqual(result.differs_from_source, ["index.html", "manifest.json"])
        validate_dir = Path([c for c in self.calls() if c[0] == "validate"][-1][1])
        self.assertTrue(validate_dir.name == "legacy" and legacy.STAGE_PREFIX in str(validate_dir))  # the CLI ran on the staging copy
        with zipfile.ZipFile(result.output) as zf:
            self.assertIn(b'"min_app_version": "5.41"', zf.read("manifest.json"))
            self.assertEqual(zf.read("styles/main.css"), before_src["styles/main.css"])
        self.assert_clean()
        self.assertEqual({p.relative_to(src).as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}, before_src)
        self.assertIn("Changes in the temporary build copy", result.log())

    def test_a_failed_legacy_build_keeps_the_old_package_and_removes_staging(self) -> None:
        src = make_legacy_widget(self.tmp / "lsrc3" / "legacy")
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        options = dict(target=TARGET_ICUEAPPS, legacy_mode=True, min_app_version="5.41")
        self.assertTrue(packager.build(self.cli, src, self.out, manifest, **options).ok)
        target = self.out / "realcliprobe-1.0.0-icueapps.icuewidget"
        old = target.read_bytes()
        os.environ["FAKE_ICUEWIDGET_MODE"] = "fail"
        result = packager.build(self.cli, src, self.out, manifest, confirm_overwrite=lambda p: True, **options)
        self.assertFalse(result.ok)
        self.assertEqual(target.read_bytes(), old)
        self.assert_clean()

    def test_dialog_legacy_mode_is_off_by_default_and_previews_the_build_copy(self) -> None:
        src = make_legacy_widget(self.tmp / "dsrc" / "legacy")
        dialog = PackageDialog(src, settings=QSettings(str(self.tmp / "l.ini"), QSettings.Format.IniFormat), path_env=self.path_env,
                               target=TARGET_ICUEAPPS)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.legacy_check.isChecked())
        self.assertTrue(dialog.build_button.isEnabled())
        self.assertIn("validation will fail; Legacy Compatibility can fix it", dialog.hints_label.text())
        self.assertIn("5.41", dialog.hints_label.text())  # hint shown, NOT filled in
        self.assertEqual(dialog.min_version_edit.text(), DOCUMENTED_MIN_APP_VERSION)
        dialog.legacy_check.setChecked(True)
        self.assertIn("handled by Legacy Compatibility", dialog.hints_label.text())
        dialog.min_version_edit.setText("5.41")
        text = dialog.preview_changes()
        self.assertIn("[fixed] manifest.min_app_version is missing", text)
        self.assertIn('+  "min_app_version": "5.41",', text)
        self.assertIn("self-closed 4 void element(s)", text)
        dialog.output_edit.setText(str(self.out))
        done: list = []
        dialog.buildFinished.connect(done.append)
        self.assertTrue(dialog.start())
        wait_for(self.app, done)
        self.assertTrue(done[0].ok, dialog.log.toPlainText())
        self.assertIn("self-closed 4 void element(s)", dialog.log.toPlainText())


@unittest.skipUnless(REAL_CLI, "the real icuewidget CLI is not installed")
class LegacyRealCliTests(RealCliTests):
    def test_the_real_cli_rejects_the_legacy_source_and_accepts_the_staging_copy(self) -> None:
        src = make_legacy_widget(self.tmp / "src" / "legacy")
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        plain = packager.build(self.cli, src, self.out, manifest, target=TARGET_ICUEAPPS, min_app_version="5.41")
        self.assertFalse(plain.ok)
        self.assertIn("non-self-closed", plain.steps[0].stderr)  # min_app_version is handled; the next real error is reported, not fixed
        result = packager.build(self.cli, src, self.out, manifest, target=TARGET_ICUEAPPS, legacy_mode=True, min_app_version="5.41")
        self.assertTrue(result.ok, result.log())
        self.assertEqual(result.differs_from_source, ["index.html", "manifest.json"])
        self.assertIn("Widget is valid", result.steps[0].stdout)
        self.assertTrue(any("below the documented minimum" in n for n in result.notes))

    def test_a_remaining_error_is_reported_not_fixed(self) -> None:
        src = make_legacy_widget(self.tmp / "src2" / "legacy", header=True)
        manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
        stages = staging_dirs()
        result = packager.build(self.cli, src, self.out, manifest, target=TARGET_ICUEAPPS, legacy_mode=True, min_app_version="5.47")
        self.assertFalse(result.ok)
        self.assertEqual([s.name for s in result.steps], ["validate"])
        self.assertIn("<header", result.steps[0].stderr)
        self.assertFalse(any(self.out.glob("*.icuewidget")))
        self.assertEqual(staging_dirs(), stages)


@unittest.skipUnless(BASKETBALL.is_dir() and SMOKECLOCK.is_dir() and RSS.is_dir(), "WidgetsXE widgets not present")
class LegacyRealWidgetAnalysisTests(unittest.TestCase):
    def test_the_three_widgets(self) -> None:
        expected = {RSS: {"head-lookalike"}, BASKETBALL: {"head-lookalike"}, SMOKECLOCK: set()}
        for folder, blockers in expected.items():
            with self.subTest(widget=folder.name):
                issues = legacy.analyze(folder)
                self.assertEqual({i.rule for i in issues if i.fixable}, {"min_app_version", "void-elements"})
                self.assertEqual({i.rule for i in issues if not i.fixable}, blockers)


if __name__ == "__main__":
    unittest.main()
