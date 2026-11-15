"""qrc:/fonts for standalone QtWebEngine (what iCUE gets from its Qt resources).

iCUE side (verified in source): cue_main.cpp and html_widget_tool/main.cpp register the "qrc" URL scheme with
Secure | Local | LocalAccessAllowed BEFORE the application object exists, and cuestyle_fonts.qrc packs the fonts
under the prefix "/fonts". Widgets (file:// pages) then load `qrc:/fonts/OpenSans-Bold.ttf` without a CORS
error. WidgetResourceAccessInterceptor only lets "qrc:/fonts/" through (WidgetQrcPermissionChecker.cpp).

Here: the same scheme flags (per scheme, CORS is NOT disabled globally) and a Qt resource built at runtime from
font files: the bundled Open Sans files plus an optional folder in the environment variable
WIDGETDEVTOOL_FONTS_DIR (e.g. a licensed copy of the Gotham files). Fonts that are not provided stay a 404.
NOT simulated: blocking of other qrc paths.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

import PySide6
from PySide6.QtCore import QCoreApplication, QResource
from PySide6.QtWebEngineCore import QWebEngineUrlScheme

BUNDLED_FONTS_DIR = Path(__file__).resolve().parent / "fonts"
ENV_FONTS_DIR = "WIDGETDEVTOOL_FONTS_DIR"
FONT_SUFFIXES = {".ttf", ".otf"}

_scheme_registered = False
_registered_fonts: list[str] | None = None


def register_qrc_scheme() -> bool:
    """Register "qrc" like iCUE. Must run before QApplication is created; returns False if too late."""
    global _scheme_registered
    if _scheme_registered:
        return True
    if QCoreApplication.instance() is not None:
        return False
    scheme = QWebEngineUrlScheme(b"qrc")
    scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme | QWebEngineUrlScheme.Flag.LocalScheme
                    | QWebEngineUrlScheme.Flag.LocalAccessAllowed)
    QWebEngineUrlScheme.registerScheme(scheme)
    _scheme_registered = True
    return True


def font_dirs() -> list[Path]:
    """Search order: user folder first (overrides), then the bundled fonts."""
    dirs = []
    extra = os.environ.get(ENV_FONTS_DIR)
    if extra and Path(extra).is_dir():
        dirs.append(Path(extra))
    dirs.append(BUNDLED_FONTS_DIR)
    return dirs


def _collect(dirs: list[Path]) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for d in dirs:
        for f in sorted(d.iterdir()) if d.is_dir() else []:
            if f.is_file() and f.suffix.lower() in FONT_SUFFIXES:
                found.setdefault(f.name, f)  # first directory wins
    return found


def register_fonts(dirs: list[Path] | None = None) -> list[str]:
    """Make the fonts reachable as qrc:/fonts/<file name>. Idempotent; returns the font file names."""
    global _registered_fonts
    if _registered_fonts is not None:
        return _registered_fonts
    fonts = _collect(dirs if dirs is not None else font_dirs())
    if not fonts:
        _registered_fonts = []
        return _registered_fonts
    key = hashlib.sha1("|".join(f"{n}:{p}:{p.stat().st_size}:{p.stat().st_mtime_ns}"
                                for n, p in sorted(fonts.items())).encode()).hexdigest()[:16]
    out_dir = Path(tempfile.gettempdir()) / "widgetdevtool-qrc"
    out_dir.mkdir(exist_ok=True)
    rcc_file = out_dir / f"fonts-{key}.rcc"
    if not rcc_file.is_file():
        qrc_file = out_dir / f"fonts-{key}.qrc"
        entries = "".join(f'<file alias="{n}">{p.as_posix()}</file>' for n, p in sorted(fonts.items()))
        qrc_file.write_text(f'<RCC><qresource prefix="/fonts">{entries}</qresource></RCC>', encoding="utf-8")
        rcc = Path(PySide6.__file__).resolve().parent / ("rcc.exe" if os.name == "nt" else "rcc")
        tmp = rcc_file.with_suffix(f".{os.getpid()}.tmp")
        subprocess.run([str(rcc), "--binary", "-o", str(tmp), str(qrc_file)], check=True, capture_output=True)
        os.replace(tmp, rcc_file)
    if not QResource.registerResource(str(rcc_file)):
        raise RuntimeError(f"cannot register Qt resource {rcc_file}")
    _registered_fonts = sorted(fonts)
    return _registered_fonts


register_qrc_scheme()  # at import time: main_window is imported before QApplication, as QtWebEngine requires