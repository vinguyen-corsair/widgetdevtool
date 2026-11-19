r"""Multi-instance host tests (D7b): several widget instances running at once, each with its own settings, WebEngine profile,
localStorage and lifecycle.

Expected behaviour comes from the iCUE v5.55 source (Widget.cpp, HtmlHelpers.cpp htmlWidgetStorageName, WidgetDeletionManagerImpl.cpp,
WidgetCacheCleanerImpl.cpp). That the isolation matches iCUE's own profile storage is NOT claimed: only that two profiles named by
that formula do not share localStorage in QtWebEngine 6.9.3 (measured here). Tests on real widgets are skipped when absent.
Run: .venv\Scripts\python.exe -m unittest discover -s tests -v
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import shiboken6  # noqa: E402
from PySide6.QtWebEngineCore import QWebEngineProfile  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402  (before QApplication)

from test_runtime_bridge import WIDGET_HTML, PageTestCase, write_widget  # noqa: E402
from test_settings_panel import BASKETBALL, RSS, SMOKECLOCK, classify_console_error  # noqa: E402
from widgetdevtool.instance_storage import StorageLayout, storage_name  # noqa: E402
from widgetdevtool.main_window import MainWindow, State  # noqa: E402

PERSONALIZED_HTML = """<!doctype html><html><head><title>p</title>
<meta name="x-icue-property" content="label" data-type="textfield" data-default="'hello'">
<meta name="x-icue-property" content="textColor" data-type="color" data-default="'#123456'">
<script>window.__updates = 0; icueEvents = { onDataUpdated: function () { window.__updates++; } };</script>
</head><body>x</body></html>"""


class MultiInstanceCase(PageTestCase):
    def rjs(self, runtime, script: str):
        """Run JavaScript in the page of ONE instance (not necessarily the active one)."""
        box: dict = {}
        runtime.page.runJavaScript(script, lambda v: box.setdefault("v", v))
        self.assertTrue(self.wait(lambda: "v" in box, 10), script)
        return box["v"]

    def open(self, root: Path, window: MainWindow | None = None, **kwargs):
        window = window or self.window
        self.assertTrue(window.load_folder(root, **kwargs))
        runtime = window.active
        self.assertTrue(self.wait(lambda: runtime.state is State.LOADED), f"state={runtime.state}")
        self.pump(0.3)
        return runtime

    def widget(self, name: str = "w", html: str = WIDGET_HTML) -> Path:
        return write_widget(Path(self._tmp.name) / name, html=html)

    def persistent_window(self, data_dir: Path) -> MainWindow:
        window = MainWindow(data_dir=data_dir)
        self.addCleanup(lambda: (window.shutdown(), self.pump(0.3)))
        return window


class StorageNameTests(unittest.TestCase):
    """htmlWidgetStorageName(uniqueId, typeId, isPreview) of HtmlHelpers.cpp."""

    ID = "11111111-1111-4111-8111-111111111111"

    def test_formula(self) -> None:
        digest = hashlib.md5(b"com.test.type").hexdigest()
        self.assertEqual(storage_name("{" + self.ID + "}", "com.test.type"), f"{self.ID}_{digest}")
        self.assertEqual(storage_name(self.ID, "com.test.type"), f"{self.ID}_{digest}")  # braces do not matter
        self.assertEqual(storage_name(self.ID, "com.test.type", True), f"{self.ID}_{digest}_p")
        self.assertNotEqual(storage_name(self.ID, "other"), storage_name(self.ID, "com.test.type"))

    def test_a_garbage_id_becomes_the_null_uuid_like_quuid_fromstring(self) -> None:
        self.assertTrue(storage_name("not a uuid", "t").startswith("00000000-0000-0000-0000-000000000000_"))

    def test_paths_stay_inside_the_tool_storage_and_never_inside_icue(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            layout = StorageLayout(tmp)
            self.assertEqual(layout.path("abc").parent, (Path(tmp) / "storage").resolve())
            for bad in ("..", "../x", "a/b", "", "..\\x"):
                with self.assertRaises(ValueError):
                    layout.path(bad)
            with self.assertRaises(ValueError):
                StorageLayout(Path(tmp) / "Corsair" / "iCUE")


class InstanceManagementTests(MultiInstanceCase):
    def test_two_instances_of_one_widget_run_together_with_separate_ids_settings_and_storage(self) -> None:
        root = self.widget()
        first = self.open(root)
        second = self.open(root)  # Add: same widget, new instance
        self.assertEqual(len(self.window.runtimes), 2)
        self.assertNotEqual(first.instance.id, second.instance.id)
        self.assertNotEqual(first.storage_name, second.storage_name)
        self.assertEqual(self.rjs(first, "uniqueId"), first.instance.unique_id())
        self.assertEqual(self.rjs(second, "uniqueId"), second.instance.unique_id())
        self.assertEqual(self.window.instance_list.count(), 2)

    def test_selecting_an_instance_shows_its_own_settings_and_page(self) -> None:
        root = self.widget()
        first, second = self.open(root), self.open(root)
        self.window._on_setting_committed("label", "second-label")
        self.assertTrue(self.window.select_instance(first.instance.id))
        self.assertIs(self.window.bridge.instance, first.instance)
        self.assertEqual(self.window.settings_panel.editors["label"].control.text(), "hello")
        self.assertIs(self.window.view, first.view)
        self.assertTrue(not first.view.isHidden() and second.view.isHidden())
        self.assertTrue(self.window.select_instance(second.instance.id))
        self.assertEqual(self.window.settings_panel.editors["label"].control.text(), "second-label")
        self.assertFalse(self.window.select_instance("nope"))

    def test_changing_one_instance_does_not_touch_the_other(self) -> None:
        root = self.widget()
        first, second = self.open(root), self.open(root)
        before = self.rjs(first, "window.__log.length")
        self.window._on_setting_committed("label", "only-second")  # second is active
        self.assertTrue(self.wait(lambda: self.rjs(second, "typeof label !== 'undefined' && label") == "only-second"))
        self.pump(0.3)
        self.assertEqual(self.rjs(first, "label"), "hello")
        self.assertEqual(self.rjs(first, "window.__log.length"), before)  # no onDataUpdated in the first page
        self.assertEqual(first.instance.data["label"], "hello")
        self.assertEqual(second.instance.data["label"], "only-second")

    def test_clone_copies_settings_but_gets_a_new_identity_and_storage(self) -> None:
        root = self.widget()
        original = self.open(root)
        self.window._on_setting_committed("label", "cloned-value")
        self.assertTrue(self.window.clone_instance())
        clone = self.window.active
        self.assertTrue(self.wait(lambda: clone.state is State.LOADED))
        self.assertIsNot(clone, original)
        self.assertNotEqual(clone.instance.id, original.instance.id)
        self.assertNotEqual(clone.storage_name, original.storage_name)
        self.assertEqual((clone.instance.data, clone.instance.source), (original.instance.data, original.instance.source))
        self.assertIsNot(clone.instance.data, original.instance.data)
        self.assertEqual(self.rjs(clone, "label"), "cloned-value")
        self.window._on_setting_committed("label", "clone-only")
        self.assertEqual(original.instance.data["label"], "cloned-value")
        self.assertEqual(len(self.window.store), 2)

    def test_close_keeps_the_instance_and_its_storage_remove_deletes_both(self) -> None:
        root = self.widget()
        keep, gone = self.open(root), self.open(root)
        for runtime in (keep, gone):
            self.rjs(runtime, "localStorage.setItem('k', 'v-" + runtime.instance.id[:4] + "'); 1")
        layout = self.window.storage
        keep_dir, gone_dir = layout.path(keep.storage_name), layout.path(gone.storage_name)
        self.assertTrue(keep_dir.is_dir() and gone_dir.is_dir())
        keep_id = keep.instance.id
        self.assertTrue(self.window.close_instance(keep_id))
        self.pump(0.3)
        self.assertNotIn(keep_id, self.window.runtimes)
        self.assertIn(keep_id, self.window.store)
        self.assertTrue(keep_dir.is_dir(), "closing must not delete the storage")
        self.assertTrue(self.window.remove_instance(gone.instance.id))
        self.assertTrue(self.wait(lambda: not gone_dir.exists() and not self.window.pending_storage_deletes, 15), "storage not deleted")
        self.assertNotIn(gone.instance.id, self.window.store)
        self.assertTrue(keep_dir.is_dir(), "removing one instance must not touch another's storage")

    def test_removing_the_active_instance_activates_a_remaining_one_and_the_last_one_gives_the_empty_state(self) -> None:
        root = self.widget()
        first, second = self.open(root), self.open(root)
        self.assertTrue(self.window.remove_instance(second.instance.id))
        self.assertIs(self.window.active, first)
        self.assertTrue(self.window.remove_instance())
        self.assertIsNone(self.window.active)
        self.assertIs(self.window.state, State.IDLE)
        self.assertIsNone(self.window.view)
        self.assertEqual(len(self.window.store), 0)

    def test_repeated_add_and_remove_releases_the_webengine_objects(self) -> None:
        root = self.widget()
        base = self.open(root)
        released = []
        for _ in range(6):
            runtime = self.open(root)
            released.append((runtime.page, runtime.profile, runtime.view, runtime))
            self.assertTrue(self.window.remove_instance(runtime.instance.id))
        self.pump(0.5)
        self.assertEqual(list(self.window.runtimes), [base.instance.id])
        self.assertEqual([[shiboken6.isValid(o) for o in group] for group in released], [[False] * 4] * 6)
        self.assertEqual(len(self.window.findChildren(QWebEngineView)), 1)
        self.assertTrue(self.wait(lambda: not self.window.pending_storage_deletes, 15))
        self.assertEqual(self.window.storage.names_on_disk(), [base.storage_name])

class StorageIsolationTests(MultiInstanceCase):
    def test_two_instances_of_the_same_type_do_not_read_or_overwrite_each_others_local_storage(self) -> None:
        root = self.widget()
        first, second = self.open(root), self.open(root)
        self.assertIn(self.rjs(first, "localStorage.getItem('shared')"), (None, ""))  # PySide returns "" for null
        self.rjs(first, "localStorage.setItem('shared', 'first'); 1")
        self.assertIn(self.rjs(second, "localStorage.getItem('shared')"), (None, ""))
        self.rjs(second, "localStorage.setItem('shared', 'second'); 1")
        self.assertEqual(self.rjs(first, "localStorage.getItem('shared')"), "first")
        self.assertEqual(self.rjs(second, "localStorage.getItem('shared')"), "second")

    def test_storage_lives_only_below_the_tool_data_folder(self) -> None:
        first = self.open(self.widget())
        self.rjs(first, "localStorage.setItem('k', 'v'); 1")
        folder = self.window.storage.path(first.storage_name)
        self.assertTrue(folder.is_dir())
        self.assertEqual(folder.parent, self.window.storage.storage_dir.resolve())
        self.assertEqual(Path(first.profile.persistentStoragePath()).resolve(), folder)
        self.assertEqual(self.window.storage.root.parent, Path(self.window.storage.root.parent))
        self.assertNotIn("corsair", {p.lower() for p in folder.parts})

    def test_a_restarted_tool_finds_the_same_ids_and_the_same_local_storage(self) -> None:
        data = Path(self._tmp.name) / "data"
        root = self.widget()
        window = self.persistent_window(data)
        first, second = self.open(root, window), self.open(root, window)
        window._on_setting_committed("label", "kept-over-restart")
        self.rjs(first, "localStorage.setItem('k', 'first-value'); 1")
        self.rjs(second, "localStorage.setItem('k', 'second-value'); 1")
        ids = [first.instance.id, second.instance.id]
        window.shutdown()
        self.pump(0.5)
        self.assertTrue((data / "workspace.json").is_file())
        self.assertTrue(all(window.storage.path(n).is_dir() for n in (first.storage_name, second.storage_name)),
                        "closing the tool must not delete the storage")
        again = self.persistent_window(data)
        self.assertEqual(again.restore_workspace(), 2)
        self.assertEqual(list(again.runtimes), ids)
        self.assertEqual(again.active.instance.id, ids[1])
        for runtime in again.runtimes.values():
            self.assertTrue(self.wait(lambda r=runtime: r.state is State.LOADED))
        self.pump(0.3)
        self.assertEqual([r.storage_name for r in again.runtimes.values()], [first.storage_name, second.storage_name])
        self.assertEqual(self.rjs(again.runtimes[ids[0]], "localStorage.getItem('k')"), "first-value")
        self.assertEqual(self.rjs(again.runtimes[ids[1]], "localStorage.getItem('k')"), "second-value")
        self.assertEqual(again.runtimes[ids[1]].instance.data["label"], "kept-over-restart")

    def test_the_workspace_keeps_device_selection_and_size_and_tells_closing_from_removing(self) -> None:
        data = Path(self._tmp.name) / "data2"
        root = self.widget()
        window = self.persistent_window(data)
        shown, closed = self.open(root, window), self.open(root, window)
        profile = next((p for p in window.profiles if p.orientations()), None)
        if profile is not None:
            size = profile.supported_sizes()[-1]
            window.select_device(profile.id, size, profile.orientations()[0])
        closed_id = closed.instance.id
        window.close_instance(closed_id)
        window.shutdown()
        self.pump(0.3)
        again = self.persistent_window(data)
        self.assertEqual(again.restore_workspace(), 1)
        self.assertIn(closed_id, again.store)  # closed instances survive, as stored instances
        self.assertNotIn(closed_id, again.runtimes)
        self.assertEqual(list(again.runtimes), [shown.instance.id])
        if profile is not None:
            self.assertEqual((again.device.id, again.device_size), (profile.id, size))
        self.assertEqual(self.window.pending_storage_deletes, [])


class LifecycleTests(MultiInstanceCase):
    def test_a_source_change_reloads_every_instance_of_that_source_once_with_one_watcher(self) -> None:
        root, other_root = self.widget("a"), self.widget("b")
        first, second, third = self.open(root), self.open(root), self.open(other_root)
        self.assertEqual(len(self.window._watchers), 2)  # one per source folder, not one per instance
        self.rjs(first, "window.__marker = 'before'; 1")
        html = (root / "index.html").read_text(encoding="utf-8").replace("<body>x</body>", "<body>edited</body>")
        (root / "index.html").write_text(html, encoding="utf-8")
        self.assertTrue(self.wait(lambda: first.reload_count >= 1 and second.reload_count >= 1 and not first.reloading
                                  and not second.reloading, 20))
        self.pump(1.0)
        self.assertEqual((first.reload_count, second.reload_count, third.reload_count), (1, 1, 0))
        self.assertEqual(self.rjs(first, "document.body.textContent"), "edited")
        self.assertEqual(self.rjs(second, "document.body.textContent"), "edited")
        self.assertEqual(self.rjs(third, "document.body.textContent"), "x")
        self.assertEqual(self.rjs(first, "typeof window.__marker"), "undefined")  # a real page reload
        self.assertEqual(self.rjs(third, "typeof window.__log"), "object")

    def test_removing_the_last_instance_of_a_source_stops_its_watcher(self) -> None:
        root, other_root = self.widget("a"), self.widget("b")
        first, second = self.open(root), self.open(other_root)
        self.assertEqual(len(self.window._watchers), 2)
        self.window.remove_instance(second.instance.id)
        self.assertEqual(len(self.window._watchers), 1)
        self.window.remove_instance(first.instance.id)
        self.assertEqual(len(self.window._watchers), 0)

    def test_a_reload_keeps_the_user_values_of_each_instance_apart(self) -> None:
        root = self.widget()
        first, second = self.open(root), self.open(root)
        self.window._on_setting_committed("label", "second-edit")
        self.window.select_instance(first.instance.id)
        self.window._on_setting_committed("label", "first-edit")
        self.assertTrue(self.window.reload_now())
        self.assertTrue(self.wait(lambda: first.reload_count == 1 and second.reload_count == 1, 20))
        self.pump(0.5)
        self.assertEqual((self.rjs(first, "label"), self.rjs(second, "label")), ("first-edit", "second-edit"))

    def test_personalization_reaches_every_instance_without_touching_either(self) -> None:
        root = self.widget("p", PERSONALIZED_HTML)
        first, second = self.open(root), self.open(root)
        raw = [dict(first.instance.data), dict(second.instance.data)]
        updates = [self.rjs(first, "window.__updates"), self.rjs(second, "window.__updates")]
        self.assertTrue(self.window.set_personalization("device", "text_color", "#ff0000"))
        self.assertTrue(self.wait(lambda: self.rjs(first, "textColor") == "#ff0000" and self.rjs(second, "textColor") == "#ff0000"))
        self.assertEqual([first.instance.data["textColor"], second.instance.data["textColor"]], [raw[0]["textColor"]] * 2)
        self.pump(0.5)
        self.assertEqual([self.rjs(first, "window.__updates"), self.rjs(second, "window.__updates")], [n + 1 for n in updates])

    def test_changing_the_device_size_loses_neither_instances_nor_settings_and_reloads_nothing(self) -> None:
        root = self.widget()
        first, second = self.open(root), self.open(root)
        self.window._on_setting_committed("label", "stays")
        profile = next((p for p in self.window.profiles if len(p.supported_sizes()) > 1), None)
        if profile is None:
            self.skipTest("no device profile with several sizes")
        for size in profile.supported_sizes():
            self.window.select_device(profile.id, size, profile.orientations()[0])
            self.pump(0.2)
        self.assertEqual(list(self.window.runtimes), [first.instance.id, second.instance.id])
        self.assertEqual((first.reload_count, second.reload_count), (0, 0))
        self.assertEqual(second.instance.data["label"], "stays")
        self.assertEqual(self.rjs(second, "label"), "stays")
        # E3: only the ACTIVE instance (second) follows the chosen size; the first keeps its own (never chosen = the device default)
        self.assertEqual(second.view.geometry().size().toTuple(), self.window.viewport.size)
        self.assertEqual(first.view.geometry().size().toTuple(), self.window.instance_viewport(first.instance).size)
        self.assertIsNone(first.instance.size)


class ReopenTests(MultiInstanceCase):
    """A1: opening a folder (Open Folder, library, command line) reuses the instance; only Add / Clone create one."""

    def counts(self, window: MainWindow | None = None) -> tuple:
        window = window or self.window
        return (len(window.store), len(window.runtimes), len(window._watchers), len(window.findChildren(QWebEngineProfile)))

    def test_opening_the_same_folder_again_never_creates_a_new_instance(self) -> None:
        root = self.widget()
        first = self.open(root)
        self.window._on_setting_committed("label", "mine")
        before = self.counts()
        for opener in (self.window.open_folder, lambda f: self.window.open_folder(str(f) + os.sep)):
            self.assertTrue(opener(root))
            self.pump(0.2)
        self.assertIs(self.window.active, first)
        self.assertEqual(self.counts(), before)
        self.assertEqual(before, (1, 1, 1, 1))
        self.assertEqual((first.reload_count, first.instance.data["label"]), (0, "mine"))  # selected, not reloaded

    def test_a_closed_instance_is_reopened_with_the_same_id_settings_and_local_storage(self) -> None:
        root = self.widget()
        first = self.open(root)
        instance_id, storage = first.instance.id, first.storage_name
        self.window._on_setting_committed("label", "kept")
        self.rjs(first, "localStorage.setItem('k', 'before-close'); 1")
        self.assertTrue(self.window.close_instance(instance_id))
        self.assertEqual(len(self.window.runtimes), 0)
        self.assertTrue(self.window.open_folder(root))
        again = self.window.active
        self.assertTrue(self.wait(lambda: again.state is State.LOADED))
        self.pump(0.3)
        self.assertEqual((again.instance.id, again.storage_name), (instance_id, storage))
        self.assertEqual(self.rjs(again, "label"), "kept")
        self.assertEqual(self.rjs(again, "localStorage.getItem('k')"), "before-close")
        self.assertEqual(self.counts(), (1, 1, 1, 1))

    def test_several_instances_of_one_folder_are_never_merged_and_the_most_recent_is_picked(self) -> None:
        root = self.widget()
        first = self.open(root)
        second = self.open(root)  # Add
        self.assertEqual(self.counts()[:2], (2, 2))
        self.assertTrue(self.window.select_instance(first.instance.id))
        self.assertTrue(self.window.select_instance(second.instance.id))
        self.assertTrue(self.window.select_instance(first.instance.id))
        self.assertTrue(self.window.open_folder(root))
        self.assertIs(self.window.active, first)
        self.window.select_instance(second.instance.id)
        self.window.close_instance(first.instance.id)  # the most recent one is `second`, `first` is closed
        self.assertTrue(self.window.open_folder(root))
        self.assertIs(self.window.active, second)
        self.assertEqual(self.counts()[:2], (2, 1))  # nothing created, nothing deleted
        self.assertTrue(self.window.clone_instance())  # explicit creation still works
        self.assertEqual(len(self.window.store), 3)

    def test_after_a_restart_opening_the_folder_restores_the_instance_and_its_local_storage(self) -> None:
        data = Path(self._tmp.name) / "data_a1"
        root = self.widget()
        window = self.persistent_window(data)
        first = self.open(root, window)
        self.assertTrue(window.open_folder(root))  # the first open already counts: no duplicate
        instance_id = first.instance.id
        self.rjs(first, "localStorage.setItem('k', 'across-restart'); 1")
        window._on_setting_committed("label", "restart-label")
        window.shutdown()
        self.pump(0.5)
        again = self.persistent_window(data)
        # what `python -m widgetdevtool <folder>` does: restore the workspace, then open the folder
        self.assertEqual(again.restore_workspace(), 1)
        self.assertTrue(again.open_folder(root))
        runtime = again.active
        self.assertTrue(self.wait(lambda: runtime.state is State.LOADED))
        self.pump(0.3)
        self.assertEqual(runtime.instance.id, instance_id)
        self.assertEqual(len(again.store), 1)
        self.assertEqual(self.rjs(runtime, "localStorage.getItem('k')"), "across-restart")
        self.assertEqual(self.rjs(runtime, "label"), "restart-label")
        # same, but the instance was closed before the restart: opening the folder reopens it
        again.close_instance(instance_id)
        again.shutdown()
        self.pump(0.5)
        third = self.persistent_window(data)
        third.restore_workspace()
        self.assertEqual(len(third.runtimes), 0)
        self.assertTrue(third.open_folder(root))
        self.assertEqual((third.active.instance.id, len(third.store)), (instance_id, 1))

    def test_reopening_keeps_live_reload_and_settings_editor_working(self) -> None:
        root = self.widget()
        first = self.open(root)
        self.window.open_folder(root)
        html = (root / "index.html").read_text(encoding="utf-8").replace("<body>x</body>", "<body>edited</body>")
        (root / "index.html").write_text(html, encoding="utf-8")
        self.assertTrue(self.wait(lambda: first.reload_count == 1 and not first.reloading, 20))
        self.pump(0.8)
        self.assertEqual(first.reload_count, 1)
        self.window._on_setting_committed("label", "edited-after-reopen")
        self.assertTrue(self.wait(lambda: self.rjs(first, "label") == "edited-after-reopen"))


class ResetStorageTests(MultiInstanceCase):
    def test_reset_clears_only_that_instances_storage_and_keeps_id_and_settings(self) -> None:
        root = self.widget()
        a, b = self.open(root), self.open(root)
        for runtime, value in ((a, "A"), (b, "B")):
            self.rjs(runtime, f"localStorage.setItem('k', '{value}'); 1")
        self.window.select_instance(a.instance.id)
        self.window._on_setting_committed("label", "a-settings")
        a_id, a_storage, b_storage = a.instance.id, a.storage_name, b.storage_name
        record_before = json.dumps(self.window.store.get(a_id).to_record(), sort_keys=True)
        b_dir = self.window.storage.path(b_storage)
        b_files = sorted(p.name for p in b_dir.iterdir())
        self.assertTrue(self.window.reset_storage(a_id))
        fresh = self.window.runtimes[a_id]
        self.assertTrue(self.wait(lambda: fresh.state is State.LOADED))
        self.pump(0.3)
        self.assertIsNot(fresh, a)
        self.assertFalse(shiboken6.isValid(a.page) if a.page else False)
        self.assertEqual((fresh.instance.id, fresh.storage_name), (a_id, a_storage))
        self.assertIn(self.rjs(fresh, "localStorage.getItem('k')"), (None, ""))  # A reset
        self.assertEqual(self.rjs(b, "localStorage.getItem('k')"), "B")  # B untouched
        self.assertEqual(sorted(p.name for p in b_dir.iterdir()), b_files)
        self.assertEqual(self.rjs(fresh, "label"), "a-settings")  # settings are not storage
        self.assertEqual(json.dumps(self.window.store.get(a_id).to_record(), sort_keys=True), record_before)
        self.assertEqual((len(self.window.store), len(self.window.runtimes), len(self.window._watchers),
                          len(self.window.findChildren(QWebEngineProfile))), (2, 2, 1, 2))
        self.assertIs(self.window.active, fresh)

    def test_reset_of_a_background_instance_keeps_the_selection_and_a_closed_one_stays_closed(self) -> None:
        root = self.widget()
        a, b = self.open(root), self.open(root)  # b is active
        self.rjs(a, "localStorage.setItem('k', 'A'); 1")
        self.assertTrue(self.window.reset_storage(a.instance.id))
        self.assertEqual(self.window.active.instance.id, b.instance.id)
        fresh = self.window.runtimes[a.instance.id]
        self.assertTrue(self.wait(lambda: fresh.state is State.LOADED))
        self.pump(0.3)
        self.assertIn(self.rjs(fresh, "localStorage.getItem('k')"), (None, ""))
        self.rjs(fresh, "localStorage.setItem('k', 'again'); 1")
        self.window.close_instance(a.instance.id)
        folder = self.window.storage.path(a.storage_name)
        self.assertTrue(folder.is_dir())
        self.assertTrue(self.window.reset_storage(a.instance.id))
        self.assertFalse(folder.exists())
        self.assertNotIn(a.instance.id, self.window.runtimes)
        self.assertIn(a.instance.id, self.window.store)

    def test_the_button_asks_first_and_nothing_else_in_the_tool_folder_is_touched(self) -> None:
        root = self.widget()
        a = self.open(root)
        self.rjs(a, "localStorage.setItem('k', 'keep'); 1")
        other = self.window.storage.storage_dir / "not-an-instance-folder"
        other.mkdir(parents=True)
        (other / "x.txt").write_text("x")
        asked = []
        self.window._ask_reset = lambda instance: asked.append(instance.id) or False
        self.window.reset_button.click()
        self.pump(0.2)
        self.assertEqual((asked, self.window.runtimes[a.instance.id], self.rjs(a, "localStorage.getItem('k')")), ([a.instance.id], a, "keep"))
        self.window._ask_reset = lambda instance: True
        self.window.reset_button.click()
        fresh = self.window.runtimes[a.instance.id]
        self.assertTrue(self.wait(lambda: fresh.state is State.LOADED))
        self.pump(0.3)
        self.assertIn(self.rjs(fresh, "localStorage.getItem('k')"), (None, ""))
        self.assertTrue((other / "x.txt").is_file())
        self.assertFalse(self.window.reset_storage("no-such-id"))
        self.assertEqual(self.window.pending_storage_deletes, [])

class PerInstanceSizeTests(MultiInstanceCase):
    """E3: size and orientation belong to each INSTANCE; the device (and mode) stay shared. Pages are measured in the real WebEngine."""

    DASH = "dash_accessory_lcd"

    def setUp(self) -> None:
        super().setUp()
        self.window.show()
        self.pump(0.3)
        self.root = self.widget()
        self.window.select_device(self.DASH, "SizeM", "landscape")

    def two(self):
        a = self.open(self.root)
        b = self.open(self.root)
        self.window.select_device(self.DASH, "SizeXL", "portrait")  # B is active
        self.window.select_instance(a.instance.id)
        self.window.select_device(self.DASH, "SizeS", "landscape")  # A is active
        self.pump(0.5)
        return a, b

    def inner(self, runtime) -> str:
        return self.rjs(runtime, "[innerWidth, innerHeight].join('x')")

    def test_each_instance_has_its_own_size_and_the_page_measures_it(self) -> None:
        a, b = self.two()
        self.assertEqual((a.instance.size, a.instance.orientation, b.instance.size, b.instance.orientation), ("SizeS", "landscape", "SizeXL", "portrait"))
        self.assertEqual((self.inner(a), self.inner(b)), ("315x129", "261x951"))
        self.assertEqual((self.window.device_size, self.window.viewport.size), ("SizeS", (315, 129)))
        self.window.select_instance(b.instance.id)
        self.pump(0.3)
        self.assertEqual((self.window.device_size, self.window.device_orientation, self.window.viewport.size), ("SizeXL", "portrait", (261, 951)))
        self.assertEqual((self.window.size_combo.currentData(), self.window.orientation_combo.currentData()), ("SizeXL", "portrait"))  # the bar shows B
        self.window.select_instance(a.instance.id)
        self.assertEqual((self.window.size_combo.currentData(), self.window.orientation_combo.currentData()), ("SizeS", "landscape"))  # ... and A
        self.assertEqual(self.window.viewport_frame.viewport, (315, 129))
        self.assertEqual((self.inner(a), self.inner(b)), ("315x129", "261x951"))  # switching resized nothing

    def test_changing_the_size_of_a_does_not_touch_b_reload_anything_or_resize_the_window(self) -> None:
        a, b = self.two()
        window_size = self.window.size().toTuple()
        b_record = json.dumps(b.instance.to_record(), sort_keys=True)
        self.window._on_setting_committed("label", "a-only")
        for size, orientation in (("SizeL", "landscape"), ("SizeXL", "portrait"), ("SizeM", "landscape"), ("SizeS", "portrait")):
            self.window.select_device(self.DASH, size, orientation)
            self.pump(0.3)
            self.assertEqual(self.inner(a), "x".join(map(str, self.window.viewport.size)))
            self.assertEqual(self.inner(b), "261x951")
        self.assertEqual(json.dumps(b.instance.to_record(), sort_keys=True), b_record)  # B: size, orientation and settings unchanged
        self.assertEqual((a.reload_count, b.reload_count), (0, 0))
        self.assertEqual(self.window.size().toTuple(), window_size)
        self.assertEqual(self.rjs(b, "label"), "hello")
        self.assertEqual(self.rjs(a, "label"), "a-only")

    def test_clone_copies_the_size_but_not_the_identity_or_storage(self) -> None:
        a, b = self.two()
        self.assertTrue(self.window.clone_instance(b.instance.id))
        clone = self.window.active
        self.assertTrue(self.wait(lambda: clone.state is State.LOADED))
        self.pump(0.4)
        self.assertEqual((clone.instance.size, clone.instance.orientation), ("SizeXL", "portrait"))
        self.assertNotEqual((clone.instance.id, clone.storage_name), (b.instance.id, b.storage_name))
        self.assertEqual(self.inner(clone), "261x951")
        self.window.select_device(self.DASH, "SizeL", "landscape")  # only the clone changes
        self.assertEqual((b.instance.size, clone.instance.size, self.inner(b)), ("SizeXL", "SizeL", "261x951"))

    def test_live_reload_reopen_and_reset_storage_keep_the_size(self) -> None:
        a, b = self.two()
        html = (self.root / "index.html").read_text(encoding="utf-8").replace("<body>x</body>", "<body>edited</body>")
        (self.root / "index.html").write_text(html, encoding="utf-8")
        self.assertTrue(self.wait(lambda: a.reload_count == 1 and b.reload_count == 1 and not a.reloading and not b.reloading, 25))
        self.pump(0.8)
        self.assertEqual((a.instance.size, b.instance.size, self.inner(a), self.inner(b)), ("SizeS", "SizeXL", "315x129", "261x951"))
        self.assertTrue(self.window.open_folder(self.root))  # Open Folder: selects an existing instance, never resizes one
        self.assertEqual((a.instance.size, b.instance.size), ("SizeS", "SizeXL"))
        b_id = b.instance.id
        self.assertTrue(self.window.reset_storage(b_id))
        fresh = self.window.runtimes[b_id]
        self.assertTrue(self.wait(lambda: fresh.state is State.LOADED))
        self.pump(0.5)
        self.assertEqual((fresh.instance.size, fresh.instance.orientation, self.inner(fresh), self.inner(a)), ("SizeXL", "portrait", "261x951", "315x129"))
        self.window.close_instance(b_id)
        self.window.select_instance(a.instance.id)
        self.assertTrue(self.window.open_instance(b_id, "restore"))  # a closed instance comes back with its size
        reopened = self.window.active
        self.assertTrue(self.wait(lambda: reopened.state is State.LOADED))
        self.pump(0.5)
        self.assertEqual((reopened.instance.size, self.inner(reopened)), ("SizeXL", "261x951"))

    def test_restart_restores_every_instances_own_size(self) -> None:
        data = Path(self._tmp.name) / "data_e3"
        window = self.persistent_window(data)
        window.show()
        window.select_device(self.DASH, "SizeM", "landscape")
        a, b = self.open(self.root, window), self.open(self.root, window)
        window.select_device(self.DASH, "SizeXL", "portrait")
        window.select_instance(a.instance.id)
        window.select_device(self.DASH, "SizeS", "landscape")
        ids = [a.instance.id, b.instance.id]
        window.shutdown()
        self.pump(0.5)
        again = self.persistent_window(data)
        again.show()
        self.assertEqual(again.restore_workspace(), 2)
        for runtime in again.runtimes.values():
            self.assertTrue(self.wait(lambda r=runtime: r.state is State.LOADED))
        self.pump(0.6)
        ra, rb = (again.runtimes[i] for i in ids)
        self.assertEqual(((ra.instance.size, ra.instance.orientation), (rb.instance.size, rb.instance.orientation)),
                         (("SizeS", "landscape"), ("SizeXL", "portrait")))
        self.assertEqual((self.inner(ra), self.inner(rb)), ("315x129", "261x951"))

    def test_a_device_without_the_stored_size_shows_a_notice_and_gives_the_size_back_later(self) -> None:
        a, b = self.two()  # A active; B stored SizeXL
        self.window.select_device("link_generic_lcd", "SizeM", "landscape")  # no XL on this device
        self.window.select_instance(b.instance.id)
        self.pump(0.4)
        self.assertEqual(b.instance.size, "SizeXL")  # stored value untouched
        self.assertIn("stored size SizeXL is not offered by", self.window.size_notice(b.instance))
        self.assertIn("NOTE", self.window.device_info.text())
        self.assertIn(self.window.device_size, ("SizeS", "SizeM", "SizeL"))
        self.window.select_instance(a.instance.id)
        self.window.select_device(self.DASH, "SizeM", "landscape")  # a device that offers XL again; B was not touched
        self.window.select_instance(b.instance.id)
        self.pump(0.4)
        self.assertEqual((self.window.device_size, self.window.size_notice(b.instance), self.inner(b)), ("SizeXL", "", "261x951"))

    def test_choosing_only_a_device_keeps_the_active_instances_own_size(self) -> None:
        a, b = self.two()
        self.window.select_instance(b.instance.id)  # SizeXL portrait
        self.window.select_device("link_generic_lcd")  # only the device, and it has no XL: the default is SHOWN, XL stays stored
        self.assertEqual((b.instance.size, b.instance.orientation), ("SizeXL", "portrait"))
        self.assertIn("stored size SizeXL is not offered", self.window.size_notice(b.instance))
        self.window.select_device(self.DASH)  # back to a device with XL: B shows XL again without anything being chosen
        self.pump(0.4)
        self.assertEqual((self.window.device_size, self.inner(b)), ("SizeXL", "261x951"))
        self.window.select_instance(a.instance.id)
        self.window.select_device(self.DASH)  # only the device: A keeps ITS size
        self.pump(0.4)
        self.assertEqual((a.instance.size, a.instance.orientation, self.inner(a)), ("SizeS", "landscape", "315x129"))
    def test_a_fixed_size_device_and_free_size_keep_the_instances_sizes(self) -> None:
        a, b = self.two()
        self.window.select_device("cooler_pump_lcd")
        self.pump(0.4)
        self.assertEqual((self.window.device_size, self.inner(a)), (None, "472x472"))
        self.window.select_instance(b.instance.id)  # a hidden page applies its new size when it is shown again
        self.pump(0.5)
        self.assertEqual(self.inner(b), "472x472")
        self.window.select_instance(a.instance.id)
        self.assertEqual((a.instance.size, b.instance.size), ("SizeS", "SizeXL"))
        self.window.select_device(None)
        self.pump(0.3)
        self.assertEqual((a.instance.size, b.instance.size), ("SizeS", "SizeXL"))
        self.window.select_device(self.DASH, "SizeM", "landscape")  # sets the ACTIVE instance (A); B keeps SizeXL
        self.assertEqual((a.instance.size, b.instance.size), ("SizeM", "SizeXL"))
        self.window.select_instance(b.instance.id)  # the hidden page is resized when shown
        self.pump(0.5)
        self.assertEqual(self.inner(b), "261x951")


@unittest.skipUnless(BASKETBALL.is_dir() and SMOKECLOCK.is_dir() and RSS.is_dir(), "WidgetsXE widgets not present")
class RealWidgetTests(MultiInstanceCase):
    """Real widgets only as data; no property or widget name is special-cased by the host."""

    def open_real(self, folder: Path):
        runtime = self.open(folder)
        self.assertTrue(self.wait(lambda: self.rjs(runtime, "typeof iCUE_initialized !== 'undefined' && iCUE_initialized") is True, 30))
        return runtime

    def test_two_basketball_scores_keep_different_favorite_teams_and_separate_storage(self) -> None:
        first = self.open_real(BASKETBALL)
        self.window._on_setting_committed("favoriteTeam", "team-one")
        second = self.open_real(BASKETBALL)
        self.window._on_setting_committed("favoriteTeam", "team-two")
        self.pump(0.5)
        self.assertEqual(self.rjs(first, "favoriteTeam"), "team-one")
        self.assertEqual(self.rjs(second, "favoriteTeam"), "team-two")
        self.assertEqual((first.instance.data["favoriteTeam"], second.instance.data["favoriteTeam"]), ("team-one", "team-two"))
        self.rjs(first, "localStorage.setItem('wdt', 'one'); 1")
        self.assertIn(self.rjs(second, "localStorage.getItem('wdt')"), (None, ""))  # PySide returns "" for null

    def test_rss_feeder_and_smokeclock_still_load_next_to_basketball_without_runtime_errors(self) -> None:
        runtimes = [self.open_real(folder) for folder in (BASKETBALL, RSS, SMOKECLOCK)]
        self.pump(1.0)
        self.assertEqual(len({r.storage_name for r in runtimes}), 3)
        for runtime in runtimes:
            self.assertIs(runtime.state, State.LOADED)
            prefix = runtime.check.folder.resolve().as_uri().lower()
            errors = {e.message[:120] for e in runtime.console.matching("error", widget_only=True)
                      if classify_console_error(e.message) == "runtime" and (not e.source or e.source.lower().startswith(prefix))}
            self.assertEqual(errors, set(), runtime.check.widget_name)


if __name__ == "__main__":
    unittest.main()