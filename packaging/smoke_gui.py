#!/usr/bin/env python3
"""Desktop/CI smoke check; fails on GTK exceptions and exercises the real window."""
from pathlib import Path
import os
import sys
import tempfile

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, Gio, GLib

if not Gtk.init_check()[0]:
    raise SystemExit("Não há desktop acessível. Execute sob Xvfb ou na sessão gráfica.")
from soundboard.gui import Application

errors = []
old_hook = sys.excepthook
def error_hook(kind, value, traceback):
    errors.append(str(value))
    old_hook(kind, value, traceback)
sys.excepthook = error_hook

with tempfile.TemporaryDirectory(prefix="smith-gui-") as temporary:
    os.environ["XDG_DATA_HOME"] = temporary
    app = Application()
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
    def verify():
        try:
            window = app.window
            assert window and window.get_visible(), "Janela não criada"
            assert window.source.get_active_id() in ("@auto", "@none") or window.source.get_active_id(), "Seleção de voz inválida"
            assert not window.normalize.get_active(), "Normalização deve começar desligada"
            assert not window.block_voice.get_active(), "A voz deve continuar durante os efeitos"
            assert window.sliders["mic"].get_value() == 100, "Ganho original deve começar em 100%"
            capture = root / "build/gui-smoke.png"
            capture.parent.mkdir(exist_ok=True)
            pixbuf = Gdk.pixbuf_get_from_window(window.get_window(), 0, 0, window.get_allocated_width(), window.get_allocated_height())
            if pixbuf:
                pixbuf.savev(str(capture), "png", [], [])
        except Exception as exc:
            errors.append(str(exc))
        app.quit()
        return False
    GLib.timeout_add(1500, verify)
    status = app.run(["smith-gui-test"])
    if errors or status:
        raise SystemExit("Falha na interface: " + "; ".join(errors))
print("Interface GTK: PASSOU")
