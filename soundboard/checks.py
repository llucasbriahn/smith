"""Dependency checks without importing the GUI or opening an audio stream."""
import shutil
import sys

CORE = ("playbin", "uridecodebin", "audioconvert", "audioresample", "capsfilter", "volume", "tee", "queue",
        "pulsesrc", "pulsesink", "rganalysis", "rglimiter", "level", "appsink", "appsrc", "wavparse", "wavenc", "fakesink")
CODECS = {"MP3 leitura": ("mpg123audiodec", "avdec_mp3"), "MP3 exportação": ("lamemp3enc",),
          "FLAC leitura": ("flacdec",), "FLAC exportação": ("flacenc",), "OGG/Vorbis": ("vorbisdec",),
          "Opus": ("opusdec",), "AAC/M4A": ("avdec_aac", "faad"), "WMA": ("avdec_wmav2",)}


def check(audio=False):
    result = {"python": sys.version.split()[0], "missing": [], "codecs": {}, "audio": None}
    if sys.version_info < (3, 10):
        result["missing"].append("Python >= 3.10")
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
        gi.require_version("Gst", "1.0")
        gi.require_foreign("cairo")
        import cairo
        from gi.repository import Gtk, Gdk, Gio, Gst
        Gst.init(None)
        result["gstreamer"] = Gst.version_string()
        result["missing"] += ["GStreamer: " + name for name in CORE if not Gst.ElementFactory.find(name)]
        result["codecs"] = {name: any(Gst.ElementFactory.find(factory) for factory in alternatives)
                            for name, alternatives in CODECS.items()}
        if Gst.ElementFactory.find("lamemp3enc") and not Gst.ElementFactory.find("id3v2mux"):
            result["codecs"]["MP3 exportação"] = False
    except (ImportError, ValueError) as exc:
        result["missing"].append("GTK 3 / GStreamer / PyGObject / Cairo: " + str(exc))
    if not shutil.which("pactl"):
        result["missing"].append("pactl (cliente PulseAudio)")
    if audio and not result["missing"]:
        try:
            from .routing import Pulse
            info = Pulse().info()
            sources, sinks = Pulse().devices()
            result["audio"] = {"ok": True, "server": info.get("server_name"), "microphones": len(sources), "outputs": len(sinks)}
        except Exception as exc:
            result["audio"] = {"ok": False, "error": str(exc)}
    result["ok"] = not result["missing"] and (not audio or bool(result["audio"] and result["audio"]["ok"]))
    return result


def format_report(result):
    lines = ["Dependências essenciais: " + ("OK" if not result["missing"] else "FALTANDO")]
    lines += ["  " + item for item in result["missing"]]
    lines += [name + ": " + ("OK" if available else "indisponível neste ambiente") for name, available in result["codecs"].items()]
    if result["audio"]:
        lines.append("Servidor de áudio: " + ("OK" if result["audio"]["ok"] else result["audio"]["error"]))
    return "\n".join(lines)
