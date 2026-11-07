import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from soundboard.routing import MIC_NAME, Router, Pulse, physical_source, choose_source
from soundboard.shortcuts import portal_trigger, x11_modifiers
from gi.repository import Gdk


class FakePulse:
    def __init__(self):
        self.modules = [dict(index=44, name="module-null-sink", argument="sink_name=unrelated")]
        self.sources = [dict(index=1, name="hardware_mic"), dict(index=2, name=MIC_NAME)]
        self.streams = [dict(index=31, source=1, properties={"application.name": "Discord", "application.process.id": "4321"})]
        self.inputs = [dict(index=20, owner_module=10)]
        self.default = "hardware_mic"
        self.calls = []

    def list(self, kind):
        return {"modules": self.modules, "sources": self.sources, "source-outputs": self.streams, "sink-inputs": self.inputs}[kind]

    def call(self, *args):
        self.calls.append(args)
        if args[0] == "get-default-source":
            return self.default
        if args[0] == "set-default-source":
            self.default = args[1]
        elif args[0] == "move-source-output":
            next(item for item in self.streams if item["index"] == args[1])["source"] = next(item["index"] for item in self.sources if item["name"] == args[2])
        elif args[0] == "unload-module":
            self.modules = [item for item in self.modules if item["index"] != args[1]]
        elif args[0] == "load-module":
            new_id = max(item["index"] for item in self.modules) + 1
            self.modules.append(dict(index=new_id, name=args[1], argument=" ".join(args[2:])))
            return str(new_id)
        return ""


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pulse = FakePulse()
        self.router = Router(self.temp.name, self.pulse)
        self.router.journal = {"token": "testtoken", "modules": [], "default_before": None, "moved": []}
        self.router.mix_sink = "smith_mix_testtoken"
        self.router._save()

    def tearDown(self):
        self.router.close()
        self.temp.cleanup()

    def test_cleanup_only_owned_modules_and_reverse_order(self):
        for index in (10, 11):
            self.pulse.modules.append(dict(index=index, name="module-null-sink", argument="sink_name=smith_testtoken"))
            self.router.journal["modules"].append(dict(id=index, name="module-null-sink"))
        self.router.journal["modules"].append(dict(id=44, name="module-null-sink"))
        self.router.close()
        unloaded = [args[1] for args in self.pulse.calls if args[0] == "unload-module"]
        self.assertEqual(unloaded, [11, 10])
        self.assertEqual(self.pulse.modules[0]["index"], 44)

    def test_explicit_default_restored(self):
        self.router.make_default()
        self.assertEqual(self.pulse.default, MIC_NAME)
        self.router.close()
        self.assertEqual(self.pulse.default, "hardware_mic")

    def test_dont_override_later_default_change(self):
        self.router.make_default()
        self.pulse.default = "other_device"
        self.router.close()
        self.assertEqual(self.pulse.default, "other_device")

    def test_capture_route_restored(self):
        self.router.move_capture(31)
        self.assertEqual(self.pulse.streams[0]["source"], 2)
        self.router.close()
        self.assertEqual(self.pulse.streams[0]["source"], 1)

    def test_reused_capture_index_not_touched(self):
        self.router.move_capture(31)
        self.pulse.streams[0]["properties"]["application.process.id"] = "9876"
        self.router.close()
        self.assertEqual(self.pulse.streams[0]["source"], 2)

    def test_loopback_voice_gain_does_not_mute_hardware(self):
        self.router.voice = 10
        self.router.set_voice(.75, True)
        self.assertIn(("set-sink-input-volume", 20, "75%"), self.pulse.calls)
        self.assertIn(("set-sink-input-mute", 20, "1"), self.pulse.calls)
        self.assertFalse(any(call[0] == "set-source-mute" for call in self.pulse.calls))

    def test_feedback_source_rejected(self):
        for source in (MIC_NAME, "speaker.monitor", "smith_mix_1"):
            with self.assertRaisesRegex(RuntimeError, "físico"):
                self.router.set_voice_source(source)

    def test_module_journal_written_after_each_load(self):
        module = self.router._load("module-null-sink", "sink_name=smith_testtoken")
        saved = json.loads(self.router.journal_path.read_text())
        self.assertEqual(saved["modules"][0]["id"], module)

    def test_portal_trigger_encoding(self):
        self.assertEqual(portal_trigger("<Control><Alt>1"), "CTRL+ALT+1")
        self.assertEqual(portal_trigger("F8"), "F8")

    def test_super_key_is_mapped_to_mod4_on_x11(self):
        self.assertEqual(x11_modifiers(Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.CONTROL_MASK), 68)

    def test_invalid_monitor_indices_keep_physical_mics(self):
        for value in (None, "n/a", "N/A", -1, 4294967295, "4294967295", ""):
            with self.subTest(value=value):
                self.assertTrue(physical_source({"name": "usb_mic", "monitor_of_sink": value}))
        self.assertFalse(physical_source({"name": "speaker.monitor"}))
        self.assertFalse(physical_source({"name": "other", "monitor_of_sink": 0}))
        self.assertFalse(physical_source({"name": MIC_NAME}))

    def test_source_selection_falls_back_to_real_microphone(self):
        sources = [{"name": "built_in"}, {"name": "usb_mic"}, {"name": MIC_NAME}]
        self.assertEqual(choose_source(sources, "usb_mic", "built_in"), "usb_mic")
        self.assertEqual(choose_source(sources, "unplugged", "usb_mic"), "usb_mic")
        self.assertEqual(choose_source(sources, None, MIC_NAME), "built_in")
        self.assertIsNone(choose_source([{"name": MIC_NAME}]))

    def test_invalid_replacement_does_not_disconnect_working_voice(self):
        self.router.voice = 10
        self.router.voice_source = "hardware_mic"
        with self.assertRaises(RuntimeError):
            self.router.set_voice_source("disconnected_usb")
        self.assertEqual(self.router.voice, 10)
        self.assertFalse(any(call[0] == "unload-module" for call in self.pulse.calls))

    def test_failed_replacement_keeps_old_voice(self):
        self.router.voice = 10
        self.router.voice_source = "previous_mic"
        original = self.pulse.call
        def failing(*args):
            if args[0] == "load-module":
                raise RuntimeError("failed to load loopback")
            return original(*args)
        self.pulse.call = failing
        with self.assertRaises(RuntimeError):
            self.router.set_voice_source("hardware_mic")
        self.assertEqual(self.router.voice, 10)
        self.assertEqual(self.router.voice_source, "previous_mic")

    def test_refresh_does_not_recreate_healthy_voice(self):
        self.router.voice = 10
        self.router.voice_source = "hardware_mic"
        self.router.voice_source_index = 1
        self.pulse.modules.append(dict(index=10, name="module-loopback", argument="sink=smith_mix_testtoken"))
        self.router.journal["modules"].append(dict(id=10, name="module-loopback"))
        self.router.set_voice_source("hardware_mic")
        self.assertFalse(any(call[0] in ("unload-module", "load-module") for call in self.pulse.calls))

    def test_replugged_mic_with_new_index_reconnects(self):
        self.router.voice = 10
        self.router.voice_source = "hardware_mic"
        self.router.voice_source_index = 9
        self.pulse.modules.append(dict(index=10, name="module-loopback", argument="sink=smith_mix_testtoken"))
        self.router.journal["modules"].append(dict(id=10, name="module-loopback"))
        self.router.set_voice_source("hardware_mic")
        self.assertNotEqual(self.router.voice, 10)
        self.assertEqual(self.router.voice_source_index, 1)
        self.assertIn(("unload-module", 10), self.pulse.calls)

    def test_partial_module_setup_rolls_back(self):
        self.router.close()
        self.pulse.sources = [dict(index=1, name="hardware_mic")]
        original = self.pulse.call
        def failing(*args):
            if args[:2] == ("load-module", "module-remap-source"):
                raise RuntimeError("Simulated unavailable module")
            return original(*args)
        self.pulse.call = failing
        with self.assertRaisesRegex(RuntimeError, "Simulated"):
            self.router.start(None)
        self.assertEqual([item["index"] for item in self.pulse.modules], [44])
        self.assertIsNone(self.router.lock_file)
        self.assertFalse(self.router.journal_path.exists())
