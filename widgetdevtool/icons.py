"""Line icons (U1): 24x24 stroke SVGs drawn for WidgetDevTool, recoloured per state. No external files or fonts."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

PATHS = {
    "folder-open": '<path d="M3 19V6a1 1 0 0 1 1-1h5l2 2h8a1 1 0 0 1 1 1v2"/><path d="M3 19l2.6-7.4A1 1 0 0 1 6.5 11H21l-2.8 7.4a1 1 0 0 1-.9.6H3z"/>',
    "library": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
               '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9"/><path d="M10 13h4"/>',
    "import": '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/>',
    "package": '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "close": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>',
    "eraser": '<path d="M20 20H9"/><path d="M4.6 14.6l9.9-9.9a2 2 0 0 1 2.8 0l2 2a2 2 0 0 1 0 2.8L11 18H7.4l-2.8-2.8a1 1 0 0 1 0-.6z"/><path d="M9 10l5 5"/>',
    "landscape": '<rect x="3" y="6" width="18" height="12" rx="2"/>',
    "portrait": '<rect x="6" y="3" width="12" height="18" rx="2"/>',
    "panel-left": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    "panel-right": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    "panel-bottom": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 15h18"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-up": '<path d="M6 15l6-6 6 6"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3"/><path d="M12 15h5"/>',
    "sliders": '<path d="M4 6h10"/><path d="M18 6h2"/><circle cx="16" cy="6" r="2"/><path d="M4 12h4"/><path d="M12 12h8"/>'
               '<circle cx="10" cy="12" r="2"/><path d="M4 18h12"/><path d="M20 18h0"/><circle cx="18" cy="18" r="2"/>',
    "palette": '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.5-.8 1.5-1.6 0-1.3-1-1.7-1-2.9 0-1 .8-1.5 1.8-1.5H17a4 4 0 0 0 4-4c0-4.4-4-8-9-8z"/>'
               '<circle cx="7.5" cy="11" r="1"/><circle cx="10.5" cy="7.5" r="1"/><circle cx="15" cy="8" r="1"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8V3"/><rect x="7" y="13" width="10" height="6" rx="1"/>',
    "folder": '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7.5v.5"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "alert": '<path d="M12 4l9 16H3z"/><path d="M12 10v4"/><path d="M12 17v.5"/>',
    "play": '<path d="M7 5l12 7-12 7z"/>',
    "restore": '<path d="M4 12a8 8 0 1 0 2.3-5.7"/><path d="M4 4v5h5"/>',
    "merge": '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="12" r="2"/><path d="M6 7v10"/><path d="M6 7c0 4 4 5 10 5"/>',
    "diff": '<path d="M8 3v8"/><path d="M4 7h8"/><path d="M4 18h8"/><rect x="14" y="3" width="7" height="18" rx="1.5"/>',
    "power": '<path d="M12 3v9"/><path d="M6.3 6.3a8 8 0 1 0 11.4 0"/>',
    "more": '<circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/>'
            '<circle cx="19" cy="12" r="1.4" fill="currentColor"/>',
    "dot": '<circle cx="12" cy="12" r="5" fill="currentColor" stroke="none"/>',
}


def svg(name: str, color: str) -> bytes:
    body = PATHS[name].replace("currentColor", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


def pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    ratio = 2.0
    app = QGuiApplication.instance()
    if app is not None and QGuiApplication.primaryScreen() is not None:
        ratio = max(2.0, QGuiApplication.primaryScreen().devicePixelRatio() * 2)
    image = QPixmap(QSize(round(size * ratio), round(size * ratio)))
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    QSvgRenderer(QByteArray(svg(name, color))).render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


@lru_cache(maxsize=None)
def icon(name: str, color: str | None = None, checked_color: str | None = None) -> QIcon:
    """Normal/active in `color` (default: the text colour), disabled in the faint colour, checked (On) in `checked_color`."""
    result = QIcon()
    normal = color or theme.TEXT
    for mode, tint in ((QIcon.Mode.Normal, normal), (QIcon.Mode.Active, normal), (QIcon.Mode.Selected, "#ffffff"),
                       (QIcon.Mode.Disabled, theme.FAINT)):
        result.addPixmap(pixmap(name, tint), mode, QIcon.State.Off)
        result.addPixmap(pixmap(name, checked_color or tint if mode is not QIcon.Mode.Disabled else tint), mode, QIcon.State.On)
    return result