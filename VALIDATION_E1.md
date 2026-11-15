# E1 - End-to-End Validation & Gap Analysis (WidgetDevTool v0.4 vs iCUE 5)

Date of measurement: 2026-11-13. No feature was added; no file of iCUE, Olympus or WidgetsXE was written (iCUE data and logs were only READ;
the tool ran with its own temporary data folder; the live-reload test used a temp COPY of RSS Feeder). Raw data: `validation/e1/measurements.json`,
script `validation/e1/e1_run.py` (re-runnable), tool screenshots in `validation/e1/` (I could not look at images in this environment, so they are NOT evidence of visual quality).

## 1. What is installed (Verified)
| Item | Value | Source |
|---|---|---|
| iCUE | 5.52.93 (`Corsair iCUE5 Software`, uninstall key + `cue.init: iCUE version: 5.52.93` in the log) | registry, `%LOCALAPPDATA%\Corsair\Logs\CUE5\*.log` |
| iCUE source used by the tool | branch v5.55 (NOT the release) | `d:\icue` |
| RSS Feeder installed in iCUE | **2.0.4** (`%APPDATA%\Corsair\CUE5\html_widgets\com\shocksim\rssfeedreader`, byte-identical to `rssfeedreader-2.0.4.icuewidget`) | MD5 |
| RSS Feeder in WidgetsXE `widget\` folder | **2.0.5** (identical to the 2.0.5 package); a **2.0.6** package also exists and is NOT the folder content | MD5 |
| 2.0.4 -> 2.0.5 | only 3 lines in index.html: `localStorage.removeItem('rssFeedReaderCache')` at startup | diff |
| 2.0.5 -> 2.0.6 | only the default of `feedUrl2` changes (CNBC URL) | diff |
| Devices in iCUE now | SLIPSTREAM WIRELESS DONGLE V2, M55 Wireless, K70 CORE SE RGB. **No XENEON EDGE connected**; the Dash LCD data in `dashlcd\storage` is stored state of an offline device | log |
| iCUE UI | `iCUE.exe` runs without a visible window (MainWindowHandle 0): it cannot be screenshotted from here, and driving its UI would touch iCUE | Get-Process |

Consequence: tool = 2.0.5, iCUE = 2.0.4. Differences from that pair are limited to the localStorage cleanup; the 2.0.6 default change must not be mixed into a comparison.

## 2. Workflow run on RSS Feeder (real window, platform `windows`, XENEON EDGE profile `dash_accessory_lcd`)
| Step | Result |
|---|---|
| Library of `D:\Widgets\WidgetsXE` -> select -> open | 39 widgets found; RSS Feeder opened; state LOADED; 0 console errors, 0 warnings (3 info) |
| Page content | real feeds loaded in the tool ("NYT > World News ... 15 stories") - the tool has network access that way; not proven for iCUE |
| Settings editor | 18 editors in groups `RSS Feeds` + `Widget Personalization`; 1 read-only (`backgroundMedia`: media assets controller not implemented) |
| Custom Style | switch present; OFF: page gets host `textColor #ffffff`; ON: page gets the instance value `#f6f8ff` (raw kept) |
| Device sizes (all 5 sizes x 2 orientations) | viewport = page `innerWidth x innerHeight` in 9 of 10 cases; **portrait L/XL at "Fit": DPR 0.667 / 0.568 and XL portrait measured 260 wide instead of 261** (page zoom side effect, known) ; at scale 100%: DPR 1, exact size |
| Live reload (temp copy) | CSS edit -> 1 reload, new CSS applied, user value (`refreshMinutes=7`) kept; JS syntax error -> reload happened, error shown in console, page stays LOADED (only manifest/index invalidity keeps the old page); fix -> reload 3 in total, no duplicate reloads |
| 2 instances, own settings | different ids/uniqueId/feedUrl1/refreshMinutes, separate profiles; only the shown one visible |
| localStorage + restart | marker per instance survived tool restart (ids identical, 2 profiles). RSS's own cache key is `{uniqueId}` (seen in instance 1 localStorage) |
| Lifecycle | `iCUE_initialized` true; Linkprovider is the only declared plugin and is a recording MOCK |
| **Workspace side effect** | after the run the restored workspace held **4 instances** (every `load_folder` adds one, including the temp copy). See bug A1 |

## 3. Comparison with the real iCUE 5.52.93 data (read-only)
`%APPDATA%\Corsair\CUE5\dashlcd\storage` (cereal XML; values are base64 `QDataStream` QVariants, decodable with PySide6) and `html_widgets_browser_storage`.
| Fact | Evidence |
|---|---|
| Storage dir name = `<settings id>_<md5(manifest id)>[_p]`; the md5 of `com.shocksim.rssfeedreader` = `a7907691...` matches 2 RSS instances; the id used is the HtmlSettings id (`35aa09fd...`), NOT the Widget slot id (`5174db82...`) | dir names vs storage file |
| Real instance profile is a full persistent Chromium profile (Local Storage, Session Storage, GPUCache, Visited Links...); the `_p` folders exist but are EMPTY (0 files) | directory listing |
| Instance key set of RSS 2.0.4 = the tool's keys (18 widget keys + `...Present`) plus `widgetPersonalizationCustomStyleEnabled` (tool adds it when the user toggles) | decoded 20 keys |
| iCUE stores numbers as double (`transparency 89.0`, `refreshMinutes 10.0`), colors as **QColor**, `backgroundMedia` as an empty map; the tool stores numbers as float and colors as `#rrggbb` strings | decode |
| `toQmlString(QVariant)` (HtmlDataProvider.cpp:202): number-convertible -> raw, else `canConvert<QString>` -> quoted string; QColor therefore most likely reaches the page as `"#rrggbb"` (HYPOTHESIS: not observed in iCUE) | source |
| Device personalization stored: text `rgb(1 1 1)`, accent `rgb(0.941 0.557 0.2)` = `#f08e33`, widget background black, widget transparency 80 = the tool's defaults exactly | decode vs `personalization.py` |
| **Size is stored per widget** (`<widgets>` entries carry `size` 1, 2, 6 and `slotIndex`) | storage file |
| No `zoomFactor` / `devicePixelRatio` in the htmlwidget/widgetbuilder/htmlbase sources (0 hits): iCUE does not zoom the page itself; its DPR comes from the QML window and is UNKNOWN | source grep |

## 4. Gap analysis
Categories: **A** tool bug, **B** host behaviour not simulated, **C** widget source bug, **D** version/device difference (not enough evidence).
| # | Gap | Cat | Impact on debugging |
|---|---|---|---|
| A1 | `load_folder` always creates a NEW instance; `python -m widgetdevtool <folder>` after a restored workspace, "Open widget folder..." and the choose-folder button add a fresh instance (new id, new empty localStorage) on every launch; stale instances (also temp copies) pile up | A | High: persistent state is the point of D7b, and RSS keys its cache by id |
| A2 | Fit zoom changes `devicePixelRatio` (0.57-0.67 in portrait L/XL) and can give a 1-px size error | A/B | Medium: canvas/size-measuring widgets see a different DPR than at 100%; the status bar shows it but does not warn |
| A3 | JS syntax error after a reload leaves a page whose lifecycle never ran, state still LOADED | A (minor) | Low: the console shows the error |
| B1 | Size, device and orientation are per WINDOW in the tool, per WIDGET in iCUE (`size` per `Widget`) | B | High for multi-widget tickets |
| B2 | Only the active instance is visible; no side-by-side layout, hidden pages may be throttled (rAF, timers) | B | High for animation/refresh tickets |
| B3 | Values: float vs double, `#rrggbb` string vs QColor; what the page really receives for QColor/transparency is unobserved | B/D | Medium: any widget that type-checks a setting |
| B4 | Real plugins are mocks (only Linkprovider simulated; Sensors, Media, StreamDeck, device, permissions undefined) | B | High for sensor/media widgets, none for RSS |
| B5 | `backgroundMedia` editor and media rendering, fonts (`fontFamily` list), `glassBlur` rendering depend on iCUE QML/Chromium flags not reproduced | B | Medium |
| B6 | No import of a real iCUE instance (settings, Custom Style, personalization, size) | B | High: the tool cannot start from the ticket reporter's state |
| B7 | Personalization (device/screen) is not saved, and per-screen selection with several screens is Unknown | B/D | Low-Medium |
| C1 | RSS 2.0.5 has no defect found here; the 2.0.4/2.0.5/2.0.6 difference is one-line each | C | none found |
| D1 | iCUE source v5.55 vs release 5.52.93; widget 2.0.5 (tool) vs 2.0.4 (iCUE) | D | Medium: keep the same widget version in both environments |
| D2 | No XENEON EDGE attached and iCUE has no visible window: pixel size, scaling and font rendering of the real device cannot be measured here | D | High (the main unknown) |

## 5. Feature table
Match = measured on both sides; Mismatch = measured different; Unknown = only one side observed.
| Feature | iCUE 5 | WidgetDevTool | Verdict | Evidence | Priority |
|---|---|---|---|---|---|
| Storage name formula + settings id (not Widget id) | `<settingsId>_<md5(typeId)>[_p]` in HtmlWidgetsBrowserStorage | same formula, own folder | **Match** | dir names + md5 + storage file | done |
| Persistent per-instance profile, empty `_p` | yes | persistent; preview = off-the-record | **Match** (the `_p` content equal: nothing) | listing | low |
| Two instances: isolated localStorage | separate dirs | separate profiles, measured isolation | **Match** (isolation inferred for iCUE from separate dirs) | tests + listing | done |
| Instance key set (RSS) | 20 keys | 19 + Enabled on toggle | **Match** | decode | low |
| Value types | double / QColor | float / `#rrggbb` | **Unknown** (page-side value unobserved) | decode + source | medium |
| Device personalization defaults | `#fff`, `#f08e33`, black, 80 | same | **Match** | decode | done |
| Custom Style semantics (Present/Enabled -> raw map) | source v5.55 | same | **Match to source**, not observed in release | D4 | low |
| Widget size | per widget | per window | **Mismatch** | storage `size` | high |
| Several widgets visible together | yes (Dash LCD screen) | one visible | **Mismatch** | B2 | high |
| Viewport size at zoom 1 | formula verified in source | exact, DPR 1 | **Unknown** (real device pixels unobserved) | S4 | high |
| Page zoom / DPR | no zoom in source; DPR unknown | Fit zoom changes DPR | **Unknown / risk** | S4 + grep | medium |
| Colors, fonts, transparency, glass blur, background media rendering | iCUE QML + Chromium | Chromium page only; media editor missing | **Unknown** | none | medium |
| Live reload | iCUE: no live reload of installed widgets (not tested) | full-page, one watcher/source, user values kept | n/a (tool-only) | S5 | - |
| Plugins | real Linkprovider etc. | Linkprovider mock only | **Mismatch** for others | B4 | high for sensor widgets |
| Console / lifecycle | not observable (no DevTools in the release) | 0 errors for RSS 2.0.5 | **Unknown** | S7 | - |
| Restart persistence of instances | iCUE keeps the screen layout | workspace.json, but A1 piles up instances | **Match with bug** | S6 | high (A1) |
| Pixel appearance | cannot be observed here | n/a | **Unknown - no claim** | D2 | - |

## 6. Three most valuable changes for a real ticket (not implemented)
1. **Fix A1 (small):** opening a folder reuses the existing instance of that source (explicit "Add" makes another); offer "Reset storage" per instance. Without it every launch loses the instance state a ticket depends on.
2. **Read-only importer of an iCUE instance:** decode `dashlcd\storage` (QVariant: double, QColor, map, bool) + the device/widget personalization and `size`, and create tool instances with the SAME settings, Custom Style and size. The decoder was prototyped here (`e1_run.py`) and works on the real file. This removes the largest uncertainty (the reporter's state) and settles B3 by showing real stored types.
3. **Per-instance size with a side-by-side view, rendered at DPR 1** (scroll/arrange instead of zoom): covers B1, B2 and A2 together.

## 7. Checklist for the user (iCUE side; I could not observe it)
Use RSS Feeder **2.0.5** installed in iCUE (or run the tool on the installed 2.0.4 copy) so the pair is equal. Needs a XENEON EDGE (or the same device model).
1. Screenshot of the widget on the device screen: Size M landscape, default settings, Custom Style OFF. Then the same with Custom Style ON and a changed text color. Note device resolution and Windows scale.
2. Same for SizeS, SizeL, SizeXL and portrait L (to compare real pixels with 315x129, 315x261, 633x261, 951x261, 261x633).
3. A second RSS instance with different `feedUrl1`; screenshot both together on one screen (tests B1/B2).
4. A widget that logs `window.devicePixelRatio, innerWidth, innerHeight` (a temp copy placed in the user widget folder) and its output on the device: settles A2/D2.
5. Copy of `%APPDATA%\Corsair\CUE5\dashlcd\storage` (may contain feed URLs) so the importer can be verified.
Tool side screenshots at the same conditions: `python -m widgetdevtool "<widget folder>"`, XENEON EDGE, same size, scale 100%.