"""Read-only diagnostics and an explicitly invoked, isolated virtual-mic test."""
import array
import os
from pathlib import Path
import platform
import shutil
import tempfile
import time

from . import __version__
from .media import Gst, message_error
from .routing import Pulse, Router


def report():
    lines = ["SMITH Soundboard " + __version__, platform.platform(), Gst.version_string(),
             "Desktop: " + os.environ.get("XDG_CURRENT_DESKTOP", "não informado"),
             "Sessão: " + os.environ.get("XDG_SESSION_TYPE", "não informada"), ""]
    for command in ("pactl", "wpctl"):
        lines.append(command + ": " + (shutil.which(command) or "ausente"))
    for name in ("playbin", "pulsesrc", "pulsesink", "audioresample", "rganalysis", "rglimiter", "wavenc",
                 "mpg123audiodec", "flacdec", "vorbisdec", "opusdec", "avdec_aac", "avdec_wmav2", "lamemp3enc"):
        lines.append(name + ": " + ("OK" if Gst.ElementFactory.find(name) else "ausente"))
    try:
        pulse = Pulse()
        info = pulse.info()
        lines += ["", "Servidor: " + info.get("server_name", "desconhecido"),
                  "Entrada padrão: " + info.get("default_source_name", "?"),
                  "Saída padrão: " + info.get("default_sink_name", "?")]
        sources, sinks = pulse.devices()
        lines += ["Microfones físicos: " + str(len(sources)), "Saídas físicas: " + str(len(sinks))]
        for item in sources + sinks:
            lines.append("  " + item["name"] + " · " + item.get("description", ""))
    except Exception as exc:
        lines += ["", "Áudio da sessão: INDISPONÍVEL", str(exc)]
    return "\n".join(lines)


def test_routing():
    """Transmit a generated tone into our virtual mic; do not use hardware/defaults."""
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", tempfile.gettempdir())) / ("smith-soundboard-test-" + str(os.getuid()))
    router = Router(runtime)
    capture = tone = None
    samples = [0, 0.0]
    try:
        router.start(None)
        capture = Gst.parse_launch("pulsesrc name=input ! audioconvert ! audioresample ! "
                                   "audio/x-raw,format=F32LE,rate=48000,channels=1 ! appsink name=measure emit-signals=true sync=false")
        capture.get_by_name("input").set_property("device", "smith_soundboard_mic")
        def received(sink):
            sample = sink.emit("pull-sample")
            buffer = sample.get_buffer()
            ok, mapped = buffer.map(Gst.MapFlags.READ)
            if ok:
                values = array.array("f", mapped.data)
                samples[0] += len(values)
                samples[1] = max(samples[1], max((abs(value) for value in values), default=0))
                buffer.unmap(mapped)
            return Gst.FlowReturn.OK
        capture.get_by_name("measure").connect("new-sample", received)
        capture.set_state(Gst.State.PLAYING)
        capture.get_state(3 * Gst.SECOND)
        tone = Gst.parse_launch("audiotestsrc wave=sine volume=0.25 num-buffers=100 samplesperbuffer=480 ! "
                                "audio/x-raw,rate=48000,channels=2 ! audioconvert ! pulsesink name=output")
        tone.get_by_name("output").set_property("device", router.mix_sink)
        tone.set_state(Gst.State.PLAYING)
        message = tone.get_bus().timed_pop_filtered(5 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
        if not message or message.type == Gst.MessageType.ERROR:
            raise RuntimeError(message_error(message) if message else "O teste de reprodução não respondeu.")
        # Capture bus wait also lets the final pulse buffers reach the microphone.
        error = capture.get_bus().timed_pop_filtered(500 * Gst.MSECOND, Gst.MessageType.ERROR)
        if error:
            raise RuntimeError(message_error(error))
        if samples[0] < 24000 or not .1 < samples[1] < .4:
            raise RuntimeError(f"Sinal inesperado: {samples[0]} amostras, pico {samples[1]:.4f}.")
        return f"PASSOU: tom digital → mistura → limitador → microfone virtual; {samples[0]} amostras, pico {samples[1]:.4f}."
    finally:
        for pipeline in (tone, capture):
            if pipeline:
                pipeline.set_state(Gst.State.NULL)
        router.close()
