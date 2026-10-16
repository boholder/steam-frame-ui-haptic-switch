#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sf-ui-haptic-switch injector — one-shot, run by a systemd timer.

Enumerates the Steam client UI DevTools endpoint (default ``127.0.0.1:8080``),
and for every ``type=="page"`` target injects:

- ``sf_ui_haptic_switch_gate.js`` into **every** page (it wraps
  ``SteamClient.OpenVR.TriggerOverlayHapticEffect`` and gates it on the
  ``localStorage`` flag) — plus
- ``sf_ui_haptic_switch_ui.js`` into the ``barpopup`` page only (it builds the
  on-screen switch).

Injection is idempotent: the current build marker is probed first, and the
script is only re-injected when it differs (the page was reloaded, or the script
changed). The injector holds **no state** and keeps **no connection** — page
state lives in ``localStorage``. It exits silently (code 0) when the endpoint is
down or has no page targets; only genuine errors return non-zero.

Python 3, stdlib only (the device has no websocket library).

This module separates the **pure decision logic** (unit-tested in
``tests/test_inject.py``) from the network **adapter**:
``build_plan`` / ``probe_expr`` / ``inject_expr`` / ``build_marker`` are pure;
``CdpTransport`` does HTTP + RFC6455 + CDP.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

DEFAULT_PORT = 8080
GLOBAL = "__SF_UI_HAPTIC_SWITCH__"
GATE_JS = "sf_ui_haptic_switch_gate.js"
UI_JS = "sf_ui_haptic_switch_ui.js"
BARPOPUP_MATCH = "barpopup"
SLOT_GATE = "gate"
SLOT_UI = "ui"

VERBOSE = False


def log(*a):
    print("[sf-ui-haptic-switch]", *a, file=sys.stderr, flush=True)


def _debug(*a):
    if VERBOSE:
        log(*a)


# --------------------------------------------------------------------------
# pure decision logic (the test seam)
# --------------------------------------------------------------------------

def build_marker(source):
    """Stable short content hash used as the per-slot build marker."""
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]


def probe_expr(slot):
    """JS expression returning the current build marker for ``slot`` (or '')."""
    return (
        "((window.%s && window.%s.build && window.%s.build[%s]) || '')"
        % (GLOBAL, GLOBAL, GLOBAL, json.dumps(slot))
    )


def inject_expr(source, slot, marker):
    """The source, followed by an epilogue that stamps ``marker`` into ``slot``."""
    epilogue = (
        ";try{window.%s=window.%s||{};window.%s.build=window.%s.build||{};"
        "window.%s.build[%s]=%s;}catch(e){}"
        % (GLOBAL, GLOBAL, GLOBAL, GLOBAL, GLOBAL, json.dumps(slot), json.dumps(marker))
    )
    return source + epilogue


def select_scripts(title, url):
    """Which scripts a page needs. The gate goes to every page; ui to barpopup."""
    if BARPOPUP_MATCH in (title or "") + (url or ""):
        return [SLOT_GATE, SLOT_UI]
    return [SLOT_GATE]


def build_plan(targets):
    """Pure: DevTools target list -> ordered list of (ws_url, [script_slots])."""
    plan = []
    for t in targets:
        if t.get("type") != "page":
            continue
        ws = t.get("webSocketDebuggerUrl")
        if not ws:
            continue
        plan.append((ws, select_scripts(t.get("title"), t.get("url"))))
    return plan


TEARDOWN_EXPR = (
    "(function(){try{var n=window.%s;if(n){"
    "if(n.ui&&n.ui.teardown){n.ui.teardown();}"
    "if(n.gate&&n.gate.teardown){n.gate.teardown();}}"
    "delete window.%s;}catch(e){}})()" % (GLOBAL, GLOBAL)
)


def teardown_all(transport, plan):
    """Best-effort removal of the injected UI from every planned target."""
    done = 0
    for ws_url, _slots in plan:
        try:
            transport.evaluate(ws_url, TEARDOWN_EXPR)
            done += 1
        except Exception as e:
            _debug("teardown failed for %s: %s" % (ws_url, e))
    return done


def run(transport, plan, sources):
    """Inject each planned (ws_url, slots). Errors are per-target isolated."""
    injected = 0
    for ws_url, slots in plan:
        for slot in slots:
            src = sources.get(slot)
            if src is None:
                _debug("no source for slot %r; skipping" % slot)
                continue
            marker = build_marker(src)
            try:
                current = transport.evaluate(ws_url, probe_expr(slot))
            except Exception as e:  # target gone / socket died — skip it
                _debug("probe failed for %s [%s]: %s" % (ws_url, slot, e))
                break
            if current == marker:
                continue
            try:
                transport.evaluate(ws_url, inject_expr(src, slot, marker), timeout=20.0)
                injected += 1
                _debug("inject -> %s [%s] build %s" % (ws_url, slot, marker))
            except Exception as e:
                _debug("inject failed for %s [%s]: %s" % (ws_url, slot, e))
                break
    return injected


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

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, payload, opcode=0x1):
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
        try:
            self._send_frame(b"", 0x8)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class CDP:
    """One short-lived CDP session over a WebSocket. No events are consumed."""

    def __init__(self, ws_url, timeout=10.0):
        self.ws = WebSocket(ws_url, timeout=timeout)
        self._id = 0

    def call(self, method, params=None, timeout=10.0):
        self._id += 1
        mid = self._id
        self.ws.send_text(json.dumps(
            {"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            if time.time() > deadline:
                raise WSError("timeout waiting for %s" % method)
            msg = json.loads(self.ws.recv_text())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise WSError("%s: %s" % (method, msg["error"]))
                return msg.get("result", {})

    def evaluate(self, expr, timeout=10.0):
        res = self.call("Runtime.evaluate",
                        {"expression": expr, "returnByValue": True,
                         "userGesture": True, "awaitPromise": False},
                        timeout=timeout)
        if res.get("exceptionDetails"):
            raise WSError("js exception: %s" % json.dumps(
                res["exceptionDetails"], ensure_ascii=False)[:400])
        return res.get("result", {}).get("value")

    def close(self):
        self.ws.close()


# --------------------------------------------------------------------------
# network adapter
# --------------------------------------------------------------------------

def http_json(url, timeout=5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


class CdpTransport:
    """Real adapter: list targets over HTTP, evaluate over a per-call WebSocket."""

    def __init__(self, port=DEFAULT_PORT):
        self.port = port

    def list_targets(self):
        return http_json("http://127.0.0.1:%d/json/list" % self.port)

    def evaluate(self, ws_url, expr, timeout=10.0):
        cdp = CDP(ws_url, timeout=timeout)
        try:
            return cdp.evaluate(expr, timeout=timeout)
        finally:
            cdp.close()


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def load_sources(script_dir):
    sources = {}
    for slot, name in ((SLOT_GATE, GATE_JS), (SLOT_UI, UI_JS)):
        path = os.path.join(script_dir, name)
        with open(path, encoding="utf-8") as f:
            sources[slot] = f.read()
    return sources


def main(argv=None):
    global VERBOSE

    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="sf-ui-haptic-switch one-shot injector")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help="Steam client DevTools port (default %d)" % DEFAULT_PORT)
    ap.add_argument("--script-dir", default=here,
                    help="directory holding the two .js scripts (default: this file's dir)")
    ap.add_argument("--teardown", action="store_true",
                    help="remove the injected UI (all page targets) and exit")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    VERBOSE = args.verbose

    transport = CdpTransport(args.port)
    try:
        targets = transport.list_targets()
    except (urllib.error.URLError, OSError) as e:
        # Endpoint not up yet (Steam not running). Not an error — exit silently.
        _debug("endpoint :%d not reachable: %s" % (args.port, e))
        return 0
    except Exception as e:
        log("unexpected error listing targets on :%d: %s" % (args.port, e))
        return 1

    plan = build_plan(targets)
    if not plan:
        _debug("no page targets on :%d" % args.port)
        return 0

    if args.teardown:
        n = teardown_all(transport, plan)
        _debug("tore down %d target(s) on :%d" % (n, args.port))
        return 0

    try:
        sources = load_sources(args.script_dir)
    except OSError as e:
        log("cannot read scripts from %s: %s" % (args.script_dir, e))
        return 2

    injected = run(transport, plan, sources)
    _debug("done: %d injection(s) across %d target(s)" % (injected, len(plan)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
