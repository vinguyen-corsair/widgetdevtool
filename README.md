# iCUE Widget Dev Tool (POC v0.7 - widget library)

Standalone previewer for iCUE HTML widgets. Python 3.13 + PySide6 6.9.3
(QtWebEngine 6.9.3 / Chromium 130, the same engine family as iCUE 5.52).

## Run

Dung venv rieng, khong can kich hoat va khong dung Python he thong:

    cd D:\WidgetDevTool
    .venv\Scripts\python.exe -m widgetdevtool
    .venv\Scripts\python.exe -m widgetdevtool "D:\Widgets\WidgetsXE\RSS Feed Reader\widget\rssfeedreader"

Bam **Open widget folder...** de chon thu muc co `manifest.json` va `index.html`.
Widget nap truc tiep bang `file://` (khong dong goi `.icuewidget`, khong import).
Panel duoi cung hien JavaScript console: log, loi chua bat (uncaught) va ghi chu cua bridge (nguon `bridge`).

## Test

    .venv\Scripts\python.exe -m unittest discover -s tests -v

Headless (offscreen), khong can iCUE, Olympus hay thiet bi that. Test RSS Feeder tu bo qua neu
thu muc WidgetsXE khong ton tai. Luu y: widget RSS Feeder that su goi mang toi cac feed.

## Runtime bridge v0.2

Co (dua tren `HtmlDataProvider.cpp` cua iCUE source v5.55, **chua doi chieu voi ban 5.52.93 da cai**):

- Template script lay nguyen van tu iCUE (`widgetdevtool/vendored/`, nguon trong `SOURCE.txt`;
  nap lai bang `tools/vendor_icue_templates.py`).
- Globals: `iCUE`, `iCUE_initialized`, `icueEvents`, `tr()` (tra ve Promise nhu iCUE), `uniqueId`,
  `tick()`, widget properties dang `window.<ten>`.
- Lifecycle: WebChannel ready -> DOMContentLoaded -> `onICUEInitialized` -> `onDataUpdated`.
- Properties: gia tri mac dinh tinh tu `<meta name="x-icue-property">` theo `HtmlParser.cpp`.
- Plugin `widgetbuilder.linkprovider`: **mock chi ghi nhan** `open(url)` (khong mo trinh duyet).

## Chua mo phong (khong phai bug)

- Plugin khac (Sensors, Media, StreamDeck...): duoc bao trong console, `window.plugins.<X>` la `undefined`.
- `device`, permissions, `pathToMacrosFormat`/`resourcePath`, timezone/locale cua `iCUE.*`,
  focus/touch interception, nghia that cua `tick`, settings editor.
- Gia tri mau (`color`) la gia thuyet (`#rrggbb`); key khong co default (vd `media-selector`) de `undefined` (chua xac minh).
- Chua co DevTools (hot reload, settings editor va device viewport: xem cac muc rieng ben duoi).
- Mock **khong tuong duong** plugin that. Trang render hoac console sach **khong chung minh** widget dung.
- `runJavaScript()` cua PySide6 6.9.3 tra ve chuoi rong cho array/object: test dung `JSON.stringify`.
## Live reload v0.3
- Full-page reload (not HMR) when any file under the open widget folder changes (HTML, CSS, JS, manifest, translations, assets). Ignored: .git, node_modules, editor swap/temp/backup files.
- Polling every 100 ms (stdlib; measured <= 0.09 ms per scan on the 31 WidgetsXE widgets), reload after the folder stayed unchanged for 250 ms, so one Save gives one reload.
- Same QWebEngineView/QWebEnginePage/QWebEngineProfile on every reload; a NEW RuntimeBridge per reload, so manifest/property edits are re-read. Values set via set_properties() are kept if the property still exists.
- ReloadAndBypassCache is used. localStorage survives reloads (measured). Since D7b each real instance has its own persistent profile (see the Multi-instance section), so it also survives a restart.
- A broken manifest/index keeps the previous page and shows the error; fixing it reloads. Auto Reload OFF keeps the baseline: edits made meanwhile reload once it is turned ON. Reload now works in both modes.
- Measure in a real window: .venv\Scripts\python.exe tools\measure_live_reload.py [widget_folder]
## Manifest modules and qrc:/fonts (v0.3.1)
- manifest.json "modules" are imported as ES modules before data-default/data-values are evaluated and exposed as a global named after the file (Timezones.mjs -> Timezones), with tr() available, like JavaScriptExecutor in iCUE. The <meta name="x-icue-module"> tag is not read by iCUE (no match in the source), so it is ignored here too. A module that is missing or fails to load is reported in the console, and a property whose default cannot be computed stays undefined (reported, not hidden).
- qrc: registered with the same scheme flags as iCUE (Secure | Local | LocalAccessAllowed, only for that scheme; CORS is not disabled). qrc:/fonts/<file> is served from widgetdevtool/fonts (Open Sans from the iCUE source tree) plus an optional folder in WIDGETDEVTOOL_FONTS_DIR (e.g. a licensed copy of Gotham). Other iCUE fonts are a 404 unless provided. Blocking of other qrc paths is not simulated.
- Offscreen runs (QT_QPA_PLATFORM=offscreen) keep document.hidden = true, so widgets that render from requestAnimationFrame stay on their placeholder; check such widgets in a real window.
## Settings parser and instance store (D1 + D2)
- `properties.parse_settings(html, translate, modules)` returns groups of `SettingParameter` (stored default, label, min/max/step, options, group, raw attributes). Defaults follow `toSetting()` of iCUE: every control has a stored default except `app-status` (display only). Nothing is clamped or corrected.
- `instance_store.WidgetInstance`: one UUID and one settings map per instance. `create` (defaults only for missing keys), `restore` (stored data as it is, nothing added), `merge` (iCUE `update()`: new defaults, old values win), `clone`. `InstanceStore` keeps many instances, also of the same type, and saves them as JSON (own format, not iCUE's).
- The runtime `uniqueId` is `{<instance uuid>}`; it is stable across live reloads. See AI_HANDOFF.md for the open points.
## Settings editor (D3)
- The right panel is generated from the widget's `x-icue-property` metadata (no widget-specific code). Supported controls: switch, slider, combobox, tab-buttons, slider-enum, textfield, color, time-format. Textfield commits on Enter/focus-out (Esc reverts); the others commit immediately. Nothing is clamped.
- Not supported (listed as `Unsupported: <reason>`, read-only): media-selector, colors, sensors-combobox, sensors-factory, search-combobox, accounts-list, app-status, stream-deck-virtual-device. Controls without a parameter (media-session, unknown data-type) are listed as "Not editable".
- An edit goes Settings -> WidgetInstance -> RuntimeBridge -> `window.<key>` + `onDataUpdated`, without reloading the page. Live reload keeps the instance and its edited values. Several instances of one widget can be created, saved/opened (restore or merge).
- Known limits: (D3 text, superseded by D7b: localStorage is now isolated per instance); the Qt controls are not the iCUE QML editors (only value semantics follow the source); personalization merge, device size and thumbnails are not done.
- Tests: 130 pass (`tests/test_settings_panel.py`: 35). Real-widget tests do not ignore console errors: errors at load are the classified baseline (network vs runtime), any new error after the edits fails; structured textfields (URLs, fonts, paths) are not mutated by the generic test and are covered by a valid-URL fixture test. CORS is never disabled.

## Device profiles and viewport (D5 + D6)
- Device profiles are generated from the real iCUE `.cuemsf` manifests into `widgetdevtool/profiles/device_profiles.json` (15 devices: XENEON EDGE, iCUE LINK GENERIC LCD, NAUTILUS II LCD, VANGUARD 96 keyboards, pump LCDs). Nothing is keyed by device name. Regenerate: `.venv\Scripts\python.exe -m widgetdevtool.device_profiles generate --icue D:\icue`. Devices without a size source in their manifest are skipped (listed in the file with the reason).
- Top bar: Device, Size (only the sizes the device's manifest has), Orientation (dashboards), Mode, Preview scale. The page gets the nominal widget size iCUE computes (`widgetSizePx`: relative size x mimicScreenSize; keyboard/pump: fixed size) and measures it as `window.innerWidth/innerHeight`. Mode "Physical" (relative size x physicalScreenSize) is EXPERIMENTAL. Free size keeps the old behaviour.
- (Since A2 the default is Native 1:1, see "Preview scaling (A2)".) A smaller preview uses the page zoom (EXPERIMENTAL); only zooms that keep the measurement exact are offered; `devicePixelRatio` is multiplied by the zoom. The label shows the measured page size and flags any mismatch.
- Changing device/size/orientation does not reload the page and does not change instance settings; live reload and the settings editor keep working.
- Unknown / not simulated: that the real host gives the web view exactly these pixels, the host's rounding, portrait zoom of the assignment view, auto-rotation, iDisplays. iCUE v5.55 source, release not compared.
- Settings and live reload (C1): one `WidgetInstance` owns the values; a live reload applies new source defaults to keys the user did not edit and keeps user-edited keys (the console lists both).
- Tests: 229 pass (`test_device_viewport.py`: 29, `test_personalization.py`: 32, `test_widget_library.py`: 27).

## Widget Personalization (D4)
- A widget that declares any of textColor, accentColor, accentColor2, backgroundColor, transparency, backgroundMedia gets a `Custom Style` switch (last settings group). The instance keeps its RAW settings; the page receives EFFECTIVE settings: while Custom Style is off, iCUE replaces those keys (only the ones the instance already has) from the active Device or Screen personalization (defaults: text `#ffffff`, accents `#f08e33`, background `#000000`, transparency 80; backgroundMedia gets `pathToAsset=""`). Custom Style on: the stored values pass through. Source: `WidgetBuilderPersonalizationSettings::settings()`, iCUE v5.55.
- While Custom Style is off the six editors are hidden (as in iCUE) and a note says what the widget receives when it differs from the stored value. The `Device / Screen Personalization` box edits the Device and Screen values and "Theme by screen"; values are kept per device profile and are not saved with instances.
- Switching Custom Style, editing personalization, or changing device updates the page through `onDataUpdated` without a reload; the instance is never rewritten (only the `widgetPersonalizationCustomStyleEnabled` switch and the `...Present` marker are stored in it).
- Not simulated / Unknown: the personalization background image and backgroundMedia selection (editor unsupported), how a QColor reaches the widget (we send `#rrggbb`), the transparency control range, whether iCUE re-sends an unchanged map, several screens per device. A passing test proves the rule is applied, not that a widget looks like in iCUE 5.

## Widget library and thumbnail inspector (D7a)
- `Widget library` (top bar) opens a dock: choose any folder; every subfolder with `manifest.json` + `index.html` is a widget (nothing hardcoded). The tree shows the widgets grouped like iCUE groups them for the selected device (Device combo), plus a `Not listed` section with the reason (invalid widget, device type mismatch, `sensor-screen` without the device's touch setup). `Open selected widget` loads it in the renderer and settings editor; settings of every widget survive switching (re-opening a widget shows its instance again).
- Image chain (iCUE v5.55 source): preview = `<meta name="x-icue-widget-preview">`, else the icon `<link rel="icon">`, else iCUE's default web-url.svg. `manifest.json` `preview_icon` is only validated (required string), it is NOT used for a thumbnail. No file existence check exists in iCUE: a missing file gives an empty image (the inspector marks it MISSING, no fallback). Group name = `x-icue-widget-group` (JS expression) else the widget name; a preview panel exists only for a group with more than one widget.
- Two static drawings of the QML rules: WidgetPreviewPanel row (128x56, PreserveAspectCrop on `layer1` #000000) and WidgetTypesPanel tile (16x16, PreserveAspectFit, ColorOverlay `layer8`/`layer1`: only the alpha survives). Palette and default icon are read from the iCUE source (`python -m widgetdevtool.icue_theme generate --icue D:\icue`). Panel background, fonts and SVG rasterisation are not iCUE's; a static image is not a live HTML preview.
- The Basketball Scores ticket is NOT reproduced or fixed by this: a black SVG would be invisible on the preview row, but Basketball Scores is alone in its group (no preview panel exists for it) and the type tile recolours the SVG. Which panel/condition shows the black thumbnails is still open.
## Multi-instance host (D7b)
- Add / Clone / Close / Remove buttons and an instance list: several widget instances run at once, each with its own settings, WebEngine profile, localStorage and console. Only the selected one is shown and edited; the others keep running (hidden).
- Storage: `<data dir>/storage/<uuid>_<md5(typeId)>` per instance (iCUE's `htmlWidgetStorageName` formula), persistent for real instances. Data dir: `%LOCALAPPDATA%\WidgetDevTool` (env `WIDGETDEVTOOL_DATA_DIR`). Never inside iCUE's folders. Close keeps the storage, Remove deletes it.
- `workspace.json` in the data dir restores the instances (same ids, settings, sources), the active one and the device/size when the tool restarts.
- A source edit reloads every instance of that widget once (one watcher per folder). Device, size and personalization are shared by all instances.
- Measured: two instances do not share localStorage; 250 tests pass. Not claimed: equivalence with iCUE 5 (see AI_HANDOFF.md, "D7b").
## Opening widgets and Reset Storage (A1)
- Open Folder, the Widget Library and `python -m widgetdevtool <folder>` reuse the instance of that folder (same id, settings and localStorage; a closed one is reopened). Only **Add** and **Clone** create new instances. With several instances of one folder, the most recently shown one opens; the list selects another. Older duplicates are kept.
- **Reset Storage** (asks first) deletes the localStorage/cache of the selected instance only and restarts it with the same id and settings. Tool storage only, never iCUE's.

## Preview scaling (A2)
- **Native 1:1** (default): the page is exactly the device viewport, page zoom 1, `devicePixelRatio` = the display's own (Windows scaling is not hard-coded); a viewport larger than the window scrolls. **Visual Fit / NN% zoom (EXPERIMENTAL)** uses the page zoom with an exact zoom only (the page still measures the exact viewport, e.g. XENEON XL portrait at 1/3), but it multiplies `devicePixelRatio` and changes sub-pixel layout - never debug sizes, DPR or responsive layout with it. The label next to the bar shows page size, DPR, display scaling and zoom, and flags any difference.
- At fractional Windows scaling (125/150 %) Qt rounding can add 1 px to the measured page; this was only simulated here (this PC is 100 %). No claim of pixel equivalence with iCUE 5.
- 323 tests pass (also on the real `windows` Qt platform).
## Window size (layout fix)
- The main window keeps its size when you change Device / Size / Orientation / preview scale: only the viewport inside the preview frame changes (a larger viewport scrolls). Long texts are clipped (tooltip = full text), the side column scrolls, the Widget Library makes room once when opened. Closing the tool hides the window.
- Cause of the earlier growth: labels/combos set the window's minimum width through the layout (see AI_HANDOFF.md, "Window growth bug").
## Performance diagnostics (P1)
- `PERFORMANCE_DIAGNOSTICS.md` + `tools/perf_probe.py` (Windows; watchdog, memory guard, timeouts): CPU/RAM of python and the QtWebEngine children for 1/3/10 instances, idle reload/event counts and Add/Close/Remove trends. Result: no loop, duplicate watcher or orphan process found; one renderer (~65-90 MB) per instance, all released after Remove; tiny unexplained growth (+0.33 MB private per Add/Remove cycle) is not concluded as a leak.
## Import from iCUE (E2, read-only)
- **Import from iCUE...** reads an iCUE `dashlcd\storage` file (choose it yourself; it is only read, never written or locked), lists its HTML widgets, shows a masked preview (URLs, paths and token-like text appear only as lengths) and creates a NEW instance (own id and storage) with the stored settings after you confirm. The widget source is found in the Widget Library by `typeId`; no match is reported, never guessed. Existing instances are never overwritten.
- Values are kept as stored (a null stays null, an undecodable value is left out and listed, missing properties get no default). iCUE's browser storage, cookies and account data are not read. The stored size becomes the instance's own size. Optional, off by default: copy the stored device/screen personalization.
- The importer reports differences (library vs iCUE widget version, older/newer storage format, missing/undeclared properties); it does not apply iCUE's migrations. Details and limits: AI_HANDOFF.md, "E2".
## Per-instance size (E3)
- Each instance has its own Size and Orientation (saved with it, restored after restart, copied by Clone/Add, taken from iCUE on import). The Device and Mode are shared. The Size/Orientation boxes always show and change the SELECTED instance; other instances keep their size and are not reloaded.
- If the device does not offer an instance's stored size, its default size is shown with a NOTE in the device info; the stored size is kept and comes back on a device that has it. Only one instance is visible at a time.

## Build Package: iCUE 5 / iCUE Apps (U2, U2.2)
- Top bar: **Build Package** (Ctrl+Shift+B) opens a menu: **Build for iCUE 5** or **Build for iCUE Apps**. The dialog shows the target (switchable), widget name, version, source folder, output folder (Browse), package file name and, for iCUE Apps, the CLI and `min_app_version`.
- Every build works on a temporary copy (`%TEMP%\wdt_stage_*`, always deleted). The widget source (manifest.json, HTML/CSS/JS) is never written. Output file names differ per target: `<id>-<version>-icue5.icuewidget` / `<id>-<version>-icueapps.icuewidget` (e.g. `sensorbar-1.0.6-icue5.icuewidget`). An existing file is replaced only after confirmation; a failed or cancelled build never damages it (hidden `.partial` file + atomic replace).
- **iCUE 5**: the format the installed iCUE 5 accepts, as WidgetsXE builds it (`tools/build-widgets.ps1`): ZIP Deflate, the widget's files at the archive root, no directory entries. The tool zips itself; `icuewidget validate` is NOT run (CLI 0.5.1 rejects every legacy manifest for the missing `min_app_version`) and `min_app_version` is NOT added: the manifest is packaged byte-identical. Checked: manifest.json + index.html present, manifest JSON with id/name/semver version, preview icon, modules, translation.json, every local `href`/`src` of index.html, archive layout. Verified: the Sensor Bar 1.0.6 build has the same entries, compression and layout as the working `sensorbar-1.0.6.icuewidget`.
- **iCUE Apps**: official CLI on the temporary copy: `icuewidget validate <copy>`, and only if it passes `icuewidget package <copy> --output <out>/.<name>.partial.icuewidget`. An existing valid `min_app_version` is kept; a missing/empty one is added to the copy only. Default **5.47** = documented minimum (iCUE widget docs, Getting Started > Prerequisites, docs.elgato.com/icue/widgets), editable in the dialog. An invalid existing value is not changed (the validator reports it). Remaining validation errors are shown, never fixed. The archive is then checked for the iCUE Apps manifest requirements (required strings, formats, windows platform, device types, preview icon in the archive, nothing but `min_app_version` changed); CLI warnings are listed.
- Install the CLI yourself (iCUE Apps only): `npm install -g icuewidget-cli` (Node.js 18+); the tool never installs it.
- Output: `Documents\WidgetDevTool\Packages` by default (`QStandardPaths`), Browse, remembered (with the last target). Never inside the widget, its repository (e.g. WidgetsXE) or the tool folder. **Open Output Folder** after success. Nothing is imported into iCUE.
- Current state (icuewidget-cli 0.5.1): Sensor Bar 1.0.6 builds for iCUE 5; for iCUE Apps it fails validation in its own source (non-self-closed `<link>` in `<head>`; with Legacy Compatibility the next error is a bare `&` in an inline `<head>` script), so no iCUE Apps package is produced until the widget source is fixed.

## Legacy Compatibility (U2.1, iCUE Apps only)
- Under **Advanced options**, **OFF by default**. When ON, the void elements in `<head>` of the temporary copy are also self-closed (`<link ...>` -> `<link ... />`). "Preview Changes (Diff)" shows the analysis and the diff without building. Anything else the CLI rejects is reported as NOT FIXED (e.g. a `<header>` element in the body).
## UI/UX (U1)
- Dark theme (`theme.py`, Fusion + one style sheet, semantic properties `variant` / `role` / `tone`), line icons (`icons.py`, inline SVG), small widgets (`ui_kit.py`: SegmentedControl, FlowLayout, ClippedLabel).
- Layout: top bar (Open Widget Ctrl+O, Widget Library Ctrl+L, Import from iCUE Ctrl+I, panel toggles, primary **Build Package** menu Ctrl+Shift+B) | Instances panel | Preview (title, Auto Reload toggle, Reload F5, Device / Orientation / Widget size / Preview scale, page size + Pixels mode) over a collapsible Console | Inspector tabs (Settings, Personalization) | status bar (state, watcher, "Simulated runtime" notes).
- **Widget size** (S/M/L/XL, stored per instance) and **Preview scale** (how the viewport is drawn; never changes the page) are separate controls; sizes a device does not offer are shown disabled with the reason.
- Package dialog: target selector; for iCUE Apps the min_app_version card and Legacy Compatibility under **Advanced options** (OFF by default); status shown as a coloured banner, log below.
- Window growth rule: changing Device / Size / Orientation / Mode / Preview scale or switching instances never changes the window (size, position), the sidebars, top bar, console or preview container; only the HTML view inside the preview changes (scroll in Native, shrink in Visual Fit). Nothing is locked: resize the window, drag the splitters, toggle panels. A narrow window shows the secondary top-bar actions as icons.
- Screenshots + layout audit at 100/125/150 %: `validation/u1/`.
## Sensors provider MOCK (Sensor Bar debugging)
- `widgetbuilder.sensorsdataprovider:Sensors:1.0` is now mocked (`sensors_mock.py`): `window.plugins.Sensorsdataprovider` with the Sensors.qml protocol (requestId + `asyncResponse`, string values, `sensorValueChanged` / `sensorUnitsChanged` / `sensorDataChanged`), and `plugins.Sensorsdataprovider.getDefaultSensorIdBlock()` while the settings are parsed (fixes `ReferenceError: plugins is not defined` for `data-default`).
- **Mock sensors** (default data source; 8 invented sensors: temperatures, loads, fan, pump, one disconnected, one "unavailable" -1/-1). Set values in the **Sensors** inspector tab; every open instance is notified. Real sensors: see Live Sensors below.
- `sensors-factory` editor: Add Sensor (data-default id + `#f08e33`, as iCUE), pick a mock sensor, colour, Remove. A new instance has no sensor (iCUE stores the empty list too), so the widget first shows its own "Select sensors" state.
- Not equivalent to iCUE: no real hardware, no update rate, reply timing / unknown-id answer / default-sensor choice are WidgetDevTool's. Observations: `validation/sensorbar/`.

## Live Sensors (Olympus Hub)
- **Sensors** tab > **Data source**: `Mock sensors (invented)` (default) or `Live sensors (Olympus Hub)`. The choice applies to every open instance at once, without a reload; Live Reload, Settings and Multi-instance are unchanged. The status line always says which source is active.
- Live reads this PC's sensors from the Olympus Hub (`olympus_hub.py`): WebSocket bus, `cue.Bus.authenticate`, Device Manager devices with `cue.device_type == "Sensor"`, `cue.property_changed` notifications (a notification without a value is re-read with `get_property`). Requires iCUE Apps (Olympus) running.
- Port: empty = detected from the processes the Hub started (`--hub-port=<port>` on their command line), never assumed; or enter host / port and press Connect. The Hub's port is detected again after a Hub restart.
- Connection states: disconnected / detecting / connecting / discovering / connected / error (with the reason, retried with backoff up to 30 s). While not connected every widget answer is empty (as iCUE while its sensor service is unavailable) and the pages are told once, so Sensor Bar shows its dash instead of stale values. Device connect / disconnect and a Device Manager restart are followed. Disconnect / closing the tool unsubscribes and closes the socket.
- What the widget receives follows what the Olympus Widget Builder plugin (Renderer 0.48.2) was **observed** to send on the test PC (probe widget in `iCUE Widget Builder Renderer.exe`, all 212 sensors identical: ids, names, device names, types, kinds, units, default sensor): ids without braces, type / kind in kebab case, units derived from the type (the Hub's `cue.sensor_unit` is not used: `Available Capacity` is GB on the Hub, `%` from the plugin; clock speeds / bandwidth have no units), temperatures in the system measurement (degrees F with converted values on an en-US PC; `Units` combo to force C / F), other values = the raw Hub string. Types never observed (e.g. efficiency) get no units and are listed in the tab.
- Only the sensors a page asked about are forwarded to it, value changes coalesced every 100 ms (the Hub sends about 60 per second for 212 sensors). Standalone protocol check: `tools/hub_sensors_poc.py --port <hub port>`.
- Run with live sensors: start **iCUE Apps** (Olympus; `iCUE Apps.exe`, `iCUE Device Manager.exe` running), start the tool, open a sensor widget (e.g. Sensor Bar), **Sensors** tab > Data source > `Live sensors (Olympus Hub)`, leave the port empty. The status line must read `Connected to 127.0.0.1:<port> (...): <n> sensors`; then pick sensors in the widget's Settings (Add Sensor / sensor combo list the live sensors). Back to `Mock sensors` closes the connection.
- Troubleshooting: `Olympus Hub is not running: no process was started with --hub-port` = iCUE Apps is not running (iCUE 5 alone has no Hub). `Hub not reachable at ...` / `No answer from the Hub ...` = wrong host / port or the Hub is starting: it retries by itself. `Device Manager is not available on the Hub yet` = the Hub runs without its Device Manager: it retries every 3 s. To find the port by hand: `Get-CimInstance Win32_Process | ? CommandLine -match '--hub-port' | select Name, CommandLine`. The connection log (last events) is shown under the sensor table.
- Tests: `tests/test_live_sensors.py` uses a fake local Hub (no iCUE needed). `test_settings_panel.SensorBarMockTests.test_show_bar_and_row_background_switches` observes the Sensor Bar source and expects the XNCW-1 fix (WidgetsXE commit 4809221: no leader line with Show Bar and Show Row Background off); it fails on a WidgetsXE checkout without that commit.
## U1 review fixes (sidebars, toolbar, instance actions)
- Right sidebar (Settings / Personalization / Sensors): vertical scrolling only; nothing is wider than the panel (long labels wrap, combo boxes elide with the full text as tooltip, tab buttons wrap to the next row, time-format options stacked, colour swatches fixed width). Resizable with the splitter, minimum 280 px, default 330 px. 12-16 px between a group heading (e.g. "Device / Screen Personalization") and its first control.
- Device toolbar: the info icon is removed (the viewport notes are in the status bar's "Simulated runtime"); Device / Orientation / Widget size / Preview scale share one height and are top-aligned (they wrap into rows in a narrow preview).
- Instances panel: Add in the header; for the selected instance four distinct icon buttons with tooltips - Clone, Reset Storage, Close (power icon: keeps instance + storage), Remove (red trash: deletes storage). Less frequent actions in the "More" menu:
  | New name | Old name | What it does (handler unchanged) |
  |---|---|---|
  | Reopen as Stored | Restore | `open_instance(id, "restore")`: reopens with exactly the stored settings, same id/storage; a running page is reloaded; the stored instance is not changed |
  | Reopen with New Defaults | Merge | `open_instance(id, "merge")`: changes the stored instance like iCUE update() (new defaults added, undeclared keys dropped, stored values win), then reopens |
  | Save Instance File... | Save... | `choose_save_instances`: writes ALL instances to a JSON file; workspace and pages unchanged |
  | Open Instance File... | Load... | `choose_open_instances`: replaces the instance list with a JSON file; nothing is opened or reloaded |