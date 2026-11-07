"""Desktop portal shortcuts on Wayland; passive X11 grabs on Xorg."""
import ctypes
import ctypes.util
import os
import uuid

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gio, GLib, Gdk, Gtk


def portal_trigger(accelerator):
    key, modifiers = Gtk.accelerator_parse(accelerator)
    parts = []
    for flag, name in ((Gdk.ModifierType.CONTROL_MASK, "CTRL"), (Gdk.ModifierType.MOD1_MASK, "ALT"),
                       (Gdk.ModifierType.SHIFT_MASK, "SHIFT"), (Gdk.ModifierType.SUPER_MASK, "LOGO")):
        if modifiers & flag:
            parts.append(name)
    parts.append(Gdk.keyval_name(key) or "")
    return "+".join(parts)


class GlobalShortcuts:
    def __init__(self, activate, status):
        self.activate, self.status = activate, status
        self.connection = None
        self.session = None
        self.subscriptions = []
        self.generation = 0
        self.x11 = None
        self.available = False

    def bind(self, entries):
        self.close()
        self.entries = list(entries)
        if not entries:
            self.status("Nenhum atalho global configurado.")
            return
        generation = self.generation
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.connection.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                 "org.freedesktop.DBus.Properties", "Get",
                                 GLib.Variant("(ss)", ("org.freedesktop.portal.GlobalShortcuts", "version")),
                                 None, Gio.DBusCallFlags.NONE, 3000, None, self._version, generation)
        except Exception as exc:
            self._fallback(str(exc))

    def _version(self, connection, result, generation):
        if generation != self.generation:
            return
        try:
            connection.call_finish(result)
            options = {"session_handle_token": GLib.Variant("s", "smith_" + uuid.uuid4().hex)}
            self._request("CreateSession", options, self._session_ready)
        except Exception as exc:
            self._fallback(str(exc))

    def _request(self, method, options, completed):
        token = "smith_" + uuid.uuid4().hex
        options["handle_token"] = GLib.Variant("s", token)
        sender = self.connection.get_unique_name().lstrip(":").replace(".", "_")
        path = "/org/freedesktop/portal/desktop/request/" + sender + "/" + token
        generation = self.generation
        def response(connection, sender, object_path, interface, signal, parameters):
            if generation == self.generation:
                code, data = parameters.unpack()
                completed(code, data)
        subscription = self.connection.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
                                                       "Response", path, None, Gio.DBusSignalFlags.NONE, response)
        self.subscriptions.append(subscription)
        if method == "CreateSession":
            parameters = GLib.Variant("(a{sv})", (options,))
        else:
            shortcuts = [(str(key), {"description": GLib.Variant("s", title),
                                    "preferred_trigger": GLib.Variant("s", portal_trigger(accelerator))})
                         for key, title, accelerator in self.entries]
            parameters = GLib.Variant("(oa(sa{sv})sa{sv})", (self.session, shortcuts, "", options))
        def returned(connection, result):
            if generation != self.generation:
                return
            try:
                connection.call_finish(result)
            except Exception as exc:
                self._fallback(str(exc))
        self.connection.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                             "org.freedesktop.portal.GlobalShortcuts", method, parameters, None,
                             Gio.DBusCallFlags.NONE, 5000, None, returned)

    def _session_ready(self, code, results):
        if code or not results.get("session_handle"):
            self._fallback("Registro de atalhos cancelado pelo desktop.")
            return
        self.session = results["session_handle"]
        def activated(connection, sender, path, interface, signal, parameters):
            session, key, timestamp, options = parameters.unpack()
            if session == self.session:
                self.activate(int(key))
        self.subscriptions.append(self.connection.signal_subscribe("org.freedesktop.portal.Desktop",
            "org.freedesktop.portal.GlobalShortcuts", "Activated", None, None, Gio.DBusSignalFlags.NONE, activated))
        self.status("Confirme os atalhos na janela de configuração do desktop.")
        self._request("BindShortcuts", {}, self._bound)

    def _bound(self, code, results):
        if code:
            self._fallback("O desktop não autorizou os atalhos globais.")
        else:
            self.available = True
            bound = results.get("shortcuts", [])
            triggers = [details.get("trigger_description", "") for key, details in bound]
            self.available = bool(bound)
            self.status(f"Portal: {len(bound)}/{len(self.entries)} atalhos globais ativos" + (": " + ", ".join(triggers) if any(triggers) else "."))

    def _fallback(self, reason):
        if self.x11:
            return
        if os.environ.get("XDG_SESSION_TYPE") == "x11" or (os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")):
            try:
                self.x11 = X11Shortcuts(self.entries, self.activate)
                self.available = True
                self.status("Atalhos globais ativos no X11.")
                return
            except Exception as exc:
                reason = str(exc)
        self.available = False
        self.status("Atalhos locais ativos; globais indisponíveis neste desktop. " + reason)

    def close(self):
        self.generation += 1
        if self.x11:
            self.x11.close()
            self.x11 = None
        if self.connection:
            for subscription in self.subscriptions:
                self.connection.signal_unsubscribe(subscription)
            if self.session:
                self.connection.call("org.freedesktop.portal.Desktop", self.session, "org.freedesktop.portal.Session",
                                     "Close", None, None, Gio.DBusCallFlags.NONE, 1000, None, None)
        self.subscriptions, self.session, self.connection = [], None, None
        self.available = False


class XKeyEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int),
                ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("root", ctypes.c_ulong),
                ("subwindow", ctypes.c_ulong), ("time", ctypes.c_ulong), ("x", ctypes.c_int), ("y", ctypes.c_int),
                ("x_root", ctypes.c_int), ("y_root", ctypes.c_int), ("state", ctypes.c_uint),
                ("keycode", ctypes.c_uint), ("same_screen", ctypes.c_int)]


class XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("key", XKeyEvent), ("pad", ctypes.c_long * 24)]


class X11Shortcuts:
    def __init__(self, entries, activate):
        self.library = ctypes.CDLL(ctypes.util.find_library("X11"))
        signatures = {"XOpenDisplay": (ctypes.c_void_p, [ctypes.c_char_p]),
                      "XDefaultRootWindow": (ctypes.c_ulong, [ctypes.c_void_p]),
                      "XStringToKeysym": (ctypes.c_ulong, [ctypes.c_char_p]),
                      "XKeysymToKeycode": (ctypes.c_uint, [ctypes.c_void_p, ctypes.c_ulong]),
                      "XConnectionNumber": (ctypes.c_int, [ctypes.c_void_p]),
                      "XPending": (ctypes.c_int, [ctypes.c_void_p]),
                      "XNextEvent": (ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(XEvent)]),
                      "XGrabKey": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int]),
                      "XUngrabKey": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]),
                      "XSync": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
                      "XCloseDisplay": (ctypes.c_int, [ctypes.c_void_p]),
                      "XSetErrorHandler": (ctypes.c_void_p, [ctypes.c_void_p])}
        for name, (result, arguments) in signatures.items():
            function = getattr(self.library, name)
            function.restype, function.argtypes = result, arguments
        self.display = self.library.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("Não foi possível abrir o display X11.")
        self.root = self.library.XDefaultRootWindow(self.display)
        self.bindings, self.activate = {}, activate
        self.watch = None
        errors = []
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        callback = callback_type(lambda display, error: (errors.append(True) or 0))
        previous = self.library.XSetErrorHandler(ctypes.cast(callback, ctypes.c_void_p))
        try:
            for clip_id, title, accelerator in entries:
                key, modifiers = Gtk.accelerator_parse(accelerator)
                keysym = self.library.XStringToKeysym((Gdk.keyval_name(key) or "").encode())
                code = self.library.XKeysymToKeycode(self.display, keysym)
                mask = x11_modifiers(modifiers)
                if not code:
                    raise RuntimeError("Tecla não disponível: " + accelerator)
                self.bindings[(code, mask)] = clip_id
                for locks in (0, 2, 16, 18):
                    self.library.XGrabKey(self.display, code, mask | locks, self.root, False, 1, 1)
            self.library.XSync(self.display, False)
        finally:
            self.library.XSetErrorHandler(previous)
        if errors:
            self.close()
            raise RuntimeError("Um dos atalhos já está reservado por outro aplicativo.")
        self.watch = GLib.io_add_watch(self.library.XConnectionNumber(self.display), GLib.IO_IN, self._events)

    def _events(self, descriptor, condition):
        while self.library.XPending(self.display):
            event = XEvent()
            self.library.XNextEvent(self.display, ctypes.byref(event))
            if event.type == 2:
                clip_id = self.bindings.get((event.key.keycode, event.key.state & 77))
                if clip_id is not None:
                    self.activate(clip_id)
        return True

    def close(self):
        if self.watch:
            GLib.source_remove(self.watch)
            self.watch = None
        if self.display:
            self.library.XUngrabKey(self.display, 0, 32768, self.root)
            self.library.XCloseDisplay(self.display)
            self.display = None


def x11_modifiers(modifiers):
    mask = int(modifiers) & 13  # Shift, Control, Mod1
    if modifiers & (Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.MOD4_MASK):
        mask |= 64
    return mask
