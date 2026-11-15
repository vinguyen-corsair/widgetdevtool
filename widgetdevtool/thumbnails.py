r"""Thumbnail rendering (D7a): the two ways iCUE draws a widget image, redrawn with QPainter from the QML source.

This is a SIMULATION OF THE RULES in the QML files, not iCUE's renderer: pixel-exact agreement (anti-aliasing, SVG rasterisation, the
panel background behind the tiles) is not claimed. What is taken from the source (iCUE v5.55):

WidgetPreviewPanel (qml/com/corsair/cue/widgets/qml/WidgetPreviewPanel.qml and the Dash LCD copy in dash_accessory_lcd/qml/misc):
  delegate 138 x 66, AssetIcon with 5 px margins = 128 x 56; AssetIcon.qml: a Rectangle colour CuePalette.layer1, radius 4, an Image
  with fillMode PreserveAspectCrop clipped by that rounded rectangle (OpacityMask). The image is the type's widgetPreviewSource().
  The current widget gets a 2 px CuePalette.layer9 border (radius 6). `iconOpacity: 0.1` is set on the AssetIcon but nothing in
  AssetIcon.qml uses it (the MaskedImage inside has no source), so it is NOT applied here (reading of the QML, not observed).
WidgetTypesPanel tile (WidgetTypePreviewLayoutBase.qml + WidgetImageTypePreviewView.qml):
  70 x 64 item; the button area is 70 x 40 with a radius-4 rectangle CuePalette.layer5 (CuePalette.layer9 when selected); the
  image is centred, sourceSize 16 x 16, PreserveAspectFit, and a ColorOverlay colours it with CuePalette.layer8 (layer1 when selected):
  only the ALPHA of the image survives, its own colours do not. The image is HtmlWidgetTypePreviewModel.imageSource() =
  typeData->imageSource() (icon, else the default icon). The tile title text (CueFonts.text4) is not drawn.
Palette values come from CuePalette.cpp via icue_theme.py. How a QML Image rasterises an SVG without sourceSize is not documented
in the source: the SVG is rasterised at its natural size (capped) and scaled smoothly (HYPOTHESIS).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgRenderer

from .icue_theme import load_palette
from .widget_library import Resolved

PREVIEW_DELEGATE = QSize(138, 66)
PREVIEW_ICON = QSize(128, 56)   # delegate minus 5 px margins
TYPE_TILE = QSize(70, 64)
TYPE_BUTTON = QSize(70, 40)
TYPE_ICON = QSize(16, 16)
MAX_NATURAL = 2048


def palette() -> dict[str, QColor]:
    return {name: QColor(value) for name, value in load_palette().items()}


def load_image(path: Path | None, natural_cap: int = MAX_NATURAL) -> QImage:
    """A raster image, or an SVG rasterised at its natural size (capped). A null image when it cannot be read."""
    if path is None or not Path(path).is_file():
        return QImage()
    if Path(path).suffix.lower() == ".svg":
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            return QImage()
        size = renderer.defaultSize()
        if size.isEmpty():
            size = QSize(256, 256)
        if max(size.width(), size.height()) > natural_cap:
            size.scale(natural_cap, natural_cap, Qt.AspectRatioMode.KeepAspectRatio)
        image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        return image
    image = QImage(str(path))
    return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied) if not image.isNull() else QImage()


def _new(size: QSize) -> QImage:
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    return image


def render_preview_tile(resolved: Resolved, current: bool = False) -> QImage:
    """One row of WidgetPreviewPanel (138 x 66, icon 128 x 56 at 5,5)."""
    colors = palette()
    out = _new(PREVIEW_DELEGATE)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    rect = QRectF(5, 5, PREVIEW_ICON.width(), PREVIEW_ICON.height())
    clip = QPainterPath()
    clip.addRoundedRect(rect, 4, 4)
    painter.fillPath(clip, colors["layer1"])  # AssetIcon background
    source = load_image(resolved.usable_path)
    if not source.isNull():
        painter.save()
        painter.setClipPath(clip)
        scaled = source.size().scaled(PREVIEW_ICON, Qt.AspectRatioMode.KeepAspectRatioByExpanding)  # PreserveAspectCrop
        target = QRectF(rect.center().x() - scaled.width() / 2, rect.center().y() - scaled.height() / 2, scaled.width(), scaled.height())
        painter.drawImage(target, source)
        painter.restore()
    if current:
        painter.setPen(QPen(colors["layer9"], 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(1, 1, PREVIEW_DELEGATE.width() - 2, PREVIEW_DELEGATE.height() - 2), 6, 6)
    painter.end()
    return out


def overlay_alpha_only(image: QImage, color: QColor) -> QImage:
    """ColorOverlay with an opaque colour: every pixel gets the colour, the source alpha is kept."""
    result = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    for y in range(result.height()):
        for x in range(result.width()):
            alpha = QColor.fromRgba(result.pixel(x, y)).alpha()
            result.setPixelColor(x, y, QColor(color.red() * alpha // 255, color.green() * alpha // 255, color.blue() * alpha // 255, alpha))
    return result


def render_type_tile(resolved: Resolved, selected: bool = False) -> QImage:
    """The 70 x 40 button area of a WidgetTypesPanel tile (the 24 px title strip below it is not drawn)."""
    colors = palette()
    out = _new(TYPE_BUTTON)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    background = QPainterPath()
    background.addRoundedRect(QRectF(0, 0, TYPE_BUTTON.width(), TYPE_BUTTON.height()), 4, 4)
    painter.fillPath(background, colors["layer9" if selected else "layer5"])
    source = load_image(resolved.usable_path)
    if not source.isNull():
        fitted = source.size().scaled(TYPE_ICON, Qt.AspectRatioMode.KeepAspectRatio)  # sourceSize 16x16 + PreserveAspectFit
        icon = _new(fitted)
        p = QPainter(icon)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawImage(QRectF(0, 0, fitted.width(), fitted.height()), source)
        p.end()
        icon = overlay_alpha_only(icon, colors["layer1" if selected else "layer8"])
        painter.drawImage((TYPE_BUTTON.width() - fitted.width()) // 2, (TYPE_BUTTON.height() - fitted.height()) // 2, icon)
    painter.end()
    return out


@dataclass(frozen=True)
class ImageStats:
    readable: bool
    kind: str = ""
    natural: tuple[int, int] = (0, 0)
    has_alpha: bool = False
    opaque_fraction: float = 0.0      # share of pixels with alpha > 127 (at <= 128 px on the long side)
    mean_luminance: float = 0.0       # 0..255, over the pixels counted above
    dark_on_layer1: bool = False      # WidgetDevTool heuristic (see DARK_LUMINANCE), NOT an iCUE rule
    overlay_coverage: float = 0.0     # share of the 16 x 16 tile that the ColorOverlay colours (alpha > 0)
    note: str = ""


DARK_LUMINANCE = 32.0


def analyze(resolved: Resolved) -> ImageStats:
    path = resolved.usable_path
    if path is None:
        return ImageStats(False, note="no readable file" if resolved.source != "none" else "no image")
    image = load_image(path)
    if image.isNull():
        return ImageStats(False, kind=path.suffix.lower().lstrip("."), note="the file cannot be decoded (iCUE would show an empty image)")
    natural = (image.width(), image.height())
    small = image.scaled(QSize(128, 128), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation) \
        if max(natural) > 128 else image
    count = opaque = 0
    lum = 0.0
    has_alpha = False
    for y in range(small.height()):
        for x in range(small.width()):
            c = QColor.fromRgba(small.pixel(x, y))
            count += 1
            if c.alpha() < 255:
                has_alpha = True
            if c.alpha() > 127:
                opaque += 1
                lum += 0.2126 * c.red() + 0.7152 * c.green() + 0.0722 * c.blue()
    mean = lum / opaque if opaque else 0.0
    tile = load_image(path)
    fitted = tile.size().scaled(TYPE_ICON, Qt.AspectRatioMode.KeepAspectRatio)
    tiny = tile.scaled(fitted, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    covered = sum(1 for y in range(tiny.height()) for x in range(tiny.width()) if QColor.fromRgba(tiny.pixel(x, y)).alpha() > 0)
    return ImageStats(True, path.suffix.lower().lstrip("."), natural, has_alpha, opaque / count if count else 0.0, mean,
                      bool(opaque) and mean <= DARK_LUMINANCE, covered / (TYPE_ICON.width() * TYPE_ICON.height()))