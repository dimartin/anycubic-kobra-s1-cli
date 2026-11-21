# kobra TODO

Ideas borrowed from other Anycubic projects (surveyed 2026-11-20). Their features are as described in those
projects and haven't all been checked against their source.

## 1. `kobra camera`
- **From:** [metheos/anycubic-s1-mqtt-bridge](https://github.com/metheos/anycubic-s1-mqtt-bridge): the S1 streams FLV at `http://<ip>:18088/flv`.
- **Task:** new subcommand. Print the stream URL by default; `--snapshot FILE` grabs one frame with `ffmpeg -frames:v 1`.
- **Why:** check a print (first layer, stringing) without walking to the printer.
- **Check first:** whether the stream needs auth or the camera to be enabled on the touchscreen.

## 2. Dryer presets + temperature cap
- **From:** decay71/multiACE (55 C / 240 min default, 70 C hard cap) and hakimio/U1-Ace (caps at 55 C).
- **Task:** in the `dry` subcommand add `--preset pla|petg|tpu|...` (PLA 45-50 C, PETG 55 C) and refuse `--temp` above 55 C
  unless `--force`. Find the ACE Pro's own max before choosing the cap.
- **Non-goal:** humidity-regulated drying (the ACE reports `humidity: 0` over LAN, so there's nothing to regulate on).

## 3. Automatic slot matching in `send`
- **From:** [justinh-rahb/ACEPRO](https://github.com/justinh-rahb/ACEPRO): exact match, then same material, then next ready slot.
- **Task:** make `--slot` optional. Read the slots (`ace_slots()`), match the `--filament` material (and colour if given),
  and refuse if more than one slot matches equally rather than guessing. Print which slot was chosen.
- **Non-goal:** endless spool / runout switching (needs printer-side support).

## 4. `kobra diag`
- **From:** ljschmitt/hass-anycubic_cloud_v3 (diagnostics export); grunna/ha-anycubic-kobra-x-lan warns that the local protocol may change.
- **Task:** dump `/info`, one `info/query` reply and one `multiColorBox/getInfo` reply to a JSON file, with credentials,
  tokens, device keys and URLs carrying secrets removed. Include the firmware version and the time.
- **Why:** after a firmware update, diff two diag files to see what changed.

## 5. Adaptive polling in `watch`
- **From:** metheos/anycubic-s1-mqtt-bridge (separate idle/busy snapshot intervals).
- **Task:** if `watch` polls anything, slow down while `state: free` and speed up while busy; add `--interval`.
  Check first whether `watch` only listens to pushed reports, in which case this item doesn't apply.
