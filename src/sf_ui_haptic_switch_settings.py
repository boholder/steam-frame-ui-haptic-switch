#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Edit SteamVR's ``steamvr.vrsettings`` without disturbing anything else.

``--enable`` sets ``VRWebHelper.DebuggerEnabled = true`` and writes a ``.bak``
next to the file. ``--disable`` removes that key (and the now-empty
``VRWebHelper`` section) without restoring the backup.

The file keeps SteamVR's own style: three-space indent and ``"key" : value``.
The write is atomic and preserves the original owner and mode.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys

SECTION = "VRWebHelper"
DEBUGGER_KEY = "DebuggerEnabled"
CONFIG_OWNER = "steamos"


def _load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("root is not a JSON object")
    return data


def _write(path, data, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=3, separators=(",", " : "), sort_keys=True)
        f.write("\n")
    if st is not None:
        os.chmod(tmp, stat.S_IMODE(st.st_mode))
        if hasattr(os, "chown"):
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except OSError:
                pass
    os.replace(tmp, path)


def enable(path):
    """Set the debugger key; create the file if needed. Returns True."""
    st = os.stat(path) if os.path.exists(path) else None
    created_dir = None
    if st is not None:
        shutil.copy2(path, path + ".bak")
        data = _load(path)
    else:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, mode=0o755, exist_ok=True)
            created_dir = directory
        data = {}

    section = data.get(SECTION)
    if not isinstance(section, dict):
        section = {}
        data[SECTION] = section
    section[DEBUGGER_KEY] = True

    _write(path, data, st)

    if st is None:
        try:
            import pwd

            ent = pwd.getpwnam(CONFIG_OWNER)
            if created_dir:
                os.chown(created_dir, ent.pw_uid, ent.pw_gid)
            os.chown(path, ent.pw_uid, ent.pw_gid)
        except Exception:
            pass
    return True


def disable(path):
    """Remove the debugger key; leave the backup alone. Returns False if no file."""
    if not os.path.exists(path):
        return False
    st = os.stat(path)
    data = _load(path)

    changed = False
    section = data.get(SECTION)
    if isinstance(section, dict):
        if DEBUGGER_KEY in section:
            section.pop(DEBUGGER_KEY)
            changed = True
        if not section:
            data.pop(SECTION, None)

    # Do not reorder/rewrite a file that never had the key.
    if changed:
        _write(path, data, st)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true")
    group.add_argument("--disable", action="store_true")
    ap.add_argument("path")
    args = ap.parse_args(argv)

    try:
        if args.enable:
            enable(args.path)
            print("enabled")
        else:
            print("disabled" if disable(args.path) else "missing")
    except Exception as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
