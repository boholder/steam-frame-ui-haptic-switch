# Controller UI Haptic — a switch for the Steam Frame

On the Steam Frame, the controller's virtual cursor fires a short haptic tick
every time it lands on a UI button. In the headset menus that feedback is
constant, and the system offers no way to turn it off.

This plugin adds one: the switch appears at the bottom of
**Quick Access Menu → Settings**, labelled **Controller UI Haptic**.

- **On** (the default): the stock behaviour — hovering a UI button ticks.
- **Off**: hovering a UI button no longer ticks — in the Steam client UI *and*
  in the SteamVR Dashboard.

Slider dragging, snapping and every other kind of feedback are left untouched;
only the button enter/leave effects are gated.

## Use

```bash
# run it on your Steam Frame
git clone "https://github.com/<owner>/steam-frame-ui-haptic-switch.git"
cd steam-frame-ui-haptic-switch
sudo ./install.sh
sudo ./uninstall.sh
```

After `install.sh`, restart SteamVR once so the Dashboard exposes its debugging
endpoint. The daemon retries on its own; no reinstall is needed.

## How it works

Both the Steam client UI and the SteamVR Dashboard are Chromium Embedded
Framework (CEF) pages with a local DevTools endpoint:

| Endpoint | Owner | Haptic bridge |
| --- | --- | --- |
| `127.0.0.1:8080` | Steam client (`steamwebhelper`) | `SteamClient.OpenVR.TriggerOverlayHapticEffect(effect, …)` |
| `127.0.0.1:8087` | SteamVR Dashboard (`vrwebhelper`) | `VRHTML.VROverlay.TriggerOverlayHapticEffect(handle, effect)` |

A resident daemon (`sf_ui_haptic_switch_daemon.py`) keeps one browser-level
CDP connection per endpoint. It uses `Target.setDiscoverTargets` and
`Target.attachToTarget {flatten:true}` to attach to every page as it appears —
no polling.

In every page it installs `sf_ui_haptic_switch_gate.js`. The gate wraps
whichever haptic bridge that page has and, while the switch is off, drops only:

```text
1 ButtonEnter   cursor enters a button / clickable row
2 ButtonLeave   cursor leaves a button / clickable row
```

`Snap(3)`, `Sliding(4)` and `SlidingEdge(5)` are deliberately left alone.

The switch itself (`sf_ui_haptic_switch_ui.js`) is injected into the Quick
Access Menu page only. Flipping it:

1. writes `localStorage['sf_ui_haptic_switch']` (`'1'`/`'0'`) — this is the
   single source of truth, and the same-page gate reacts immediately;
2. calls the `__sfUiHapticSwitchSend` CDP binding to tell the daemon.

The daemon reads the value back from `:8080`, persists it to
`~/.local/state/sf-ui-haptic-switch/state.json`, and pushes it into every
`:8087` page. It never writes back to `:8080`, so the switch cannot bounce.
There is no periodic polling: the daemon also re-syncs on startup, on
reconnect, and whenever a page gets a new execution context.

## Files

| Path | Role |
| --- | --- |
| `install.sh` / `uninstall.sh` | one-command install and uninstall |
| `src/sf_ui_haptic_switch_gate.js` | wraps both haptic bridges; gates the two button effects |
| `src/sf_ui_haptic_switch_ui.js` | renders the switch and notifies the daemon |
| `src/sf_ui_haptic_switch_daemon.py` | resident daemon (Python 3, stdlib only) |
| `src/sf_ui_haptic_switch_settings.py` | safe `steamvr.vrsettings` editor used by install/uninstall |
| `src/sf-ui-haptic-switch.service` | systemd daemon unit |

Installed layout:

```text
/home/steamos/sf-ui-haptic-switch/                    program + scripts
/etc/systemd/system/sf-ui-haptic-switch.service       daemon unit
/home/steamos/.local/state/sf-ui-haptic-switch/       persisted switch value
~/.config/openvr/config/steamvr.vrsettings            VRWebHelper.DebuggerEnabled
```

## Limitations

- `:8087` must be enabled through `VRWebHelper.DebuggerEnabled = true` in
  `steamvr.vrsettings` (install does this) and requires a SteamVR restart.
- The switch value lives in the Steam client profile / state file. Resetting
  that profile resets the switch to **on**.
- Only the two button-hover effects are gated. Slider and snap haptics are
  deliberately left alone.
- Haptics emitted by games and OpenXR applications are out of scope entirely.

## Logs and status

```bash
journalctl -u sf-ui-haptic-switch.service -f
systemctl status sf-ui-haptic-switch.service
python3 /home/steamos/sf-ui-haptic-switch/sf_ui_haptic_switch_daemon.py --status
```

`--status` prints the persisted value plus the reachability and page count of
both endpoints. Add `--verbose` to the daemon for attach/inject/push details.
