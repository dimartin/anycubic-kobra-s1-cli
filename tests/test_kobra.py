"""Offline tests: no printer, no network, no OrcaSlicer. Run with `python -m unittest`."""

import argparse
import json
import os
import stat
import tempfile
import types
import unittest
from unittest import mock

from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from kobra_s1 import cli, slicer


def msg(typ, action, data=None, code=200):
    return types.SimpleNamespace(topic=f"anycubic/x/{typ}/report", payload=json.dumps(
        {"type": typ, "action": action, "code": code, "data": {} if data is None else data}).encode())


class FakeClient:
    """Answers each publish by delivering the queued printer messages to the Kobra instance."""

    def __init__(self, k, replies):
        self.k, self.replies = k, replies

    def publish(self, topic, payload):
        for m in self.replies:
            self.k._on_message(self, None, m)


def kobra_with(replies):
    k = cli.Kobra("printer.invalid")
    k.client = FakeClient(k, replies)
    return k


class ReplyMatching(unittest.TestCase):
    def test_query_is_answered_by_a_report(self):
        r = kobra_with([msg("info", "report", {"state": "free"})]).command("info", "query", wait=0.2)
        self.assertEqual(r["data"]["state"], "free")

    def test_leftover_report_is_not_a_reply_to_a_command(self):
        k = kobra_with([msg("print", "report", {"state": "auto_leveling"})])
        self.assertIsNone(k.command("print", "start", {}, wait=0.2))

    def test_command_reply_is_matched_by_action(self):
        k = kobra_with([msg("multiColorBox", "report"), msg("multiColorBox", "setDry", code=500)])
        r = k.command("multiColorBox", "setDry", {}, wait=0.2)
        self.assertEqual((r["action"], r["code"]), ("setDry", 500))

    def test_other_types_are_ignored(self):
        self.assertIsNone(kobra_with([msg("status", "workReport")]).command("info", "query", wait=0.2))


class Guards(unittest.TestCase):
    def test_no_status_reply_refuses_to_send(self):
        with self.assertRaises(SystemExit) as e:
            cli.check_idle(kobra_with([]))
        self.assertIn("no status reply", str(e.exception))

    def test_busy_refuses_and_idle_passes(self):
        with self.assertRaises(SystemExit):
            cli.check_idle(kobra_with([msg("info", "report", {"state": "printing"})]))
        cli.check_idle(kobra_with([msg("info", "report", {"state": "free"})]))

    def test_no_ace_reply_is_none_not_empty(self):
        self.assertIsNone(kobra_with([]).ace_slots())
        self.assertEqual(kobra_with([msg("multiColorBox", "getInfo", {"multi_color_box": []})]).ace_slots(), [])

    def test_connect_failure_is_reported(self):
        k = cli.Kobra("printer.invalid")
        client = mock.Mock()
        k._on_connect(client, None, None, ReasonCode(PacketTypes.CONNACK, "Not authorized"))
        self.assertTrue(k._connected.is_set())
        self.assertEqual(k._connect_error, "Not authorized")
        client.subscribe.assert_not_called()

    def test_refusal_always_names_the_code(self):
        self.assertEqual(cli.refusal({"code": 500}), "code 500")
        self.assertEqual(cli.refusal({"code": 403, "state": "failed", "msg": "busy"}), "code 403 failed busy")

    def test_dryer_bounds(self):
        temp = cli.bounded(int, 35, 55)
        self.assertEqual(temp("50"), 50)
        for bad in ("-1", "70"):
            with self.assertRaises(argparse.ArgumentTypeError):
                temp(bad)


class Slicer(unittest.TestCase):
    def test_blank_filament_falls_back_to_pla(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "filament"))
            for name in ("PLA", "PETG"):
                open(os.path.join(d, "filament", f"Anycubic {name} @Anycubic Kobra S1 0.4 nozzle.json"), "w").close()
            orca = types.SimpleNamespace(profiles=d)
            self.assertIn("PLA @", slicer.filament_profile(orca, "   "))
            self.assertIn("PETG @", slicer.filament_profile(orca, "petg"))

    def test_user_appimage_unpacks_into_a_fresh_cache(self):
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "Orca.AppImage")      # stands in for an AppImage: handles --appimage-extract
            with open(img, "w") as f:
                f.write("#!/bin/sh\nmkdir -p squashfs-root/resources/profiles/Anycubic\n"
                        "touch squashfs-root/AppRun\n")
            os.chmod(img, 0o755)
            cache = os.path.join(d, "no", "such", "cache")
            with mock.patch.object(slicer, "CACHE", cache):
                orca = slicer.find_orca(img)
            self.assertTrue(orca.root.startswith(cache))
            self.assertTrue(os.path.isfile(orca.exe))

    def test_non_executable_appimage_is_refused_not_chmodded(self):
        with tempfile.TemporaryDirectory() as d:
            img = os.path.join(d, "Orca.AppImage")
            open(img, "w").close()
            with self.assertRaises(SystemExit):
                slicer.find_orca(img)
            self.assertFalse(os.stat(img).st_mode & stat.S_IXUSR)


if __name__ == "__main__":
    unittest.main()
