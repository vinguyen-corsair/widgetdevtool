r"""Runtime performance / stability probe (P1). MEASURES only; it changes nothing in the tool.

Windows only (ctypes toolhelp/psapi, no extra dependency). Safety: a watchdog thread kills the whole process tree after --deadline
seconds, a memory guard aborts when python + QtWebEngine children use more than --max-gb, every wait has a timeout.
Run: .venv\Scripts\python.exe tools\perf_probe.py --out probe.json [--max-instances 10] [--cycles 20]
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import gc
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

k32, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
PROCESS_QUERY_LIMITED, TH32CS_SNAPPROCESS = 0x1000, 0x2


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG),
                ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]


class MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD), ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]


k32.OpenProcess.restype = wt.HANDLE
k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
MB = 1024 * 1024


def process_table() -> dict[int, tuple[int, str]]:
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    entry, table = PROCESSENTRY32W(), {}
    entry.dwSize = ctypes.sizeof(entry)
    ok = k32.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        table[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
        ok = k32.Process32NextW(snap, ctypes.byref(entry))
    k32.CloseHandle(snap)
    return table


def descendants(root: int) -> list[int]:
    table, found, frontier = process_table(), [], [root]
    while frontier:
        parent = frontier.pop()
        for pid, (ppid, _name) in table.items():
            if ppid == parent and pid not in found and pid != root:
                found.append(pid)
                frontier.append(pid)
    return found


def stats(pid: int):
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
    if not handle:
        return None
    try:
        pmc = PMC()
        pmc.cb = ctypes.sizeof(pmc)
        psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)
        times = [wt.FILETIME() for _ in range(4)]
        k32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times])
        cpu = sum((t.dwHighDateTime << 32 | t.dwLowDateTime) for t in times[2:]) / 1e7  # kernel + user seconds
        return pmc.WorkingSetSize, pmc.PrivateUsage, cpu
    finally:
        k32.CloseHandle(handle)


def machine_memory() -> tuple[float, float]:
    ms = MEMSTAT()
    ms.dwLength = ctypes.sizeof(ms)
    k32.GlobalMemoryStatusEx(ctypes.byref(ms))
    return ms.ullTotalPhys / 1024 ** 3, ms.ullAvailPhys / 1024 ** 3


def process_types(root: int) -> dict[str, int]:
    """QtWebEngine children by `--type=` (renderer, gpu-process, utility ...) through one CIM query."""
    ids = set(descendants(root))
    cmd = "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    try:
        raw = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60).stdout
        rows = json.loads(raw)
    except Exception:  # noqa: BLE001 - a diagnostic must not fail the run
        return {"?": len(ids)}
    out: dict[str, int] = {}
    for row in rows:
        if row["ProcessId"] in ids:
            m = re.search(r"--type=([\w-]+)", row.get("CommandLine") or "")
            key = m.group(1) if m else "browser-helper"
            out[key] = out.get(key, 0) + 1
    return out


class Abort(Exception):
    pass


class Sampler:
    def __init__(self, max_gb: float) -> None:
        self.me, self.max_bytes, self.last = os.getpid(), max_gb * 1024 ** 3, None

    def sample(self) -> dict:
        now, pids = time.monotonic(), [self.me] + descendants(self.me)
        per = {pid: stats(pid) for pid in pids}
        per = {pid: s for pid, s in per.items() if s}
        me = per.get(self.me, (0, 0, 0))
        kids = [s for pid, s in per.items() if pid != self.me]
        row = {"t": now, "py_ws": me[0] / MB, "py_priv": me[1] / MB, "kids": len(kids), "kids_ws": sum(s[0] for s in kids) / MB,
               "kids_priv": sum(s[1] for s in kids) / MB, "cpu": {pid: s[2] for pid, s in per.items()}}
        row["total_ws"] = row["py_ws"] + row["kids_ws"]
        if row["total_ws"] * MB > self.max_bytes:
            raise Abort(f"memory guard: {row['total_ws'] / 1024:.1f} GB > limit")
        return row

    @staticmethod
    def cpu_percent(a: dict, b: dict) -> tuple[float, float]:
        """(python, children) CPU in % of ONE core between two samples, only processes alive in both."""
        wall = max(b["t"] - a["t"], 1e-6)
        py = ch = 0.0
        for pid, after in b["cpu"].items():
            if pid in a["cpu"]:
                delta = (after - a["cpu"][pid]) / wall * 100
                if pid == os.getpid():
                    py = delta
                else:
                    ch += delta
        return py, ch


def watchdog(deadline: float) -> None:
    def run() -> None:
        time.sleep(deadline)
        for pid in descendants(os.getpid()):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        print("WATCHDOG: deadline reached, process killed", flush=True)
        os._exit(3)
    threading.Thread(target=run, daemon=True).start()

# ------------------------------------------------------------------------------------------------------------------------
STATIC_HTML = """<!doctype html><html><head><title>Probe</title>
<meta name="x-icue-property" content="label" data-type="textfield" data-default="'hello'">
<meta name="x-icue-property" content="level" data-type="slider" data-default="5" data-min="0" data-max="10" data-step="1">
<script>window.__updates = 0; icueEvents = { onICUEInitialized: function () {}, onDataUpdated: function () { window.__updates++; } };</script>
</head><body>static probe widget</body></html>"""
from widgetdevtool.paths import WIDGETS_DIR as WIDGETS  # noqa: E402
REAL = [WIDGETS / "RSS Feed Reader" / "widget" / "rssfeedreader", WIDGETS / "Basketball Scores" / "widget" / "basketballscores",
        WIDGETS / "SmokeClock" / "widget" / "smokeclock"]
COUNTS: dict[str, int] = {}


def count(key: str) -> None:
    COUNTS[key] = COUNTS.get(key, 0) + 1


def instrument() -> None:
    """Counting wrappers only (no behaviour change): reload requests, watcher events, polls, scans, dataChanged, new bridges/runtimes."""
    from widgetdevtool import live_reload, main_window, runtime_bridge, instance_runtime

    def wrap(owner, name, key):
        original = getattr(owner, name)

        def counted(*a, **k):
            count(key)
            return original(*a, **k)
        setattr(owner, name, counted)
    wrap(main_window.MainWindow, "reload", "reload_calls")
    wrap(main_window.MainWindow, "_on_source_changed", "watcher_events")
    wrap(main_window.MainWindow, "refresh_measurement", "measure_js")
    wrap(main_window.MainWindow, "_on_load_finished", "load_finished")
    wrap(live_reload.FolderWatcher, "poll", "watcher_polls")
    wrap(runtime_bridge.Backend, "set_data", "backend_set_data")
    wrap(runtime_bridge.RuntimeBridge, "__init__", "bridges_created")
    wrap(instance_runtime.InstanceRuntime, "__init__", "runtimes_created")
    wrap(instance_runtime.InstanceRuntime, "dispose", "runtimes_disposed")
    scan = live_reload.snapshot

    def counted_scan(root):
        count("folder_scans")
        return scan(root)
    live_reload.snapshot = counted_scan


class Probe:
    def __init__(self, args) -> None:
        self.args, self.app = args, QApplication.instance()
        from widgetdevtool.main_window import MainWindow, State
        self.State = State
        self.win = MainWindow()
        self.win.resize(1100, 820)
        self.win.show()
        self.sampler = Sampler(args.max_gb)
        self.tmp = Path(tempfile.mkdtemp(prefix="wdt_probe_"))
        self.results: dict = {"machine": {}, "levels": [], "cycles": {}, "final": {}}

    # ---- helpers ----
    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def wait(self, predicate, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def widget_copy(self, name: str) -> Path:
        root = self.tmp / name
        root.mkdir(parents=True)
        (root / "manifest.json").write_text(json.dumps({"id": "com.test.probe", "name": "Probe"}), encoding="utf-8")
        (root / "index.html").write_text(STATIC_HTML, encoding="utf-8")
        return root

    def window_sample(self, seconds: float, step: float = 1.0) -> dict:
        """Samples every `step` s for `seconds`: average and max CPU (% of one core), peak memory, counter deltas."""
        before, c0 = self.sampler.sample(), dict(COUNTS)
        rows = [before]
        cpu = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.pump(step)
            row = self.sampler.sample()
            cpu.append(Sampler.cpu_percent(rows[-1], row))
            rows.append(row)
        total_py, total_kids = Sampler.cpu_percent(before, rows[-1])
        return {"seconds": seconds, "cpu_py_avg": round(total_py, 2), "cpu_kids_avg": round(total_kids, 2),
                "cpu_py_max": round(max(c[0] for c in cpu), 2), "cpu_kids_max": round(max(c[1] for c in cpu), 2),
                "ws_peak_mb": round(max(r["total_ws"] for r in rows), 1), "ws_end_mb": round(rows[-1]["total_ws"], 1),
                "counters": {k: COUNTS.get(k, 0) - c0.get(k, 0) for k in COUNTS if COUNTS.get(k, 0) != c0.get(k, 0)}}

    def mem(self) -> dict:
        s = self.sampler.sample()
        return {"py_ws": round(s["py_ws"], 1), "py_priv": round(s["py_priv"], 1), "kids": s["kids"], "kids_ws": round(s["kids_ws"], 1),
                "kids_priv": round(s["kids_priv"], 1), "total_ws": round(s["total_ws"], 1)}

    def page_updates(self) -> list:
        out = []
        for runtime in self.win.runtimes.values():
            box: dict = {}
            runtime.page.runJavaScript("window.__updates", lambda v, b=box: b.setdefault("v", v))
            self.wait(lambda b=box: "v" in b, 5)
            out.append(box.get("v"))
        return out

    def remove_all(self, settle: float = 3.0) -> None:
        for instance_id in list(self.win.store.all()):
            self.win.remove_instance(instance_id.id)
        self.settle_cleanup(settle)

    def settle_cleanup(self, seconds: float) -> None:
        self.pump(seconds)
        gc.collect()
        self.pump(0.5)

    def open_n(self, sources: list[Path], n: int, timeout: float = 90) -> float:
        started, peak = time.monotonic(), 0.0
        for i in range(n):
            if not self.win.load_folder(sources[i % len(sources)]):
                raise RuntimeError(f"cannot open {sources[i % len(sources)]}")
            peak = max(peak, self.sampler.sample()["total_ws"])
        if not self.wait(lambda: all(r.state is self.State.LOADED for r in self.win.runtimes.values()), timeout):
            raise RuntimeError("instances did not finish loading in time")
        return time.monotonic() - started

    # ---- scenarios ----
    def level(self, kind: str, n: int) -> dict:
        self.remove_all(2.0)
        if kind == "static-same":
            sources = [self.widget_copy(f"same_{n}")]
        elif kind == "static-distinct":
            sources = [self.widget_copy(f"distinct_{n}_{i}") for i in range(n)]
        else:
            sources = REAL
        row: dict = {"kind": kind, "n": n, "baseline": self.mem()}
        row["load_seconds"] = round(self.open_n(sources, n), 1)
        ramp_peak = self.window_sample(2.0, 0.5)
        self.pump(3.0)
        row["steady_after_load"], row["peak_during_load_mb"] = self.mem(), ramp_peak["ws_peak_mb"]
        row["types"] = process_types(os.getpid())
        row["watchers"], row["active_timers"] = len(self.win._watchers), len([t for t in self.win.findChildren(QTimer) if t.isActive()])
        row["idle"] = self.window_sample(self.args.idle)
        if kind != "real":
            before = self.page_updates()
            started = self.window_sample(0.01)
            for i in range(20):
                self.win._on_setting_committed("label", f"value-{i}")
                self.pump(0.1)
            row["settings"] = self.window_sample(2.0)
            row["settings"]["page_onDataUpdated_after_20_edits_per_instance"] = [a - b for a, b in zip(self.page_updates(), before)]
        reloads_before = {i: r.reload_count for i, r in self.win.runtimes.items()}
        c0 = dict(COUNTS)
        if kind == "real":
            self.win.reload_now()  # WidgetsXE must not be modified: a manual reload of the active source
            expected = sum(1 for r in self.win.runtimes.values() if self.win._key(r.check.folder) == self.win._key(self.win.active.check.folder))
        else:
            index = sources[0] / "index.html"
            index.write_text(index.read_text(encoding="utf-8") + "\n<!-- edit -->", encoding="utf-8")
            expected = n if kind == "static-same" else 1
        started = time.monotonic()
        done = self.wait(lambda: sum(r.reload_count - reloads_before[i] for i, r in self.win.runtimes.items()) >= expected
                         and not any(r.reloading for r in self.win.runtimes.values()), 40)
        self.pump(1.5)
        row["reload"] = {"expected_reloads": expected, "done": done, "seconds": round(time.monotonic() - started, 1),
                         "counters": {k: COUNTS.get(k, 0) - c0.get(k, 0) for k in COUNTS if COUNTS.get(k, 0) != c0.get(k, 0)},
                         "reloads_per_instance": [r.reload_count - reloads_before[i] for i, r in self.win.runtimes.items()]}
        row["after_reload"] = self.mem()
        for runtime in list(self.win.runtimes.values())[: max(1, n // 2)]:  # Close half (instances stay in the store)
            self.win.close_instance(runtime.instance.id)
        self.settle_cleanup(4.0)
        row["after_close_half"] = self.mem()
        self.remove_all(6.0)
        row["after_remove_all"], row["types_after"] = self.mem(), process_types(os.getpid())
        return row

    def cycles(self, label: str, sources: list[Path], count_: int, per_cycle: int) -> dict:
        self.remove_all(2.0)
        rows = []
        for i in range(count_):
            self.open_n(sources, per_cycle)
            self.pump(1.0)
            for runtime in list(self.win.runtimes.values()):
                self.win.close_instance(runtime.instance.id)
            self.pump(0.5)
            for instance in list(self.win.store.all()):
                self.win.remove_instance(instance.id)
            self.settle_cleanup(2.5)
            m = self.mem()
            m.update({"cycle": i + 1, "py_objects": len(gc.get_objects()), "qobject_children": len(self.win.findChildren(QObject)),
                      "store": len(self.win.store), "runtimes": len(self.win.runtimes), "storage_dirs": len(self.win.storage.names_on_disk())})
            rows.append(m)
        tail = rows[3:] if len(rows) > 6 else rows

        def slope(key):
            xs, ys = [r["cycle"] for r in tail], [r[key] for r in tail]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            den = sum((x - mx) ** 2 for x in xs) or 1
            return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den, 3)
        return {"label": label, "per_cycle": per_cycle, "rows": rows,
                "slope_per_cycle": {k: slope(k) for k in ("py_ws", "py_priv", "kids_ws", "total_ws", "py_objects", "qobject_children", "kids")}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="probe.json")
    parser.add_argument("--max-instances", type=int, default=10)
    parser.add_argument("--max-gb", type=float, default=8.0)
    parser.add_argument("--deadline", type=float, default=1500)
    parser.add_argument("--cycles", type=int, default=20)
    parser.add_argument("--idle", type=float, default=10)
    parser.add_argument("--levels", default="1,3,10")
    parser.add_argument("--only", default="")
    args = parser.parse_args()
    watchdog(args.deadline)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication([])
    instrument()
    total, free = machine_memory()
    probe = Probe(args)
    probe.results["machine"] = {"ram_gb": round(total, 1), "free_gb_at_start": round(free, 1), "logical_cpus": os.cpu_count(), "max_gb_guard": args.max_gb}
    out = Path(args.out)

    def save() -> None:
        out.write_text(json.dumps(probe.results, indent=1, default=str), encoding="utf-8")
    try:
        probe.pump(1.0)
        probe.results["baseline_no_instance"] = probe.window_sample(args.idle)
        probe.results["baseline_no_instance"]["mem"] = probe.mem()
        save()
        levels = [n for n in (int(x) for x in args.levels.split(",")) if n <= args.max_instances]
        for kind in ("static-same", "static-distinct", "real"):
            if args.only and args.only != kind:
                continue
            for n in levels:
                print(f"level {kind} n={n}", flush=True)
                probe.results["levels"].append(probe.level(kind, n))
                save()
        if not args.only or args.only == "cycles":
            print("cycles static", flush=True)
            probe.results["cycles"]["static"] = probe.cycles("static x3 add/close/remove", [probe.widget_copy("cyc")], args.cycles, 3)
            save()
            print("cycles real", flush=True)
            probe.results["cycles"]["real"] = probe.cycles("real RSS/Basketball/SmokeClock add/close/remove", REAL, max(6, args.cycles // 3), 3)
            save()
        probe.remove_all(8.0)
        probe.results["final"] = {"mem_after_all_removed": probe.mem(), "types_after_all_removed": process_types(os.getpid()),
                                  "counters_total": dict(COUNTS), "runtimes": len(probe.win.runtimes), "watchers": len(probe.win._watchers)}
        probe.win.shutdown()
        probe.pump(4.0)
        probe.results["final"]["mem_after_shutdown"] = probe.mem()
        probe.results["final"]["types_after_shutdown"] = process_types(os.getpid())
    except Abort as exc:
        probe.results["aborted"] = str(exc)
        print("ABORTED:", exc, flush=True)
    except Exception as exc:  # noqa: BLE001
        probe.results["error"] = repr(exc)
        print("ERROR:", repr(exc), flush=True)
    finally:
        save()
        shutil.rmtree(probe.tmp, ignore_errors=True)
    print("PROBE DONE", out, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())