# Controller UI Haptic — a switch for the Steam Frame

**English** | [简体中文](docs/README_ZH.md) | [日本語](docs/README_JP.md)

> [!NOTE]
> The author has personally reviewed all of the code.

> [!NOTE]
> The **Steam** tab that appears after connecting to SteamVR, and the native
> Dashboard frame controls, are not covered by the default switch. The optional
> [Aggressive UI haptics off (optional)](#aggressive-ui-haptics-off-optional)
> script can mute all laser-mouse UI haptics, including these.
> It requires root, also mutes sliders/snap/edges, and leaves game/OpenXR haptics untouched.

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

1. writes `localStorage['sf_ui_haptic_switch']` (`true`/`false`) — this is the
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
| `install.sh` / `uninstall.sh` | run in the checkout; install/remove the user unit and its SteamVR link, does not copy the repo |
| `src/sf_ui_haptic_switch_gate.js` | wraps both haptic bridges; gates the two button effects |
| `src/sf_ui_haptic_switch_ui.js` | renders the switch and notifies the daemon |
| `src/sf_ui_haptic_switch_daemon.py` | resident daemon and CDP transport (Python 3, stdlib only) |
| `src/sf_ui_haptic_switch_settings.py` | safe `steamvr.vrsettings` editor used by install/uninstall |
| `installation_lib.sh` | shared install/uninstall paths, user/systemd helpers, and output helpers |
| `src/sf-ui-haptic-switch.service` | user systemd daemon unit (follows `steamvr.service`) |

Installed layout:

```text
<this checkout>/                                      source + scripts (install runs in place)
~/.config/systemd/user/sf-ui-haptic-switch.service    user daemon unit, points at the checkout
~/.config/systemd/user/steamvr.service.wants/         enable link: starts the daemon with SteamVR
~/.local/state/sf-ui-haptic-switch/                   persisted switch value
~/.config/openvr/config/steamvr.vrsettings            VRWebHelper.DebuggerEnabled
```

The service is a **user** unit (`systemctl --user`), because SteamVR is a user
service. It is ordered after `steamvr.service` and `PartOf=steamvr.service`, so
it starts with SteamVR and stops or restarts with it. `install.sh` writes the
unit and creates the `steamvr.service.wants` symlink; `uninstall.sh` removes
both. It runs as the desktop user even when invoked through `sudo`; override
with `sudo SF_USER=<user> ./install.sh` if that user is not `steamos`.

The service executes `src/sf_ui_haptic_switch_daemon.py` directly from the
checkout. Moving or deleting the checkout requires a reinstall.

`install.sh` runs in place: it sets `HERE` to this checkout, sources
`installation_lib.sh` (which sets `DEST="$HERE"`), and writes the unit with
`INSTALLATION_PATH` replaced by the checkout path. It copies no repository
files. `uninstall.sh` removes the unit, its symlink, state and
`VRWebHelper.DebuggerEnabled`, but leaves the checkout.

## Limitations

- `:8087` must be enabled through `VRWebHelper.DebuggerEnabled = true` in
  `steamvr.vrsettings` (install does this) and requires a SteamVR restart.
- The switch value lives in the Steam client profile / state file. Resetting
  that profile resets the switch to **on**.
- Only the two button-hover effects are gated. Slider and snap haptics are
  deliberately left alone.
- Haptics emitted by games and OpenXR applications are out of scope entirely.

## Aggressive UI haptics off (optional)

The default switch gates only `ButtonEnter`/`ButtonLeave` in CEF pages. Dashboard
native frame controls and the remote Steam tab use SteamVR's native
`lasermouse` haptic action, so they still tick.

`scripts/aggressive-ui-haptics.sh` masks that action with a bind mount over the
read-only SteamOS root file:

```bash
cd steam-frame-ui-haptic-switch
sudo ./scripts/aggressive-ui-haptics.sh apply
sudo ./scripts/aggressive-ui-haptics.sh persist   # recreate at boot
sudo ./scripts/aggressive-ui-haptics.sh revert    # undo
```

- Mutes **all** laser-mouse UI haptics (hover, sliders, snap, slider edges),
  including native Dashboard controls and the remote Steam tab.
- Does **not** affect game / OpenXR / Steam Input haptics.
- Requires root. `persist` installs `sf-ui-haptic-binding.service`.
- The QAM switch does not control this mode.
- After a SteamVR update, run `revert` then `apply` to refresh the copy.

## Logs and status

```bash
journalctl --user-unit sf-ui-haptic-switch.service -f
systemctl --user status sf-ui-haptic-switch.service
python3 src/sf_ui_haptic_switch_daemon.py --status
```

`--status` prints the persisted value plus the reachability and page count of
both endpoints. Add `--verbose` to the daemon for attach/inject/push details.
