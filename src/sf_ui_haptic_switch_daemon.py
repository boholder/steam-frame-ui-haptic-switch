#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sf-ui-haptic-switch daemon.

A small resident process that keeps the "Controller UI Haptic" switch alive
across two Chromium Embedded Framework (CEF) DevTools endpoints:

- ``:8080`` — the Steam client UI (``steamwebhelper``)
- ``:8087`` — the SteamVR Dashboard (``vrwebhelper``)

The switch itself lives in the Quick Access Menu on ``:8080``. Its value lives
in ``localStorage['sf_ui_haptic_switch']`` and is the single source of truth.
When the user flips it, the UI writes that key and calls the
``__sfUiHapticSwitchSend`` CDP binding. The daemon reads the key back, persists
it to ``~/.local/state/sf-ui-haptic-switch/state.json`` and pushes it into every
``:8087`` page. The daemon never writes back to ``:8080``.

Target discovery is event-driven: one browser-level WebSocket per endpoint,
``Target.setDiscoverTargets``, then ``Target.attachToTarget {flatten:true}`` for
every page. No polling.

Python 3, standard library only (the device has no websocket package).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import re
import signal
import socket
import struct
import sys
import threading
import time
import urllib.request

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

DEFAULT_STEAM_PORT = 8080
DEFAULT_VR_PORT = 8087

GLOBAL = "__SF_UI_HAPTIC_SWITCH__"
BINDING = "__sfUiHapticSwitchSend"
KEY = "sf_ui_haptic_switch"
UI_MATCH = "valve.steam.gamepadui.barpopup"

GATE_JS = "sf_ui_haptic_switch_gate.js"
UI_JS = "sf_ui_haptic_switch_ui.js"

SLOT_GATE = "gate"
SLOT_UI = "ui"

RETRY_SECONDS = 1.0
HANDSHAKE_TIMEOUT = 1.0
HANDSHAKE_ATTEMPTS = 3
LOOP_SECONDS = 0.05

VERBOSE = False


def log(*a):
    print("[sf-ui-haptic-switch]", *a, file=sys.stderr, flush=True)


def _debug(*a):
    if VERBOSE:
        log(*a)


# --------------------------------------------------------------------------
# pure decision logic (the test seam)
# --------------------------------------------------------------------------

def normalize_enabled(value):
    """Off only for explicit "0"/"false"; missing or junk means on."""
    return not (value == "0" or value == "false")


def storage_value(enabled):
    return "1" if enabled else "0"


def parse_binding_payload(payload):
    """Parse a binding payload.

    Returns ``{"kind": "hello"}``, ``{"kind": "haptic", "enabled": bool|None}``
    or ``None`` for malformed / unknown payloads.
    """
    try:
        msg = json.loads(payload)
    except Exception:
        return None
    if not isinstance(msg, dict):
        return None

    kind = msg.get("c")
    if kind == "hello":
        return {"kind": "hello"}
    if kind != "haptic":
        return None

    value = msg.get("v")
    if isinstance(value, bool):
        return {"kind": "haptic", "enabled": value}
    return {"kind": "haptic", "enabled": None}


def is_page_target(info):
    return isinstance(info, dict) and info.get("type") == "page"


def is_ui_target(title, url):
    return UI_MATCH in ((title or "") + (url or ""))


def scripts_for(title, url, kind="steam"):
    """Which scripts a page needs.

    The gate goes everywhere. The QAM UI is a Steam-client concept: it must
    never be installed into a SteamVR Dashboard page, even if a Dashboard URL
    happens to contain the barpopup key.
    """
    if kind == "steam" and is_ui_target(title, url):
        return [SLOT_GATE, SLOT_UI]
    return [SLOT_GATE]


def build_marker(source):
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def probe_expr(slot):
    return (
        "((window.%s && window.%s.build && window.%s.build[%s]) || '')"
        % (GLOBAL, GLOBAL, GLOBAL, json.dumps(slot))
    )


def inject_expr(source, slot, marker):
    epilogue = (
        ";try{window.%s=window.%s||{};window.%s.build=window.%s.build||{};"
        "window.%s.build[%s]=%s;}catch(e){}"
        % (GLOBAL, GLOBAL, GLOBAL, GLOBAL, GLOBAL, json.dumps(slot), json.dumps(marker))
    )
    return source + epilogue


def page_script(sources, slots):
    """Script registered with Page.addScriptToEvaluateOnNewDocument."""
    return "\n;\n".join(
        inject_expr(sources[slot], slot, build_marker(sources[slot]))
        for slot in slots
    )


def teardown_expr():
    return (
        "(function(){try{var n=window.%s;if(n){"
        "if(n.ui&&n.ui.teardown){n.ui.teardown();}"
        "if(n.gate&&n.gate.teardown){n.gate.teardown();}}"
        "delete window.%s;}catch(e){}})()" % (GLOBAL, GLOBAL)
    )


def default_state_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "sf-ui-haptic-switch", "state.json")


def load_state(path):
    """Read the persisted switch value; missing/corrupt means on."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("enabled"), bool):
            return data["enabled"]
    except FileNotFoundError:
        pass
    except Exception as e:
        _debug("state read failed: %s" % e)
    return True


def save_state(path, enabled):
    """Atomic write, 0600, creating the state directory if needed."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"enabled": bool(enabled)}, f)
        f.write("\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def load_sources(script_dir):
    sources = {}
    for slot, name in ((SLOT_GATE, GATE_JS), (SLOT_UI, UI_JS)):
        with open(os.path.join(script_dir, name), encoding="utf-8") as f:
            sources[slot] = f.read()
    return sources


# --------------------------------------------------------------------------
# minimal websocket client (stdlib only)
# --------------------------------------------------------------------------

class WSError(Exception):
    pass


class WebSocket:
    def __init__(self, url, timeout=10.0):
        m = re.match(r"ws://([^/:]+)(?::(\d+))?(/.*)?$", url)
        if not m:
            raise WSError("bad ws url: %r" % url)
        host = m.group(1)
        port = int(m.group(2) or 80)
        path = m.group(3) or "/"

        self.sock = socket.create_connection((host, port), timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\n"
               "Host: %s:%d\r\n"
               "Upgrade: websocket\r\n"
               "Connection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key))
        self.sock.sendall(req.encode())

        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("handshake: connection closed")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        status = head.split(b"\r\n")[0]
        if b"101" not in status:
            raise WSError("handshake failed: %s" % status.decode("utf-8", "replace"))
        self._buf = rest
        self._frag = b""
        self._send_lock = threading.Lock()
        self._closed = False
        # The reader thread blocks on recv(); close() wakes it with shutdown().
        self.sock.settimeout(None)

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, payload, opcode=0x1):
        with self._send_lock:
            if self._closed:
                raise WSError("socket closed")
            header = bytearray()
            header.append(0x80 | opcode)
            n = len(payload)
            if n < 126:
                header.append(0x80 | n)
            elif n < (1 << 16):
                header.append(0x80 | 126)
                header += struct.pack("!H", n)
            else:
                header.append(0x80 | 127)
                header += struct.pack("!Q", n)
            mask = os.urandom(4)
            header += mask
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            self.sock.sendall(bytes(header) + masked)

    def send_text(self, text):
        self._send_frame(text.encode("utf-8"), 0x1)

    def recv_text(self):
        while True:
            b1, b2 = self._read(2)
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if masked else b""
            payload = self._read(length)
            if mask:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))

            if opcode == 0x8:
                raise WSError("server sent close")
            if opcode == 0x9:
                self._send_frame(payload, 0xA)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x0, 0x1, 0x2):
                self._frag += payload
                if fin:
                    data, self._frag = self._frag, b""
                    return data.decode("utf-8", "replace")
            else:
                continue

    def close(self):
        self._closed = True
        try:
            self._send_frame(b"", 0x8)
        except Exception:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# --------------------------------------------------------------------------
# endpoint adapter
# --------------------------------------------------------------------------

def http_json(url, timeout=5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


class Endpoint:
    """One browser-level CDP connection plus its reader thread."""

    def __init__(self, port, events, kind):
        self.port = port
        self.events = events
        self.kind = kind
        self.ws = None
        self.reader = None
        self.alive = False
        self.discovered = False
        self.ever_connected = False
        self.next_connect = 0.0
        self._id = 0
        self._id_lock = threading.Lock()
        self._pending = {}
        self._pending_lock = threading.Lock()

    def connect(self):
        ver = http_json("http://127.0.0.1:%d/json/version" % self.port)
        ws_url = ver.get("webSocketDebuggerUrl")
        if not ws_url:
            raise WSError("no browser webSocketDebuggerUrl on :%d" % self.port)
        if self.reader is not None:
            self.close()
        self.ws = WebSocket(ws_url)
        self.alive = True
        self.discovered = False
        self.reader = threading.Thread(
            target=self._read_loop, name="cdp-%d" % self.port, daemon=True)
        self.reader.start()
        self.call("Target.setDiscoverTargets", {"discover": True})
        self.discovered = True

    def _read_loop(self):
        try:
            while self.alive:
                text = self.ws.recv_text()
                msg = json.loads(text)
                if "id" in msg:
                    with self._pending_lock:
                        item = self._pending.pop(msg.get("id"), None)
                    if item is None:
                        continue
                    ev, box = item
                    if "error" in msg:
                        box["error"] = msg["error"]
                    else:
                        box["result"] = msg.get("result", {})
                    ev.set()
                elif "method" in msg:
                    self.events.put((
                        self,
                        msg.get("method"),
                        msg.get("params") or {},
                        msg.get("sessionId"),
                    ))
        except Exception as e:
            _debug("endpoint :%d reader stopped: %s" % (self.port, e))
        finally:
            self.alive = False
            self._fail_pending(WSError("endpoint :%d disconnected" % self.port))

    def _fail_pending(self, error):
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for ev, box in pending:
            box["error"] = error
            ev.set()

    def call(self, method, params=None, session=None, timeout=10.0):
        with self._id_lock:
            self._id += 1
            mid = self._id
        ev = threading.Event()
        box = {}
        with self._pending_lock:
            self._pending[mid] = (ev, box)
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        try:
            self.ws.send_text(json.dumps(msg))
        except Exception as e:
            with self._pending_lock:
                self._pending.pop(mid, None)
            raise WSError("send %s failed: %s" % (method, e))
        if not ev.wait(timeout):
            with self._pending_lock:
                self._pending.pop(mid, None)
            raise WSError("timeout waiting for %s" % method)
        if "error" in box:
            raise WSError("%s: %s" % (method, box["error"]))
        return box.get("result", {})

    def evaluate(self, expr, session=None, timeout=10.0):
        res = self.call("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "userGesture": True,
            "awaitPromise": False,
        }, session=session, timeout=timeout)
        if res.get("exceptionDetails"):
            raise WSError("js exception: %s" % json.dumps(
                res["exceptionDetails"], ensure_ascii=False)[:400])
        return res.get("result", {}).get("value")

    def close(self):
        self.alive = False
        self._fail_pending(WSError("closed"))
        if self.ws is not None:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None
        reader = self.reader
        self.reader = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=1.0)


# --------------------------------------------------------------------------
# daemon
# --------------------------------------------------------------------------

class Session:
    def __init__(self, endpoint, session_id, target_id, title, url):
        self.endpoint = endpoint
        self.session_id = session_id
        self.target_id = target_id
        self.title = title
        self.url = url
        self.is_ui = endpoint.kind == "steam" and is_ui_target(title, url)
        self.slots = scripts_for(title, url, endpoint.kind)
        self.addscript_id = None
        self.handshake_ok = False
        self.handshake_attempts = 0
        self.handshake_deadline = 0.0

    def call(self, method, params=None, timeout=10.0):
        return self.endpoint.call(
            method, params, session=self.session_id, timeout=timeout)

    def evaluate(self, expr, timeout=10.0):
        return self.endpoint.evaluate(
            expr, session=self.session_id, timeout=timeout)

    @property
    def steam(self):
        return self.endpoint.kind == "steam"

    @property
    def vr(self):
        return self.endpoint.kind == "vr"


class Daemon:
    def __init__(self, steam_port, vr_port, sources, state_file):
        self.steam_port = steam_port
        self.vr_port = vr_port
        self.sources = sources
        self.markers = {slot: build_marker(src) for slot, src in sources.items()}
        self.state_file = state_file
        self.enabled = load_state(state_file)
        self.events = queue.Queue()
        self.endpoints = [
            Endpoint(steam_port, self.events, kind="steam"),
            Endpoint(vr_port, self.events, kind="vr"),
        ]
        self.sessions = {}
        self.target_sessions = {}
        self.steam_synced = False
        self.running = True
        self.fatal = False

    # -- endpoint lifecycle -------------------------------------------------

    def _drop_endpoint_sessions(self, endpoint):
        for sid, session in list(self.sessions.items()):
            if session.endpoint is endpoint:
                self._drop_session(sid)
        if endpoint.port == self.steam_port:
            self.steam_synced = False

    def _ensure_endpoint(self, endpoint, now):
        if endpoint.alive:
            return
        self._drop_endpoint_sessions(endpoint)
        # A connection that died must back off; an endpoint that has never
        # been seen (Steam still starting) is allowed to try immediately.
        if endpoint.ever_connected and endpoint.next_connect == 0:
            endpoint.next_connect = now + RETRY_SECONDS
        if now < endpoint.next_connect:
            return
        try:
            endpoint.connect()
            endpoint.ever_connected = True
            endpoint.next_connect = 0.0
            log("attached endpoint :%d" % endpoint.port)
        except Exception as e:
            endpoint.close()
            endpoint.next_connect = now + RETRY_SECONDS
            _debug("endpoint :%d not ready: %s" % (endpoint.port, e))

    # -- event loop ---------------------------------------------------------

    def run(self):
        try:
            while self.running and not self.fatal:
                now = time.time()
                for endpoint in self.endpoints:
                    self._ensure_endpoint(endpoint, now)
                self._drain_events()
                self._check_handshakes(now)
                if self.fatal:
                    break
                time.sleep(LOOP_SECONDS)
        finally:
            self.teardown_all()
            for endpoint in self.endpoints:
                endpoint.close()
        return 1 if self.fatal else 0

    def _drain_events(self):
        for _ in range(200):
            try:
                endpoint, method, params, session_id = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                self._handle_event(endpoint, method, params, session_id)
            except Exception as e:
                log("event %s failed: %s" % (method, e))

    def _handle_event(self, endpoint, method, params, session_id):
        if method == "Target.targetCreated":
            info = params.get("targetInfo") or {}
            if is_page_target(info):
                self._attach(endpoint, info)
        elif method == "Target.targetInfoChanged":
            info = params.get("targetInfo") or {}
            self._refresh_target(endpoint, info)
        elif method == "Target.targetDestroyed":
            self._drop_target(endpoint, params.get("targetId"))
        elif method == "Target.detachedFromTarget":
            self._drop_session(params.get("sessionId"))
        elif method == "Runtime.executionContextCreated":
            context = params.get("context") or {}
            aux = context.get("auxData") or {}
            if aux.get("isDefault") is False:
                return
            session = self.sessions.get(session_id)
            if session is not None:
                self._on_new_context(session)
        elif method == "Runtime.bindingCalled":
            if params.get("name") == BINDING:
                self._on_binding(session_id, params.get("payload") or "")

    # -- target lifecycle ---------------------------------------------------

    def _attach(self, endpoint, info):
        target_id = info.get("targetId")
        if not target_id:
            return
        key = (endpoint.port, target_id)
        if key in self.target_sessions:
            return
        try:
            result = endpoint.call("Target.attachToTarget", {
                "targetId": target_id,
                "flatten": True,
            })
            session_id = result.get("sessionId")
            if not session_id:
                return
            session = Session(
                endpoint, session_id, target_id,
                info.get("title") or "", info.get("url") or "")
            self.sessions[session_id] = session
            self.target_sessions[key] = session_id
            session.call("Runtime.enable")
            session.call("Page.enable")
            try:
                added = session.call(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": page_script(self.sources, session.slots)})
                session.addscript_id = added.get("identifier")
            except Exception as e:
                _debug("addScript failed on %s: %s" % (session.title, e))
            self._inject_current(session)
            if session.is_ui:
                self._start_handshake(session)
            if session.vr:
                self._push_session(session)
            if session.steam and not self.steam_synced:
                self._sync_from_steam()
            _debug("attached target %s (%s) on :%d" % (
                session.title, session.slots, endpoint.port))
        except Exception as e:
            log("attach failed on :%d target %s: %s" % (
                endpoint.port, info.get("title"), e))
            self._drop_target(endpoint, target_id)

    def _refresh_target(self, endpoint, info):
        target_id = info.get("targetId")
        sid = self.target_sessions.get((endpoint.port, target_id))
        session = self.sessions.get(sid) if sid else None
        if session is None:
            if is_page_target(info):
                self._attach(endpoint, info)
            return
        session.title = info.get("title") or session.title
        session.url = info.get("url") or session.url
        if (not session.is_ui and endpoint.kind == "steam"
                and is_ui_target(session.title, session.url)):
            session.is_ui = True
            session.slots = scripts_for(session.title, session.url, endpoint.kind)
            try:
                self._inject_current(session)
                self._start_handshake(session)
            except Exception as e:
                _debug("ui upgrade failed: %s" % e)

    def _drop_target(self, endpoint, target_id):
        sid = self.target_sessions.pop((endpoint.port, target_id), None)
        if sid:
            self._drop_session(sid)

    def _drop_session(self, session_id):
        if not session_id:
            return
        session = self.sessions.pop(session_id, None)
        if session is None:
            return
        for key, sid in list(self.target_sessions.items()):
            if sid == session_id:
                self.target_sessions.pop(key, None)

    # -- injection ----------------------------------------------------------

    def _inject_current(self, session):
        for slot in session.slots:
            source = self.sources.get(slot)
            if source is None:
                continue
            marker = self.markers[slot]
            current = session.evaluate(probe_expr(slot))
            if current == marker:
                continue
            session.evaluate(inject_expr(source, slot, marker), timeout=20.0)
            _debug("inject -> %s [%s] build %s" % (
                session.title, slot, marker))

    def _on_new_context(self, session):
        if session.is_ui:
            self._start_handshake(session)
            self._sync_from_steam()
        if session.vr:
            # A Dashboard page reloaded: its localStorage may have been
            # recreated, so put the current value back immediately.
            self._push_session(session)
        try:
            self._inject_current(session)
        except Exception as e:
            _debug("reinject after navigation failed: %s" % e)

    # -- binding handshake --------------------------------------------------

    def _start_handshake(self, session):
        session.handshake_ok = False
        session.handshake_attempts = 0
        session.handshake_deadline = 0.0
        self._send_handshake(session)

    def _send_handshake(self, session):
        session.handshake_attempts += 1
        session.handshake_deadline = time.time() + HANDSHAKE_TIMEOUT
        try:
            session.call("Runtime.addBinding", {"name": BINDING})
            session.evaluate(
                "window.%s(JSON.stringify({c:'hello'}))" % BINDING)
            _debug("handshake -> %s (attempt %d)" % (
                session.title, session.handshake_attempts))
        except Exception as e:
            _debug("handshake send failed: %s" % e)

    def _check_handshakes(self, now):
        for session in list(self.sessions.values()):
            if not session.is_ui or session.handshake_ok:
                continue
            if not session.handshake_deadline or now < session.handshake_deadline:
                continue
            if session.handshake_attempts >= HANDSHAKE_ATTEMPTS:
                log("binding handshake failed after %d attempts; restarting" %
                    session.handshake_attempts)
                self.fatal = True
                return
            self._send_handshake(session)

    def _on_binding(self, session_id, payload):
        msg = parse_binding_payload(payload)
        if msg is None:
            return
        session = self.sessions.get(session_id)
        if msg["kind"] == "hello":
            if session is not None and not session.handshake_ok:
                session.handshake_ok = True
                log("binding verified on %s" % session.title)
                self._sync_from_steam()
            return
        _debug("binding <- %s" % payload[:120])
        self._on_user_change(msg.get("enabled"))

    # -- state --------------------------------------------------------------

    def _choose_steam_session(self):
        # The QAM page is always the best place to read the flag. The fallback
        # avoids the data: utility target, whose origin is not the shared
        # steamloopback.host storage.
        for session in self.sessions.values():
            if session.steam and session.is_ui:
                return session
        for session in self.sessions.values():
            if session.steam and not (session.url or "").startswith("data:"):
                return session
        for session in self.sessions.values():
            if session.steam:
                return session
        return None

    def _read_steam_enabled(self):
        session = self._choose_steam_session()
        if session is None:
            return None
        try:
            raw = session.evaluate("localStorage.getItem(%s)" % json.dumps(KEY))
            return normalize_enabled(raw)
        except Exception as e:
            _debug("read :8080 flag failed: %s" % e)
            return None

    def _set_enabled(self, enabled):
        if enabled == self.enabled:
            return False
        self.enabled = enabled
        try:
            save_state(self.state_file, enabled)
        except Exception as e:
            log("state write failed: %s" % e)
        return True

    def _sync_from_steam(self):
        enabled = self._read_steam_enabled()
        if enabled is None:
            return
        self.steam_synced = True
        if self._set_enabled(enabled):
            _debug("state <- :8080 %s" % storage_value(enabled))
        self._push_vr()

    def _on_user_change(self, hint):
        enabled = self._read_steam_enabled()
        if enabled is None:
            if hint is None:
                return
            enabled = bool(hint)
        if self._set_enabled(enabled):
            log("switch -> %s" % storage_value(enabled))
        self.steam_synced = True
        self._push_vr()

    def _push_vr(self):
        for session in list(self.sessions.values()):
            if session.vr:
                self._push_session(session)

    def _push_session(self, session):
        value = storage_value(self.enabled)
        try:
            session.evaluate(
                "localStorage.setItem(%s, %s)" % (
                    json.dumps(KEY), json.dumps(value)))
            _debug("push -> %s %s" % (session.title, value))
        except Exception as e:
            # A reload may have invalidated the context. Target events will
            # attach and push again; do not delete the session permanently.
            _debug("push to %s failed: %s" % (session.title, e))

    # -- shutdown -----------------------------------------------------------

    def teardown_all(self):
        expr = teardown_expr()
        for session in list(self.sessions.values()):
            try:
                session.evaluate(expr, timeout=2.0)
            except Exception:
                pass


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def status(state_file, steam_port, vr_port):
    def endpoint(port):
        info = {"port": port, "reachable": False, "pages": 0, "ui": False}
        try:
            targets = http_json("http://127.0.0.1:%d/json/list" % port)
        except Exception:
            return info
        info["reachable"] = True
        for t in targets:
            if not is_page_target(t):
                continue
            info["pages"] += 1
            if is_ui_target(t.get("title"), t.get("url")):
                info["ui"] = True
        return info

    print(json.dumps({
        "enabled": load_state(state_file),
        "stateFile": state_file,
        "steam": endpoint(steam_port),
        "vr": endpoint(vr_port),
    }, ensure_ascii=False, indent=2))
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def parse_args(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="sf-ui-haptic-switch daemon")
    ap.add_argument("--steamwebhelper-port", dest="steamwebhelper_port",
                    type=int, default=DEFAULT_STEAM_PORT,
                    help="Steam client DevTools port (default %d)" % DEFAULT_STEAM_PORT)
    ap.add_argument("--vrwebhelper-port", dest="vrwebhelper_port",
                    type=int, default=DEFAULT_VR_PORT,
                    help="SteamVR Dashboard DevTools port (default %d)" % DEFAULT_VR_PORT)
    ap.add_argument("--script-dir", default=here,
                    help="directory holding the two .js scripts (default: this file's dir)")
    ap.add_argument("--state-file", default=None,
                    help="state file (default: XDG state directory)")
    ap.add_argument("--status", action="store_true",
                    help="print the persisted value and endpoint status, then exit")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    global VERBOSE
    args = parse_args(argv)
    VERBOSE = args.verbose
    state_file = args.state_file or default_state_path()

    if args.status:
        return status(state_file, args.steamwebhelper_port, args.vrwebhelper_port)

    try:
        sources = load_sources(args.script_dir)
    except OSError as e:
        log("cannot read scripts from %s: %s" % (args.script_dir, e))
        return 2

    daemon = Daemon(
        args.steamwebhelper_port,
        args.vrwebhelper_port,
        sources,
        state_file,
    )

    def stop(*_args):
        daemon.running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log("started; steam=:%d vr=:%d state=%s" % (
        args.steamwebhelper_port, args.vrwebhelper_port, state_file))
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())
