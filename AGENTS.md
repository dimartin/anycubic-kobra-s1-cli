# Instructions for AI coding agents

`kobra` controls an Anycubic Kobra S1 and its ACE Pro over the printer's LAN mode. Read the README first; it is the
user-facing contract, and changes to behaviour must keep it true.

## Layout
- `kobra_s1/cli.py`: the LAN protocol (handshake, MQTT, upload) and the `kobra` command.
- `kobra_s1/slicer.py`: optional slicing through OrcaSlicer's command line, including the pinned download.
- `tests/`: offline tests. Run `python -m unittest`; they need no printer, network or OrcaSlicer.

## Rules

**A real printer is hardware that heats up and moves.**
- Never run `send`, `start`, `pause`, `resume`, `cancel` or `dry on` against a printer unless the person you are
  working for asks for that specific action. `status`, `dry` with no argument, and `watch` only read.
- Tests and examples must not need a printer, and must not contact one when `KOBRA_HOST` is unset.

**Don't leak credentials.**
- The `/info` reply holds the printer's master token, the handshake yields MQTT credentials, and
  `kobra --json status` prints the upload URL, whose query string is an access token, and the printer's IP.
- Never commit, log or paste these. Redact them from example output, issues and test fixtures.

**A missing reply is a failure.**
- The printer can stay silent or answer late. Code that acts on the printer (starting a print, changing the dryer)
  must refuse when it gets no reply, never treat silence as "idle" or "accepted".
- Replies don't echo the request's `msgid`. A reply carries the action it answers (`getInfo`, `setDry`, ...), and
  a query is answered by a plain `report`. Any other `report` is unrelated status and must not be taken as a reply.
  `tests/test_kobra.py` covers this; keep it passing.

**Keep the core Python-only.**
- Runtime dependencies are `requests`, `paho-mqtt` and `cryptography`. Adding one needs a strong reason.
- Everything that needs OrcaSlicer stays in `slicer.py`, and only `slice` and `send` with a model file reach it.

**OrcaSlicer is pinned.**
- To move to a new version, change `ORCA_VERSION` and both `ORCA_APPIMAGES` checksums together. Compute each
  checksum from the official release file yourself; never copy one from a web page or issue.
- Re-test slicing from an empty cache (`XDG_CACHE_HOME=$(mktemp -d)`), and update the version and sizes in the README.

**The protocol is undocumented.**
- When you change how `kobra` talks to the printer, say in the commit message which printer and firmware version you
  checked it on, or that you couldn't check it.
- Don't present a guess about the protocol as fact in code comments or the README.

**Style and licence**
- Match the surrounding code: plain functions, short comments that explain why, and messages that tell the user what
  to do next.
- The project is AGPL-3.0-or-later, and parts are adapted from ianloic/anycubic-orca-plugin. Contributions are under
  the same licence, and borrowed code must be credited in the README.
