import array
from dataclasses import replace
import json
import math
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
import wave

from gi.repository import GLib

from soundboard.library import Clip
from soundboard import media
from soundboard.media import Gst


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.source = Path(cls.temp.name) / "áudio 'com espaços'.wav"
        # First half quiet, second half loud; enables signal-based trim verification.
        with wave.open(str(cls.source), "wb") as output:
            output.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
            output.writeframes(b"".join(struct.pack("<h", int((2000 if index < 44100 else 18000) *
                                                            math.sin(2 * math.pi * 440 * index / 44100))) for index in range(88200)))
        cls.metadata = media.probe(cls.source)
        cls.clip = Clip(id=1, **cls.metadata)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def destination(self, suffix):
        return Path(self.temp.name) / (self._testMethodName + suffix)

    def test_probe_native_rate_and_unicode_path(self):
        self.assertAlmostEqual(self.metadata["duration"], 2, places=3)
        self.assertEqual(self.metadata["rate"], 44100)
        self.assertEqual(self.metadata["channels"], 1)

    def test_replaygain_and_bounded_waveform(self):
        analysis = media.analyze(self.source, 2)
        waveform = json.loads(analysis["waveform"])
        self.assertIsInstance(analysis["replay_gain"], float)
        self.assertAlmostEqual(analysis["peak"], 18000 / 32768, delta=.002)
        self.assertLessEqual(len(waveform), 1024)
        self.assertGreater(len(waveform), 10)
        self.assertGreater(max(waveform[len(waveform) // 2:]), max(waveform[:len(waveform) // 2]) * 4)

    def test_analysis_cancellation(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(media.MediaError, "cancelada"):
            media.analyze(self.source, 2, cancel)

    def test_export_trim_has_correct_signal_and_48k_pcm24(self):
        target = self.destination(".wav")
        media.export_clip(replace(self.clip, trim_start=1.1, trim_end=1.6), target, normalize=False)
        metadata = media.probe(target)
        self.assertAlmostEqual(metadata["duration"], .5, delta=.005)
        self.assertEqual(metadata["rate"], 48000)
        self.assertEqual(metadata["channels"], 2)
        analysis = media.analyze(target, .5)
        self.assertAlmostEqual(analysis["peak"], 18000 / 32768, delta=.01)
        self.assertIn("24-bit", metadata["codec"])

    def test_mp3_encode_and_decode(self):
        target = self.destination(".mp3")
        media.export_clip(self.clip, target, normalize=False)
        metadata = media.probe(target)
        self.assertAlmostEqual(metadata["duration"], 2, delta=.1)
        self.assertEqual(metadata["rate"], 48000)

    def test_flac_roundtrip(self):
        target = self.destination(".flac")
        media.export_clip(replace(self.clip, trim_end=.6), target, normalize=False)
        self.assertAlmostEqual(media.probe(target)["duration"], .6, delta=.01)

    def test_limiter_caps_overdriven_signal(self):
        target = self.destination(".wav")
        media.export_clip(replace(self.clip, gain_db=12), target, normalize=False)
        analysis = media.analyze(target, 2)
        self.assertLessEqual(analysis["peak"], 1.0001)
        self.assertGreater(analysis["peak"], .8)

    def test_peak_aware_gain(self):
        clip = replace(self.clip, replay_gain=15, peak=.8)
        self.assertLessEqual(media.gain_for(clip, normalize=True) * .8, 10 ** (-1 / 20) + .0001)
        self.assertEqual(media.gain_for(clip, normalize=False), 1)

    def test_original_quality_is_the_default(self):
        clip = replace(self.clip, replay_gain=-18, peak=.6)
        self.assertEqual(media.gain_for(clip), 1)

    def test_original_pcm_is_preserved_without_bass_boost(self):
        original = self.destination("-input.wav")
        samples = [int(4000 * math.sin(2 * math.pi * 80 * i / 48000) +
                       4000 * math.sin(2 * math.pi * 4000 * i / 48000)) for i in range(4800)]
        with wave.open(str(original), "wb") as output:
            output.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            output.writeframes(struct.pack("<" + "h" * len(samples), *samples))
        clip = Clip(id=4, **media.probe(original), replay_gain=-18)
        target = self.destination("-output.wav")
        media.export_clip(clip, target)
        with wave.open(str(target), "rb") as output:
            raw = output.readframes(4800)
        decoded = [int.from_bytes(raw[i:i + 3], "little", signed=True) for i in range(0, len(raw), 6)]
        self.assertEqual(len(decoded), len(samples))
        self.assertLessEqual(max(abs(left - right * 256) for left, right in zip(decoded, samples)), 2)

    def test_voice_and_clip_signals_survive_mixing(self):
        pipeline = Gst.parse_launch("audiomixer name=mix ! audioconvert ! " + media.CAPS +
            " ! rglimiter ! appsink name=out sync=false max-buffers=16 "
            "audiotestsrc freq=220 volume=0.10 num-buffers=10 samplesperbuffer=480 ! " + media.CAPS + " ! mix. "
            "audiotestsrc freq=1000 volume=0.15 num-buffers=10 samplesperbuffer=480 ! " + media.CAPS + " ! mix.")
        values = array.array("f")
        try:
            sink = pipeline.get_by_name("out")
            pipeline.set_state(Gst.State.PLAYING)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                sample = sink.emit("try-pull-sample", 100 * Gst.MSECOND)
                if sample:
                    buffer = sample.get_buffer()
                    values.frombytes(buffer.extract_dup(0, buffer.get_size()))
                elif sink.get_property("eos"):
                    break
            self.assertTrue(sink.get_property("eos"))
        finally:
            pipeline.set_state(Gst.State.NULL)
        mono = values[::2]
        self.assertEqual(len(mono), 4800)
        for frequency, amplitude in ((220, .1), (1000, .15)):
            sine = sum(sample * math.sin(2 * math.pi * frequency * i / 48000) for i, sample in enumerate(mono))
            cosine = sum(sample * math.cos(2 * math.pi * frequency * i / 48000) for i, sample in enumerate(mono))
            detected = 2 / len(mono) * math.hypot(sine, cosine)
            self.assertAlmostEqual(detected, amplitude, delta=.001)

    def test_missing_and_invalid_media_fail(self):
        with self.assertRaises(media.MediaError):
            media.probe(self.destination(".missing"))
        target = self.destination(".txt")
        target.write_text("no audio")
        with self.assertRaises(media.MediaError):
            media.probe(target)

    def run_player(self, clip, mode="both", pause=False):
        player = media.Player(sink_factory="fakesink")
        loop = GLib.MainLoop()
        events, errors, positions = [], [], []
        started = time.monotonic()
        timeout_hit = []
        player.connect("changed", lambda item, state: events.append(state))
        player.connect("error", lambda item, text: (errors.append(text), loop.quit()))
        player.connect("finished", lambda item: loop.quit())
        def expired():
            timeout_hit.append(True)
            loop.quit()
            return False
        watchdog = GLib.timeout_add_seconds(5, expired)
        def sample():
            positions.append(player.position())
            return True
        timer = GLib.timeout_add(40, sample)
        player.play(clip, mode=mode)
        if pause:
            GLib.timeout_add(120, lambda: (player.pause() or False))
            GLib.timeout_add(320, lambda: (player.pause() or False))
        loop.run()
        elapsed = time.monotonic() - started
        GLib.source_remove(timer)
        if not timeout_hit:
            GLib.source_remove(watchdog)
        player.stop()
        self.assertFalse(timeout_hit, "Player did not terminate")
        self.assertFalse(errors, errors)
        self.assertIn("playing", events)
        return elapsed, events, positions

    def test_player_dual_output_trim_eos(self):
        elapsed, events, positions = self.run_player(replace(self.clip, trim_start=1.1, trim_end=1.6))
        self.assertAlmostEqual(elapsed, .5, delta=.15)
        self.assertLessEqual(max(positions), .52)

    def test_player_pause_resume_local(self):
        elapsed, events, positions = self.run_player(replace(self.clip, trim_end=.5), mode="local", pause=True)
        self.assertIn("paused", events)
        self.assertAlmostEqual(elapsed, .7, delta=.16)

    def test_player_mp3_trim(self):
        target = self.destination(".mp3")
        media.export_clip(self.clip, target, normalize=False)
        clip = Clip(id=2, **media.probe(target), trim_start=.7, trim_end=1.2)
        elapsed, events, positions = self.run_player(clip, mode="mic")
        self.assertAlmostEqual(elapsed, .5, delta=.16)

    def test_other_installed_audio_formats_decode(self):
        formats = ((".ogg", "vorbisenc ! oggmux"), (".opus", "opusenc ! oggmux"),
                   (".m4a", "avenc_aac ! aacparse ! mp4mux"), (".wma", "avenc_wmav2 ! asfmux"))
        for suffix, encoder in formats:
            with self.subTest(format=suffix):
                target = self.destination(suffix)
                pipeline = Gst.parse_launch("audiotestsrc num-buffers=50 samplesperbuffer=480 volume=0.2 ! "
                                            "audio/x-raw,rate=48000,channels=2 ! audioconvert ! " + encoder + " ! filesink name=file")
                pipeline.get_by_name("file").set_property("location", str(target))
                try:
                    pipeline.set_state(Gst.State.PLAYING)
                    message = pipeline.get_bus().timed_pop_filtered(10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
                    self.assertIsNotNone(message)
                    self.assertNotEqual(message.type, Gst.MessageType.ERROR,
                                        media.message_error(message) if message.type == Gst.MessageType.ERROR else "")
                finally:
                    pipeline.set_state(Gst.State.NULL)
                metadata = media.probe(target)
                self.assertAlmostEqual(metadata["duration"], .5, delta=.12)
                self.assertEqual(metadata["rate"], 48000)
                self.assertGreater(media.analyze(target, metadata["duration"])["peak"], .1)

    def test_gtk_css_and_modules_load_without_audio_hardware(self):
        from soundboard.gui import CSS
        from gi.repository import Gtk
        provider = Gtk.CssProvider()
        errors = []
        provider.connect("parsing-error", lambda provider, section, error: errors.append(str(error)))
        provider.load_from_data(CSS)
        self.assertFalse(errors, errors)
