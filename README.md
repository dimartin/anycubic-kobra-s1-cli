# kobra: command-line control for the Anycubic Kobra S1 and ACE Pro (LAN mode, no cloud)

`kobra` prints to an **Anycubic Kobra S1** from a Linux terminal over your own network. It talks to the printer's
**LAN mode** directly: no Anycubic cloud account, no app, and no custom firmware or rooting.

- **Send G-code and start a print**, choosing which **ACE Pro** slot to feed from.
- **Slice STL, 3MF or STEP models** with the stock Kobra S1 profiles, plus fixes for warping and stringing (optional).
- **Check status**: temperatures, job progress, and what's loaded in each ACE Pro slot.
- **Pause, resume or cancel** a print.
- **Run the ACE Pro filament dryer** without the touchscreen.
- **Watch** the printer's live reports.

```console
$ kobra send bracket.stl --slot 4
using slot 4: PLA+
slicing bracket.stl with Anycubic PLA+ @Anycubic Kobra S1 0.4 nozzle ...
sliced -> bracket.gcode: 1h 29m 55s, 34.38 g, 91 layers, 2 walls, 15% infill
uploading bracket.gcode (6.0 MB) ...
stored on printer as bracket.gcode
```

Tested on a Kobra S1 with an ACE Pro, firmware 2.7.2.7, from Linux. Other Kobra models with a LAN mode may work for
sending and status, but haven't been tried, and slicing only knows the Kobra S1 profiles.

## What you need

| | |
|---|---|
| **Always** | Python 3.10 or newer, and the printer on the same network in LAN mode. |
| **Only for slicing models** | OrcaSlicer. You don't have to install it: see [Slicing and OrcaSlicer](#slicing-and-orcaslicer). |

`kobra` itself depends only on three Python libraries: `requests`, `paho-mqtt` and `cryptography`. Sending G-code,
status, pause/resume/cancel, the dryer and `watch` never touch a slicer.

## Install

```sh
pipx install git+https://github.com/dimartin/anycubic-kobra-s1-cli
```

Or, without pipx:

```sh
git clone https://github.com/dimartin/anycubic-kobra-s1-cli
cd anycubic-kobra-s1-cli
python3 -m venv .venv && .venv/bin/pip install -e .
ln -s "$PWD/.venv/bin/kobra" ~/.local/bin/kobra
```

## Set up the printer

1. On the printer's touchscreen, turn on **LAN mode** in the network settings. Anycubic's
   [LAN connection guide](https://wiki.anycubic.com/en/fdm-3d-printer/kobra-s1/lan-connection-guide) shows
   where. LAN mode switches off the printer's cloud connection; that's what lets `kobra` talk to it.
2. Note the printer's **IP address** from the same network screen. Giving the printer a fixed address in your router
   saves looking it up again.
3. Tell `kobra` where it is, once per shell or in your `~/.profile`:

   ```sh
   export KOBRA_HOST=192.168.1.50      # your printer's address
   ```

   or pass `--host 192.168.1.50` on any command.
4. Check the connection:

   ```console
   $ kobra status
   printer : Anycubic Kobra S1 (Anycubic Kobra S1) fw 2.7.2.7
   state   : free
   nozzle  : 23 -> 0 C
   bed     : 21 -> 0 C
   slot 1 : PLA          #212721
   slot 2 : (empty)      #000000
   slot 3 : (empty)      #000000
   slot 4 : PLA+         #eff0f1
   ```

## Commands

### `kobra status`
Temperatures, the current job (file, percent, layer, minutes left) and each ACE Pro slot's material and colour.
`kobra --json status` prints the printer's raw report.

### `kobra send FILE --slot N`
Uploads a file and starts printing it from ACE Pro slot `N` (1–4).

- A `.gcode` file is sent as it is, so G-code from **any slicer** works: Anycubic Slicer Next, OrcaSlicer,
  PrusaSlicer and so on.
- Any other file (`.stl`, `.3mf`, `.step`) is sliced first, with the material loaded in that slot.
  See [Slicing](#slicing-and-orcaslicer).
- `--slot` is required when an ACE Pro is attached. `kobra` shows the slots and stops rather than guessing.
- It refuses to send while the printer is busy.

| Option | What it does |
|---|---|
| `--no-start` | Upload only. |
| `--no-level` | Skip the automatic bed levelling before the print. |
| `--filament NAME` | Slice with a different profile from the slot's material, e.g. `PLA` for a faster silk print. |
| `--walls N`, `--infill PCT` | Wall count and infill percentage, when slicing. |
| `--temp C` | Nozzle temperature, when slicing (the first layer gets +5 C). |
| `--retract MM` | Retraction length, when slicing. Raise it a little if you see stringing. |

### `kobra slice FILE`
Slices a model to G-code without contacting the printer. It takes the same `--filament`, `--walls`, `--infill`,
`--temp` and `--retract` options, plus `-o out.gcode` (the default is next to the model).

```sh
kobra slice bracket.stl --filament PETG --walls 4 --infill 25
```

### `kobra start FILE --slot N`
Starts a file that's already on the printer. Give the local copy too: the printer wants its size and checksum.

### `kobra pause`, `kobra resume`, `kobra cancel`
Act on the current print.

### `kobra dry [on|off]`
Shows, starts or stops the ACE Pro's filament dryer.

```console
$ kobra dry on --temp 50 --hours 4
ACE 0: 31 C, dryer ON -> 50 C, 240 of 240 min left
$ kobra dry off
ACE 0: 31 C, dryer off
```

`--temp` defaults to 50 C (allowed 35–55, the most Anycubic quotes for the ACE Pro) and `--hours` to 4 (allowed 0.5–24). Typical settings are 45–50 C for PLA and 55 C for PETG. The dryer
keeps running when a print starts.

### `kobra watch`
Prints every report the printer sends, one line each, until Ctrl-C. Useful for seeing what the printer is doing or
for working out a message nobody has documented yet.

## Slicing and OrcaSlicer

**Slicing is the only part of `kobra` that needs other software.** Turning a model into G-code is a large job that
OrcaSlicer does well, so `kobra slice` (and `kobra send` with a model file) runs OrcaSlicer's own command line with
Anycubic's stock Kobra S1 profiles, which ship inside OrcaSlicer.

**You don't need to install OrcaSlicer.** `kobra` finds one in this order:

1. `--orca PATH` on the command line, or the `KOBRA_ORCA` environment variable. `PATH` can be an unpacked
   OrcaSlicer folder (the one containing `AppRun`) or an OrcaSlicer `.AppImage` file (made executable with `chmod +x`), which `kobra` unpacks once into its cache.
2. Otherwise, the first time you slice, `kobra` **downloads OrcaSlicer 2.4.2** itself:
   - the official Linux AppImage from
     [OrcaSlicer's GitHub releases](https://github.com/OrcaSlicer/OrcaSlicer/releases/tag/v2.4.2), for x86_64 or
     aarch64;
   - about 140 MB to download, about 380 MB once unpacked;
   - checked against a SHA-256 checksum written in `kobra`'s source, and thrown away if it doesn't match;
   - unpacked into `~/.cache/kobra/orca-2.4.2/` (or `$XDG_CACHE_HOME/kobra/`), with no FUSE needed and nothing
     installed system-wide. Later slices reuse it. Delete that folder to remove it.

The version is **pinned on purpose**: an OrcaSlicer update can change how prints come out, so `kobra` only moves to a
new version when its source is updated and re-tested. If you point `--orca` at a different version, it will probably
work, but the profiles and results may differ.

On macOS or Windows there's no automatic download. Install OrcaSlicer and point `--orca` at it, or slice in any
slicer and `kobra send` the `.gcode`.

### What `kobra` changes in the stock profiles

`kobra` slices with Anycubic's own Kobra S1 machine, process (0.20 mm Standard) and filament profiles, which match
the ones in Anycubic Slicer Next. It changes only these:

| Setting | Stock (OrcaSlicer command line) | `kobra` | Why |
|---|---|---|---|
| Bed plate type | Cool Plate (35 C bed) | **Textured PEI** (55 C for PLA) | The S1 ships with a textured PEI plate. At 35 C, prints warp and come loose. |
| Avoid crossing walls | Off | **On** | Off, the nozzle travels straight across open slots and holes and leaves strings inside them. |
| Profile inheritance | Not resolved by OrcaSlicer's command line | **Flattened** | Without this, OrcaSlicer silently falls back to slow default settings. |

Plus whatever you pass with `--walls`, `--infill`, `--temp` and `--retract`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Connection refused` on every command | The printer sometimes stops answering on its LAN ports (18910 and 9883). Turn it off and on again. |
| `printer is not in LAN mode` | Turn on LAN mode on the touchscreen (see [Set up the printer](#set-up-the-printer)). |
| `an ACE Pro is attached: choose the filament with --slot N` | Add `--slot` with the slot holding the filament you want. |
| `printer is busy` | A print is running or finishing. Wait, or `kobra cancel`. |
| First layer warps or won't stick | Make sure the G-code came from `kobra slice` or a slicer set to the textured PEI plate, and that the plate is clean. |
| Strings across gaps | Dry the filament (`kobra dry on`), then try `--temp` 5 C lower and `--retract` 0.2–0.4 mm longer. |
| `status` shows an old job's progress right after `send` | It's the previous job's leftover report. It updates once the new print gets going. |

## How it works

The printer's LAN mode is undocumented. `kobra` follows what the community has worked out:

1. `GET http://PRINTER:18910/info` returns the printer's details, a token and the upload address.
2. A signed request to the printer's control address returns MQTT credentials, encrypted with AES-128-CBC using part
   of that token.
3. `kobra` connects to the printer's MQTT broker over TLS on port 9883 and exchanges JSON messages with it: status
   reports, print commands, ACE Pro slot information and the dryer.
4. G-code is uploaded to the printer with an HTTP POST.

The printer uses a self-signed certificate, so `kobra` doesn't verify it. That's fine on a home network, but don't
expose the printer's ports to the internet.

## Credits

The protocol was worked out by others, and `kobra`'s handshake, upload and print-start code is adapted from
[ianloic/anycubic-orca-plugin](https://github.com/ianloic/anycubic-orca-plugin) (AGPL-3.0-or-later). The MQTT
message documentation in [rvanderp3/kobra-connect](https://github.com/rvanderp3/kobra-connect) (Apache-2.0) was
also a reference. Slicing is done by [OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer) (AGPL-3.0), which `kobra`
runs but doesn't include.

## Licence

[AGPL-3.0-or-later](LICENSE), the same as the plugin it builds on.

## Plans

See [TODO.md](TODO.md): camera snapshots, dryer presets with a temperature limit, choosing the ACE Pro slot
automatically, and a diagnostics dump for comparing firmware versions.


## Development

```sh
python -m unittest      # offline tests: no printer, network or OrcaSlicer needed
```

Not affiliated with or endorsed by Anycubic. "Anycubic", "Kobra" and "ACE Pro" are Anycubic's trademarks.
