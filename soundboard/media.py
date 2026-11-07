"""GStreamer decoding, ReplayGain, playback, recording and lossless export."""
import json
import math
from pathlib import Path
import time

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GObject

Gst.init(None)
RATE = 48000
CAPS = "audio/x-raw,format=F32LE,rate=48000,channels=2,layout=interleaved"
EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma", ".aif", ".aiff", ".mp4", ".mkv", ".webm"}


class MediaError(RuntimeError):
    pass


def element(factory, name=None, **properties):
    result = Gst.ElementFactory.make(factory, name)
    if result is None:
        raise MediaError("Plugin GStreamer ausente: " + factory)
    for key, value in properties.items():
        result.set_property(key.replace("_", "-"), value)
    return result


def uri(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise MediaError("Arquivo não encontrado: " + str(path))
    return path.as_uri()


def message_error(message):
    error, details = message.parse_error()
    return str(error.message)


def probe(path, cancel=None):
    player = element("playbin", flags=2, uri=uri(path))
    sink = element("fakesink", sync=False)
    player.set_property("audio-sink", sink)
    try:
        player.set_state(Gst.State.PAUSED)
        result, state, pending = player.get_state(15 * Gst.SECOND)
        if result == Gst.StateChangeReturn.FAILURE or state != Gst.State.PAUSED:
            error = player.get_bus().pop_filtered(Gst.MessageType.ERROR)
            raise MediaError(message_error(error) if error else "Não foi possível decodificar o áudio.")
        ok, duration = player.query_duration(Gst.Format.TIME)
        if not player.get_property("n-audio"):
            raise MediaError("O arquivo não possui uma faixa de áudio.")
        if not ok or duration <= 0:
            # Headerless/CBR MP3 and some containers only reveal duration after
            # decoding. Decode offline instead of importing a zero-length clip.
            player.set_state(Gst.State.PLAYING)
            deadline = time.monotonic() + 300
            while True:
                if cancel and cancel.is_set():
                    raise MediaError("Operação cancelada.")
                message = player.get_bus().timed_pop_filtered(100 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if message and message.type == Gst.MessageType.ERROR:
                    raise MediaError(message_error(message))
                if message and message.type == Gst.MessageType.EOS:
                    ok, duration = player.query_duration(Gst.Format.TIME)
                    if not ok:
                        ok, duration = player.query_position(Gst.Format.TIME)
                    break
                if time.monotonic() > deadline:
                    raise MediaError("Não foi possível determinar a duração do arquivo.")
        caps = sink.get_static_pad("sink").get_current_caps()
        structure = caps.get_structure(0) if caps else None
        tags = player.emit("get-audio-tags", 0)
        def tag(name, fallback):
            found, value = tags.get_string(name) if tags else (False, None)
            return value if found else fallback
        return {"path": str(Path(path).resolve()), "title": tag("title", Path(path).stem),
                "duration": duration / Gst.SECOND if ok else 0,
                "rate": structure.get_value("rate") if structure else 0,
                "channels": structure.get_value("channels") if structure else 0,
                "codec": tag("audio-codec", Path(path).suffix.lstrip(".").upper())}
    finally:
        player.set_state(Gst.State.NULL)


def analyze(path, duration=0, cancel=None):
    """Stream the file through ReplayGain; retain at most ~1024 waveform bins."""
    interval = max(10_000_000, int(duration * Gst.SECOND / 1024))
    pipeline = Gst.parse_launch("uridecodebin name=decode ! audioconvert ! audioresample quality=10 ! " + CAPS +
                                " ! rganalysis ! level name=meter post-messages=true interval=" + str(interval) +
                                " ! fakesink sync=false")
    pipeline.get_by_name("decode").set_property("uri", uri(path))
    bus = pipeline.get_bus()
    gain, peak, wave = None, None, []
    deadline = time.monotonic() + max(60, duration * 2)
    try:
        pipeline.set_state(Gst.State.PLAYING)
        while True:
            if cancel and cancel.is_set():
                raise MediaError("Operação cancelada.")
            message = bus.timed_pop_filtered(100 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS |
                                            Gst.MessageType.TAG | Gst.MessageType.ELEMENT)
            if time.monotonic() > deadline:
                raise MediaError("A análise de áudio excedeu o tempo de resposta.")
            if not message:
                continue
            if message.type == Gst.MessageType.ERROR:
                raise MediaError(message_error(message))
            if message.type == Gst.MessageType.EOS:
                break
            if message.type == Gst.MessageType.TAG:
                tags = message.parse_tag()
                ok, value = tags.get_double("replaygain-track-gain")
                if ok:
                    gain = value
                ok, value = tags.get_double("replaygain-track-peak")
                if ok:
                    peak = value
            elif message.get_structure() and message.get_structure().get_name() == "level":
                values = message.get_structure().get_value("peak")
                wave.append(10 ** (max(values) / 20) if values else 0)
                if len(wave) > 4096:
                    # Also bounds memory when a file has unknown or incorrect duration.
                    wave = [max(wave[i:i + 2]) for i in range(0, len(wave), 2)]
                    interval *= 2
                    pipeline.get_by_name("meter").set_property("interval", interval)
        if len(wave) > 1024:
            ratio = len(wave) / 1024
            wave = [max(wave[int(i * ratio):max(int((i + 1) * ratio), int(i * ratio) + 1)]) for i in range(1024)]
        return {"replay_gain": gain, "peak": peak, "waveform": json.dumps(wave, separators=(",", ":"))}
    finally:
        pipeline.set_state(Gst.State.NULL)


def gain_for(clip, normalize=False, target_db=0):
    gain = (clip.replay_gain or 0) + target_db if normalize else 0
    if normalize and clip.peak and clip.peak > 0:
        gain = min(gain, -1 - 20 * math.log10(clip.peak))
    return 10 ** (max(-60, min(20, gain + clip.gain_db)) / 20)


class Player(GObject.GObject):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "finished": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, sink_factory="pulsesink"):
        super().__init__()
        self.sink_factory = sink_factory
        self.pipeline = None
        self.clip = None
        self.state = "stopped"
        self.preparing = False
        self.volumes = {}

    def play(self, clip, mode="both", local_device=None, mic_device=None, local_volume=1,
             mic_volume=1, normalize=False, target_db=0):
        self.stop()
        self.clip, self.mode = clip, mode
        try:
            sink = Gst.Bin.new("soundboard_outputs")
            convert = element("audioconvert")
            resample = element("audioresample", quality=10)
            caps = element("capsfilter", caps=Gst.Caps.from_string(CAPS))
            gain = element("volume", volume=gain_for(clip, normalize, target_db))
            limiter = element("rglimiter")
            tee = element("tee")
            parts = [convert, resample, caps, gain, limiter, tee]
            for part in parts:
                sink.add(part)
            for left, right in zip(parts, parts[1:]):
                if not left.link(right):
                    raise MediaError("Não foi possível conectar o processamento de áudio.")
            for name, device, volume in (("local", local_device, local_volume), ("mic", mic_device, mic_volume)):
                if (mode == "local" and name == "mic") or (mode == "mic" and name == "local"):
                    continue
                queue = element("queue", max_size_time=250 * Gst.MSECOND, max_size_bytes=0, max_size_buffers=0)
                control = element("volume", volume=volume)
                output = element(self.sink_factory, sync=True)
                if self.sink_factory == "pulsesink":
                    if device:
                        output.set_property("device", device)
                    output.set_property("buffer-time", 60000)
                    output.set_property("latency-time", 10000)
                    output.set_property("stream-properties", Gst.Structure.new_from_string("props,application.name=SMITH-Soundboard,media.role=Music"))
                for part in (queue, control, output):
                    sink.add(part)
                if not tee.link(queue) or not queue.link(control) or not control.link(output):
                    raise MediaError("Não foi possível conectar a saída " + name)
                self.volumes[name] = control
            sink.add_pad(Gst.GhostPad.new("sink", convert.get_static_pad("sink")))
            self.pipeline = element("playbin", flags=2, uri=uri(clip.path))
            self.pipeline.set_property("audio-sink", sink)
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            self.bus_handler = bus.connect("message", self._message)
            self.preparing = True
            self._state("loading")
            self.pipeline.set_state(Gst.State.PAUSED)
        except Exception:
            self.stop()
            raise

    def _state(self, state):
        self.state = state
        self.emit("changed", state)

    def _message(self, bus, message):
        if not self.pipeline or message.src is None:
            return
        if message.type == Gst.MessageType.ERROR:
            text = message_error(message)
            self.stop()
            self.emit("error", text)
        elif message.type == Gst.MessageType.ASYNC_DONE and self.preparing:
            self.preparing = False
            self.seek(0)
            self.pipeline.set_state(Gst.State.PLAYING)
            self._state("playing")
        elif message.type in (Gst.MessageType.EOS, Gst.MessageType.SEGMENT_DONE):
            self.stop()
            self.emit("finished")

    def seek(self, position):
        if not self.pipeline or not self.clip:
            return False
        start = self.clip.trim_start + max(0, min(position, self.clip.length))
        end = self.clip.trim_end if self.clip.trim_end is not None else self.clip.duration
        return self.pipeline.seek(1.0, Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
                                  Gst.SeekType.SET, int(start * Gst.SECOND),
                                  Gst.SeekType.SET if end else Gst.SeekType.NONE, int(end * Gst.SECOND) if end else 0)

    def position(self):
        if not self.pipeline or not self.clip:
            return 0
        ok, position = self.pipeline.query_position(Gst.Format.TIME)
        return max(0, position / Gst.SECOND - self.clip.trim_start) if ok else 0

    def pause(self):
        if self.pipeline and not self.preparing:
            paused = self.state == "playing"
            self.pipeline.set_state(Gst.State.PAUSED if paused else Gst.State.PLAYING)
            self._state("paused" if paused else "playing")

    def set_volume(self, name, value):
        if name in self.volumes:
            self.volumes[name].set_property("volume", max(0, min(1, value)))

    def stop(self):
        pipeline, self.pipeline = self.pipeline, None
        self.preparing = False
        self.volumes = {}
        if pipeline:
            bus = pipeline.get_bus()
            bus.disconnect(self.bus_handler)
            bus.remove_signal_watch()
            pipeline.set_state(Gst.State.NULL)
        self._state("stopped")


class Recorder:
    def __init__(self, source, destination):
        self.destination = Path(destination)
        self.pipeline = Gst.parse_launch("pulsesrc name=input ! audioconvert ! audioresample quality=10 ! "
                                         "audio/x-raw,format=S24LE,rate=48000,channels=2 ! wavenc ! filesink name=output")
        self.pipeline.get_by_name("input").set_property("device", source)
        self.pipeline.get_by_name("input").set_property("stream-properties", Gst.Structure.new_from_string("props,application.name=SMITH-Soundboard,media.role=Production"))
        self.pipeline.get_by_name("output").set_property("location", str(self.destination))

    def start(self):
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise MediaError("Não foi possível iniciar a gravação.")

    def error(self):
        message = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
        return message_error(message) if message else None

    def stop(self):
        try:
            error = self.error()
            if error:
                raise MediaError(error)
            self.pipeline.send_event(Gst.Event.new_eos())
            message = self.pipeline.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            if not message:
                raise MediaError("A gravação não foi finalizada a tempo.")
            if message.type == Gst.MessageType.ERROR:
                raise MediaError(message_error(message))
            return self.destination
        finally:
            self.pipeline.set_state(Gst.State.NULL)


def export_clip(clip, destination, normalize=False, target_db=0, cancel=None):
    """Render the selected trim and gain. Run in a worker, never the GTK thread."""
    suffix = Path(destination).suffix.lower()
    encoder = {".wav": "audioconvert ! audio/x-raw,format=S24LE ! wavenc",
               ".flac": "audioconvert ! audio/x-raw,format=S24LE ! flacenc quality=8",
               ".mp3": "audioconvert ! audio/x-raw,format=S16LE ! lamemp3enc target=bitrate bitrate=320 cbr=true ! id3v2mux"}.get(suffix)
    if not encoder:
        raise MediaError("Escolha WAV, FLAC ou MP3 para exportar.")
    # Decode to a bounded raw queue, trim by sample count, then encode once.
    # This also works with encoders that cannot preroll or flush-seek reliably.
    decoder = Gst.parse_launch("uridecodebin name=decode ! audioconvert ! audioresample quality=10 ! " + CAPS +
                               " ! appsink name=raw sync=false max-buffers=8 drop=false")
    decoder.get_by_name("decode").set_property("uri", uri(clip.path))
    raw = decoder.get_by_name("raw")
    output = Gst.parse_launch("appsrc name=raw format=time is-live=false block=true max-bytes=1048576 ! "
                              "volume name=gain ! rglimiter ! " + encoder + " ! filesink name=file")
    source = output.get_by_name("raw")
    source.set_property("caps", Gst.Caps.from_string(CAPS))
    output.get_by_name("gain").set_property("volume", gain_for(clip, normalize, target_db))
    output.get_by_name("file").set_property("location", str(destination))
    begin = round(clip.trim_start * RATE)
    end_time = clip.trim_end if clip.trim_end is not None else clip.duration
    end = round(end_time * RATE) if end_time else None
    read, written = 0, 0
    deadline = time.monotonic() + max(60, clip.duration * 2)
    try:
        output.set_state(Gst.State.PLAYING)
        decoder.set_state(Gst.State.PLAYING)
        while end is None or read < end:
            if cancel and cancel.is_set():
                raise MediaError("Operação cancelada.")
            for pipeline in (decoder, output):
                error = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
                if error:
                    raise MediaError(message_error(error))
            sample = raw.emit("try-pull-sample", 100 * Gst.MSECOND)
            if not sample:
                if raw.get_property("eos"):
                    break
                if time.monotonic() > deadline:
                    raise MediaError("A exportação excedeu o tempo de resposta.")
                continue
            buffer = sample.get_buffer()
            frames = buffer.get_size() // 8
            first, last = max(read, begin), min(read + frames, end) if end is not None else read + frames
            if last > first:
                trimmed = buffer.copy_region(Gst.BufferCopyFlags.MEMORY, (first - read) * 8, (last - first) * 8)
                trimmed.pts = written * Gst.SECOND // RATE
                trimmed.dts = Gst.CLOCK_TIME_NONE
                trimmed.duration = (last - first) * Gst.SECOND // RATE
                trimmed.offset, trimmed.offset_end = written, written + last - first
                if source.emit("push-buffer", trimmed) != Gst.FlowReturn.OK:
                    error = output.get_bus().pop_filtered(Gst.MessageType.ERROR)
                    raise MediaError(message_error(error) if error else "A exportação foi interrompida.")
                written += last - first
            read += frames
        if not written:
            raise MediaError("O intervalo selecionado não contém áudio.")
        source.emit("end-of-stream")
        message = output.get_bus().timed_pop_filtered(20 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if not message:
            raise MediaError("Não foi possível finalizar a exportação.")
        if message.type == Gst.MessageType.ERROR:
            raise MediaError(message_error(message))
    finally:
        decoder.set_state(Gst.State.NULL)
        output.set_state(Gst.State.NULL)
