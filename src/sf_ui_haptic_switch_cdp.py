#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal CDP transport for the sf-ui-haptic-switch daemon.

One :class:`Endpoint` owns a browser-level WebSocket, a reader thread and a
pending-request map. The daemon consumes events from the shared queue and
sends commands with an optional flattened ``sessionId``.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import threading
import urllib.request


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
        # Send the close frame while the socket is still open, then mark it
        # closed even if the peer is already gone.
        try:
            self._send_frame(b"", 0x8)
        except Exception:
            pass
        finally:
            self._closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def http_json(url, timeout=5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def describe_command(method, params=None):
    """One-line, bounded representation for --verbose command logging."""
    text = json.dumps(params or {}, ensure_ascii=False, sort_keys=True)
    if len(text) > 200:
        text = text[:197] + "..."
    return "command %s %s" % (method, text)


class Endpoint:
    """One browser-level CDP connection plus its reader thread."""

    def __init__(self, port, events, kind, debug=None):
        self.port = port
        self.events = events
        self.kind = kind
        self._debug = debug or (lambda *a: None)
        self.ws = None
        self.reader = None
        self.alive = False
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
        ws = WebSocket(ws_url)
        self.ws = ws
        self.alive = True
        self.reader = threading.Thread(
            target=self._read_loop, args=(ws,),
            name="cdp-%d" % self.port, daemon=True)
        self.reader.start()
        self.call("Target.setDiscoverTargets", {"discover": True})

    def _read_loop(self, ws):
        try:
            while self.alive and self.ws is ws:
                text = ws.recv_text()
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
            self._debug("endpoint :%d reader stopped: %s" % (self.port, e))
        finally:
            # Only the reader that owns the current socket may flip the state;
            # a stale reader must not clobber a freshly reconnected endpoint.
            if self.ws is ws:
                self.alive = False
                self._fail_pending(
                    WSError("endpoint :%d disconnected" % self.port))

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
        self._debug(describe_command(method, params))
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
