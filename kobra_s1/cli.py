"""kobra - control an Anycubic Kobra S1 and its ACE Pro over your network (stock firmware, LAN mode, no cloud).

Protocol (community research; see ianloic/anycubic-orca-plugin and rvanderp3/kobra-connect):
  GET  http://IP:18910/info            -> device info, master token, upload URL
  POST ctrlInfoUrl?ts&nonce&sign&did   -> AES-128-CBC blob with MQTT credentials
  MQTT over TLS on IP:9883             -> status reports and print commands
  POST fileUploadurl (multipart)       -> stores the file on the printer

Usage (set the printer's address with --host or $KOBRA_HOST):
  kobra status
  kobra send part.gcode --slot N [--no-start] [--no-level]
  kobra send part.stl --slot N [--walls N] [--infill PCT] [--temp C] [--retract MM]   (slices first)
  kobra slice part.stl [--filament PLA] [--walls N] [--infill PCT] [--temp C] [--retract MM] [-o out.gcode]
  kobra start part.gcode [--no-level] [--slot N]
  kobra pause | resume | cancel
  kobra dry [on|off] [--temp C] [--hours H]
  kobra watch
"""

import argparse
import base64
import hashlib
import json
import os
import random
import ssl
import string
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt
import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .slicer import slice_model

DEFAULT_HOST = os.environ.get("KOBRA_HOST")
HTTP_PORT, MQTT_PORT = 18910, 9883
PCID = "4C3E98B0F5931D7188A31D1FDB17369D"   # client id the printer sees; any 32-hex string works


class Kobra:
    def __init__(self, host):
        self.host = host
        self.info = {}
        self.creds = {}
        self.model_id = ""
        self.device_id = ""
        self.client = None
        self._connected = threading.Event()
        self._connect_error = None
        self._waiters = {}          # msg type -> (Event, [payload])
        self._lock = threading.Lock()
        self.on_report = None

    # ---------- LAN handshake ----------
    def handshake(self):
        r = requests.get(f"http://{self.host}:{HTTP_PORT}/info", timeout=6)
        r.raise_for_status()
        self.info = r.json()
        if self.info.get("ctrlType") != "lan":
            sys.exit(f"printer is not in LAN mode (ctrlType={self.info.get('ctrlType')})")
        token = self.info["token"]
        self.model_id = str(self.info.get("modelId", "20025"))
        ctrl = self.info.get("ctrlInfoUrl", f"http://{self.host}:{HTTP_PORT}/ctrl")
        ts = int(time.time() * 1000)
        nonce = "".join(random.choices(string.ascii_letters + string.digits, k=6))
        sign = hashlib.md5(f"{hashlib.md5(token[:16].encode()).hexdigest()}{ts}{nonce}".encode()).hexdigest()
        r = requests.post(f"{ctrl}?ts={ts}&nonce={nonce}&sign={sign}&did={PCID}", timeout=6)
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 200:
            sys.exit(f"LAN authentication failed: {d.get('msg', d)}")
        dec = Cipher(algorithms.AES(token[16:32].encode()), modes.CBC(d["data"]["token"].encode()[:16])).decryptor()
        raw = dec.update(base64.b64decode(d["data"]["info"])) + dec.finalize()
        unpad = padding.PKCS7(128).unpadder()
        self.creds = json.loads((unpad.update(raw) + unpad.finalize()).decode())
        self.device_id = self.creds["deviceId"]

    # ---------- MQTT ----------
    def connect(self):
        if not self.creds:
            self.handshake()
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"kobra_cli_{uuid.uuid4().hex[:8]}")
        c.username_pw_set(self.creds["username"], self.creds["password"])
        # The printer uses a self-signed certificate; this is a LAN-only connection.
        c.tls_set(cert_reqs=ssl.CERT_NONE)
        c.tls_insecure_set(True)
        c.on_connect = self._on_connect
        c.on_message = self._on_message
        c.connect(self.host, MQTT_PORT, keepalive=60)
        c.loop_start()
        self.client = c
        if not self._connected.wait(8):
            sys.exit("could not connect to the printer's MQTT broker")
        if self._connect_error:
            sys.exit(f"the printer's MQTT broker refused the connection: {self._connect_error}")

    def close(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc.is_failure:
            self._connect_error = str(rc)
            self._connected.set()
            return
        client.subscribe(f"anycubic/anycubicCloud/v1/printer/+/{self.model_id}/{self.device_id}/#")
        self._connected.set()

    def _on_message(self, client, userdata, msg):
        try:
            p = json.loads(msg.payload.decode())
        except ValueError:
            return
        with self._lock:
            w = self._waiters.get(p.get("type"))
        # Replies don't echo our msgid. A reply carries the action it answers (getInfo, setDry, ...); a query is
        # answered by a plain report, but for any other action a report is unrelated status and must not count.
        if w and (p.get("action") == w[2] or (w[2] == "query" and p.get("action") == "report")) \
                and p.get("data") is not None:
            w[1].append(p)
            w[0].set()
        if self.on_report:
            self.on_report(msg.topic, p)

    def command(self, typ, action, data=None, web=False, wait=0.0):
        ns = "web" if web else "slicer"
        topic = f"anycubic/anycubicCloud/v1/{ns}/printer/{self.model_id}/{self.device_id}/{typ}"
        ev, box = threading.Event(), []
        if wait:
            with self._lock:
                self._waiters[typ] = (ev, box, action)
        self.client.publish(topic, json.dumps({"type": typ, "action": action, "timestamp": int(time.time() * 1000),
                                               "msgid": str(uuid.uuid4()), "data": data}))
        if wait:
            ev.wait(wait)
            with self._lock:
                self._waiters.pop(typ, None)
            return box[-1] if box else None

    def status(self):
        r = self.command("info", "query", wait=5)
        return (r or {}).get("data") or {}

    def ace_slots(self):
        """ACE Pro slots as [{index, status, type, color}], [] when no ACE Pro is attached, None on no reply."""
        r = self.command("multiColorBox", "getInfo", wait=5)
        if r is None:
            return None
        boxes = ((r or {}).get("data") or {}).get("multi_color_box") or []
        return [s for b in boxes for s in b.get("slots", [])]

    # ---------- file upload ----------
    def upload(self, path, name=None):
        name = name or os.path.basename(path)
        size = os.path.getsize(path)
        url = self.info.get("fileUploadurl") or f"http://{self.host}:{HTTP_PORT}/gcode_upload"
        headers = {"User-Agent": "AnycubicSlicerNext/1.3.7.3", "X-BBL-Client-Name": "AnycubicSlicerNext",
                   "X-BBL-Client-Type": "slicer", "X-BBL-Client-Version": "01.03.07.03",
                   "X-BBL-Device-ID": PCID, "X-BBL-OS-Type": "linux", "X-File-Length": str(size)}
        with open(path, "rb") as f:
            r = requests.post(url, headers=headers, timeout=600,
                              files={"filename": (None, name), "gcode": (name, f, "application/octet-stream")})
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 200:
            sys.exit(f"upload failed: {d.get('msg', d)}")
        return d["data"]["gcode"], size

    def start(self, name, size, md5, level=True, slot=None, slot_info=None):
        rgb = list((slot_info or {}).get("color") or [255, 255, 255])[:3]
        ams = {"use_ams": slot is not None, "ams_box_mapping": [] if slot is None else [
            {"paint_index": 0, "ams_index": slot - 1, "paint_color": rgb + [255],
             "ams_color": rgb, "material_type": (slot_info or {}).get("type") or "PLA"}]}
        task = {"auto_leveling": int(level), "vibration_compensation": 0, "flow_calibration": 0, "dry_mode": 0,
                "timelapse": {"status": 0, "count": 0, "type": 0}, "ai_settings": {"status": 0, "count": 0, "type": 0},
                "drying_settings": {"status": 0, "target_temp": 0, "duration": 0, "remain_time": 0},
                "model_objects_skip_parts": []}
        data = {"taskid": "-1", "url": "", "filename": name, "md5": md5, "filepath": None, "filetype": 1,
                "project_type": 1, "filesize": size, "ams_settings": ams, "task_settings": task}
        return self.command("print", "start", data, wait=8)


def md5_file(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def show_status(d):
    t = d.get("temp") or {}
    proj = d.get("project") or {}
    print(f"printer : {d.get('printerName', '?')} ({d.get('model', '?')}) fw {d.get('version', '?')}")
    print(f"state   : {d.get('state', '?')}")
    print(f"nozzle  : {t.get('curr_nozzle_temp', '?')} -> {t.get('target_nozzle_temp', '?')} C")
    print(f"bed     : {t.get('curr_hotbed_temp', '?')} -> {t.get('target_hotbed_temp', '?')} C")
    if proj:
        print(f"job     : {proj.get('filename', '?')}  {proj.get('progress', '?')}%  "
              f"layer {proj.get('curr_layer', '?')}/{proj.get('total_layers', '?')}  "
              f"remaining {proj.get('remain_time', '?')} min  [{proj.get('print_status', proj.get('state', ''))}]")


def show_slots(slots):
    if slots is None:
        print("ACE Pro  : no reply")
    for s in slots or []:
        c = s.get("color") or []
        hexc = "#%02x%02x%02x" % tuple(c[:3]) if len(c) >= 3 else "?"
        print(f"slot {s.get('index', 0) + 1} : {s.get('type') or '(empty)':12s} {hexc}")


IDLE = ("free", "idle", "done", "finish", "stoped", "stopped")


def check_idle(k):
    """Exit unless the printer positively reports an idle state; no reply is not permission to print."""
    st = k.status().get("state")
    if st is None:
        sys.exit("no status reply from the printer; not sending")
    if st not in IDLE:
        sys.exit(f"printer is busy (state: {st}); not sending")


def refusal(r):
    """Describe a printer's non-200 reply; the code is always there even when state and msg are empty."""
    return " ".join(str(x) for x in (f"code {r.get('code')}", r.get("state"), r.get("msg")) if x)


def bounded(kind, lo, hi):
    def parse(v):
        x = kind(v)
        if not lo <= x <= hi:
            raise argparse.ArgumentTypeError(f"must be between {lo} and {hi}")
        return x
    return parse


def main():
    ap = argparse.ArgumentParser(prog="kobra", description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST, help="printer IP address (default: $KOBRA_HOST)")
    ap.add_argument("--orca", help="OrcaSlicer to slice with: an unpacked folder or an .AppImage "
                                   "(default: $KOBRA_ORCA, else a pinned download)")
    ap.add_argument("--json", action="store_true", help="print raw JSON for status")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show temperatures and job progress")
    p = sub.add_parser("slice", help="slice a model to G-code with OrcaSlicer (no printer needed)")
    p.add_argument("file")
    p.add_argument("--filament", default="PLA", help="material, e.g. PLA, 'PLA Silk', PETG, ASA (default PLA)")
    p.add_argument("--walls", type=int)
    p.add_argument("--infill", type=int, metavar="PCT")
    p.add_argument("--temp", type=int, help="nozzle temperature (first layer +5)")
    p.add_argument("--retract", type=float, metavar="MM", help="retraction length (stringing)")
    p.add_argument("-o", "--output", help="output .gcode (default: next to the model)")
    sub.add_parser("watch", help="stream live reports until Ctrl-C")
    for name in ("send", "start"):
        p = sub.add_parser(name, help="upload and print a G-code file" if name == "send"
                           else "print a file already uploaded (give the local copy for size/md5)")
        p.add_argument("file")
        p.add_argument("--no-level", action="store_true", help="skip auto bed leveling")
        p.add_argument("--slot", type=int, choices=range(1, 5), metavar="1-4",
                       help="ACE Pro slot to print from (required when an ACE Pro is attached)")
        if name == "send":
            p.add_argument("--no-start", action="store_true", help="upload only")
            p.add_argument("--walls", type=int, help="when slicing a model: wall loops")
            p.add_argument("--infill", type=int, metavar="PCT", help="when slicing a model: infill %%")
            p.add_argument("--temp", type=int, help="when slicing a model: nozzle temperature (first layer +5)")
            p.add_argument("--retract", type=float, metavar="MM", help="when slicing a model: retraction length")
            p.add_argument("--filament", help="when slicing a model: profile to slice with "
                                              "(default: the slot's material), e.g. PLA for faster silk prints")
    for name in ("pause", "resume", "cancel"):
        sub.add_parser(name)
    p = sub.add_parser("dry", help="ACE Pro filament dryer: show, start, or stop (`kobra dry off`)")
    p.add_argument("state", nargs="?", choices=("on", "off"), help="omit to show the dryer's state")
    p.add_argument("--temp", type=bounded(int, 35, 55), default=50,
                   help="target C, 35-55 (default %(default)s; PLA 45-50, PETG 55)")
    p.add_argument("--hours", type=bounded(float, 0.5, 24), default=4, help="duration, 0.5-24 (default %(default)s)")
    a = ap.parse_args()

    if a.cmd == "slice":
        out = a.output or os.path.splitext(a.file)[0] + ".gcode"
        slice_model(a.file, out, a.filament, a.walls, a.infill, temp=a.temp, retract=a.retract, orca_path=a.orca)
        return

    if not a.host:
        sys.exit("which printer? pass --host IP or set KOBRA_HOST (the IP is on the printer's network settings screen)")
    k = Kobra(a.host)
    k.connect()
    try:
        if a.cmd == "status":
            d = k.status()
            if not d:
                sys.exit("no status reply from the printer")
            if a.json:
                print(json.dumps(d, indent=1))
            else:
                show_status(d)
                show_slots(k.ace_slots())
        elif a.cmd == "watch":
            k.on_report = lambda topic, p: print(time.strftime("%H:%M:%S"), p.get("type"), p.get("action"),
                                                 json.dumps(p.get("data"))[:300], flush=True)
            while True:
                time.sleep(1)
        elif a.cmd in ("send", "start"):
            if not os.path.isfile(a.file):
                sys.exit(f"no such file: {a.file}")
            check_idle(k)
            slots = k.ace_slots()
            if slots is None:
                sys.exit("no reply from the printer about the ACE Pro; not sending")
            slot_info = None
            if slots and a.slot is None:
                show_slots(slots)
                sys.exit("an ACE Pro is attached: choose the filament with --slot N")
            if a.slot is not None:
                slot_info = next((s for s in slots if s.get("index") == a.slot - 1), None)
                if not slot_info or not slot_info.get("type"):
                    sys.exit(f"ACE Pro slot {a.slot} is empty")
                print(f"using slot {a.slot}: {slot_info.get('type')}")
            if a.cmd == "send" and not a.file.lower().endswith(".gcode"):
                material = a.filament or (slot_info or {}).get("type") or "PLA"
                a.file = slice_model(a.file, os.path.splitext(a.file)[0] + ".gcode", material, a.walls, a.infill,
                                     temp=a.temp, retract=a.retract, orca_path=a.orca)
                check_idle(k)       # slicing can take a while; someone may have started a print meanwhile
            name, size = os.path.basename(a.file), os.path.getsize(a.file)
            if a.cmd == "send":
                print(f"uploading {name} ({size / 1e6:.1f} MB) ...", flush=True)
                name, size = k.upload(a.file)
                print(f"stored on printer as {name}")
                if a.no_start:
                    return
            r = k.start(name, size, md5_file(a.file), level=not a.no_level, slot=a.slot, slot_info=slot_info)
            if r is not None and r.get("code") not in (None, 200):
                sys.exit(f"printer refused the print: {refusal(r)}")
            # The printer doesn't always answer a start directly, so confirm from its state instead.
            for _ in range(10):
                st = k.status().get("state")
                if st and st not in IDLE:
                    print(f"print started (printer state: {st})")
                    break
                time.sleep(2)
            else:
                print("start command sent, but the printer hasn't reported starting - check `kobra status` or its screen")
        elif a.cmd == "dry":
            if a.state:     # verified on hardware 2026-11-20: duration is minutes, the reply echoes drying_status
                ds = {"status": 1, "target_temp": a.temp, "duration": int(a.hours * 60)} if a.state == "on" else \
                     {"status": 0, "target_temp": 0, "duration": 0}
                r = k.command("multiColorBox", "setDry", {"multi_color_box": [{"id": 0, "drying_status": ds}]}, wait=8)
                if r is None:
                    sys.exit("no reply from the printer to the dryer command; check `kobra dry`")
                if r.get("code") not in (None, 200):
                    sys.exit(f"printer refused the dryer command: {refusal(r)}")
            else:       # after a change, show the setDry reply: a getInfo sent straight after it answers with the old state
                r = k.command("multiColorBox", "getInfo", wait=5)
            if r is None:
                sys.exit("no reply from the printer about the ACE Pro")
            for b in ((r or {}).get("data") or {}).get("multi_color_box") or []:
                d = b.get("drying_status") or {}
                state = f"ON -> {d.get('target_temp')} C, {d.get('remain_time')} of {d.get('duration')} min left" \
                    if d.get("status") else "off"
                print(f"ACE {b.get('id')}: {b.get('temp')} C, dryer {state}")
        else:
            action = {"pause": "pause", "resume": "resume", "cancel": "stop"}[a.cmd]
            k.command("print", action, {"taskid": "-1"})
            print(f"{a.cmd} sent")
    except KeyboardInterrupt:
        pass
    finally:
        k.close()


if __name__ == "__main__":
    main()
