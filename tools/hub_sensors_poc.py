r"""Phase 1 POC (standalone, NOT used by the app): read REAL hardware sensors from the Olympus Hub bus.

Protocol, as found in the Olympus sources:
- transport: WebSocket ws://<host>:<port>/ with JSON text frames (ui-npm-registry ipc-client message-bus-client-impl.ts)
- request {"id","target","method","params"}; reply {"id","result"} or {"id","error":{"code","message"}};
  notification {"signal","source","params"} (ipc-client-if protocol.ts)
- cue.Bus.authenticate {name, description} on target "cue/Bus", then cue.Bus.subscribe {source, signal} (ipc-client index.tsx)
- sensors are Device Manager devices with cue.device_type == "Sensor" (widgets SensorsCollection.cpp); target
  "devmgr/DeviceManager" (DeviceCollection.cpp); cue.devmgr.Device.get_property -> {"data": {"value": ...}};
  value updates arrive as notification_received(id="cue.property_changed", data={property_id[, value]}) (Devices.cidl)

Usage:
  .venv\Scripts\python.exe tools\hub_sensors_poc.py --port <hub port> [--host 127.0.0.1] [--duration 30]
Stop with Ctrl+C or --duration. Exit code 0 only if authenticated, sensors were found and real value changes arrived.
"""
from __future__ import annotations

import argparse
import itertools
import json
import signal
import sys
import time
from collections import Counter

from PySide6.QtCore import QCoreApplication, QTimer, QUrl
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

BUS = "cue/Bus"
DEVMGR = "devmgr/DeviceManager"
DEVICE_NOTIFICATION = "cue.devmgr.Device.notification_received"
PROPERTY_CHANGED = "cue.property_changed"
SENSOR_VALUE = "cue.sensor_value"
SENSOR_PROPS = {"name": "cue.device_name", "type": "cue.sensor_type", "kind": "cue.sensor_kind", "unit": "cue.sensor_unit",
                "value": SENSOR_VALUE}


def log(tag: str, message: str) -> None:
    now = time.time()
    print(f"{time.strftime('%H:%M:%S', time.localtime(now))}.{int(now * 1000) % 1000:03d} [{tag:<9}] {message}", flush=True)


def property_value(result):
    """cue.devmgr.Device.get_property returns Result {data: {value: ...}}."""
    if isinstance(result, dict):
        data = result.get("data") or {}
        return data.get("value") if isinstance(data, dict) else None
    return result


def fmt_error(error) -> str:
    if isinstance(error, dict):
        return f"{error.get('code')}: {error.get('message') or ''}".strip()
    return str(error)


class HubClient:
    """Minimal async bus client: request/reply with timeout, notifications. Runs on the Qt event loop (never blocks)."""

    def __init__(self, timeout_ms: int) -> None:
        self.ws = QWebSocket()
        self.timeout_ms = timeout_ms
        self._ids = itertools.count(1)
        self._pending: dict[str, tuple] = {}
        self.on_connected = lambda: None
        self.on_closed = lambda: None
        self.on_notification = lambda msg: None
        self.sent = 0
        self.ws.connected.connect(lambda: self.on_connected())
        self.ws.disconnected.connect(self._on_disconnected)
        self.ws.textMessageReceived.connect(self._on_text)
        self.ws.errorOccurred.connect(lambda _e: log("error", f"socket error: {self.ws.errorString()}"))

    def open(self, host: str, port: int) -> None:
        self.ws.open(QUrl(f"ws://{host}:{port}/"))

    def is_connected(self) -> bool:
        return self.ws.state() == QAbstractSocket.SocketState.ConnectedState

    def request(self, target: str, method: str, params: dict, callback, timeout_ms: int | None = None) -> None:
        if not self.is_connected():
            callback(None, {"code": "no-channel", "message": "socket is not connected"})
            return
        rid = next(self._ids)
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._on_timeout(str(rid)))
        timer.start(timeout_ms or self.timeout_ms)
        self._pending[str(rid)] = (callback, timer, method, target)
        self.sent += 1
        self.ws.sendTextMessage(json.dumps({"id": rid, "target": target, "method": method, "params": params}))

    def close(self) -> None:
        self.ws.close()

    def _on_timeout(self, rid: str) -> None:
        entry = self._pending.pop(rid, None)
        if entry:
            callback, _timer, method, target = entry
            callback(None, {"code": "timeout", "message": f"{method} on {target}: no reply within the timeout"})

    def _on_text(self, text: str) -> None:
        try:
            msg = json.loads(text)
        except ValueError as exc:
            log("protocol", f"unparsable frame ({exc}): {text[:200]!r}")
            return
        if not isinstance(msg, dict):
            log("protocol", f"unexpected frame: {text[:200]!r}")
        elif "signal" in msg and "source" in msg:
            self.on_notification(msg)
        elif "id" in msg and ("result" in msg or "error" in msg) and "method" not in msg:
            entry = self._pending.pop(str(msg["id"]), None)
            if entry is None:
                log("protocol", f"reply for an unknown/expired request id {msg['id']!r}")
                return
            callback, timer, _method, _target = entry
            timer.stop()
            callback(msg.get("result"), msg.get("error"))
        elif "method" in msg and "id" in msg:
            log("protocol", f"incoming request {msg.get('method')!r} on {msg.get('target')!r}: answering no-such-object")
            self.ws.sendTextMessage(json.dumps({"id": msg["id"], "error": {"code": "no-such-object", "message": "POC client"}}))
        else:
            log("protocol", f"unrecognised message: {text[:200]!r}")

    def _on_disconnected(self) -> None:
        pending, self._pending = self._pending, {}
        for callback, timer, method, target in pending.values():
            timer.stop()
            callback(None, {"code": "disconnected", "message": f"{method} on {target}: connection closed"})
        self.on_closed()

class SensorsPoc:
    def __init__(self, args, app: QCoreApplication) -> None:
        self.args, self.app = args, app
        self.client = HubClient(args.timeout)
        self.client.on_connected = self.on_connected
        self.client.on_closed = self.on_closed
        self.client.on_notification = self.on_notification
        self.authenticated = False
        self.discovery_done = False
        self.stopping = False
        self.device_types: Counter = Counter()
        self.errors: Counter = Counter()
        self.sensors: dict[str, dict] = {}  # device objid -> sensor record
        self.watch: set[str] = set()
        self.notifications = 0
        self.raw_logged = 0
        self.value_events = Counter()  # "inline" / "refetched" / "refetch-error" / "unchanged"
        self.started = time.monotonic()
        self._queue: list = []
        self._inflight = 0
        self._timers: list[QTimer] = []

    # ---- lifecycle -------------------------------------------------------------------------------------------------
    def start(self) -> None:
        log("connect", f"opening ws://{self.args.host}:{self.args.port}/ (request timeout {self.args.timeout} ms)")
        self._single_shot(self.args.connect_timeout, self._check_connected)
        self.client.open(self.args.host, self.args.port)

    def _single_shot(self, ms: int, fn) -> QTimer:
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.start(ms)
        self._timers.append(timer)
        return timer

    def _check_connected(self) -> None:
        if not self.client.is_connected() and not self.stopping:
            log("error", f"could not connect within {self.args.connect_timeout} ms: {self.client.ws.errorString()}")
            self.stop()

    def on_connected(self) -> None:
        log("connect", "WebSocket connected")
        log("auth", f"cue.Bus.authenticate name={self.args.client_name!r}")
        self.client.request(BUS, "cue.Bus.authenticate",
                            {"name": self.args.client_name, "description": "WidgetDevTool Phase 1 Live Sensors POC"},
                            self.on_authenticated)

    def on_authenticated(self, result, error) -> None:
        if error:
            log("error", f"authentication failed: {fmt_error(error)}")
            self.stop()
            return
        self.authenticated = True
        prefix = (result or {}).get("objid_prefix", (result or {}).get("objid-prefix")) if isinstance(result, dict) else None
        log("auth", f"authenticated, raw result={json.dumps(result)} objid_prefix={prefix!r}")
        for sig in ("device_connected", "device_disconnected"):
            self.subscribe(DEVMGR, f"cue.devmgr.DeviceManager.{sig}")
        for sig in ("known_object_registered", "known_object_unregistered"):
            self.subscribe(BUS, f"cue.Bus.{sig}")
        log("discovery", f"{DEVMGR} cue.devmgr.DeviceManager.get_devices")
        self.client.request(DEVMGR, "cue.devmgr.DeviceManager.get_devices", {}, self.on_devices)

    def subscribe(self, source: str, sig: str, done=None) -> None:
        def on_reply(_result, error):
            if error:
                self.errors[f"subscribe {sig}: {error.get('code') if isinstance(error, dict) else error}"] += 1
                log("error", f"subscribe {sig} on {source} failed: {fmt_error(error)}")
            if done:
                done()
        self.client.request(BUS, "cue.Bus.subscribe", {"source": source, "signal": sig}, on_reply)

    def on_closed(self) -> None:
        if self.stopping:
            self.app.exit(self.exit_code())
            return
        log("error", "connection closed by the Hub")
        self.stop()

    # ---- discovery --------------------------------------------------------------------------------------------------
    def on_devices(self, result, error) -> None:
        if error:
            log("error", f"get_devices failed: {fmt_error(error)} (is the Device Manager registered on this Hub?)")
            self.stop()
            return
        objids = list(result or [])
        log("discovery", f"get_devices returned {len(objids)} device object(s); reading cue.device_type "
                         f"(max {self.args.concurrency} requests in flight)")
        if not objids:
            self.on_discovery_finished()
        for objid in objids:
            self._enqueue(lambda done, o=objid: self._check_device(o, done))

    def _enqueue(self, job) -> None:
        self._queue.append(job)
        self._pump()

    def _pump(self) -> None:
        while self._inflight < self.args.concurrency and self._queue and not self.stopping:
            job = self._queue.pop(0)
            self._inflight += 1
            job(self._job_done)

    def _job_done(self) -> None:
        self._inflight -= 1
        self._pump()
        if self._inflight == 0 and not self._queue and not self.discovery_done and not self.stopping:
            self.on_discovery_finished()

    def get_property(self, objid: str, prop: str, callback) -> None:
        self.client.request(objid, "cue.devmgr.Device.get_property", {"id": prop}, callback)

    def _check_device(self, objid: str, done) -> None:
        def on_type(result, error):
            if error:
                self.errors[f"get_property cue.device_type: {error.get('code') if isinstance(error, dict) else error}"] += 1
                done()
                return
            dtype = property_value(result)
            self.device_types[str(dtype)] += 1
            if dtype == "Sensor":
                self._load_sensor(objid, done)
            else:
                done()
        self.get_property(objid, "cue.device_type", on_type)

    def _load_sensor(self, objid: str, done) -> None:
        record = {"objid": objid, "id": None, "missing": [], "updates": 0, "changes": 0, "last_update": None}
        remaining = {"n": len(SENSOR_PROPS) + 1}

        def finished():
            remaining["n"] -= 1
            if remaining["n"] == 0:
                self.sensors[objid] = record
                self.subscribe(objid, DEVICE_NOTIFICATION, done)

        def on_id(result, error):
            record["id"] = None if error else result
            if error:
                record["missing"].append(f"get_id ({fmt_error(error)})")
            finished()
        self.client.request(objid, "cue.devmgr.Device.get_id", {}, on_id)

        for key, prop in SENSOR_PROPS.items():
            def on_prop(result, error, key=key, prop=prop):
                record[key] = None if error else property_value(result)
                if error:
                    record["missing"].append(f"{prop} ({error.get('code') if isinstance(error, dict) else error})")
                finished()
            self.get_property(objid, prop, on_prop)
    def on_discovery_finished(self) -> None:
        self.discovery_done = True
        elapsed = time.monotonic() - self.started
        types = ", ".join(f"{t}={n}" for t, n in self.device_types.most_common())
        log("discovery", f"done in {elapsed:.1f}s: device types: {types or '-'}")
        log("discovery", f"{len(self.sensors)} sensor(s) found:")
        ordered = sorted(self.sensors.values(), key=lambda s: (str(s.get("type")), str(s.get("name"))))
        for s in ordered:
            missing = f"  missing: {', '.join(s['missing'])}" if s["missing"] else ""
            log("sensor", f"id={s['id']} obj={s['objid']} name={s.get('name')!r} type={s.get('type')!r} "
                          f"kind={s.get('kind')!r} unit={s.get('unit')!r} value={s.get('value')!r}{missing}")
        if self.errors:
            for err, n in self.errors.most_common():
                log("error", f"{n} x {err}")
        if not self.sensors:
            log("error", "no device with cue.device_type == 'Sensor' was found")
            self.stop()
            return
        limit = self.args.log_updates
        self.watch = {s["objid"] for s in (ordered if limit < 0 else ordered[:limit])}
        log("updates", f"listening for {DEVICE_NOTIFICATION}; printing every change for "
                       f"{'all' if limit < 0 else len(self.watch)} sensor(s), summary every {self.args.summary_interval}s")
        summary = QTimer()
        summary.timeout.connect(self.print_summary)
        summary.start(self.args.summary_interval * 1000)
        self._timers.append(summary)
        if self.args.duration > 0:
            self._single_shot(self.args.duration * 1000, self.stop)

    # ---- notifications ---------------------------------------------------------------------------------------------
    def on_notification(self, msg: dict) -> None:
        self.notifications += 1
        sig, source, params = msg.get("signal"), msg.get("source"), msg.get("params") or {}
        if self.raw_logged < self.args.raw:
            self.raw_logged += 1
            log("raw", json.dumps(msg)[:400])
        if sig == DEVICE_NOTIFICATION:
            data = params.get("data") or {}
            if params.get("id") == PROPERTY_CHANGED and data.get("property_id") == SENSOR_VALUE and source in self.sensors:
                if "value" in data:
                    self.apply_value(source, data["value"], "inline")
                else:
                    self.get_property(source, SENSOR_VALUE, lambda r, e, o=source: self._on_refetch(o, r, e))
        elif sig and sig.startswith(("cue.devmgr.DeviceManager.", "cue.Bus.known_object")):
            log("event", f"{sig} {json.dumps(params)}")

    def _on_refetch(self, objid: str, result, error) -> None:
        if error:
            self.value_events["refetch-error"] += 1
            log("error", f"get_property {SENSOR_VALUE} after notification failed for {objid}: {fmt_error(error)}")
            return
        self.apply_value(objid, property_value(result), "refetched")

    def apply_value(self, objid: str, value, mode: str) -> None:
        s = self.sensors[objid]
        old = s.get("value")
        s["updates"] += 1
        s["last_update"] = time.monotonic()
        self.value_events[mode] += 1
        if value == old:
            self.value_events["unchanged"] += 1
            return
        s["value"] = value
        s["changes"] += 1
        if objid in self.watch:
            log("update", f"{s.get('name')!r} [{s.get('type')}]: {old} -> {value} {s.get('unit') or ''} ({mode})")

    def print_summary(self) -> None:
        updated = sum(1 for s in self.sensors.values() if s["changes"])
        log("summary", f"t={time.monotonic() - self.started:.0f}s notifications={self.notifications} "
                       f"value events={dict(self.value_events)} sensors changed={updated}/{len(self.sensors)} "
                       f"requests sent={self.client.sent}")

    # ---- shutdown ---------------------------------------------------------------------------------------------------
    def exit_code(self) -> int:
        changed = any(s["changes"] for s in self.sensors.values())
        return 0 if self.authenticated and self.sensors and changed else 1

    def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        for timer in self._timers:
            timer.stop()
        self._queue.clear()
        self.final_report()
        self._single_shot(3000, lambda: self.app.exit(self.exit_code()))  # failsafe
        if self.client.is_connected():
            log("shutdown", "cue.Bus.unsubscribe (all), then closing the socket")
            self.client.request(BUS, "cue.Bus.unsubscribe", {}, lambda _r, _e: self.client.close(), timeout_ms=1500)
        else:
            self.app.exit(self.exit_code())

    def final_report(self) -> None:
        sensors = list(self.sensors.values())
        changed = [s for s in sensors if s["changes"]]
        log("report", f"authenticated={self.authenticated} sensors={len(sensors)} changed={len(changed)} "
                      f"notifications={self.notifications} value events={dict(self.value_events)} "
                      f"runtime={time.monotonic() - self.started:.1f}s")
        for s in sorted(changed, key=lambda x: -x["changes"])[:10]:
            log("report", f"{s.get('name')!r} [{s.get('type')}] changes={s['changes']} updates={s['updates']} "
                          f"last={s.get('value')} {s.get('unit') or ''}")
        log("report", f"result: {'PASS' if self.exit_code() == 0 else 'FAIL'}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Olympus Hub live sensors POC (Phase 1, standalone).")
    parser.add_argument("--host", default="127.0.0.1", help="Hub bus host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, required=True, help="Hub bus port (the --hub-port the Hub gives its child processes)")
    parser.add_argument("--duration", type=int, default=30, help="seconds to listen after discovery; 0 = until Ctrl+C")
    parser.add_argument("--timeout", type=int, default=5000, help="per-request timeout in ms")
    parser.add_argument("--connect-timeout", type=int, default=5000, help="WebSocket connect timeout in ms")
    parser.add_argument("--concurrency", type=int, default=16, help="max discovery requests in flight")
    parser.add_argument("--log-updates", type=int, default=5, help="print every change for N sensors (-1 = all)")
    parser.add_argument("--summary-interval", type=int, default=5, help="seconds between summaries")
    parser.add_argument("--raw", type=int, default=3, help="print the first N raw notifications")
    parser.add_argument("--client-name", default="WidgetDevToolPOC", help="name passed to cue.Bus.authenticate")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    app = QCoreApplication(sys.argv[:1])
    poc = SensorsPoc(args, app)
    signal.signal(signal.SIGINT, lambda *_: QTimer.singleShot(0, poc.stop))
    heartbeat = QTimer()  # lets the Python interpreter run the SIGINT handler while Qt's loop is idle
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(200)
    QTimer.singleShot(0, poc.start)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))