"""Optional slicing for kobra: turns STL/3MF/STEP models into G-code with OrcaSlicer's command line.

Nothing else in kobra needs this. If OrcaSlicer isn't given with --orca or $KOBRA_ORCA, the first slice
downloads the pinned official AppImage, checks its SHA-256, unpacks it under the cache directory and reuses it.
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile

import requests

ORCA_VERSION = "2.4.2"
ORCA_RELEASE = f"https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v{ORCA_VERSION}/"
# Checksums computed from the official release assets (GitHub publishes none); bump both together.
ORCA_APPIMAGES = {
    "x86_64": (f"OrcaSlicer_Linux_AppImage_Ubuntu2404_V{ORCA_VERSION}.AppImage",
               "d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd"),
    "aarch64": (f"OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V{ORCA_VERSION}.AppImage",
                "e1a07275a25f176626c55a5df39e91bc4476d8c28ee4a3192ff758e29dd5c3ba"),
}
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "kobra")

MACHINE = "Anycubic Kobra S1 0.4 nozzle"
PROCESS = "0.20mm Standard @Anycubic Kobra S1 0.4 nozzle"


class Orca:
    """An unpacked OrcaSlicer: the program to run and its bundled Anycubic profiles."""

    def __init__(self, root):
        self.root = root
        self.exe = os.path.join(root, "AppRun")
        self.profiles = os.path.join(root, "resources", "profiles", "Anycubic")
        if not (os.path.isfile(self.exe) and os.path.isdir(self.profiles)):
            sys.exit(f"{root} doesn't look like an unpacked OrcaSlicer (need AppRun and resources/profiles/Anycubic)")


def find_orca(path=None):
    """Use --orca / $KOBRA_ORCA if given (an unpacked folder or an .AppImage), else the pinned download."""
    path = path or os.environ.get("KOBRA_ORCA")
    if path:
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path):
            return Orca(path)
        if path.endswith(".AppImage") and os.path.isfile(path):
            return Orca(_unpack(path, os.path.join(CACHE, "orca-" + os.path.basename(path)[:-9])))
        sys.exit(f"--orca: {path} is neither an unpacked OrcaSlicer folder nor an .AppImage")
    return Orca(_download())


def _download():
    root = os.path.join(CACHE, f"orca-{ORCA_VERSION}")
    if os.path.isfile(os.path.join(root, "AppRun")):
        return root
    arch = platform.machine().lower()
    if sys.platform != "linux" or arch not in ORCA_APPIMAGES:
        sys.exit(f"automatic OrcaSlicer download covers Linux x86_64/aarch64 only (this is {sys.platform} {arch}).\n"
                 f"Install OrcaSlicer {ORCA_VERSION} and pass its folder with --orca or $KOBRA_ORCA, "
                 "or slice in any slicer and `kobra send` the .gcode.")
    name, want = ORCA_APPIMAGES[arch]
    os.makedirs(CACHE, exist_ok=True)
    img = os.path.join(CACHE, name)
    print(f"slicing needs OrcaSlicer {ORCA_VERSION}; downloading it once (~140 MB) to {CACHE} ...", flush=True)
    h = hashlib.sha256()
    with requests.get(ORCA_RELEASE + name, stream=True, timeout=60) as r, open(img + ".part", "wb") as f:
        r.raise_for_status()
        for chunk in r.iter_content(1 << 20):
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != want:
        os.remove(img + ".part")
        sys.exit(f"OrcaSlicer download failed its checksum ({h.hexdigest()} != {want}); nothing was kept")
    os.replace(img + ".part", img)
    root = _unpack(img, root)
    os.remove(img)
    return root


def _unpack(img, root):
    """Unpack an AppImage (no FUSE needed) into root, once."""
    if os.path.isfile(os.path.join(root, "AppRun")):
        return root
    os.chmod(img, 0o755)
    with tempfile.TemporaryDirectory(dir=os.path.dirname(root) or ".") as tmp:
        r = subprocess.run([os.path.abspath(img), "--appimage-extract"], cwd=tmp, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"could not unpack {img}: {r.stderr[-300:]}")
        shutil.rmtree(root, ignore_errors=True)
        os.replace(os.path.join(tmp, "squashfs-root"), root)
    return root


def filament_profile(orca, material):
    """Map an ACE slot material (e.g. 'PLA Silk') to the matching Anycubic Kobra S1 filament profile."""
    d = os.path.join(orca.profiles, "filament")
    names = {f[:-5].split(" @")[0].removeprefix("Anycubic ").lower(): f
             for f in os.listdir(d) if f.endswith("@Anycubic Kobra S1 0.4 nozzle.json")}
    m = (material or "PLA").strip().lower()
    f = names.get(m) or names.get(m.split()[0]) or names["pla"]
    return os.path.join(d, f)


def resolved_profile(orca, overrides, kind="process", start=PROCESS):
    """Flatten a stock profile's 'inherits' chain (Orca's CLI won't resolve it for a
    user file and silently falls back to slow defaults), then apply overrides."""
    d = os.path.join(orca.profiles, kind)
    by_name = {}
    for f in os.listdir(d):
        if f.endswith(".json"):
            with open(os.path.join(d, f)) as fh:
                j = json.load(fh)
            by_name[j.get("name", f[:-5])] = j
    chain, name = [], start
    while name:
        j = by_name.get(name)
        if j is None:
            sys.exit(f"{kind} profile not found: {name}")
        chain.append(j)
        name = j.get("inherits")
    merged = {}
    for j in reversed(chain):
        merged.update(j)
    merged.pop("inherits", None)
    merged.update({"name": "kobra-cli", "from": "User", "instantiation": "true",
                   ("print_settings_id" if kind == "process" else "filament_settings_id"): "kobra-cli"})
    merged.update(overrides)
    return merged


def slice_model(model, out_gcode, material="PLA", walls=None, infill=None, plate="Textured PEI Plate",
                temp=None, retract=None, orca_path=None):
    """Slice an STL/3MF/STEP with OrcaSlicer's CLI using the stock Kobra S1 profiles."""
    orca = find_orca(orca_path)
    with tempfile.TemporaryDirectory(prefix="kobra-slice-") as tmp:
        # Orca's CLI defaults to "Cool Plate" (35 C bed) unless the plate type is set; the S1 ships with textured PEI
        over = {"curr_bed_type": plate,
                # stock profile travels straight across open slots and leaves strings in them; route around inside the part
                "reduce_crossing_wall": "1"}
        if walls is not None:
            over["wall_loops"] = str(walls)
        if infill is not None:
            over["sparse_infill_density"] = f"{infill}%"
        process = os.path.join(tmp, "process.json")
        with open(process, "w") as f:
            json.dump(resolved_profile(orca, over), f, indent=1)
        fil = filament_profile(orca, material)
        if temp is not None or retract is not None:     # stringing knobs live in the filament profile
            with open(fil) as fh:
                fname = json.load(fh).get("name", os.path.basename(fil)[:-5])
            fover = {}
            if temp is not None:
                fover["nozzle_temperature"] = [str(temp)]
                fover["nozzle_temperature_initial_layer"] = [str(temp + 5)]
            if retract is not None:
                fover["filament_retraction_length"] = [str(retract)]
            fil = os.path.join(tmp, os.path.basename(fil))
            with open(fil, "w") as fh:
                json.dump(resolved_profile(orca, fover, "filament", fname), fh, indent=1)
        cmd = [orca.exe, "--slice", "0", "--arrange", "1", "--orient", "0",
               "--load-settings", f"{os.path.join(orca.profiles, 'machine', MACHINE + '.json')};{process}",
               "--load-filaments", fil, "--outputdir", tmp, os.path.abspath(model)]
        print(f"slicing {os.path.basename(model)} with {os.path.basename(fil)[:-5]} ...", flush=True)
        r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=1800)
        res = {}
        try:
            with open(os.path.join(tmp, "result.json")) as f:
                res = json.load(f)
        except (OSError, ValueError):
            pass
        g = os.path.join(tmp, "plate_1.gcode")
        if r.returncode != 0 or not os.path.exists(g):
            sys.exit(f"slicing failed: {res.get('error_string') or r.stderr[-500:] or r.returncode}")
        for p in res.get("sliced_plates", []):
            if p.get("warning_message"):
                print("orca warning:", p["warning_message"])
        shutil.copyfile(g, out_gcode)
    summary = {}
    with open(out_gcode, errors="replace") as f:
        for line in f:
            for key in ("estimated printing time (normal mode)", "total filament used [g]", "total layers count",
                        "wall_loops", "sparse_infill_density"):
                if line.startswith(f"; {key} ="):
                    summary[key] = line.split("=", 1)[1].strip()
    print(f"sliced -> {out_gcode}: {summary.get('estimated printing time (normal mode)', '?')}, "
          f"{summary.get('total filament used [g]', '?')} g, {summary.get('total layers count', '?')} layers, "
          f"{summary.get('wall_loops', '?')} walls, {summary.get('sparse_infill_density', '?')} infill")
    return out_gcode
