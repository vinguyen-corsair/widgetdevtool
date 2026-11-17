r"""Device profiles: what a device says about the widget screen, read from the real iCUE `.cuemsf` manifests.

Source (iCUE v5.55, NOT verified against release 5.52.93 / 5.46.67):
- WidgetScreenCapabilities (lib/interfaces/manifest/src/WidgetScreenCapabilities.h): mimicScreenSize, physicalScreenSize,
  widgetSizeMaps (relative sizes per EWidgetSize, landscape and portrait), landscapeGrid, portraitGrid.
- libs/dashlcdlib/src/WidgetSizeUtils.cpp: size() = relative size x mimicScreenSize (landscape: w*mimic.w, h*mimic.h; portrait:
  w*mimic.h, h*mimic.w); deviceSupportedSizes() = the keys of landscapeRelativeSizes; screenSizeText() truncates to int.
- mod/widgets/widgetseditors/src/WidgetSizeWatcherImpl.cpp widgetSizePx(): Dash LCD -> WidgetSizeUtils::size(); else keyboard ->
  ScreenModuleCapabilities.screenGeometry; else pump -> WidgetsRects[HardwareWidgetSetup]; else iDisplays -> hardware property
  LCDScreenSize (not in the manifests, so not importable); fallback 480x480.

No size is written by hand here: profiles are generated from the manifests (`generate`) into profiles/device_profiles.json,
which the tool loads at run time (it needs no iCUE checkout).
Run:  .venv\Scripts\python.exe -m widgetdevtool.device_profiles generate [--icue DIR]  (default: widgetdevtool.paths.ICUE_DIR) [--out FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from .paths import ICUE_DIR
from typing import Any

SIZE_ORDER = ("SizeS", "SizeM", "SizeL", "SizeXL")
SIZE_LABEL = {"SizeS": "S", "SizeM": "M", "SizeL": "L", "SizeXL": "XL"}
LANDSCAPE, PORTRAIT = "landscape", "portrait"
KIND_DASHBOARD, KIND_KEYBOARD, KIND_PUMP = "dashboard", "keyboard", "pump"
PROFILES_FILE = Path(__file__).parent / "profiles" / "device_profiles.json"
MANIFEST_DIR = Path("Application") / "src" / "mod" / "devices"
FORMAT_VERSION = 1
TEST_MANIFEST_PREFIXES = ("dummy_", "fake_", "demo_")


@dataclass
class DeviceProfile:
    id: str                      # manifest file name without "_manifest.cuemsf"
    name: str                    # UserModelName
    kind: str                    # dashboard | keyboard | pump (the widgetSizePx source, in iCUE's order)
    html_types: list[str]        # SupportedHtmlWidgetDeviceTypes
    source: str                  # manifest path relative to the iCUE checkout
    sha256: str                  # of that manifest file
    mimic: tuple[int, int] = (0, 0)       # dashboard only
    physical: tuple[int, int] = (0, 0)    # dashboard only
    sizes: dict[str, dict[str, tuple[float, float]]] = field(default_factory=dict)  # size -> orientation -> relative (w, h)
    grid: dict[str, list[tuple[float, float]]] = field(default_factory=dict)        # orientation -> slot positions
    fixed: tuple[int, int] = (0, 0)       # keyboard / pump: the one widget size in px
    fixed_from: str = ""                  # which manifest property it came from
    auto_rotation: bool | None = None     # SupportsScreenAutoRotation (metadata only)
    initial_orientation: str = ""         # InitialScreenOrientation (metadata only)
    rotations: list[str] = field(default_factory=list)  # LcdScreenRotationDegrees (metadata only, not simulated)
    touch_setup: bool = False             # HasTouchScreenSetupNotification: a "sensor-screen" widget needs it (HtmlWidgetDeviceUtils)
    notes: list[str] = field(default_factory=list)

    # ---- what the device supports ----
    def supported_sizes(self) -> list[str]:
        """Sizes with a landscape AND a portrait entry, S..XL order (iCUE asserts both maps have the same keys)."""
        return [s for s in SIZE_ORDER if s in self.sizes and LANDSCAPE in self.sizes[s] and PORTRAIT in self.sizes[s]]

    def orientations(self) -> list[str]:
        return [LANDSCAPE, PORTRAIT] if self.kind == KIND_DASHBOARD and self.supported_sizes() else []

    def slot_count(self) -> int:
        return len(self.grid.get(LANDSCAPE, []))

    # ---- sizes (pure formulas, see module doc) ----
    def widget_size(self, size: str, orientation: str) -> tuple[float, float]:
        """widgetsize_utils::size(): relative size x mimicScreenSize. Keyboard / pump: the fixed size."""
        if self.kind != KIND_DASHBOARD:
            return float(self.fixed[0]), float(self.fixed[1])
        return self._scaled(size, orientation, self.mimic)

    def widget_size_physical(self, size: str, orientation: str) -> tuple[float, float]:
        """EXPERIMENTAL: relative size x physicalScreenSize (what the assignment view lays out at scale 1; the device's own
        rendering is not known). Dashboard only."""
        if self.kind != KIND_DASHBOARD:
            raise ValueError("physical size exists only for dashboard devices")
        return self._scaled(size, orientation, self.physical)

    def screen_size(self, orientation: str) -> tuple[int, int]:
        """widgetsize_utils::screenSize(): physicalScreenSize, swapped in portrait."""
        w, h = self.physical
        return (w, h) if orientation == LANDSCAPE else (h, w)

    def _scaled(self, size: str, orientation: str, base: tuple[int, int]) -> tuple[float, float]:
        if size not in self.supported_sizes():
            raise ValueError(f"{self.name} does not support {size}")
        if orientation not in (LANDSCAPE, PORTRAIT):
            raise ValueError(f"unknown orientation {orientation!r}")
        rw, rh = self.sizes[size][orientation]
        bw, bh = (base[0], base[1]) if orientation == LANDSCAPE else (base[1], base[0])
        return rw * bw, rh * bh

    # ---- (de)serialisation ----
    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in ("id", "name", "kind", "html_types", "source", "sha256", "auto_rotation",
                                           "initial_orientation", "rotations", "notes", "fixed_from", "touch_setup")}
        d.update(mimic=list(self.mimic), physical=list(self.physical), fixed=list(self.fixed),
                 sizes={s: {o: list(v) for o, v in per.items()} for s, per in self.sizes.items()},
                 grid={o: [list(p) for p in pts] for o, pts in self.grid.items()})
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DeviceProfile":
        return cls(id=d["id"], name=d["name"], kind=d["kind"], html_types=list(d.get("html_types", [])), source=d["source"],
                   sha256=d["sha256"], mimic=tuple(d.get("mimic", (0, 0))), physical=tuple(d.get("physical", (0, 0))),
                   sizes={s: {o: tuple(v) for o, v in per.items()} for s, per in d.get("sizes", {}).items()},
                   grid={o: [tuple(p) for p in pts] for o, pts in d.get("grid", {}).items()},
                   fixed=tuple(d.get("fixed", (0, 0))), fixed_from=d.get("fixed_from", ""),
                   auto_rotation=d.get("auto_rotation"), initial_orientation=d.get("initial_orientation", ""),
                   rotations=list(d.get("rotations", [])), notes=list(d.get("notes", [])),
                   touch_setup=bool(d.get("touch_setup", False)))


def to_px(size: tuple[float, float]) -> tuple[int, int]:
    """int() truncation, as WidgetSizeUtils::screenSizeText() does (the only int conversion in iCUE's size code). How the
    host rounds the size of the web view itself is NOT known."""
    return int(size[0]), int(size[1])

# ---------------------------------------------------------------------------------------------- import (manifest -> profile)
def _find(obj: Any, key: str) -> Any:
    """First value stored under `key` in the manifest (the `common` object first, then anywhere below it)."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find(value, key)
            if found is not None:
                return found
    return None


def _wh(value: Any) -> tuple[int, int]:
    return int(value["width"]), int(value["height"])


def import_manifest(path: str | Path, icue_root: str | Path | None = None) -> tuple[DeviceProfile | None, str]:
    """One `.cuemsf` -> (profile, "") or (None, reason). Only devices that can show widgets are of interest: a manifest with
    WidgetScreenCapabilities or a non-empty SupportedHtmlWidgetDeviceTypes. Nothing is invented: a device without a size
    source is refused with the reason."""
    path = Path(path)
    raw = path.read_bytes()
    doc = json.loads(raw.decode("utf-8-sig"))
    common = doc.get("common", doc)
    html_types = [str(t) for t in (_find(common, "SupportedHtmlWidgetDeviceTypes") or [])]
    caps = _find(common, "WidgetScreenCapabilities")
    if not caps and not html_types:
        return None, "not a widget device"
    name = str(_find(common, "UserModelName") or path.stem)
    try:
        source = str(path.relative_to(icue_root)) if icue_root else path.name
    except ValueError:
        source = str(path)
    profile = DeviceProfile(id=path.stem.removesuffix("_manifest"), name=name, kind="", html_types=html_types,
                            source=source.replace("\\", "/"), sha256=hashlib.sha256(raw).hexdigest(),
                            auto_rotation=_find(common, "SupportsScreenAutoRotation"),
                            initial_orientation=str(_find(common, "InitialScreenOrientation") or ""),
                            rotations=[str(r) for r in (_find(common, "LcdScreenRotationDegrees") or [])],
                            touch_setup=bool(_find(common, "HasTouchScreenSetupNotification")))
    if caps:  # Dash LCD: the same order as WidgetSizeWatcherImpl::widgetSizePx()
        profile.kind = KIND_DASHBOARD
        profile.mimic, profile.physical = _wh(caps["mimicScreenSize"]), _wh(caps["physicalScreenSize"])
        maps = caps.get("widgetSizeMaps", {})
        for orientation, key in ((LANDSCAPE, "landscapeRelativeSizes"), (PORTRAIT, "portraitRelativeSizes")):
            for size, wh in maps.get(key, {}).items():
                profile.sizes.setdefault(size, {})[orientation] = (float(wh["width"]), float(wh["height"]))
        profile.grid = {LANDSCAPE: [(float(p["x"]), float(p["y"])) for p in caps.get("landscapeGrid", [])],
                        PORTRAIT: [(float(p["x"]), float(p["y"])) for p in caps.get("portraitGrid", [])]}
        lone = sorted(s for s, per in profile.sizes.items() if len(per) != 2)
        if lone:
            profile.notes.append(f"size(s) {lone} exist in only one orientation: not offered (iCUE asserts equal key sets)")
        if len(profile.grid[LANDSCAPE]) != len(profile.grid[PORTRAIT]):
            profile.notes.append("landscape and portrait grids differ in length (iCUE asserts equal)")
        return profile, ""
    geometry = (_find(common, "ScreenModuleCapabilities") or {}).get("screenGeometry")
    if geometry:
        profile.kind, profile.fixed, profile.fixed_from = KIND_KEYBOARD, _wh(geometry), "ScreenModuleCapabilities.screenGeometry"
        return profile, ""
    rects = _find(common, "WidgetsRects") or {}
    if "HardwareWidgetSetup" in rects:
        profile.kind, profile.fixed, profile.fixed_from = KIND_PUMP, _wh(rects["HardwareWidgetSetup"]), \
            "WidgetsRects.HardwareWidgetSetup"
        other = rects.get("WidgetSetup")
        if other and _wh(other) != profile.fixed:
            profile.notes.append(f"WidgetSetup rect is {_wh(other)[0]}x{_wh(other)[1]} (setup page); widgetSizePx uses "
                                 "HardwareWidgetSetup")
        return profile, ""
    return None, ("widget device without a size source in the manifest (iCUE would read the hardware property "
                  "LCDScreenSize or fall back to 480x480)")


def generate_profiles(icue_root: str | Path) -> dict[str, Any]:
    """Scan every manifest of the checkout; return the document stored in device_profiles.json."""
    root = Path(icue_root)
    profiles, skipped = [], []
    for path in sorted((root / MANIFEST_DIR).rglob("*.cuemsf")):
        if path.stem.startswith(TEST_MANIFEST_PREFIXES):  # dummy / fake / demo devices are not products
            continue
        try:
            profile, reason = import_manifest(path, root)
        except (ValueError, KeyError, TypeError) as exc:
            profile, reason = None, f"manifest not understood: {exc!r}"
        if profile is not None:
            profiles.append(profile)
        elif reason != "not a widget device":
            skipped.append({"id": path.stem.removesuffix("_manifest"), "source": path.relative_to(root).as_posix(),
                            "reason": reason})
    order = {KIND_DASHBOARD: 0, KIND_KEYBOARD: 1, KIND_PUMP: 2}
    profiles.sort(key=lambda p: (order[p.kind], p.name.lower(), p.id))
    return {"version": FORMAT_VERSION, "icue_branch": _git_branch(root), "profiles": [p.to_dict() for p in profiles],
            "skipped": skipped}


def _git_branch(root: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), "branch", "--show-current"], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def load_profiles(path: str | Path = PROFILES_FILE) -> list[DeviceProfile]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("version") != FORMAT_VERSION:
        raise ValueError("unsupported device profiles file")
    return [DeviceProfile.from_dict(d) for d in doc["profiles"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="widgetdevtool.device_profiles")
    sub = parser.add_subparsers(dest="cmd", required=True)
    gen = sub.add_parser("generate", help="read the .cuemsf manifests of an iCUE checkout and write the profiles file")
    gen.add_argument("--icue", default=str(ICUE_DIR))
    gen.add_argument("--out", default=str(PROFILES_FILE))
    args = parser.parse_args(argv)
    doc = generate_profiles(args.icue)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(doc['profiles'])} profiles, {len(doc['skipped'])} skipped (iCUE branch {doc['icue_branch']}) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())