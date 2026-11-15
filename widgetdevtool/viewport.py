r"""Device viewport: the pixel size the widget page gets for a device + size + orientation, and the frame that shows it.

Rules (iCUE v5.55, see device_profiles.py for the sources):
- NOMINAL  = WidgetSizeWatcherImpl::widgetSizePx(): Dash LCD -> relative size x mimicScreenSize (int() truncation as in
  screenSizeText); keyboard / pump -> the one fixed size from the manifest. The FORMULA is verified; that the real host gives
  the web view exactly these pixels is NOT (Unknown).
- PHYSICAL = relative size x physicalScreenSize: what the Dash LCD assignment view lays out at zoom 1 (DashLCDScreenView.qml).
  EXPERIMENTAL: the real device rendering is not known. Dashboard devices only.
- Not simulated (Unsupported): the portrait zoom of the assignment view (0.33..1), screen auto-rotation, LCD rotation degrees.

Preview scale (A2): NATIVE 1:1 is the default and the only mode that keeps window.devicePixelRatio and the exact page size (a larger
viewport scrolls). Showing a viewport smaller than it is (FIT / zoom, EXPERIMENTAL) uses the page zoom factor: the view is W*z pixels wide,
the page still lays out in W CSS pixels, but window.devicePixelRatio is multiplied by z and a non-whole W*z gives a fractional page size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from . import theme
from .device_profiles import KIND_DASHBOARD, LANDSCAPE, SIZE_LABEL, DeviceProfile, to_px

MODE_NOMINAL, MODE_PHYSICAL = "nominal", "physical"
NATIVE, FIT = "native", "fit"  # preview scale: 1:1 (scrolls) / EXPERIMENTAL page-zoom fit
MIN_ZOOM, MAX_ZOOM = 0.25, 1.0  # QtWebEngine accepts 0.25..5; a preview larger than the page is never useful


@dataclass(frozen=True)
class Viewport:
    width: int
    height: int
    exact: tuple[float, float]
    mode: str
    experimental: bool
    description: str

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


def compute_viewport(profile: DeviceProfile, size: str | None, orientation: str | None, mode: str = MODE_NOMINAL) -> Viewport:
    """Pure: profile + choice -> pixels. Raises ValueError for a choice the device does not offer."""
    if mode not in (MODE_NOMINAL, MODE_PHYSICAL):
        raise ValueError(f"unknown mode {mode!r}")
    if profile.kind != KIND_DASHBOARD:
        if mode == MODE_PHYSICAL:
            raise ValueError(f"{profile.name} has no physicalScreenSize")
        exact = profile.widget_size("", "")
        w, h = to_px(exact)
        return Viewport(w, h, exact, mode, False, f"{profile.name}: {w}x{h} px fixed ({profile.fixed_from})")
    size = size or (profile.supported_sizes() or [""])[0]
    orientation = orientation or LANDSCAPE
    exact = (profile.widget_size_physical if mode == MODE_PHYSICAL else profile.widget_size)(size, orientation)
    w, h = to_px(exact)
    base = "physicalScreenSize, EXPERIMENTAL" if mode == MODE_PHYSICAL else "mimicScreenSize"
    return Viewport(w, h, exact, mode, mode == MODE_PHYSICAL,
                    f"{profile.name}: {SIZE_LABEL[size]} {orientation} {w}x{h} px ({base})")


def exact_zooms(width: int, height: int) -> list[float]:
    """Zooms k/g (g = gcd(width, height), MIN_ZOOM <= zoom <= 1), largest first. Only at these does the page measure exactly
    width x height, because only then are width*zoom and height*zoom whole pixels."""
    g = math.gcd(width, height)
    return [k / g for k in range(g, 0, -1) if MIN_ZOOM <= k / g <= MAX_ZOOM]


def fit_scale(width: int, height: int, avail_w: int, avail_h: int) -> float:
    """Zoom for the EXPERIMENTAL Visual Fit. Only an EXACT zoom (k/g, g = gcd(width, height), whole pixels) is ever used, so the page
    always measures exactly width x height (the old approximate zoom gave 260.64 instead of 261). It is the largest exact zoom that fits
    the area; when even the smallest one is larger than the area, that one is used (the preview then scrolls); when no exact zoom
    exists below 100 % (gcd 1) the result is 1.0 (= Native, scrolled). devicePixelRatio is still multiplied by the zoom."""
    raw = max(MIN_ZOOM, min(MAX_ZOOM, avail_w / max(width, 1), avail_h / max(height, 1)))
    exact = exact_zooms(width, height)  # largest first
    fitting = [z for z in exact if z <= raw]
    if fitting:
        return fitting[0]
    return exact[-1] if exact else 1.0

class _Canvas(QWidget):
    """The surface the web views sit on (inside the scroll area). Draws the dashed outline of the viewport."""

    def __init__(self, frame: "ViewportFrame") -> None:
        super().__init__()
        self._frame = frame

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        frame = self._frame
        if frame.viewport is None or frame.view is None:
            return
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.CANVAS))
        painter.setPen(QPen(QColor("#5b6472"), 1, Qt.PenStyle.DashLine))
        painter.drawRect(frame.view.geometry().adjusted(-1, -1, 0, 0))


class ViewportFrame(QWidget):
    """Holds the web views.

    Free mode (no device): the view fills the frame.
    Device mode, `scale`:
    - NATIVE (default): the view is exactly width x height logical pixels and the page zoom stays 1, so the page measures exactly the
      viewport and keeps the display's own devicePixelRatio (Windows scaling). A viewport larger than the frame is SCROLLED, never shrunk.
    - FIT or a number: EXPERIMENTAL "visual" scaling with the page zoom factor (the view is W*z wide, the page still lays out in W CSS
      pixels). Known side effects (measured): window.devicePixelRatio is multiplied by z, and unless W*z and H*z are whole pixels the page
      measures a fraction of a pixel less (visualViewport.width 260.64). Use it only to look at a layout, never to debug sizes or DPR."""

    applied = Signal()
    MARGIN = 8  # canvas space around a scrolled native viewport

    def __init__(self, view: QWidget | None = None) -> None:
        super().__init__()
        self.view: QWidget | None = None  # the shown (active) view
        self._views: list[QWidget] = []  # every instance view; all get the same geometry and zoom, only `view` is visible
        self._viewports: dict[QWidget, tuple[int, int]] = {}  # E3: every instance view has its OWN viewport (device size x orientation)
        self._free = True  # no device: every view fills the frame
        self._scale: float | str = NATIVE
        self.zoom = 1.0
        self.canvas = _Canvas(self)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setWidget(self.canvas)
        self.scroll.viewport().setAutoFillBackground(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll)
        self.setMinimumSize(120, 80)
        if view is not None:
            self.add_view(view)

    # ---- views ----
    def add_view(self, view: QWidget) -> None:
        view.setParent(self.canvas)
        self._views.append(view)
        self.show_view(view)

    def remove_view(self, view: QWidget) -> None:
        if view in self._views:
            self._views.remove(view)
        self._viewports.pop(view, None)
        if self.view is view:
            self.view = None
            self.show_view(self._views[-1] if self._views else None)

    def show_view(self, view: QWidget | None) -> None:
        self.view = view
        for other in self._views:
            other.setVisible(other is view)
        self._apply()

    # ---- mode ----
    def set_free(self) -> None:
        self._free = True
        self._viewports.clear()
        self._apply()

    def set_viewport(self, width: int, height: int, view: QWidget | None = None) -> None:
        """The viewport of `view` (default: the shown one). Other views keep theirs, so choosing one instance's size never resizes another page."""
        self._free = False
        target = view if view is not None else self.view
        if target is not None:
            self._viewports[target] = (width, height)
        self._apply()

    def set_scale(self, scale: float | str) -> None:
        """NATIVE (1:1, scrolls), FIT or a zoom factor (both EXPERIMENTAL)."""
        if scale != NATIVE and scale != FIT and not isinstance(scale, (int, float)):
            raise ValueError(f"unknown preview scale {scale!r}")
        self._scale = scale
        self._apply()

    @property
    def scale(self) -> float | str:
        return self._scale

    @property
    def viewport(self) -> tuple[int, int] | None:
        """The viewport of the SHOWN view (None: free size)."""
        return None if self._free else self._viewports.get(self.view)

    @property
    def native(self) -> bool:
        return self.viewport is not None and self._scale == NATIVE

    def _available(self) -> tuple[int, int]:
        size = self.scroll.viewport().size()
        return max(1, size.width()), max(1, size.height())

    def _zoom_for(self, viewport: tuple[int, int], avail: tuple[int, int]) -> float:
        w, h = viewport
        if self._scale == NATIVE:
            return 1.0
        if self._scale == FIT:
            return fit_scale(w, h, avail[0] - 4, avail[1] - 4)
        return float(self._scale)

    def _apply(self) -> None:
        avail = self._available()
        shown = self.viewport
        canvas = avail
        if shown is not None:
            zoom = self._zoom_for(shown, avail)
            canvas = (max(avail[0], round(shown[0] * zoom) + 2 * self.MARGIN), max(avail[1], round(shown[1] * zoom) + 2 * self.MARGIN))
        self.zoom = self._zoom_for(shown, avail) if shown is not None else 1.0
        policy = Qt.ScrollBarPolicy.ScrollBarAlwaysOff if shown is None else Qt.ScrollBarPolicy.ScrollBarAsNeeded  # free size never scrolls
        if self.scroll.horizontalScrollBarPolicy() != policy:
            self.scroll.setHorizontalScrollBarPolicy(policy)
            self.scroll.setVerticalScrollBarPolicy(policy)
            avail = self._available()
            canvas = avail if shown is None else canvas
        self.canvas.resize(*canvas)
        for view in self._views:  # every view keeps ITS size and zoom: switching the shown one never resizes the others
            own = None if self._free else self._viewports.get(view)
            if own is None:
                view.setGeometry(0, 0, avail[0], avail[1])
                view.setZoomFactor(1.0)
                continue
            zoom = self._zoom_for(own, avail)
            vw, vh = max(1, round(own[0] * zoom)), max(1, round(own[1] * zoom))
            view.setGeometry((canvas[0] - vw) // 2, (canvas[1] - vh) // 2, vw, vh)
            view.setZoomFactor(zoom)
        self.canvas.update()
        self.applied.emit()
    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._apply()