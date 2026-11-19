r"""UI layout regression tests (U1): window sizes, panels, preview toolbar, segmented controls, console, theme, package dialog.

Run: .venv\Scripts\python.exe -m unittest discover -s tests -v   (offscreen by default; QT_QPA_PLATFORM=windows for a real window)
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
if os.name == "nt":  # the offscreen platform ships no fonts: without real ones every text width (and so every layout) is wrong
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QRect, QSettings, QSize, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401  (before QApplication)
from PySide6.QtWidgets import QAbstractButton, QApplication, QCheckBox, QComboBox, QLineEdit, QScrollArea, QWidget  # noqa: E402

from test_packager import make_compliant_widget, make_legacy_widget  # noqa: E402
from test_runtime_bridge import PageTestCase, write_widget  # noqa: E402
from test_settings_panel import ALL, meta  # noqa: E402
from widgetdevtool.device_profiles import LANDSCAPE, PORTRAIT  # noqa: E402
from widgetdevtool.main_window import MainWindow  # noqa: E402
from widgetdevtool import theme  # noqa: E402
from widgetdevtool.package_dialog import PackageDialog  # noqa: E402
from widgetdevtool.packager import TARGET_ICUE5, TARGET_ICUEAPPS  # noqa: E402
from widgetdevtool.viewport import FIT, MODE_NOMINAL, MODE_PHYSICAL, NATIVE  # noqa: E402


def controls(root: QWidget) -> list[QWidget]:
    return [w for w in root.findChildren(QWidget) if isinstance(w, (QAbstractButton, QComboBox, QLineEdit)) and not isinstance(w, QCheckBox)
            and w.isVisible()]


def rect_in(window: QWidget, widget: QWidget) -> QRect:
    return QRect(widget.mapTo(window, widget.rect().topLeft()), widget.size())


class MainWindowLayoutTests(PageTestCase):
    def setUp(self) -> None:
        PageTestCase.setUp(self)
        self.window.show()
        self.window.resize(1280, 840)  # the offscreen screen is 800x800: the default size is bounded by the screen
        self.pump(0.3)
        self.load(write_widget(Path(self._tmp.name) / "w"))
        self.window.select_device("dash_accessory_lcd", "SizeM", LANDSCAPE)
        self.pump(0.3)

    def test_default_and_minimum_window_size(self) -> None:
        fresh = MainWindow()
        self.addCleanup(fresh.shutdown)
        fresh.show()
        self.pump(0.2)
        available = fresh.screen().availableGeometry().size()
        self.assertEqual(fresh.size(), QSize(1280, 840).boundedTo(available))
        self.assertEqual(fresh.minimumSize(), QSize(900, 600).boundedTo(available))
        self.assertTrue(QApplication.instance().property("wdtTheme"))

    def test_main_actions_are_in_the_top_bar_with_icons_tooltips_and_one_primary(self) -> None:
        w = self.window
        for widget in (w.open_button, w.import_icue_button, w.package_button):
            self.assertIs(widget.parentWidget(), w.top_bar)
            self.assertFalse(widget.icon().isNull())
            self.assertTrue(widget.toolTip())
        self.assertEqual([b for b in controls(w) if b.property("variant") == "primary"], [w.package_button])

    def test_controls_have_one_height_and_no_truncated_text_at_every_window_size(self) -> None:
        w = self.window
        for size in ((1280, 840), (900, 600)):
            w.resize(*size)
            self.pump(0.3)
            for widget in controls(w.top_bar) + controls(w.instances_panel) + controls(w.preview_header):
                name = f"{size} {widget.metaObject().className()} {getattr(widget, 'text', str)()!r}"
                self.assertEqual(widget.height(), theme.CONTROL_HEIGHT, name)
                self.assertTrue(w.rect().contains(rect_in(w, widget)), name)
                if isinstance(widget, QAbstractButton) and widget.text():
                    self.assertGreaterEqual(widget.width() + 1, widget.sizeHint().width(), f"truncated {name}")

    def test_widget_size_is_a_segmented_control_separate_from_the_preview_scale(self) -> None:
        w = self.window
        instance = w.bridge.instance
        self.assertEqual(w.size_combo.currentText(), "M")
        QTest.mouseClick(w.size_combo.button(2), Qt.MouseButton.LeftButton)  # L
        self.assertEqual((instance.size, w.viewport.size, w.scale_combo.currentData()), ("SizeL", (633, 261), NATIVE))
        w.size_combo.button(2).setFocus()
        QTest.keyClick(w.size_combo.button(2), Qt.Key.Key_Right)  # keyboard: next size
        self.assertEqual(instance.size, "SizeXL")
        w.scale_combo.setCurrentIndex(w.scale_combo.findData(FIT))
        w.scale_combo.activated.emit(w.scale_combo.currentIndex())
        self.assertEqual((instance.size, w.viewport.size), ("SizeXL", (951, 261)))  # the scale never changes the widget size
        w.select_device("taiga_lcd", "SizeS", LANDSCAPE)
        QTest.mouseClick(w.size_combo.button(3), Qt.MouseButton.LeftButton)  # XL: not offered, disabled
        self.assertEqual((w.size_combo.currentText(), w.bridge.instance.size), ("S", "SizeS"))
        self.assertIn("not offered", w.size_combo.button(3).toolTip())

    def test_console_and_side_panels_collapse_without_resizing_the_window(self) -> None:
        w = self.window
        size, frame = w.size().toTuple(), w.viewport_frame.size()
        w.set_console_collapsed(True)
        self.pump(0.2)
        self.assertFalse(w.console_view.isVisible())
        self.assertGreater(w.viewport_frame.height(), frame.height())
        w.set_console_collapsed(False)
        self.pump(0.2)
        self.assertEqual(w.viewport_frame.size(), frame)
        w.instances_toggle.click()
        w.inspector_toggle.click()
        self.pump(0.2)
        self.assertFalse(w.instances_panel.isVisible() or w.inspector.isVisible())
        self.assertGreater(w.viewport_frame.width(), frame.width() + 400)
        w.instances_toggle.click()
        w.inspector_toggle.click()
        self.pump(0.2)
        self.assertEqual((w.size().toTuple(), w.viewport_frame.size()), (size, frame))

    def test_a_wider_window_gives_the_extra_width_to_the_preview(self) -> None:
        w = self.window
        sides = (w.instances_panel.width(), w.inspector.width())
        w.resize(1700, 900)
        self.pump(0.3)
        self.assertEqual((w.instances_panel.width(), w.inspector.width()), sides)
        w.resize(900, 600)
        self.pump(0.3)
        self.assertGreaterEqual(w.viewport_frame.width(), 380)  # the side panels gave width, down to their minimum
        w.resize(1280, 840)
        self.pump(0.3)
        self.assertEqual((w.instances_panel.width(), w.inspector.width()), sides)  # and get it back

    def test_auto_reload_toggle_and_shortcuts(self) -> None:
        w = self.window
        self.assertTrue(w.auto_reload.isCheckable() and w.auto_reload.isChecked())
        w.auto_reload.click()
        self.assertIn("OFF", w.watch_label.text())
        w.auto_reload.click()
        self.assertEqual(w.reload_button.shortcut().toString(), "F5")
        self.assertEqual(w.open_button.shortcut().toString(), "Ctrl+O")
        w.resize(900, 600)  # compact top bar (icons only) keeps the shortcuts and the names as tooltips
        self.pump(0.3)
        self.assertEqual(w.open_button.shortcut().toString(), "Ctrl+O")
        self.assertIn("Open a widget folder", w.open_button.toolTip())

    def test_no_light_theme_leftovers_in_widget_style_sheets(self) -> None:
        sheets = [w.styleSheet() for w in self.window.findChildren(QWidget) if w.styleSheet()]
        self.assertFalse([s for s in sheets if "#fff3cd" in s or "#e7f1ff" in s])


class LayoutGeometryStabilityTests(PageTestCase):
    """Window growth (mandatory): changing Device / Size / Orientation / Mode / Preview scale or switching instances keeps the MainWindow
    (size AND position), both sidebars, the top bar, the console, the preview header and the preview container exactly where they are.
    Only the HTML view inside the preview may change. Nothing is locked: the user can still resize, drag the splitters and toggle panels."""

    def setUp(self) -> None:
        PageTestCase.setUp(self)
        w = self.window
        w.show()
        w.resize(1280, 840)
        w.move(40, 30)
        self.pump(0.3)
        self.load(write_widget(Path(self._tmp.name) / "a"))
        self.first = w.active.instance.id
        self.load(write_widget(Path(self._tmp.name) / "b"))
        self.second = w.active.instance.id
        w.select_device("dash_accessory_lcd", "SizeS", LANDSCAPE)
        self.pump(0.4)

    def geometry(self) -> dict:
        w = self.window
        parts = {"window": w.frameGeometry().getRect(), "window_size": w.size().toTuple(),
                 "splitter": tuple(w.splitter.sizes()), "center_splitter": tuple(w.center_splitter.sizes())}
        for name in ("top_bar", "instances_panel", "inspector", "preview_header", "viewport_frame", "console_panel", "console_view"):
            parts[name] = rect_in(w, getattr(w, name)).getRect()
        return parts

    def settle(self) -> None:
        self.window.refresh_measurement()
        self.pump(0.15)

    def check(self, start: dict, what: str) -> None:
        self.assertEqual(self.geometry(), start, what)
        if self.window.viewport is not None and self.window.viewport_frame.native:
            self.assertEqual(self.window.view.size().toTuple(), self.window.viewport.size, what)  # only the HTML view changed

    def test_size_s_m_l_xl_s_keeps_window_sidebars_and_preview(self) -> None:
        w = self.window
        start = self.geometry()
        views = set()
        for _ in range(3):
            for size in ("SizeS", "SizeM", "SizeL", "SizeXL", "SizeS"):
                w.select_device("dash_accessory_lcd", size, LANDSCAPE)
                self.settle()
                views.add(w.view.size().toTuple())
                self.check(start, size)
        self.assertEqual(len(views), 4)  # the HTML view did change with every size
        for size in ("SizeS", "SizeM", "SizeL", "SizeXL", "SizeS"):  # through the segmented control, as the user does
            w.size_combo.button(w.size_combo.findData(size)).click()
            self.settle()
            self.check(start, f"click {size}")

    def test_device_orientation_mode_and_scale_changes_keep_the_layout(self) -> None:
        w = self.window
        start = self.geometry()
        for device, size, orientation in (("dash_accessory_lcd", "SizeXL", PORTRAIT), ("taiga_lcd", "SizeM", PORTRAIT), ("vanguard_96", None, None),
                                          ("cooler_pump_lcd", None, None), ("link_generic_lcd", "SizeL", LANDSCAPE), (None, None, None),
                                          ("dash_accessory_lcd", "SizeS", LANDSCAPE)):
            w.select_device(device, size, orientation)
            self.settle()
            self.check(start, f"{device} {size} {orientation}")
        for mode in (MODE_PHYSICAL, MODE_NOMINAL):
            w.select_device("dash_accessory_lcd", "SizeXL", PORTRAIT, mode)
            self.settle()
            self.check(start, mode)
        for scale in (FIT, NATIVE, FIT, NATIVE):
            w.viewport_frame.set_scale(scale)
            self.settle()
            self.check(start, f"scale {scale}")

    def test_switching_instances_with_their_own_sizes_keeps_the_layout(self) -> None:
        w = self.window
        w.select_device("dash_accessory_lcd", "SizeXL", PORTRAIT)  # the second instance: XL portrait
        w.select_instance(self.first)
        w.select_device("dash_accessory_lcd", "SizeS", LANDSCAPE)  # the first: S landscape
        self.settle()
        start = self.geometry()
        for _ in range(3):
            for instance_id, size in ((self.second, (261, 951)), (self.first, (315, 129))):
                w.select_instance(instance_id)
                self.settle()
                self.assertEqual(w.view.size().toTuple(), size)
                self.check(start, instance_id[:8])

    def test_a_widget_larger_than_the_preview_scrolls_in_native_and_shrinks_in_fit(self) -> None:
        w, frame = self.window, self.window.viewport_frame
        start = self.geometry()
        w.select_device("dash_accessory_lcd", "SizeXL", PORTRAIT)  # 261 x 951: taller than the preview
        self.settle()
        self.check(start, "native XL portrait")
        self.assertGreater(frame.scroll.verticalScrollBar().maximum(), 0)  # Native: exact size, scrolled
        w.scale_combo.setCurrentIndex(w.scale_combo.findData(FIT))  # as the user does: the choice survives later device changes
        w.scale_combo.activated.emit(w.scale_combo.currentIndex())
        self.settle()
        self.check(start, "fit XL portrait")
        self.assertLess(frame.zoom, 1.0)
        self.assertLessEqual((w.view.width(), w.view.height()), (frame.width(), frame.height()))  # Fit: shrunk into the preview
        w.resize(900, 600)  # a preview lower than the smallest EXACT zoom (A2 rule): Fit uses that zoom and scrolls the rest
        self.pump(0.4)
        small = self.geometry()
        w.select_device("dash_accessory_lcd", "SizeS", LANDSCAPE)
        self.settle()
        w.select_device("dash_accessory_lcd", "SizeXL", PORTRAIT)
        self.settle()
        self.assertEqual(self.geometry(), small)
        self.assertLess(w.view.height(), 951)

    def test_nothing_is_locked_the_user_can_still_resize_drag_and_toggle(self) -> None:
        w = self.window
        w.resize(1500, 900)
        self.pump(0.3)
        self.assertEqual(w.size().toTuple(), (1500, 900))
        sizes = w.splitter.sizes()
        w.splitter.moveSplitter(sizes[0] + 60, 1)  # drag the left handle
        w._remember_side_widths()
        self.pump(0.2)
        self.assertEqual(w.instances_panel.width(), sizes[0] + 60)
        start = self.geometry()
        for size in ("SizeM", "SizeXL", "SizeS"):
            w.select_device("dash_accessory_lcd", size, LANDSCAPE)
            self.settle()
            self.check(start, f"after drag {size}")
        w.inspector_toggle.click()
        self.pump(0.2)
        self.assertFalse(w.inspector.isVisible())
        w.inspector_toggle.click()
        self.pump(0.2)
        self.assertEqual(self.geometry(), start)

class SidebarReviewTests(PageTestCase):
    """U1 review: right sidebar never wider than its panel, aligned device toolbar, clear instance actions, stable window."""

    LONG = ("<html><head><title>Long</title>"
            + meta("cb", "combobox", data_label="'A very long combobox label that has to wrap inside the settings panel'",
                   data_default="'a'", data_values='[{"key":"a","value":"An extremely long option text that must not widen the panel"},'
                                                   '{"key":"b","value":"B"}]')
            + meta("tb", "tab-buttons", data_default="'one'", data_values='["one","two","three","four","five","six"]')
            + "</head><body></body></html>")

    def setUp(self) -> None:
        PageTestCase.setUp(self)
        self.window.show()
        self.window.resize(1100, 820)
        self.pump(0.3)

    def fits(self, scroll) -> None:
        self.assertEqual(scroll.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.assertLessEqual(scroll.widget().minimumSizeHint().width(), scroll.viewport().width(), scroll.widget())

    def test_every_inspector_tab_fits_the_minimum_panel_width(self) -> None:
        w = self.window
        for html in (ALL, self.LONG):
            self.load(write_widget(Path(self._tmp.name) / str(len(html)), html=html))
            w.select_device("dash_accessory_lcd", "SizeM", LANDSCAPE)
            sizes = w.splitter.sizes()
            w.splitter.setSizes([sizes[0], sizes[1] + sizes[2] - w.inspector.minimumWidth(), w.inspector.minimumWidth()])
            self.pump(0.3)
            self.assertEqual(w.inspector.width(), 280)
            bar = w.inspector.tabBar()  # every tab visible, no scroll arrows, texts not elided at the minimum width
            self.assertLessEqual(sum(bar.tabRect(i).width() for i in range(bar.count())), bar.width())
            self.assertFalse([b for b in bar.findChildren(QAbstractButton) if b.isVisible()])
            for index in range(w.inspector.count()):
                w.inspector.setCurrentIndex(index)
                self.pump(0.1)
                page = w.inspector.widget(index)
                self.fits(page if isinstance(page, QScrollArea) else page.findChild(QScrollArea))

    def test_personalization_heading_has_room_above_the_first_control(self) -> None:
        panel = self.window.personalization_panel
        self.window.inspector.setCurrentIndex(1)
        self.pump(0.2)
        self.assertGreaterEqual(panel.theme_by_screen.y() - panel.fontMetrics().height(), 12)

    def test_device_toolbar_has_no_info_icon_and_one_control_height(self) -> None:
        w = self.window
        self.load(write_widget(Path(self._tmp.name) / "w"))
        w.select_device("dash_accessory_lcd", "SizeM", LANDSCAPE)
        self.assertFalse(hasattr(w, "viewport_notice"))
        controls_ = (w.device_combo, w.orientation_combo, w.size_combo, w.scale_combo)
        for size in ((1600, 900), (1100, 820)):  # one row / wrapped into rows: every row is top-aligned, one control height
            w.resize(*size)
            self.pump(0.3)
            self.assertEqual({c.height() for c in controls_}, {theme.CONTROL_HEIGHT})
            tops = sorted({rect_in(w, c).y() for c in controls_})
            self.assertTrue(len(tops) == 1 or all(b - a >= theme.CONTROL_HEIGHT for a, b in zip(tops, tops[1:])), (size, tops))
            if size == (1600, 900):
                self.assertEqual(len(tops), 1)

    def test_instance_actions_are_distinct_and_the_menu_keeps_the_handlers(self) -> None:
        from unittest import mock
        w = self.window
        icons_ = [w.clone_button, w.reset_button, w.close_button, w.remove_button]
        self.assertEqual(len({b.icon().cacheKey() for b in icons_}), 4)
        self.assertEqual([b.toolTip().split("\n")[0] for b in icons_], ["Clone", "Reset Storage", "Close", "Remove"])
        self.assertTrue(w.more_button.toolTip())
        menu = w.more_button.menu()
        self.assertEqual([a.text() for a in menu.actions() if a.text()],
                         ["Reopen as Stored", "Reopen with New Defaults", "Save Instance File...", "Open Instance File..."])
        self.assertTrue(all(a.toolTip() != a.text() for a in menu.actions() if a.text()))
        self.load(write_widget(Path(self._tmp.name) / "w"))
        calls = []
        with mock.patch.object(w, "open_instance", lambda i, m: calls.append((i, m))):
            w.restore_action.trigger()
            w.merge_action.trigger()
        self.assertEqual(calls, [(w.active.instance.id, "restore"), (w.active.instance.id, "merge")])
        target = Path(self._tmp.name) / "instances.json"
        with mock.patch("widgetdevtool.main_window.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            w.save_instances_action.trigger()
        self.assertIn(w.active.instance.id, target.read_text(encoding="utf-8"))

    def test_window_keeps_its_size_at_1100x820_and_900x600(self) -> None:
        w = self.window
        self.load(write_widget(Path(self._tmp.name) / "w"))
        for size in ((1100, 820), (900, 600)):
            w.resize(*size)
            self.pump(0.3)
            start = (w.size().toTuple(), tuple(w.splitter.sizes()), w.viewport_frame.size().toTuple())
            for choice in ("SizeS", "SizeM", "SizeL", "SizeXL", "SizeS"):
                w.select_device("dash_accessory_lcd", choice, LANDSCAPE)
                self.pump(0.1)
                self.assertEqual((w.size().toTuple(), tuple(w.splitter.sizes()), w.viewport_frame.size().toTuple()), start, (size, choice))
            sizes = w.splitter.sizes()
            w.splitter.moveSplitter(sizes[0] + sizes[1] - 40, 2)  # the user widens the inspector
            self.pump(0.2)
            self.assertEqual(w.size().toTuple(), size)


class CompactControlsAndTooltipsTests(PageTestCase):
    """U1 compact pass: one control height (28-30 px), icon-only buttons square, 6-8 px spacing, fonts 11-14 px, and every icon-only button
    (and every text button) has a tooltip that describes the action; disabled instance actions keep theirs."""

    def setUp(self) -> None:
        PageTestCase.setUp(self)
        self.window.show()
        self.window.resize(1100, 820)
        self.pump(0.3)

    def all_buttons(self, root: QWidget) -> list:
        return [b for b in root.findChildren(QAbstractButton) if b.isVisible() and not isinstance(b, QCheckBox)
                and b.parentWidget().metaObject().className() not in ("QTabBar", "QScrollBar")]

    def test_shared_sizes(self) -> None:
        self.assertTrue(28 <= theme.CONTROL_HEIGHT <= 30)
        self.assertTrue(6 <= theme.SPACING <= 8)
        self.assertTrue(12 <= theme.FONT_BODY <= 13 and 13 <= theme.FONT_TITLE <= 14)
        w = self.window
        self.load(write_widget(Path(self._tmp.name) / "w", html=ALL))
        w.select_device("dash_accessory_lcd", "SizeM", LANDSCAPE)
        self.pump(0.2)
        status_bar = w.statusBar()
        for b in self.all_buttons(w):
            if status_bar.isAncestorOf(b):
                continue
            self.assertEqual(b.height(), theme.CONTROL_HEIGHT, b.text() or b.toolTip()[:30])
            if not b.text():
                self.assertEqual(b.size().toTuple(), (theme.CONTROL_HEIGHT, theme.CONTROL_HEIGHT), b.toolTip()[:30])
        for c in w.findChildren(QComboBox) + w.findChildren(QLineEdit):
            if c.isVisible():
                self.assertEqual(c.height(), theme.CONTROL_HEIGHT)

    def test_every_button_has_a_descriptive_tooltip(self) -> None:
        from widgetdevtool.import_dialog import ImportDialog
        w = self.window
        self.assertFalse(any(b.isEnabled() for b in (w.clone_button, w.reset_button, w.close_button, w.remove_button)))  # nothing to act on
        self.assertTrue(all(b.toolTip() for b in (w.clone_button, w.reset_button, w.close_button, w.remove_button)))
        self.load(write_widget(Path(self._tmp.name) / "w", html=ALL))
        dialog = PackageDialog(make_compliant_widget(Path(self._tmp.name) / "p"), path_env=str(Path(self._tmp.name) / "none"),
                               settings=QSettings(str(Path(self._tmp.name) / "s.ini"), QSettings.Format.IniFormat), target=TARGET_ICUEAPPS)
        importer = ImportDialog([])
        for widget in (dialog, importer):
            self.addCleanup(widget.close)
            widget.show()
        dialog.legacy_check.setChecked(True)
        self.pump(0.3)
        for index in range(w.inspector.count()):
            w.inspector.setCurrentIndex(index)
            self.pump(0.1)
            for root in (w, dialog, importer):
                for b in self.all_buttons(root):
                    self.assertTrue(b.toolTip().strip(), f"{root.windowTitle()}: {b.text()!r}")
                    if not b.text():  # icon-only: more than a name
                        self.assertGreater(len(b.toolTip()), 15, b.toolTip())
        self.assertEqual([b.toolTip().split("\n")[1] for b in (w.clone_button, w.reset_button, w.close_button, w.remove_button)],
                         ["Duplicate instance with independent storage: same settings, new id, empty storage. The original keeps running.",
                          "Clear browser storage (localStorage, cache), keep instance settings and id. Asks first; the page restarts.",
                          "Stop instance, keep settings and storage. Reopen it from the instance list.",
                          "Delete instance and its storage. Cannot be undone."])
        self.assertTrue(dialog.build_button.toolTip().startswith("Build realcliprobe-1.0.0-icueapps.icuewidget"))  # not wiped when allowed
        self.assertIn("Validate, then package", dialog.build_button.toolTip())
        w.set_console_collapsed(True)
        self.assertTrue(w.console_toggle.toolTip().startswith("Expand"))
        w.set_console_collapsed(False)
        self.assertTrue(w.console_toggle.toolTip().startswith("Collapse"))
        self.assertTrue(w.testAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips))


class InspectorRhythmTests(PageTestCase):
    """Inspector vertical rhythm (theme constants): section title -> card, card top -> first field, label -> control, field -> field,
    card -> next section; tabs inset from the panel edges; compact tooltips. Controls stay CONTROL_HEIGHT."""

    def setUp(self) -> None:
        PageTestCase.setUp(self)
        self.window.show()
        self.window.resize(1280, 840)
        self.pump(0.3)
        self.load(write_widget(Path(self._tmp.name) / "w", html=ALL))
        self.pump(0.3)

    @staticmethod
    def r(widget, ref):
        from PySide6.QtCore import QPoint
        return QRect(widget.mapTo(ref, QPoint(0, 0)), widget.size())

    def test_section_card_and_field_spacing(self) -> None:
        from PySide6.QtWidgets import QStyle, QStyleOptionGroupBox
        panel = self.window.settings_panel
        first, second = panel.group_boxes[0], panel.group_boxes[1]
        opt = QStyleOptionGroupBox()
        first.initStyleOption(opt)
        title = first.style().subControlRect(QStyle.ComplexControl.CC_GroupBox, opt, QStyle.SubControl.SC_GroupBoxLabel, first)
        frame = first.style().subControlRect(QStyle.ComplexControl.CC_GroupBox, opt, QStyle.SubControl.SC_GroupBoxFrame, first)
        self.assertEqual(title.x(), frame.x())  # the title is flush with the card edge
        self.assertEqual(frame.top() - title.bottom() - 1, theme.SECTION_GAP)
        editors = [e for e in panel.editors.values() if e.parentWidget() is first and e.isVisible()]
        layout = first.layout()
        items = [layout.itemAt(i).widget() for i in range(layout.count()) if layout.itemAt(i).widget() is not None
                 and layout.itemAt(i).widget().isVisible()]  # group info / description, then the editors
        self.assertEqual(self.r(items[0], first).y() - frame.top() - 1, theme.CARD_PADDING)  # inside the 1 px border
        self.assertEqual(self.r(items[0], first).x() - frame.x() - 1, theme.CARD_PADDING)
        for a, b in zip(items, items[1:]):
            self.assertEqual(self.r(b, first).y() - self.r(a, first).bottom() - 1, theme.FIELD_GAP)
        for editor in editors:
            self.assertEqual(self.r(editor.control, editor).y() - self.r(editor.label, editor).bottom() - 1, theme.LABEL_GAP, editor.label.text())
        parent = first.parentWidget()
        self.assertEqual(self.r(second, parent).y() - self.r(first, parent).bottom() - 1, theme.CARD_GAP)

    def test_tabs_are_inset_and_tooltips_compact(self) -> None:
        bar = self.window.inspector.tabBar()
        self.assertGreaterEqual(self.r(bar, self.window.inspector).x(), 4)
        self.assertEqual(bar.tabRect(0).height(), theme.SECTION_GAP + theme.CONTROL_HEIGHT)  # 8 px above a 30 px tab
        sheet = QApplication.instance().styleSheet()
        self.assertIn("QToolTip { background:", sheet)
        self.assertIn("padding: 3px 6px", sheet[sheet.index("QToolTip"):sheet.index("QToolTip") + 200])


class PackageDialogLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.settings = QSettings(str(self.tmp / "s.ini"), QSettings.Format.IniFormat)

    def test_target_is_shown_and_icue_apps_options_only_for_icue_apps(self) -> None:
        dialog = PackageDialog(make_legacy_widget(self.tmp / "l" / "legacy"), settings=self.settings, path_env=str(self.tmp / "none"),
                               target=TARGET_ICUE5)
        self.addCleanup(dialog.close)
        dialog.show()
        self.assertEqual((dialog.target_combo.currentText(), dialog.title.text()), ("Build for iCUE 5", "Build Package - iCUE 5"))
        self.assertTrue(dialog.version_label.isVisible() and dialog.source_label.isVisible() and dialog.output_edit.isVisible())
        self.assertFalse(dialog.apps_card.isVisible() or dialog.advanced_toggle.isVisible() or dialog.cli_label.isVisible())
        dialog.set_target(TARGET_ICUEAPPS)
        self.assertEqual((dialog.target_combo.currentText(), dialog.title.text()), ("Build for iCUE Apps", "Build Package - iCUE Apps"))
        self.assertTrue(dialog.apps_card.isVisible() and dialog.min_version_edit.isVisible() and dialog.cli_label.isVisible())

    def test_legacy_compatibility_is_off_inside_advanced_options(self) -> None:
        dialog = PackageDialog(make_legacy_widget(self.tmp / "l" / "legacy"), settings=self.settings, path_env=str(self.tmp / "none"),
                               target=TARGET_ICUEAPPS)
        self.addCleanup(dialog.close)
        dialog.show()
        self.assertFalse(dialog.legacy_check.isChecked() or dialog.advanced_box.isVisible() or dialog.preview_button.isVisible())
        self.assertEqual((dialog.build_button.property("variant"), dialog.cancel_button.text()), ("primary", "Close"))
        dialog.legacy_check.setChecked(True)
        self.assertTrue(dialog.advanced_box.isVisible())
        self.assertIn("Legacy Compatibility ON", dialog.advanced_toggle.text())
        self.assertTrue(dialog.min_version_edit.isVisible() and dialog.preview_button.isVisible())

    def test_failures_are_shown_as_an_error_banner(self) -> None:
        dialog = PackageDialog(make_compliant_widget(self.tmp / "w"), settings=self.settings, path_env=str(self.tmp / "none"), target=TARGET_ICUEAPPS)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.cli_label.property("tone"), "error")
        dialog.output_edit.setText(str(self.tmp / "w" / "dist"))
        dialog.cli = object()  # an output-folder problem is reported before the CLI runs
        self.assertFalse(dialog.start())
        self.assertEqual((dialog.status.property("tone"), dialog.status.property("banner")), ("error", True))


if __name__ == "__main__":
    unittest.main()