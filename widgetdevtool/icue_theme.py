r"""Static iCUE UI facts read from the source tree (never typed in by hand): the CuePalette colours and the default widget icon.

Source (iCUE v5.55, NOT verified against release 5.52.93 / 5.46.67):
- qml/com/corsair/cue/cuestyle/src/CuePalette.cpp: `const auto g_layerNColor = 0xRRGGBB;`, returned by `CuePalette::layerN()`
  through `QColor::fromRgb`. The palette has no theme branch (one fixed set of constants).
- mod/widgets/htmlbase/src/HtmlTypeInterfacesImpl.cpp: the default widget image is `qrc:///cue-icons/4.0/screeneffects/web-url.svg`
  (the file sits in qml/com/corsair/cue/cuestyle/icons/screeneffects/web-url.svg).
`generate` writes profiles/palette.json (colours, source line, sha256) and copies the icon to assets/web-url.svg; the tool loads
those two files at run time and needs no iCUE checkout.
Run:  .venv\Scripts\python.exe -m widgetdevtool.icue_theme generate [--icue DIR]  (default: widgetdevtool.paths.ICUE_DIR)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from .paths import ICUE_DIR

PALETTE_FILE = Path(__file__).parent / "profiles" / "palette.json"
DEFAULT_ICON_FILE = Path(__file__).parent / "assets" / "web-url.svg"
PALETTE_SOURCE = Path("Application/src/qml/com/corsair/cue/cuestyle/src/CuePalette.cpp")
DEFAULT_ICON_SOURCE = Path("Application/src/qml/com/corsair/cue/cuestyle/icons/screeneffects/web-url.svg")
DEFAULT_ICON_URL = "qrc:///cue-icons/4.0/screeneffects/web-url.svg"
_COLOR = re.compile(r"const auto g_(\w+?)Color = 0x([0-9A-Fa-f]{6});")
_HOVER = re.compile(r"const auto g_(hover\d) = 0x([0-9A-Fa-f]{6});")
_HOVER_OPACITY = re.compile(r"const auto g_(hover\d)opacity = ([0-9.]+);")


def generate(icue_root: str | Path, palette_out: Path = PALETTE_FILE, icon_out: Path = DEFAULT_ICON_FILE) -> dict:
    root = Path(icue_root)
    source = root / PALETTE_SOURCE
    text = source.read_text(encoding="utf-8-sig")
    colors = {name: "#" + value.lower() for name, value in _COLOR.findall(text)}
    colors.update({name: "#" + value.lower() for name, value in _HOVER.findall(text)})
    opacities = {name: float(value) for name, value in _HOVER_OPACITY.findall(text)}
    if not {"layer1", "layer5", "layer8", "layer9"} <= set(colors):
        raise ValueError("CuePalette.cpp: layer colours not found")
    icon = root / DEFAULT_ICON_SOURCE
    doc = {"version": 1, "source": PALETTE_SOURCE.as_posix(), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
           "colors": dict(sorted(colors.items())), "hover_opacity": opacities,
           "default_icon": {"url": DEFAULT_ICON_URL, "source": DEFAULT_ICON_SOURCE.as_posix(),
                            "sha256": hashlib.sha256(icon.read_bytes()).hexdigest()}}
    palette_out.parent.mkdir(parents=True, exist_ok=True)
    palette_out.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    icon_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(icon, icon_out)
    return doc


def load_palette(path: Path = PALETTE_FILE) -> dict[str, str]:
    """Colour name (layer1..layer9, accentN, hoverN...) -> '#rrggbb'."""
    return dict(json.loads(path.read_text(encoding="utf-8"))["colors"])


def palette_document(path: Path = PALETTE_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="widgetdevtool.icue_theme")
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate", help="read CuePalette.cpp and the default icon of an iCUE checkout")
    gen.add_argument("--icue", default=str(ICUE_DIR))
    args = parser.parse_args(argv)
    doc = generate(args.icue)
    print(f"palette: {len(doc['colors'])} colours; layer1={doc['colors']['layer1']} layer5={doc['colors']['layer5']} "
          f"layer8={doc['colors']['layer8']} layer9={doc['colors']['layer9']}; default icon copied")
    return 0


if __name__ == "__main__":
    sys.exit(main())