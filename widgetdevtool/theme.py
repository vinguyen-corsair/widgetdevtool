"""Dark theme (U1): one palette, one style sheet, semantic properties instead of inline colours.

Widgets opt in with dynamic properties that the style sheet matches:
- `variant`: "primary" | "ghost" | "danger" | "toggle" (buttons)
- `role`: "title" | "subtitle" | "section" | "muted" | "hint" | "mono" (labels)
- `tone`: "neutral" | "info" | "success" | "warning" | "error" (status texts); `banner` = True adds a tinted background
- `segment`: "first" | "middle" | "last" | "only" (SegmentedControl buttons)
Use `set_tone` / `set_props` to change a property after the widget is shown (the style must be re-polished)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory, QWidget

BG = "#15171c"          # window
SURFACE = "#1b1e24"     # panels
SURFACE_2 = "#22262e"   # inputs, buttons
SURFACE_3 = "#2b303a"   # hover
BORDER = "#2e333d"
BORDER_STRONG = "#3b4250"
TEXT = "#e6e8ee"
MUTED = "#9aa3b2"
FAINT = "#646c7a"
ACCENT = "#4f8cff"
ACCENT_HOVER = "#6a9dff"
ACCENT_PRESSED = "#3b76e6"
CANVAS = "#101216"      # behind the widget viewport
TONES = {"neutral": MUTED, "info": "#6aa9ff", "success": "#4cc36a", "warning": "#e3b341", "error": "#ff6b61"}
TONE_BORDER = {"neutral": "rgba(154,163,178,0.30)", "info": "rgba(79,140,255,0.40)", "success": "rgba(76,195,106,0.40)",
               "warning": "rgba(227,179,65,0.40)", "error": "rgba(255,107,97,0.45)"}
TONE_BG = {"neutral": "rgba(154,163,178,0.10)", "info": "rgba(79,140,255,0.12)", "success": "rgba(76,195,106,0.12)",
           "warning": "rgba(227,179,65,0.12)", "error": "rgba(255,107,97,0.13)"}
CONTROL_HEIGHT = 30       # every button, input, combo box, segment and icon-only button (icon-only: CONTROL_HEIGHT x CONTROL_HEIGHT)
ICON_SIZE = 16            # icons in buttons, segments and menus
SPACING = 6               # between neighbouring controls
# Vertical rhythm of the inspector (section title -> card -> fields):
PANEL_MARGIN = 12         # panel edge -> content (all sides)
SECTION_GAP = 8           # section title -> its card
CARD_GAP = 16             # card -> next section title
CARD_PADDING = 12         # card edge -> first / last field and sides
FIELD_GAP = 12            # field -> next field inside a card
LABEL_GAP = 6             # label -> its control (and control -> its hint)
SEGMENT_MIN_WIDTH = 34    # S / M / L / XL
SWATCH_WIDTH = 76         # colour buttons
RADIUS = 6
FONT_BODY, FONT_SMALL, FONT_TITLE = 12, 11, 14
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "Courier New"]


def _tone_rules() -> str:
    rules = []
    for tone, color in TONES.items():
        rules.append(f'QLabel[tone="{tone}"] {{ color: {color}; }}')
        rules.append(f'QLabel[tone="{tone}"][banner="true"] {{ background: {TONE_BG[tone]}; border: 1px solid {TONE_BORDER[tone]}; '
                     f'border-radius: 6px; padding: 6px 10px; }}')
    return "\n".join(rules)


STYLE_SHEET = f"""
QWidget {{ color: {TEXT}; font-size: {FONT_BODY}px; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 3px 6px; border-radius: 4px; font-size: {FONT_SMALL}px; }}

/* ---- surfaces ---- */
QFrame#topBar {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QFrame#panel, QWidget#panel {{ background: {SURFACE}; }}
QFrame#previewHeader {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QFrame#panelHeader {{ background: {SURFACE}; border-bottom: 1px solid {BORDER}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QToolButton {{ min-height: 20px; padding: 0px 6px; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:hover {{ background: {ACCENT}; }}
QDockWidget::title {{ background: {SURFACE}; padding: 6px 10px; border-bottom: 1px solid {BORDER}; }}

/* ---- text roles ---- */
QLabel[role="title"] {{ font-size: {FONT_TITLE}px; font-weight: 600; }}
QLabel[role="subtitle"] {{ color: {MUTED}; }}
QLabel[role="section"] {{ color: {MUTED}; font-size: {FONT_SMALL}px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="hint"] {{ color: {FAINT}; font-size: {FONT_SMALL}px; }}
QLabel[role="brand"] {{ font-size: {FONT_TITLE}px; font-weight: 600; color: {TEXT}; }}
{_tone_rules()}

/* ---- buttons ---- */
QPushButton, QToolButton {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS}px;
    padding: 0px 10px; min-height: {CONTROL_HEIGHT - 2}px; }}
QPushButton:hover, QToolButton:hover {{ background: {SURFACE_3}; border-color: #4a5262; }}
QPushButton:pressed, QToolButton:pressed {{ background: {BORDER}; }}
QPushButton:focus, QToolButton:focus {{ border-color: {ACCENT}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {FAINT}; background: {SURFACE}; border-color: {BORDER}; }}
QPushButton:checked, QToolButton:checked {{ background: rgba(79,140,255,0.18); border-color: {ACCENT}; color: #ffffff; }}
QPushButton[variant="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[variant="primary"]:pressed {{ background: {ACCENT_PRESSED}; }}
QPushButton[variant="primary"]:focus {{ border-color: #ffffff; }}
QPushButton[variant="primary"]:disabled {{ background: #2a3550; border-color: #2a3550; color: #7f8aa3; }}
QPushButton[variant="ghost"], QToolButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
QPushButton[variant="ghost"]:hover, QToolButton[variant="ghost"]:hover {{ background: {SURFACE_3}; }}
QPushButton[variant="ghost"]:focus, QToolButton[variant="ghost"]:focus {{ border-color: {ACCENT}; }}
QPushButton[variant="ghost"]:checked, QToolButton[variant="ghost"]:checked {{ background: rgba(79,140,255,0.16); border-color: transparent; }}
QPushButton[variant="danger"], QToolButton[variant="danger"] {{ color: {TONES["error"]}; }}
QPushButton[variant="danger"]:hover, QToolButton[variant="danger"]:hover {{ background: rgba(255,107,97,0.14); border-color: {TONES["error"]}; }}
QToolButton::menu-indicator {{ image: none; width: 0px; }}
QToolButton[iconOnly="true"] {{ padding: 0px; min-width: {CONTROL_HEIGHT - 2}px; max-width: {CONTROL_HEIGHT - 2}px; }}
QPushButton[variant="toggle"] {{ padding-left: 8px; padding-right: 10px; }}
QPushButton[variant="toggle"]:checked {{ background: rgba(76,195,106,0.14); border-color: #3f9f58; color: {TEXT}; }}

/* segmented control */
QToolButton[segment] {{ border-radius: 0px; margin: 0px; padding: 0px 8px; min-width: 16px; }}
QToolButton[segment="first"] {{ border-top-left-radius: {RADIUS}px; border-bottom-left-radius: {RADIUS}px; }}
QToolButton[segment="last"] {{ border-top-right-radius: {RADIUS}px; border-bottom-right-radius: {RADIUS}px; }}
QToolButton[segment="only"] {{ border-radius: {RADIUS}px; }}
QToolButton[segment]:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; font-weight: 600; }}
QToolButton[segment]:disabled {{ color: #4b5260; background: {SURFACE}; border-color: {BORDER}; text-decoration: line-through; }}
QToolButton[segment]:checked:disabled {{ background: #2a3550; color: #8a94ab; text-decoration: none; }}

/* ---- inputs ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS}px;
    padding: 0px 7px; min-height: {CONTROL_HEIGHT - 2}px; selection-background-color: {ACCENT}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: #4a5262; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {FAINT}; background: {SURFACE}; border-color: {BORDER}; }}
QLineEdit:read-only {{ background: {SURFACE}; color: {MUTED}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; selection-background-color: {ACCENT};
    outline: 0; padding: 4px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox:disabled {{ color: {FAINT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER_STRONG}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #ffffff; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
QSlider::handle:horizontal:disabled {{ background: {FAINT}; }}

/* ---- lists, text areas, tabs ---- */
QListWidget, QTreeWidget, QPlainTextEdit, QTextEdit {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    selection-background-color: rgba(79,140,255,0.30); outline: 0; }}
QListWidget::item {{ padding: 4px 6px; border-radius: 4px; margin: 1px 2px; }}
QListWidget::item:hover {{ background: {SURFACE_3}; }}
QListWidget::item:selected {{ background: rgba(79,140,255,0.22); color: #ffffff; }}
QTreeWidget::item {{ padding: 3px 2px; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 4px 6px; }}
QPlainTextEdit[role="mono"] {{ font-family: "{MONO_FAMILIES[0]}", "{MONO_FAMILIES[1]}", monospace; font-size: 12px; background: {CANVAS};
    border: none; border-radius: 0px; padding: 4px 6px; }}
QPlainTextEdit[role="log"] {{ font-family: "{MONO_FAMILIES[0]}", "{MONO_FAMILIES[1]}", monospace; font-size: 12px; background: {CANVAS}; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; background: {SURFACE}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 7px 10px; border: none; border-bottom: 2px solid transparent; }}
QTabWidget#inspector::tab-bar {{ left: {PANEL_MARGIN - 8}px; }}
QTabWidget#inspector QTabBar::tab {{ margin-top: {SECTION_GAP}px; margin-right: 2px; padding: 6px 6px; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; font-weight: 600; }}
QTabBar::tab:focus {{ color: #ffffff; }}
/* group box = section title ABOVE a card: the title sits in the margin, flush with the card edge; padding comes from the layout */
QGroupBox {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 8px; margin-top: {FONT_BODY + 4 + SECTION_GAP}px; padding: 0px;
    font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 0px; top: 0px; padding: 0px; color: {TEXT}; }}
QGroupBox QLabel, QGroupBox QCheckBox {{ font-weight: 400; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {BORDER_STRONG}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: #545d6e; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; padding: 4px; }}
QMenu::item {{ padding: 5px 16px 5px 8px; border-radius: 4px; }}
QMenu::icon {{ padding-left: 6px; }}
QMenu::item:selected {{ background: rgba(79,140,255,0.25); }}
QMessageBox QLabel {{ font-size: {FONT_BODY}px; }}
"""


def color_swatch_style(color: str, light: bool) -> str:
    """Style of a button that shows a chosen colour (the only place where a colour is set inline)."""
    return (f"QPushButton {{ background: {color}; color: {'#000000' if light else '#ffffff'}; border: 1px solid {BORDER_STRONG}; "
            f"border-radius: {RADIUS}px; padding: 0px 6px; min-height: {CONTROL_HEIGHT - 2}px; }} QPushButton:hover {{ border-color: {ACCENT}; }}")


def palette() -> QPalette:
    pal = QPalette()
    roles = {QPalette.ColorRole.Window: BG, QPalette.ColorRole.WindowText: TEXT, QPalette.ColorRole.Base: SURFACE_2,
             QPalette.ColorRole.AlternateBase: SURFACE, QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: SURFACE_2,
             QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.Highlight: ACCENT, QPalette.ColorRole.HighlightedText: "#ffffff",
             QPalette.ColorRole.ToolTipBase: SURFACE_2, QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.PlaceholderText: FAINT,
             QPalette.ColorRole.Link: ACCENT_HOVER, QPalette.ColorRole.Mid: BORDER_STRONG, QPalette.ColorRole.Dark: BG,
             QPalette.ColorRole.Light: SURFACE_3}
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(FAINT))
    return pal


def apply(app: QApplication | None = None) -> bool:
    """Fusion + dark palette + the style sheet, once per application. Returns True when it was applied now."""
    app = app or QApplication.instance()
    if app is None or app.property("wdtTheme"):
        return False
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setPalette(palette())
    font = QFont(app.font())
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI", font.family()])
    app.setFont(font)
    app.setStyleSheet(STYLE_SHEET)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    app.setProperty("wdtTheme", True)
    return True


def set_props(widget: QWidget, **props) -> QWidget:
    """Set dynamic style properties and re-polish so the style sheet picks them up."""
    for name, value in props.items():
        widget.setProperty(name, value)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
    return widget


def set_tone(widget: QWidget, tone: str, banner: bool | None = None) -> QWidget:
    if tone not in TONES:
        raise ValueError(f"unknown tone {tone!r}")
    props = {"tone": tone}
    if banner is not None:
        props["banner"] = banner
    if widget.property("tone") == tone and (banner is None or widget.property("banner") == banner):
        return widget
    return set_props(widget, **props)