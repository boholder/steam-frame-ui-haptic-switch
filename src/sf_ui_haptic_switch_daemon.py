#!/usr/bin/env python3
"""sf-ui-haptic-switch daemon.

A small resident process that keeps the "Controller UI Haptic" switch alive
across two Chromium Embedded Framework (CEF) DevTools endpoints:

- ``:8080`` — the Steam client UI (``steamwebhelper``)
- ``:8087`` — the SteamVR Dashboard (``vrwebhelper``)

The switch itself lives in the Quick Access Menu on ``:8080``. Its value lives
in ``localStorage['sf_ui_haptic_switch']`` and is the single source of truth.
When the user flips it, the UI writes that key and calls the
``__sfUiHapticSwitchSend`` CDP (Chrome DevTools Protocol) binding. The daemon
reads the key back, persists it to ``~/.local/state/sf-ui-haptic-switch/state.json``
and pushes it into every ``:8087`` page. The daemon never writes back to ``:8080``.

Target discovery is event-driven: one browser-level WebSocket per endpoint,
``Target.setDiscoverTargets``, then ``Target.attachToTarget {flatten:true}`` for
every page. No polling.

The CDP transport (WebSocket framing, ``Endpoint``, its reader/sender threads)
lives in this module; there is no separate transport module.

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
import typing
import urllib.request

# --------------------------------------------------------------------------
# CDP transport
# --------------------------------------------------------------------------


class WSError(Exception):
    pass


class WebSocket:
    def __init__(self, url: str, timeout: float = 10.0) -> None:
        m = re.match(r"ws://([^/:]+)(?::(\d+))?(/.*)?$", url)
        if not m:
            raise WSError("bad ws url: %r" % url)
        host = m.group(1)
        port = int(m.group(2) or 80)
        path = m.group(3) or "/"

        self.sock = socket.create_connection((host, port), timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)
        )
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
        self._buf = rest  # receive buffer; may already hold early frames
        self._frag = b""  # fragments of the message being assembled
        self._send_lock = threading.Lock()  # serializes frames onto the socket
        self._closed = False  # set by close(); later sends refuse to run
        # The reader thread blocks on recv(); close() wakes it with shutdown().
        self.sock.settimeout(None)

    def _read_exactly(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame_unlocked(self, payload: bytes, opcode: int = 0x1) -> None:
        if self._closed:
            raise WSError("socket closed")
        header = bytearray()
        # FIN is always set: outgoing frames are never fragmented.
        header.append(0x80 | opcode)
        n = len(payload)
        # Length forms: 7-bit inline, or 126/127 for 16/64-bit; the 0x80 bit
        # on the length byte marks client frames as masked.
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

    def _send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        with self._send_lock:
            self._send_frame_unlocked(payload, opcode)

    def send_text(self, text: str) -> None:
        self._send_frame(text.encode("utf-8"), 0x1)

    # noinspection bad-return
    def recv_text(self) -> str:
        while True:
            b1, b2 = self._read_exactly(2)
            fin = b1 & 0x80
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exactly(8))[0]
            mask = self._read_exactly(4) if masked else b""
            payload = self._read_exactly(length)
            if mask:
                payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))

            # Control frames: close ends the stream, ping gets a pong, pong is ignored.
            if opcode == 0x8:
                raise WSError("server sent close")
            if opcode == 0x9:
                self._send_frame(payload, 0xA)
                continue
            if opcode == 0xA:
                continue
            # Data frames (text/binary/continuation) accumulate until FIN.
            if opcode in (0x0, 0x1, 0x2):
                self._frag += payload
                if fin:
                    data, self._frag = self._frag, b""
                    return data.decode("utf-8", "replace")
            else:
                continue

    def close(self) -> None:
        # The sender may be blocked in sendall() while holding _send_lock.
        # Never wait for it here: send the close frame only if the lock is
        # free, otherwise skip straight to shutdown, which wakes sendall.
        locked = self._send_lock.acquire(blocking=False)
        if locked:
            try:
                self._send_frame_unlocked(b"", 0x8)
            except Exception:
                pass
            finally:
                self._send_lock.release()
        self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def http_get_json(url: str, timeout: float = 5.0) -> typing.Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def describe_cdp_command(method: str, params: dict[str, typing.Any] | None = None) -> str:
    """One-line, bounded representation for --verbose command logging."""
    text = json.dumps(params or {}, ensure_ascii=False, sort_keys=True)
    if len(text) > 200:
        text = text[:197] + "..."
    return "command %s %s" % (method, text)


class Endpoint:
    """One browser-level CDP connection and its transport resources.

    Owns the WebSocket, the reader/sender threads, the request id and pending
    table that turn replies into blocking ``send_command()`` results, the
    outgoing send queue, and the reconnect timestamp.

    It forwards methods, params and session ids between ``Daemon`` and browsers,
    and pushes raw ``(endpoint, method, params, sessionId)`` events to the shared queue.

    It knows nothing about pages, scripts or the switch value; that is the
    ``Daemon``'s job.
    """

    def __init__(
        self,
        port: int,
        events: queue.Queue[tuple[Endpoint, str, dict[str, typing.Any], str | None]],
        kind: str,
        debug: typing.Callable[..., None] | None = None,
    ) -> None:
        self.port = port
        self.events = events
        self.kind = kind
        self._debug = debug or (lambda *a: None)
        self.ws: WebSocket | None = None
        self.reader: threading.Thread | None = None
        self.sender: threading.Thread | None = None
        self.alive = False
        # Retry bookkeeping in one field:
        # None = never connected yet,
        # 0.0 = connected (reconnect allowed immediately),
        # a timestamp = wait.
        self._retry_at: float | None = None
        # Monotonic CDP request id; _pending is keyed by it.
        self._id = 0
        self._id_lock = threading.Lock()
        # id -> (event, box): send_command() blocks on the event, the reader fills box.
        self._pending: dict[int, tuple[threading.Event, dict[str, typing.Any]]] = {}
        self._pending_lock = threading.Lock()
        # Outgoing messages for the sender thread; it owns all socket writes.
        self._send_queue: queue.Queue[str | None] = queue.Queue()

    @property
    def connection_dropped(self) -> bool:
        """True right after an established connection died."""
        return self._retry_at == 0.0

    @property
    def next_connect_at(self) -> float:
        """Earliest allowed reconnect; 0.0 means there is no wait."""
        return self._retry_at or 0.0

    def schedule_retry(self, now: float) -> None:
        self._retry_at = now + RETRY_SECONDS

    def mark_connected(self) -> None:
        self._retry_at = 0.0

    def connect(self) -> None:
        ver = http_get_json("http://127.0.0.1:%d/json/version" % self.port)
        ws_url = ver.get("webSocketDebuggerUrl")
        if not ws_url:
            raise WSError("no browser webSocketDebuggerUrl on :%d" % self.port)
        if self.reader is not None:
            self.close()
        # close() leaves a sentinel in the old queue; a fresh sender must not
        # consume it (or any stale message) or the new connection dies at once.
        self._send_queue = queue.Queue()
        ws = WebSocket(ws_url)
        self.ws = ws
        self.alive = True
        self.reader = threading.Thread(
            target=self._reader_loop,
            args=(ws,),
            name="cdp-%d" % self.port,
            daemon=True,
        )
        self.reader.start()
        self.sender = threading.Thread(
            target=self._sender_loop,
            args=(ws,),
            name="cdp-send-%d" % self.port,
            daemon=True,
        )
        self.sender.start()
        # Subscribe to target events, so new pages are notified through
        # Target.target* pushes instead of continuelly actively polling /json/list.
        # It must come after the threads start: send_command() waits for
        # a reply that only the reader thread can deliver.
        self.send_command("Target.setDiscoverTargets", {"discover": True})
        self.mark_connected()

    def _sender_loop(self, ws: WebSocket) -> None:
        # A dedicated writer keeps a stuck sendall() from freezing the daemon's
        # main loop. On failure the whole endpoint is dead; close() reconnects.
        while self.alive and self.ws is ws:
            msg = self._send_queue.get()
            if msg is None:
                return
            try:
                ws.send_text(msg)
            except Exception as e:
                if self.ws is ws:
                    self.alive = False
                    self._fail_all_pending_calls(WSError("send failed: %s" % e))
                return

    def _reader_loop(self, ws: WebSocket) -> None:
        try:
            while self.alive and self.ws is ws:
                text = ws.recv_text()
                msg = json.loads(text)
                # Replies carry an id and resolve a pending send_command(); anything
                # else is an event for the main loop.
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
                    self.events.put(
                        (
                            self,
                            msg.get("method"),
                            msg.get("params") or {},
                            msg.get("sessionId"),
                        )
                    )
        except Exception as e:
            self._debug("endpoint :%d reader stopped: %s" % (self.port, e))
        finally:
            # Only the reader that owns the current socket may flip the state;
            # a stale reader must not clobber a freshly reconnected endpoint.
            if self.ws is ws:
                self.alive = False
                self._fail_all_pending_calls(WSError("endpoint :%d disconnected" % self.port))

    def _fail_all_pending_calls(self, error: WSError) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for ev, box in pending:
            box["error"] = error
            ev.set()

    def send_command(
        self,
        method: str,
        params: dict[str, typing.Any] | None = None,
        session: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, typing.Any]:
        with self._id_lock:
            self._id += 1
            mid = self._id
        ev = threading.Event()
        box: dict[str, typing.Any] = {}
        # Register before sending so an immediate reply cannot miss the pending entry.
        with self._pending_lock:
            self._pending[mid] = (ev, box)
        msg: dict[str, typing.Any] = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self._debug(describe_cdp_command(method, params))
        if not self.alive or self.ws is None:
            with self._pending_lock:
                self._pending.pop(mid, None)
            raise WSError("endpoint :%d is not connected" % self.port)
        self._send_queue.put(json.dumps(msg))
        if not ev.wait(timeout):
            with self._pending_lock:
                self._pending.pop(mid, None)
            # A peer that never answers is untrusted: drop the endpoint and let
            # _ensure_endpoint_connected reconnect instead of keeping a dead session.
            self.close()
            raise WSError("timeout waiting for %s" % method)
        if "error" in box:
            raise WSError("%s: %s" % (method, box["error"]))
        return box.get("result", {})

    def evaluate(self, expr: str, session: str | None = None, timeout: float = 10.0) -> typing.Any:
        res = self.send_command(
            "Runtime.evaluate",
            {
                "expression": expr,
                "returnByValue": True,
                "userGesture": True,
                "awaitPromise": False,
            },
            session=session,
            timeout=timeout,
        )
        if res.get("exceptionDetails"):
            raise WSError("js exception: %s" % json.dumps(res["exceptionDetails"], ensure_ascii=False)[:400])
        return res.get("result", {}).get("value")

    def close(self) -> None:
        self.alive = False
        # None is the sentinel that ends the sender loop.
        self._send_queue.put(None)
        self._fail_all_pending_calls(WSError("closed"))
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
        sender = self.sender
        self.sender = None
        if sender is not None and sender is not threading.current_thread():
            sender.join(timeout=1.0)


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

DEFAULT_STEAM_PORT = 8080
DEFAULT_VR_PORT = 8087

KIND_STEAM = "steam"
KIND_VR = "vr"

GLOBAL = "__SF_UI_HAPTIC_SWITCH__"
BINDING = "__sfUiHapticSwitchSend"
KEY = "sf_ui_haptic_switch"
UI_MATCH = "valve.steam.gamepadui.barpopup"
# that very bottom menu that always appears below,
# contains steam logo, opened tabs, quick settings, user avatar.

GATE_JS = "sf_ui_haptic_switch_gate.js"
UI_JS = "sf_ui_haptic_switch_ui.js"

SCRIPT_GATE = "gate"
SCRIPT_UI = "ui"

RETRY_SECONDS = 1.0
HANDSHAKE_TIMEOUT = 1.0
HANDSHAKE_RETRIES = 3
HANDSHAKE_MAX_SENDS = 1 + HANDSHAKE_RETRIES
LOOP_SECONDS = 0.05

# Runs in the page on shutdown: let each script unhook, then delete the global.
JS_TEAR_DOWN_EXPR = (
    "(function(){var n=window.%s;if(!n)return;"
    "try{if(n.ui&&n.ui.teardown)n.ui.teardown();}catch(e){}"
    "try{if(n.gate&&n.gate.teardown)n.gate.teardown();}catch(e){}"
    "try{delete window.%s;}catch(e){}})()" % (GLOBAL, GLOBAL)
)

VERBOSE = False


def log(*a: object) -> None:
    print("[sf-ui-haptic-switch]", *a, file=sys.stderr, flush=True)


def _debug(*a: object) -> None:
    if VERBOSE:
        log(*a)


# --------------------------------------------------------------------------
# pure decision logic (the test seam)
# --------------------------------------------------------------------------


def storage_value_to_enabled(value: str | None) -> bool:
    """Off only for explicit "0"/"false"; missing or junk means on."""
    return not (value == "0" or value == "false")


def parse_binding_message(payload: str) -> dict[str, typing.Any] | None:
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


def is_page_type_target(info: object) -> bool:
    return isinstance(info, dict) and info.get("type") == "page"


def is_quick_access_menu(title: str | None, url: str | None) -> bool:
    return UI_MATCH in ((title or "") + (url or ""))


def script_names_for_target(title: str, url: str, kind: str) -> list[str]:
    """Which scripts a page needs.

    The gate goes everywhere. The QAM UI is a Steam-client concept: it must
    never be installed into a SteamVR Dashboard page, even if a Dashboard URL
    happens to contain the vary barpopup key.
    """
    if kind == KIND_STEAM and is_quick_access_menu(title, url):
        return [SCRIPT_GATE, SCRIPT_UI]
    return [SCRIPT_GATE]


def build_script_marker(source: str) -> str:
    # Short content hash; pages report it so unchanged builds are not reinjected.
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def build_script_marker_probe(script: str) -> str:
    return "((window.%s && window.%s.build && window.%s.build[%s]) || '')" % (GLOBAL, GLOBAL, GLOBAL, json.dumps(script))


def build_script_injection(source: str, name: str, marker: str) -> str:
    # The appended epilogue records the marker in window[GLOBAL].build[name],
    # which is exactly what build_script_marker_probe() reads back.
    epilogue = ";try{window.%s=window.%s||{};window.%s.build=window.%s.build||{};window.%s.build[%s]=%s;}catch(e){}" % (
        GLOBAL,
        GLOBAL,
        GLOBAL,
        GLOBAL,
        GLOBAL,
        json.dumps(name),
        json.dumps(marker),
    )
    return source + epilogue


def build_new_document_script(script_injections: dict[str, str], script_names: list[str]) -> str:
    """Join the precomputed injections for a page, in the given order."""
    # The ";" guards concatenation if an injected script omits its trailing semicolon.
    parts = (script_injections[name] for name in script_names if name in script_injections)
    return "\n;\n".join(parts)


def default_state_file_path() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "sf-ui-haptic-switch", "state.json")


def read_persisted_enabled(path: str) -> bool:
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


def write_persisted_enabled(path: str, enabled: bool) -> None:
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


def read_script_sources(script_dir: str) -> dict[str, str]:
    """{"gate":"gate js content", "ui":"ui js content"}"""
    sources = {}
    for script, name in ((SCRIPT_GATE, GATE_JS), (SCRIPT_UI, UI_JS)):
        with open(os.path.join(script_dir, name), encoding="utf-8") as f:
            sources[script] = f.read()
    return sources


# --------------------------------------------------------------------------
# daemon
# --------------------------------------------------------------------------


class Handshake:
    """The binding handshake for one UI session: first send + 3 retries."""

    def __init__(self) -> None:
        self.ok = False
        self.attempts = 0
        self.deadline: float | None
        self.restart()

    def restart(self) -> None:
        # ok: the page answered; attempts: sends so far; deadline: when the
        # next retry is due (None until the first send).
        self.ok = False
        self.attempts = 0
        self.deadline = None

    def sent(self, now: float) -> None:
        self.attempts += 1
        self.deadline = now + HANDSHAKE_TIMEOUT

    def due(self, now: float) -> bool:
        return not self.ok and self.deadline is not None and now >= self.deadline

    def exhausted(self) -> bool:
        return self.attempts >= HANDSHAKE_MAX_SENDS


class Session:
    """One attached page target on one endpoint, as seen by the daemon.

    Holds the CDP ``sessionId`` and ``targetId`` that identify the page, its
    latest title/url, the injected scripts it should receive, and its handshake
    progress.

    ``send_command()``/``evaluate()`` add ``sessionId`` to the endpoint's calls,
    and ``start_handshake()``/``send_handshake()`` drive the binding handshake
    for this page.

    Registered and torn down by ``Daemon``.
    """

    def __init__(self, endpoint: Endpoint, session_id: str, target_id: str, title: str, url: str) -> None:
        self.endpoint = endpoint
        self.session_id = session_id
        self.target_id = target_id
        self.title = title
        self.url = url
        # Selected scripts that should be injected into this page
        self.script_names = script_names_for_target(title, url, endpoint.kind)
        # is_ui caches the QAM check.
        self.is_ui = SCRIPT_UI in self.script_names
        self.handshake = Handshake()

    def send_command(
        self,
        method: str,
        params: dict[str, typing.Any] | None = None,
        timeout: float = 10.0,
    ) -> dict[str, typing.Any]:
        return self.endpoint.send_command(method, params, session=self.session_id, timeout=timeout)

    def evaluate(self, expr: str, timeout: float = 10.0) -> typing.Any:
        return self.endpoint.evaluate(expr, session=self.session_id, timeout=timeout)

    def start_handshake(self) -> None:
        """Reset the handshake state and send the first attempt."""
        self.handshake.restart()
        self.send_handshake()

    def send_handshake(self) -> None:
        """Send one attempt; Daemon retries until ``handshake.exhausted()``."""
        self.handshake.sent(time.time())
        try:
            self.send_command("Runtime.addBinding", {"name": BINDING})
            # Daemon._on_binding_called() will receive "hello" message
            self.evaluate("window.%s(JSON.stringify({c:'hello'}))" % BINDING)
            _debug("handshake -> %s (attempt %d)" % (self.title, self.handshake.attempts))
        except Exception as e:
            _debug("handshake send failed: %s" % e)

    def retry_handshake_if_due(self, now: float) -> bool:
        """Retry when due; return True when the handshake is exhausted."""
        if not self.is_ui or not self.handshake.due(now):
            return False
        if self.handshake.exhausted():
            return True
        self.send_handshake()
        return False

    @property
    def shares_local_storage(self) -> bool:
        """A data: utility page has an opaque origin, not the shared storage."""
        return not (self.url or "").startswith("data:")

    @property
    def can_read_local_storage_flag(self) -> bool:
        """Only the Steam client's own pages can read the flag we wrote."""
        return self.steam and self.shares_local_storage

    @property
    def steam(self) -> bool:
        """the Steam client UI, port 8080"""
        return self.endpoint.kind == KIND_STEAM

    @property
    def vr(self) -> bool:
        """the SteamVR Dashboard, port 8087"""
        return self.endpoint.kind == KIND_VR


class Daemon:
    """Policy and orchestration for the switch across both endpoints.

    Owns the injected script sources and content markers, the persisted and
    in-memory switch value, the shared CDP event queue, the two ``Endpoint``
    instances, and the session registries (``session_id -> Session`` and
    ``(port, targetId) -> session_id``).

    It decides which targets to attach, injects scripts, runs the binding handshake,
    syncs and pushes the switch value, reconnects dead endpoints,
    and tears everything down on exit.

    Sessions are registered here rather than on ``Endpoint``: events are
    consumed single-threaded by the main loop, and attach/inject/push
    decisions need the whole session set across both endpoints.
    """

    def __init__(
        self,
        steam_port: int,
        vr_port: int,
        js_sources: dict[str, str],
        state_file: str,
    ) -> None:
        self.js_sources = js_sources
        # Only two scripts, so precompute the strings every page reuses.
        # Expected content hash per script: what a page should report back.
        self.expected_markers = {script: build_script_marker(src) for script, src in js_sources.items()}
        # JS expression that asks a page which marker it currently has.
        self.marker_probe_exprs = {name: build_script_marker_probe(name) for name in js_sources}
        self.injections = {name: build_script_injection(src, name, self.expected_markers[name]) for name, src in js_sources.items()}
        self.state_file = state_file
        self.enabled = read_persisted_enabled(state_file)
        # Reader threads push (endpoint, method, params, session_id) here.
        self.events: queue.Queue[tuple[Endpoint, str, dict[str, typing.Any], str | None]] = queue.Queue()
        self.endpoints = [
            Endpoint(steam_port, self.events, KIND_STEAM, debug=_debug),
            Endpoint(vr_port, self.events, KIND_VR, debug=_debug),
        ]
        # session_id -> Session
        # A session is one attached page target on one endpoint: several pages
        # share a single browser WebSocket, each with its own CDP session id.
        self.sessions: dict[str, Session] = {}
        # reverse index (endpoint port, CDP targetId) -> session_id
        # For ensuring one target is attached at most once per endpoint.
        self.target_sessions: dict[tuple[int, str], str] = {}
        # running: cleared by the SIGTERM/SIGINT handler; fatal: restart us.
        self.running = True
        self.fatal = False

    # -- endpoint lifecycle -------------------------------------------------

    def _ensure_endpoint_connected(self, endpoint: Endpoint, now: float) -> None:
        if endpoint.alive:
            return
        # Remove (daemon) held references of the sessions that related to this endpoint.
        for session in [s for s in self.sessions.values() if s.endpoint is endpoint]:
            self._drop(session)
        # An established connection that just dropped waits RETRY_SECONDS before
        # trying again; an endpoint that never came up retries immediately.
        if endpoint.connection_dropped:
            endpoint.schedule_retry(now)
        if now < endpoint.next_connect_at:
            return
        try:
            endpoint.connect()
            log("attached endpoint :%d" % endpoint.port)
        except Exception as e:
            endpoint.close()
            endpoint.schedule_retry(now)
            _debug("endpoint :%d not ready: %s" % (endpoint.port, e))

    # -- event loop ---------------------------------------------------------

    def run(self) -> int:
        try:
            while self.running and not self.fatal:
                now = time.time()
                for endpoint in self.endpoints:
                    self._ensure_endpoint_connected(endpoint, now)
                self._process_queued_events()
                self._retry_due_handshakes(now)
                if self.fatal:
                    break
                time.sleep(LOOP_SECONDS)
        finally:
            # tear down injected js
            for session in list(self.sessions.values()):
                try:
                    session.evaluate(JS_TEAR_DOWN_EXPR, timeout=2.0)
                except Exception:
                    pass
            for endpoint in self.endpoints:
                endpoint.close()
        return 1 if self.fatal else 0

    def _process_queued_events(self) -> None:
        for _ in range(200):
            try:
                # Endpoint._reader_loop() will put session_id in event
                endpoint, method, params, session_id = self.events.get_nowait()
            except queue.Empty:
                return
            try:
                self._dispatch_cdp_event(endpoint, method, params, session_id)
            except Exception as e:
                log("event %s failed: %s" % (method, e))

    def _dispatch_cdp_event(
        self,
        endpoint: Endpoint,
        method: str,
        params: dict[str, typing.Any],
        session_id: str | None,
    ) -> None:
        if method == "Target.targetCreated":
            info = params.get("targetInfo") or {}
            if is_page_type_target(info):
                self._attach_to_page_target(endpoint, info)

        elif method == "Target.targetInfoChanged":
            info = params.get("targetInfo") or {}
            self._on_target_info_changed(endpoint, info)

        elif method == "Target.targetDestroyed":
            target_id = params.get("targetId") or ""
            sid = self.target_sessions.get((endpoint.port, target_id))
            session = self.sessions.get(sid) if sid else None
            if session is not None:
                self._drop(session)

        elif method == "Target.detachedFromTarget":
            session = self.sessions.get(params.get("sessionId") or "")
            if session is not None:
                self._drop(session)

        elif method == "Runtime.executionContextCreated":
            # A "world" is an independent JS global (window) inside the page.
            # Extensions and CDP isolated worlds get their own;
            # only the page's default world carries our scripts and the binding.
            context = params.get("context") or {}
            aux = context.get("auxData") or {}
            if aux.get("isDefault") is False:
                return

            session = self.sessions.get(session_id or "")
            if session is not None:
                self._on_execution_context_created(session)

        elif method == "Runtime.bindingCalled":
            if params.get("name") == BINDING:
                self._on_binding_called(session_id or "", params.get("payload") or "")

    # -- target lifecycle ---------------------------------------------------

    def _attach_to_page_target(self, endpoint: Endpoint, info: dict[str, typing.Any]) -> None:
        target_id: str = info.get("targetId") or ""
        if not target_id:
            return
        key = (endpoint.port, target_id)
        # already attached, skip
        if key in self.target_sessions:
            return

        session_id: str | None = None
        try:
            result = endpoint.send_command(
                "Target.attachToTarget",
                {
                    "targetId": target_id,
                    # flatten: true multiplexes every target session
                    # over single browser socket.
                    "flatten": True,
                },
            )

            # save references
            session_id = result.get("sessionId")
            if not session_id:
                return
            session = Session(
                endpoint,
                session_id,
                target_id,
                info.get("title") or "",
                info.get("url") or "",
            )
            self.sessions[session_id] = session
            self.target_sessions[key] = session_id

            # Runtime.enable delivers bindingCalled and executionContextCreated.
            session.send_command("Runtime.enable")
            session.send_command("Page.enable")
            self._inject(session)
            _debug("attached target %s (%s) on :%d" % (session.title, session.script_names, endpoint.port))

        except Exception as e:
            log("attach failed on :%d target %s: %s" % (endpoint.port, info.get("title"), e))
            if session_id:
                try:
                    endpoint.send_command(
                        "Target.detachFromTarget",
                        {"sessionId": session_id},
                        timeout=2.0,
                    )
                except Exception:
                    pass
            session = self.sessions.get(session_id) if session_id else None
            if session is not None:
                self._drop(session)

    def _inject(self, session: Session) -> None:
        """Inject the page's scripts.
        The binding handshake and the VR state push are left to the
        execution-context event.
        """
        # Register for future documents first, then inject into the live one.
        try:
            session.send_command(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": build_new_document_script(self.injections, session.script_names)},
            )
        except Exception as e:
            _debug("addScript failed on %s: %s" % (session.title, e))
        self._inject_into_current_document(session)

    def _on_target_info_changed(self, endpoint: Endpoint, info: dict[str, typing.Any]) -> None:
        target_id: str = info.get("targetId") or ""
        sid = self.target_sessions.get((endpoint.port, target_id))
        session = self.sessions.get(sid) if sid else None

        if session is None:
            if is_page_type_target(info):
                self._attach_to_page_target(endpoint, info)
            return

        # update related Session instance's fields
        session.title = info.get("title") or session.title
        session.url = info.get("url") or session.url

        script_names = script_names_for_target(session.title, session.url, endpoint.kind)
        # A page can navigate into the QAM after attach
        if SCRIPT_UI in script_names and not session.is_ui:
            session.is_ui = True
            session.script_names = script_names
            try:
                self._inject(session)
                # An in-page navigation may reuse the current document, so no
                # execution-context event would start the handshake for it.
                # Thus we manually start the handshake.
                session.start_handshake()
            except Exception as e:
                _debug("ui upgrade failed: %s" % e)

    def _drop(self, session: Session) -> None:
        """Remove a session reference from both indexes."""
        self.sessions.pop(session.session_id, None)
        self.target_sessions.pop((session.endpoint.port, session.target_id), None)

    # -- injection ----------------------------------------------------------

    def _inject_into_current_document(self, session: Session) -> None:
        for name in session.script_names:
            injection = self.injections.get(name)
            if injection is None:
                continue
            marker = self.expected_markers[name]
            current = session.evaluate(self.marker_probe_exprs[name])
            # Same marker: the page already runs this exact build.
            if current == marker:
                continue
            session.evaluate(injection, timeout=20.0)
            _debug("inject -> %s [%s] build %s" % (session.title, name, marker))

    def _on_execution_context_created(self, session: Session) -> None:
        if session.is_ui:
            # On a first load or a reload this is the moment to (re)send the
            # binding: whatever the page answered before no longer applies.
            session.start_handshake()
            self._sync_state_from_steam_client()
        if session.vr:
            # A Dashboard page attached or reloaded: its localStorage may be
            # missing or recreated, so put the current value back.
            self._push_state_to(session)
        try:
            self._inject_into_current_document(session)
        except Exception as e:
            _debug("reinject after navigation failed: %s" % e)

    # -- binding handshake --------------------------------------------------

    def _retry_due_handshakes(self, now: float) -> None:
        for session in list(self.sessions.values()):
            if session.retry_handshake_if_due(now):
                # Exit non-zero on purpose: the service manager restarts us.
                log("binding handshake failed after %d attempts; restarting" % session.handshake.attempts)
                self.fatal = True
                return

    def _on_binding_called(self, session_id: str, payload: str) -> None:
        msg = parse_binding_message(payload)
        if msg is None:
            return
        session = self.sessions.get(session_id)
        # Session.send_handshake() will send "hello" message
        if msg["kind"] == "hello":
            if session is not None and not session.handshake.ok:
                session.handshake.ok = True
                _debug("binding verified on %s" % session.title)
                self._sync_state_from_steam_client()
            return
        _debug("binding <- %s (v=%s)" % (payload[:120], msg.get("enabled")))
        self._on_switch_toggled_by_user()

    # -- state --------------------------------------------------------------

    def _choose_steam_session_for_flag_read(self) -> Session | None:
        # The QAM page is always the best place to read the flag.
        candidates = [s for s in self.sessions.values() if s.steam]
        for session in candidates:
            if session.is_ui and session.can_read_local_storage_flag:
                return session
        # Fall back to any client page that shares the storage.
        for session in candidates:
            if session.can_read_local_storage_flag:
                return session
        return None

    def _read_state_from_steam_client(self) -> bool | None:
        session = self._choose_steam_session_for_flag_read()
        if session is None:
            return None
        try:
            raw = session.evaluate("localStorage.getItem(%s)" % json.dumps(KEY))
            return storage_value_to_enabled(raw)
        except Exception as e:
            _debug("read :8080 flag failed: %s" % e)
            return None

    def _persist_enabled_change(self, enabled: bool) -> bool:
        if enabled == self.enabled:
            return False
        previous = self.enabled
        self.enabled = enabled
        try:
            write_persisted_enabled(self.state_file, enabled)
        except Exception as e:
            self.enabled = previous
            log("state write failed: %s; reverting" % e)
            return False
        return True

    def _apply_state_and_push_to_vr(self, enabled: bool, source: str) -> None:
        if self._persist_enabled_change(enabled):
            if source == "switch":
                log("switch -> %s" % enabled)
            else:
                _debug("state <- :8080 %s" % enabled)
        self._push_state_to_all_vr_sessions()

    def _sync_state_from_steam_client(self) -> None:
        enabled = self._read_state_from_steam_client()
        if enabled is None:
            return
        self._apply_state_and_push_to_vr(enabled, "sync")

    def _on_switch_toggled_by_user(self) -> None:
        enabled = self._read_state_from_steam_client()
        if enabled is None:
            # :8080 is the authority and could not be read. Keep the persisted
            # value; never persist the binding hint in its place.
            self._push_state_to_all_vr_sessions()
            return
        self._apply_state_and_push_to_vr(enabled, "switch")

    def _push_state_to_all_vr_sessions(self) -> None:
        # localStorage is per origin: one write per endpoint reaches every
        # page that shares it. data: pages have an opaque origin and are skipped.
        for endpoint in self.endpoints:
            if endpoint.kind != KIND_VR:
                continue
            for session in self.sessions.values():
                if session.endpoint is endpoint and session.shares_local_storage:
                    self._push_state_to(session)
                    break

    def _push_state_to(self, session: Session) -> None:
        value = json.dumps(self.enabled)
        try:
            session.evaluate("localStorage.setItem(%s, %s)" % (json.dumps(KEY), value))
            _debug("push -> %s %s" % (session.title, value))
        except Exception as e:
            # A reload may have invalidated the context. Target events will
            # attach and push again; do not delete the session permanently.
            _debug("push to %s failed: %s" % (session.title, e))


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


def status(state_file: str, steam_port: int, vr_port: int) -> int:
    def summarize_targets(targets: typing.Any, kind: str) -> dict[str, typing.Any]:
        pages = 0
        ui = False
        for target in targets:
            if not is_page_type_target(target):
                continue
            pages += 1
            # there is only one special ui page that we want to inject our switch
            if kind == KIND_STEAM and is_quick_access_menu(target.get("title"), target.get("url")):
                ui = True
        return {"pages": pages, "ui": ui}

    def endpoint(port: int, kind: str) -> dict[str, typing.Any]:
        info: dict[str, typing.Any] = {
            "port": port,
            "reachable": False,
            "pages": 0,
            "ui": False,
        }
        try:
            targets = http_get_json("http://127.0.0.1:%d/json/list" % port)
        except Exception:
            return info
        info["reachable"] = True
        info.update(summarize_targets(targets, kind))
        return info

    print(
        json.dumps(
            {
                "enabled": read_persisted_enabled(state_file),
                "stateFile": state_file,
                "steam": endpoint(steam_port, KIND_STEAM),
                "vr": endpoint(vr_port, KIND_VR),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="sf-ui-haptic-switch daemon")
    ap.add_argument(
        "--steamwebhelper-port",
        dest="steamwebhelper_port",
        type=int,
        default=DEFAULT_STEAM_PORT,
        help="Steam client DevTools port (default %d)" % DEFAULT_STEAM_PORT,
    )
    ap.add_argument(
        "--vrwebhelper-port",
        dest="vrwebhelper_port",
        type=int,
        default=DEFAULT_VR_PORT,
        help="SteamVR Dashboard DevTools port (default %d)" % DEFAULT_VR_PORT,
    )
    ap.add_argument(
        "--script-dir",
        default=here,
        help="directory holding the two .js scripts (default: this file's dir)",
    )
    ap.add_argument(
        "--state-file",
        default=None,
        help="state file (default: $XDG_STATE_HOME first, then ~/.local/state)",
    )
    ap.add_argument("--status", action="store_true", help="print the persisted value and endpoint status, then exit")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global VERBOSE
    args = parse_args(argv)
    VERBOSE = args.verbose
    state_file = args.state_file or default_state_file_path()

    if args.status:
        return status(state_file, args.steamwebhelper_port, args.vrwebhelper_port)

    try:
        source_map = read_script_sources(args.script_dir)
    except OSError as e:
        log("cannot read scripts from %s: %s" % (args.script_dir, e))
        return 2

    daemon = Daemon(
        args.steamwebhelper_port,
        args.vrwebhelper_port,
        source_map,
        state_file,
    )

    def stop(*_args: object) -> None:
        daemon.running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log("started; steam=:%d vr=:%d state=%s" % (args.steamwebhelper_port, args.vrwebhelper_port, state_file))
    return daemon.run()


if __name__ == "__main__":
    sys.exit(main())
