# Controller UI Haptic — a switch for the Steam Frame

On the Steam Frame, the controller's virtual cursor fires a short haptic tick
every time it lands on a UI button. In the headset menus that feedback is
constant, and the system offers no way to turn it off.

This plugin adds one: The switch appears at the bottom of 
**Quick Access Menu → Settings**, labelled **Controller UI Haptic**.

- **On** (the default): the stock behaviour — hovering a UI button ticks.
- **Off**: hovering a UI button no longer ticks.

It runs without a resident process — a systemd timer periodically injects a
small script into the client's pages, and the switch and the effect gate share a
single value through the pages' `localStorage`.
Slider dragging, snapping and every other kind of feedback are left untouched;
only the button enter/leave effects are gated.

## Use

```bash
# run it in your steam frame
git clone "https://github.com/<owner>/steam-frame-ui-haptic-switch.git"
cd steam-frame-ui-haptic-switch
sudo ./install.sh
sudo ./uninstall.sh
```

## How it works

The Steam client's interface is a set of Chromium Embedded Framework (CEF)
pages. The client exposes a DevTools endpoint on `127.0.0.1:8080` that speaks
the Chrome DevTools Protocol (CDP).

`sf-ui-haptic-switch.timer` fires `sf-ui-haptic-switch.service` about every 3
seconds (and 15 seconds after boot). The service is a **one-shot**: it runs
`sf_ui_haptic_switch_inject.py`, which lists the client's pages over CDP and
injects a script into each, then exits. There is no long-running process and
nothing is left running between ticks.

Two scripts are injected:

- **`sf_ui_haptic_switch_gate.js`** goes into *every* page. It wraps
  `SteamClient.OpenVR.TriggerOverlayHapticEffect` — the bridge the UI calls to
  fire a haptic — and, while the switch is off, drops the two button effects
  (`ButtonEnter`, `ButtonLeave`). The value is read on every call, so flipping
  the switch takes effect immediately, without re-injecting.
- **`sf_ui_haptic_switch_ui.js`** goes into the Quick Access Menu page only. It
  renders the switch and writes the value to `localStorage`.

Every client UI page shares one origin (`https://steamloopback.host`), so they
share `localStorage`: the switch writes the value once and every page's gate
reads the same one. A page reload wipes injected scripts, and the timer
re-injects them within about 3 seconds.

## Files

| Path | Role |
| --- | --- |
| `install.sh` / `uninstall.sh` | one-command install and uninstall |
| `src/sf_ui_haptic_switch_gate.js` | wraps the haptic bridge in every page; gates the button effects on the flag |
| `src/sf_ui_haptic_switch_ui.js` | renders the switch in the Quick Access Menu page |
| `src/sf_ui_haptic_switch_inject.py` | the one-shot injector (Python 3, stdlib only) |
| `src/sf-ui-haptic-switch.service` | one-shot systemd service that runs the injector |
| `src/sf-ui-haptic-switch.timer` | drives the service about every 3 seconds |

Installed layout:

```
/home/steamos/sf-ui-haptic-switch/                 program + scripts
/etc/systemd/system/sf-ui-haptic-switch.service    unit
/etc/systemd/system/sf-ui-haptic-switch.timer      timer
```

## Limitations

- It covers the **Steam client UI pages** (`:8080`). The SteamVR dashboard
  pages (`:8087`) are **not** covered yet — that endpoint is not exposed by
  default. Haptics emitted by games and OpenXR applications are out of scope
  entirely.
- Only the two button-hover effects are gated. Slider and snap haptics are
  deliberately left alone.
- The switch value lives in the Steam client's profile. Resetting that profile
  resets the switch to **on**.

## Logs

Each tick is logged by the service:

```bash
journalctl -u sf-ui-haptic-switch.service -f
systemctl status sf-ui-haptic-switch.timer
```

With `--verbose`, the injector also prints what it injected:

```bash
python3 src/sf_ui_haptic_switch_inject.py --verbose
```
