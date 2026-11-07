"""Own only our PulseAudio-compatible modules, and restore explicit routing changes."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import uuid

from .media import CAPS, Gst, MediaError, message_error

MIC_NAME = "smith_soundboard_mic"
NO_MONITOR = (None, "", "n/a", "N/A", "4294967295", 4294967295, -1, "-1")


def physical_source(item):
    """PulseAudio versions encode PA_INVALID_INDEX as numbers or 'n/a'."""
    return (not item["name"].startswith("smith_") and not item["name"].endswith(".monitor") and
            item.get("monitor_of_sink") in NO_MONITOR and
            item.get("monitor_of_sink_name") in NO_MONITOR and
            item.get("properties", {}).get("device.class") != "monitor")


def choose_source(sources, preferred=None, default=None):
    available = {item["name"] for item in sources if physical_source(item)}
    for name in (preferred, default):
        if name in available:
            return name
    return next((item["name"] for item in sources if item["name"] in available), None)


class RoutingError(RuntimeError):
    pass


class Pulse:
    def call(self, *args, json_output=False):
        command = ["pactl"] + (["--format=json"] if json_output else []) + [str(arg) for arg in args]
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RoutingError("Não foi possível executar pactl: " + str(exc)) from exc
        if result.returncode:
            raise RoutingError("Não foi possível acessar o áudio da sessão. Execute no desktop, sem sudo.\n" + result.stderr.strip())
        return json.loads(result.stdout) if json_output else result.stdout.strip()

    def list(self, kind):
        return self.call("list", kind, json_output=True)

    def info(self):
        return self.call("info", json_output=True)

    def devices(self):
        sources = [item for item in self.list("sources") if physical_source(item)]
        sinks = [item for item in self.list("sinks") if not item["name"].startswith("smith_")]
        return sources, sinks


class Router:
    def __init__(self, state_dir, pulse=None):
        self.pulse = pulse or Pulse()
        self.directory = Path(state_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.directory / "audio-session.json"
        self.journal = None
        self.lock_file = None
        self.bridge = None
        self.voice = None
        self.voice_source = None
        self.voice_source_index = None
        self.mix_sink = None
        self.output_sink = None
        self.voice_gain = 1
        self.voice_muted = False
        self.bridge_error = None

    @property
    def active(self):
        return self.journal is not None

    def _save(self):
        temporary = self.journal_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.journal, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.journal_path)

    def _load(self, name, *arguments):
        module_id = int(self.pulse.call("load-module", name, *arguments))
        self.journal["modules"].append({"id": module_id, "name": name})
        self._save()
        return module_id

    def start(self, source=None):
        if self.active:
            return
        self.lock_file = open(self.directory / "audio.lock", "a+")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock_file.close()
            self.lock_file = None
            raise RoutingError("Outra instância está gerenciando o microfone virtual.") from exc
        try:
            if self.journal_path.exists():
                self._cleanup(json.loads(self.journal_path.read_text(encoding="utf-8")))
            sources = self.pulse.list("sources")
            if any(item["name"] == MIC_NAME for item in sources):
                raise RoutingError("Já existe um microfone com o nome " + MIC_NAME + ". Feche a outra instância antes de continuar.")
            token = uuid.uuid4().hex[:12]
            self.mix_sink, self.output_sink = "smith_mix_" + token, "smith_out_" + token
            self.journal = {"token": token, "modules": [], "default_before": None, "moved": []}
            self._save()
            for name, label in ((self.mix_sink, "SMITH_Mix"), (self.output_sink, "SMITH_Output")):
                self._load("module-null-sink", "sink_name=" + name, "rate=48000", "channels=2",
                           "format=float32le", "channel_map=front-left,front-right", "sink_properties=device.description=" + label)
            self._load("module-remap-source", "master=" + self.output_sink + ".monitor", "source_name=" + MIC_NAME,
                       "channels=2", "channel_map=front-left,front-right", "master_channel_map=front-left,front-right",
                       "rate=48000", "format=float32le", "remix=no", "source_properties=device.description=SMITH_Soundboard_Microphone")
            self.bridge = Gst.parse_launch("pulsesrc name=input provide-clock=false ! audioconvert ! audioresample quality=10 ! " +
                                           CAPS + " ! rglimiter ! level name=meter post-messages=true interval=100000000 ! "
                                           "pulsesink name=output sync=true")
            self.bridge.get_by_name("input").set_property("device", self.mix_sink + ".monitor")
            properties = Gst.Structure.new_from_string("props,application.name=SMITH-Soundboard,media.role=Production")
            self.bridge.get_by_name("input").set_property("stream-properties", properties)
            self.bridge.get_by_name("output").set_property("stream-properties", properties)
            self.bridge.get_by_name("input").set_property("buffer-time", 60000)
            self.bridge.get_by_name("input").set_property("latency-time", 10000)
            self.bridge.get_by_name("output").set_property("device", self.output_sink)
            self.bridge.get_by_name("output").set_property("buffer-time", 60000)
            self.bridge.get_by_name("output").set_property("latency-time", 10000)
            self.bridge.set_state(Gst.State.PLAYING)
            result, state, pending = self.bridge.get_state(4 * Gst.SECOND)
            if result == Gst.StateChangeReturn.FAILURE or state != Gst.State.PLAYING:
                error = self.bridge.get_bus().pop_filtered(Gst.MessageType.ERROR)
                raise RoutingError(message_error(error) if error else "O processador do microfone não iniciou.")
            self.set_voice_source(source)
        except Exception:
            self.close()
            raise

    def set_voice_source(self, source):
        if not self.active:
            return
        if source:
            sources = self.pulse.list("sources")
            if source == MIC_NAME or source.endswith(".monitor") or source.startswith("smith_"):
                raise RoutingError("Escolha um microfone físico para evitar realimentação.")
            selected = next((item for item in sources if item["name"] == source and physical_source(item)), None)
            if not selected:
                raise RoutingError("O microfone selecionado não está disponível.")
            if source == self.voice_source and self.voice_source_index == selected.get("index") and self.voice is not None and any(
                    str(item["index"]) == str(self.voice) for item in self.pulse.list("modules")):
                self.set_voice(self.voice_gain, self.voice_muted)
                return
        previous = self.voice
        replacement = None
        if source:
            replacement = self._load("module-loopback", "source=" + source, "sink=" + self.mix_sink,
                                     "rate=48000", "channels=2", "latency_msec=30", "source_dont_move=true", "sink_dont_move=true",
                                     "sink_input_properties=application.name=SMITH-Soundboard",
                                     "source_output_properties=application.name=SMITH-Soundboard")
        try:
            if previous is not None and any(str(item["index"]) == str(previous) for item in self.pulse.list("modules")):
                self.pulse.call("unload-module", previous)
        except Exception:
            if replacement is not None:
                self.pulse.call("unload-module", replacement)
                self.journal["modules"] = [item for item in self.journal["modules"] if item["id"] != replacement]
                self._save()
            raise
        self.journal["modules"] = [item for item in self.journal["modules"] if item["id"] != previous]
        self.voice, self.voice_source = replacement, source
        self.voice_source_index = selected.get("index") if source else None
        self._save()
        if self.voice is not None:
            self.set_voice(self.voice_gain, self.voice_muted)

    def set_voice(self, gain=None, muted=None):
        if gain is not None:
            self.voice_gain = max(0, min(1.5, gain))
        if muted is not None:
            self.voice_muted = bool(muted)
        if self.voice is not None:
            for stream in self.pulse.list("sink-inputs"):
                if str(stream.get("owner_module")) == str(self.voice):
                    self.pulse.call("set-sink-input-volume", stream["index"], str(round(self.voice_gain * 100)) + "%")
                    self.pulse.call("set-sink-input-mute", stream["index"], "1" if self.voice_muted else "0")

    def meter(self):
        if not self.bridge:
            return 0
        bus, level = self.bridge.get_bus(), 0
        while True:
            message = bus.pop_filtered(Gst.MessageType.ELEMENT | Gst.MessageType.ERROR)
            if not message:
                break
            if message.type == Gst.MessageType.ERROR:
                self.bridge_error = message_error(message)
            elif message.get_structure().get_name() == "level":
                peaks = message.get_structure().get_value("peak")
                level = max(level, 10 ** (max(peaks) / 20)) if peaks else level
        return min(level, 1)

    def make_default(self):
        if not self.active:
            raise RoutingError("Ative o microfone virtual primeiro.")
        current = self.pulse.call("get-default-source")
        if current == MIC_NAME:
            return
        self.journal["default_before"] = current
        self._save()
        self.pulse.call("set-default-source", MIC_NAME)

    def capture_apps(self):
        own = {str(item["id"]) for item in self.journal["modules"]} if self.journal else set()
        return [item for item in self.pulse.list("source-outputs")
                if str(item.get("owner_module")) not in own and
                item.get("properties", {}).get("application.name", "") != "SMITH-Soundboard"]

    def move_capture(self, index):
        if not self.active:
            raise RoutingError("Ative o microfone virtual primeiro.")
        stream = next((item for item in self.capture_apps() if item["index"] == index), None)
        if not stream:
            raise RoutingError("O aplicativo encerrou a captura. Abra o teste de microfone nele e tente novamente.")
        sources = {str(item["index"]): item["name"] for item in self.pulse.list("sources")}
        original = sources.get(str(stream["source"]))
        if original == MIC_NAME:
            return
        props = stream.get("properties", {})
        self.journal["moved"].append({"index": index, "source": original,
                                      "pid": props.get("application.process.id"), "name": props.get("application.name")})
        self._save()
        self.pulse.call("move-source-output", index, MIC_NAME)

    def _cleanup(self, journal):
        if not isinstance(journal, dict) or not journal.get("token"):
            raise RoutingError("Registro de áudio inválido: " + str(self.journal_path))
        sources = {str(item["index"]): item["name"] for item in self.pulse.list("sources")}
        available = set(sources.values())
        for saved in journal.get("moved", []):
            if saved.get("source") not in available:
                continue
            for stream in self.pulse.list("source-outputs"):
                props = stream.get("properties", {})
                if (stream["index"] == saved["index"] and sources.get(str(stream["source"])) == MIC_NAME and
                        props.get("application.process.id") == saved.get("pid") and props.get("application.name") == saved.get("name")):
                    self.pulse.call("move-source-output", stream["index"], saved["source"])
        previous = journal.get("default_before")
        if previous in available and self.pulse.call("get-default-source") == MIC_NAME:
            self.pulse.call("set-default-source", previous)
        modules = {str(item["index"]): item for item in self.pulse.list("modules")}
        for saved in reversed(journal.get("modules", [])):
            module = modules.get(str(saved["id"]))
            if module and module["name"] == saved["name"] and journal["token"] in module.get("argument", ""):
                self.pulse.call("unload-module", saved["id"])
        self.journal_path.unlink(missing_ok=True)

    def close(self):
        if self.bridge:
            self.bridge.set_state(Gst.State.NULL)
            self.bridge = None
        try:
            if self.journal:
                self._cleanup(self.journal)
                self.journal = None
        finally:
            self.voice = None
            self.voice_source = None
            self.voice_source_index = None
            if self.lock_file:
                fcntl.flock(self.lock_file, fcntl.LOCK_UN)
                self.lock_file.close()
                self.lock_file = None
