r"""Windowed live-reload measurement (real window, GPU left ON). Prints numbers; asserts nothing.
Run: .venv\Scripts\python.exe tools\measure_live_reload.py [widget_folder_for_idle_check]
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: E402,F401
from PySide6.QtWidgets import QApplication  # noqa: E402

from widgetdevtool.main_window import MainWindow  # noqa: E402

app = QApplication([])
w = MainWindow()
w.show()


def pump(seconds, cond=None):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if cond and cond():
            return True
        time.sleep(0.005)
    return cond is None


def luma():
    img = w.view.grab().toImage().scaled(8, 8, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    return sum((p >> 16 & 255) * 0.3 + (p >> 8 & 255) * 0.59 + (p & 255) * 0.11
               for p in (img.pixel(x, y) for x in range(8) for y in range(8))) / 64


def sample(seconds):
    t0, out = time.monotonic(), []
    while time.monotonic() - t0 < seconds:
        app.processEvents()
        out.append((time.monotonic() - t0, luma()))
        time.sleep(0.016)
    return out


def write(root, color, tag):
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text('{"id":"com.t.flick","name":"Flick"}')
    (root / "index.html").write_text('<!doctype html><html><head><style>html,body{margin:0;height:100%%;background:#202020}</style>'
                                     '<link rel="stylesheet" href="s.css"><script src="a.js"></script></head><body><div id=b>%s</div></body></html>' % tag)
    (root / "s.css").write_text("#b{color:%s;font:40px sans-serif;padding:20px}" % color)
    (root / "a.js").write_text("window.__tag='%s'" % tag)


tmp = Path(tempfile.mkdtemp())
write(tmp, "red", "v0")
n = {"v": 0}
w.loadSettled.connect(lambda ok: n.__setitem__("v", n["v"] + 1))
w.load_folder(tmp)
pump(15, lambda: n["v"] >= 1)
pump(1.0)
print("renderer: GPU flags default; first luma =", round(luma(), 1))
idle = sample(8.0)
vals = [v for _, v in idle]
print(f"IDLE 8s: frames={len(idle)} luma min/max={min(vals):.1f}/{max(vals):.1f} reloads={w.reload_count}")
for i, color in enumerate(("blue", "green", "yellow"), 1):
    write(tmp, color, "v%d" % i)
    t0 = time.monotonic()
    s = sample(2.0)
    flash = [(round(t, 3), round(v, 1)) for t, v in s if v > 120]
    print(f"EDIT {i}: reloads={w.reload_count} status={w.watch_label.text()!r} bright(>120) frames={len(flash)}"
          + (f" first={flash[0]} last={flash[-1]}" if flash else ""))
after = sample(4.0)
print("POST idle 4s reloads =", w.reload_count, "luma min/max = %.1f/%.1f" % (min(v for _, v in after), max(v for _, v in after)))
if len(sys.argv) > 1:
    w.load_folder(sys.argv[1]); n["v"] = 0; pump(20, lambda: n["v"] >= 1); pump(1)
    box = {}
    w._page.runJavaScript("localStorage.setItem('wdt_marker','1'); localStorage.length", lambda v: box.setdefault("a", v)); pump(2, lambda: "a" in box)
    pump(8.0); print("EXTERNAL widget idle 8s reloads =", w.reload_count)
    w.reload_now(); pump(20, lambda: w.reload_count >= 1 and not w._reloading); pump(1)
    w._page.runJavaScript("localStorage.getItem('wdt_marker')", lambda v: box.setdefault("b", v)); pump(2, lambda: "b" in box)
    errs = [e.format() for e in w.console.entries if e.level == "error"]
    print("EXTERNAL after manual reload: marker =", box.get("b"), "| reloads =", w.reload_count, "| console errors =", len(errs))
w.shutdown()