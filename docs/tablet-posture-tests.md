# Tablet posture: manual test plan (Windows host)

Covers `touch_tablet_mode` (see docs/touch-posture.md). The host reports laptop or slate to Windows from client
posture; these tests check each trigger and the Windows result.

## Tools

- Host state: `GET https://<host>:47990/api/tablet-posture` (Bearer API token). Fields: `applied` (what Windows was
  told), `wanted`, `override`, `clients[]` with `touch_primary`.
- Simulate the keyboard without a client: `POST /api/tablet-posture` with `{"mode":"slate"}` (detached),
  `{"mode":"laptop"}` (attached), `{"mode":"auto"}` (clients decide again).
- Windows truth, PowerShell on the host:

  ```powershell
  (Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\PriorityControl).ConvertibleSlateMode   # 0 slate, 1 laptop
  ```

  Visible result: in a streamed Windows Terminal tap the prompt twice; slate shows the docked touch keyboard and the
  tablet taskbar, laptop shows neither.
- Log lines (sunshine.log): `Tablet posture: host is now slate (tablet)` / `laptop (desktop)`, `Tablet posture: override ...`,
  `Client [..] posture update ...`.
- Dell without a keyboard dock: `sudo scripts/sim-tablet-switch.py tablet|laptop|cycle` creates a virtual tablet-mode
  switch; KWin follows it and `moonlight-posture` reports the change like a real keyboard detach.

## Expected states

| Situation | `ConvertibleSlateMode` | `applied` |
|---|---|---|
| No stream | 1 (laptop) after the 3 s grace | laptop |
| A55 / phone / handheld / tablet class, or paired touch device | 0 | slate |
| `laptop` or `desktop` posture | 1 | laptop |
| Override `slate` / `laptop` | 0 / 1 | slate / laptop |

While slate the values `ConvertibilityEnabled` and `CSMIgnoreDisplayConfig` under `PriorityControl` are 1; afterwards they are
back to what they were (normally absent), and `tablet_posture.journal` in the config folder is gone.

## Tests

1. **Override, no client.** Before: `applied` laptop. POST `slate`: within 2 s `applied` slate, `ConvertibleSlateMode` 0, taskbar
   changes to tablet layout. POST `laptop`: back within 3 s (not earlier: 3 s grace only applies to client driven changes;
   override flips apply the same worker, expect up to 3 s). POST `auto`: stays laptop with no client.
2. **A55 launch.** Stream Desktop. `clients[]` shows the A55 with `touch_primary: true`, `applied` slate, tap a Windows Terminal
   prompt: docked keyboard. Stop the stream: laptop 3 s later, values restored.
3. **Stock client (ROG Ally, Artemis).** Client must be flagged `touch_device` (paired client list). Same as test 2.
4. **Keyboard detach and flip, Dell.** Start a stream in laptop posture (`applied` laptop). Detach the keyboard (or run
   `sudo scripts/sim-tablet-switch.py tablet`): the client posts `/posture`, `applied` becomes slate within about 2 s. Attach
   it (or `laptop`): laptop again after 3 s. Repeat flipping three times; the log must show one transition per flip and no error.
   Client option "Reconnect when posture changes" restarts the stream on a size boundary change; the host must still end in the
   right state (the 3 s grace hides the stop/start between the two sessions).
5. **Two clients.** Dell (laptop) and A55 streaming: slate. Stop the A55: laptop after 3 s while the Dell keeps streaming.
6. **Disabled.** `touch_tablet_mode = disabled`, restart the service, stream from the A55: `applied` stays laptop, `enabled`
   false in the API, no registry change.
7. **Crash recovery.** Slate from a stream, kill the host process (`taskkill /f /im sunshine.exe`), start it again: log line
   `restoring the state left by a previous host run`, `ConvertibleSlateMode` 1, declaration values removed, journal gone.
8. **Uninstall.** After uninstalling, `Get-PnpDevice | ? HardwareID -contains PNP0C60` lists no `ROOT\HIDCLASS` node.

## Known limits

- Needs administrator rights for the host service (the HKLM declaration and the device).
- The virtual display must be the only active display; a display positioned away from the origin keeps Windows out of tablet posture.
- WPF text boxes (for example in Playnite) do not auto-show the keyboard even in slate; that is the application.
