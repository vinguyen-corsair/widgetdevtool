# P1 - Runtime Performance & Stability Diagnostics (WidgetDevTool v0.4, 271 tests)

Measured 2026-11-13 on Windows (20 logical CPUs, 31.8 GB RAM, 14 GB free), real `windows` Qt platform, default QtWebEngine flags (GPU on), display scaling 100 %.
**Nothing was optimised or changed in the runtime**; the only addition is the measuring script `tools/perf_probe.py` (counting wrappers + Win32 process counters, no behaviour change).
Raw data: `validation/p1/probe_full.json` (levels + 20/6 cycles), `probe_cycles_60.json` (60 + 20 cycles), `python_object_growth.txt`.
Safety used: a watchdog thread kills the whole process tree after `--deadline` (2700 s / 1500 s), a memory guard aborts above 8 GB (peak seen: 1.1 GB), every wait has a timeout, a temp data dir is used; no run aborted. WidgetsXE and iCUE were only read (no edit; the "real" series edits nothing: its reload is `reload_now`).
Units: CPU = % of ONE core (20 cores available); RAM = Windows working set (WS) of python + its QtWebEngine child processes, MB; "private" = private bytes (commit).
Caveat: the probe pumps the Qt loop every 10 ms and samples once per second, so even "no instance" shows ~1.7 % python CPU: that is mostly probe overhead, not the tool.

## 1. CPU / RAM by number of instances
Series: **same** = N instances of one static probe widget (1 watcher); **distinct** = N copies in N folders (N watchers); **real** = RSS Feeder / Basketball Scores / SmokeClock cycled (3 watchers, real JS, network).
Baseline with no instance: python 126 MB WS, CPU 1.7 % (probe loop), 0 children, 0 counters except watcher polls.
| series | N | base MB | steady MB (python + kids) | kids | peak MB (load) | idle CPU python / kids avg (max) | after Close half | after Remove all (kids) |
|---|---|---|---|---|---|---|---|---|
| same | 1 | 126 | 330 (266 + 65) | 1 | 330 | 4.3 / 0.0 (7.8 / 0.0) | 266 | 266 (0) |
| same | 3 | 266 | 459 (275 + 184) | 3 | 460 | 2.5 / 0.0 (6.2 / 0.0) | 399 | 272 (0) |
| same | 10 | 272 | 908 (292 + 616) | 10 | 911 | 2.9 / 0.0 (4.6 / 0.0) | 609 | 276 (0) |
| distinct | 1 | 277 | 349 (285 + 65) | 1 | 349 | 2.3 / 0.1 (6.2 / 1.5) | 281 | 281 (0) |
| distinct | 3 | 281 | 470 (286 + 184) | 3 | 472 | 2.9 / 0.0 (9.2 / 0.0) | 401 | 278 (0) |
| distinct | 10 | 279 | 908 (298 + 610) | 10 | 914 | 5.4 / 0.0 (10.8 / 0.0) | 600 | 285 (0) |
| real | 1 | 285 | 389 (298 + 92) | 1 | 410 | 2.8 / 0.1 (6.2 / 1.6) | 292 | 288 (0) |
| real | 3 | 289 | 531 (303 + 228) | 3 | 531 | 6.0 / 1.4 (35.6 / 13.9) | 465 | 297 (0) |
| real | 10 | 297 | 1107 (334 + 773) | 10 | 1108 | 7.4 / 7.5 (20.0 / 37.0) | 756 | 327 (0) |
- Cost per instance: one renderer process of ~61-65 MB (static) / ~77-92 MB (real widgets) and ~2 MB in python. First instance adds ~140 MB to python (QtWebEngine initialisation) that stays after everything is removed (126 -> ~266 MB): expected, not a leak.
- Peak during load equals the steady value (no transient spike); loading 10 instances took 2.4-3.5 s.
- Static widgets: renderer CPU 0.0 % idle: the tool adds no renderer load. The real-widget CPU (kids 1.4 % at N=3, 7.5 % avg / 37 % max at N=10) is the widgets' own JS (feed fetch/refresh, SmokeClock animation) - legitimate widget work, see section 2.
- After Remove all: 0 child processes in every run.

## 2. Loops, polling and callbacks (idle = 10 s with nobody saving)
| counter in 10 s idle | value | verdict |
|---|---|---|
| `reload` requests / watcher change events | 0 / 0 (every series, N up to 10) | no reload loop |
| `Backend.set_data` (the source of every `onDataUpdated`) | 0 | no feedback loop; page `onDataUpdated` = 0 |
| new bridges / runtimes / page loads / viewport JS measurements | 0 / 0 / 0 / 0 | quiet |
| folder watcher polls = folder scans | ~93 per watcher (9.3/s; timer 100 ms); N distinct sources: 281 (3) and 937 (10) | by design; scales with the number of SOURCES, not instances |
- Active QTimers = number of watchers (1 / 3 / 10); no extra timers; watchers == number of distinct source folders (1 for 10 instances of one source: no duplicated watchers or callbacks).
- Settings: 20 edits -> exactly 20 `onDataUpdated` in the ACTIVE instance, 0 in the others, 0 reloads, 0 new bridges.
- Save of a source file: 1 watcher event -> exactly one reload per instance of that source (same: 1 / 3 / 10 reloads; distinct: 1 reload only for the edited source; real `reload_now`: 4 of 10), no repeats, 2.1-3.2 s to settle (250 ms debounce + page load).
- Whole run (143 page loads, 120 instances created and disposed): `watcher_events` 6 for 6 edits, `reload_calls` 23 = all explained (6 edits' reloads + manual), `runtimes_created` = `runtimes_disposed` = 120.
- JS polling of widgets vs. tool: the tool generates no periodic JS except the polling timers above (python side). `refresh_measurement` (one `runJavaScript`) fires per viewport re-layout (add/remove/show/resize), not periodically: 435 calls for 143 loads (~3 per instance lifecycle), 0 when idle. Renderer CPU of static widgets is 0, so anything above that in the real series is widget-originated.

## 3. Lifecycle and cleanup
| check | result |
|---|---|
| QtWebEngine children while instances run | exactly N `renderer` processes for N instances (no gpu/utility children seen) |
| after Close (instance stays in the store) | its renderer is gone (children drop from N to N - closed); WS follows |
| after Remove all / after `shutdown()` | **0** children (also after 60-cycle runs) |
| orphans after the probe process exited | none from the tool (the only `QtWebEngineProcess` left on the machine belongs to Elgato Stream Deck, PID 38712, started hours earlier) |
| objects after cycles | `runtimes` 0, `store` 0, storage dirs 0, `findChildren(QObject)` constant 292 over 60 cycles (no Qt object accumulation); `runtimes_created` == `disposed` |
**Trend over many Add(3)/Close/Remove cycles** (3 instances per cycle, 2.5 s settle, `gc.collect()` each time):
| series | cycles | python WS slope | python private slope | python objects slope | kids / QObjects |
|---|---|---|---|---|---|
| static | 20 / 60 | -0.8 / **-0.05 MB per cycle** (flat, noisy +-8 MB) | +0.42 / **+0.33 MB per cycle** (215 -> 237 MB over 60 cycles; mean early5 226.3 -> last5 238.6) | **+16.0 per cycle** (exact) | 0 / 292 constant |
| real | 6 / 20 | +1.7 / **-1.7 MB per cycle** | +1.8 / +0.76 MB per cycle (255 -> 271 over 20 cycles) | +17.0 per cycle | 0 / 292 constant |
- WS is flat or falling; no trend in renderer memory (children are always 0 after each cycle).
- Verified small growth: python objects +16 per 3-instance cycle (162 `weakref` objects over 10 cycles, no other type; likely PySide6 signal/slot bookkeeping, owner not pinned down; ~ a few KB in total) and private bytes +0.33 MB per cycle (~110 KB per instance lifecycle).
- **Not concluded as a leak:** the private-bytes slope is small, not matched by the working set, not matched by Python objects (16 objects cannot be 330 KB) and could be native allocator fragmentation/caches in Qt/Chromium. At 0.33 MB per cycle it would take thousands of cycles to matter. Needs a longer run (>= 300 cycles) and a native heap tool to decide.

## 4. Normal and abnormal signs
Normal: 1 renderer per instance (65-90 MB), no spike on load, 0 reloads and 0 `onDataUpdated` when idle, one watcher per source, reload count == instances of the source, all children gone after Remove/shutdown, flat working set over 60 cycles, constant QObject count, 0 orphan processes.
Worth watching (not harmful today): (a) watcher polling 10 scans/s per source folder (CPU 2-5 % of one core with 10 sources, includes probe overhead; a 30-file widget scans in ~0.1 ms); (b) +16 python weakrefs and +0.33 MB private per cycle; (c) python RAM never returns to the 126 MB start (QtWebEngine initialisation, +140 MB, one-time); (d) `refresh_measurement` JS per viewport re-layout (cheap, not periodic).
Abnormal: none found (no loop, no duplicate watcher/callback, no leaked process, no unbounded growth in WS/children/QObjects).

## 5. Verified / not verified / next
Verified: everything in sections 1-3 for the tested configurations (static widgets up to 10 instances, real RSS/Basketball/SmokeClock mix up to 10, 60 + 20 cycles).
Not verified: idle behaviour over hours; hidden-view throttling impact on real widgets' CPU; 20+ instances; GPU memory (not in WS/private figures); other real widgets; fractional display scaling effects; the native source of the private-bytes drift; per-process CPU of individual real widgets (only the sum is recorded).
Suggested handling (no change made): none required now. If needed later: (1) replace polling by a single native directory watcher when there are many sources; (2) debounce `refresh_measurement` on viewport changes; (3) run `tools/perf_probe.py --only cycles --cycles 300` and a native heap profiler before calling the private-bytes drift a leak. Re-run: `.venv\Scripts\python.exe tools\perf_probe.py --out probe.json` (about 27 min for all series; `--levels`, `--only`, `--cycles`, `--max-gb`, `--deadline` limit it).