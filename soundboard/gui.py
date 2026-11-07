"""Native GTK desktop soundboard. No browser server or elevated privileges."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import json
import math
import os
from pathlib import Path
import random
import sqlite3
import threading
import time
import uuid

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, Gio, GLib, Gtk

from . import APP_ID, __version__
from .library import Library
from . import media
from .routing import MIC_NAME, Pulse, Router, choose_source
from .shortcuts import GlobalShortcuts

CSS = b"""window { background: #171a20; color: #edf0f5; }
headerbar { background: #222630; border-bottom: 1px solid #363c49; }
button { border-radius: 5px; padding: 6px 12px; }
button.suggested-action { background: #167d78; color: white; border-color: #167d78; }
.transport { background: #222630; border-top: 1px solid #363c49; }
.sidebar { background: #1e222b; }
list row { padding: 9px 10px; }
treeview { background: #171a20; color: #edf0f5; }
treeview:selected, list row:selected { background: #235d62; color: white; }
treeview header button { background: #262b35; color: #b8c4d2; border-radius: 0; }
.muted { color: #9faebd; }
.title { font-size: 16px; font-weight: bold; }
.badge { color: #69d7c8; font-weight: bold; }
scale trough { min-height: 4px; }
scale highlight { background: #41b8aa; }
entry, combobox button, spinbutton { background: #222630; color: #edf0f5; }
"""


def data_directory():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "smith-soundboard"


def clock(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def button(label, callback, icon=None):
    result = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON) if icon else Gtk.Button(label=label)
    result.set_tooltip_text(label)
    result.connect("clicked", lambda item: callback())
    return result


def scroll(child):
    view = Gtk.ScrolledWindow()
    view.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    view.add(child)
    return view


class Waveform(Gtk.DrawingArea):
    def __init__(self, clip, on_pick):
        super().__init__()
        self.clip, self.on_pick = clip, on_pick
        self.start, self.end = clip.trim_start, clip.trim_end or clip.duration
        self.wave = json.loads(clip.waveform) if clip.waveform else []
        self.set_size_request(600, 150)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("draw", self.draw_wave)
        self.connect("button-press-event", self.pick)

    def pick(self, widget, event):
        if event.button in (1, 3):
            self.on_pick(event.x / max(1, self.get_allocated_width()) * self.clip.duration, event.button)

    def draw_wave(self, widget, context):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        context.set_source_rgb(.08, .10, .14)
        context.paint()
        duration = max(self.clip.duration, .001)
        context.set_source_rgba(.15, .7, .65, .18)
        context.rectangle(self.start / duration * width, 0, (self.end - self.start) / duration * width, height)
        context.fill()
        context.set_source_rgb(.36, .85, .78)
        for index, peak in enumerate(self.wave):
            x = index / len(self.wave) * width
            amplitude = min(1, math.sqrt(max(0, peak))) * height * .44
            context.move_to(x, height / 2 - amplitude)
            context.line_to(x, height / 2 + amplitude)
        context.set_line_width(max(1, width / max(1, len(self.wave)) * .7))
        context.stroke()
        context.set_source_rgb(.97, .66, .32)
        for position in (self.start, self.end):
            x = min(width - 1, max(1, position / duration * width))
            context.rectangle(x, 0, 2, height)
        context.fill()
        if not self.wave:
            context.set_source_rgb(.65, .7, .75)
            context.move_to(20, height / 2)
            context.show_text("Use Analisar volume para gerar a forma de onda.")
        return False


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="SMITH Soundboard")
        self.app = app
        self.library, self.player, self.router = app.library, app.player, app.router
        if not self.library.preference("fidelity_v1_1", False):
            for key, value in (("normalize", False), ("block_voice", False), ("volume_local", 1), ("volume_mic", 1)):
                self.library.set_preference(key, value)
            self.library.set_preference("fidelity_v1_1", True)
        self.category = None
        self.filter_kind = "all"
        self.sort = "title"
        self.updating_seek = False
        self.closing = False
        self.cancel = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="soundboard")
        self.last_played = None
        self.hotkey_times = {}
        self.undo_ids = []
        self.recording = None
        self.refreshing_devices = False
        self.device_snapshot = None
        self.device_poll_pending = False
        self.set_default_size(1180, 780)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.set_icon_name("audio-speakers")
        header = Gtk.HeaderBar(title="SMITH Soundboard", subtitle="Biblioteca de sons · Microfone virtual")
        header.set_show_close_button(True)
        self.set_titlebar(header)
        add = button("Adicionar áudios", self.choose_files, "list-add-symbolic")
        add.get_style_context().add_class("suggested-action")
        header.pack_start(add)
        header.pack_start(button("Adicionar pasta", self.choose_folder, "folder-open-symbolic"))
        header.pack_end(button("Configurações de áudio", lambda: self.audio_expander.set_expanded(not self.audio_expander.get_expanded()), "emblem-system-symbolic"))
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        root.pack_start(self.menu(), False, False, 0)
        searchbar = Gtk.Box(spacing=12, margin=12)
        self.search = Gtk.SearchEntry(placeholder_text="Buscar na biblioteca…")
        self.search.connect("search-changed", lambda item: self.reload())
        searchbar.pack_start(self.search, True, True, 0)
        order = Gtk.ComboBoxText()
        for key, label in (("title", "Nome"), ("added", "Adicionados recentemente"), ("recent", "Últimos reproduzidos"), ("duration", "Duração")):
            order.append(key, label)
        order.set_active_id("title")
        order.connect("changed", lambda item: self.set_sort(item.get_active_id()))
        searchbar.pack_end(order, False, False, 0)
        root.pack_start(searchbar, False, False, 0)
        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.sidebar.get_style_context().add_class("sidebar")
        self.sidebar.connect("row-selected", self.category_selected)
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.pack_start(scroll(self.sidebar), True, True, 0)
        sidebar_box.pack_end(button("＋ Nova categoria", self.new_category), False, False, 8)
        panes.pack1(sidebar_box, False, False)
        panes.set_position(190)
        central = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.model = Gtk.ListStore(int, str, str, str, str, str, str)
        self.table = Gtk.TreeView(model=self.model)
        self.table.set_enable_search(False)
        self.table.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        for index, title in enumerate(("#", "Nome do áudio", "Duração", "Atalho", "Categoria", "Formato", "Ganho")):
            renderer = Gtk.CellRendererText()
            if index == 1:
                renderer.set_property("ellipsize", 3)
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_expand(index == 1)
            column.set_min_width(60 if index != 1 else 250)
            self.table.append_column(column)
        self.table.connect("row-activated", lambda view, path, column: self.play_id(self.model[path][0]))
        self.table.connect("button-press-event", self.context_menu)
        self.table.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        self.table.drag_dest_add_uri_targets()
        self.table.connect("drag-data-received", self.drop_files)
        central.pack_start(scroll(self.table), True, True, 0)
        self.more = button("Carregar mais", lambda: self.reload(append=True))
        central.pack_end(self.more, False, False, 0)
        panes.pack2(central, True, False)
        root.pack_start(panes, True, True, 0)
        self.audio_expander = Gtk.Expander(label="Áudio e microfone virtual", margin=10)
        self.audio_expander.add(self.audio_panel())
        root.pack_start(self.audio_expander, False, False, 0)
        root.pack_start(self.transport(), False, False, 0)
        self.status = Gtk.Label(xalign=0, margin_start=12, margin_end=12, margin_top=7, margin_bottom=7)
        self.status.set_ellipsize(3)
        self.status.get_style_context().add_class("muted")
        root.pack_start(self.status, False, False, 0)
        self.shortcuts = GlobalShortcuts(self.hotkey, self.shortcut_status)
        self.player.connect("changed", self.playback_state)
        self.player.connect("finished", self.finished)
        self.player.connect("error", lambda player, text: self.error(text))
        self.connect("key-press-event", self.keypress)
        self.connect("delete-event", self.on_close)
        self.refresh_categories()
        self.reload()
        self.show_all()
        self.more.set_visible(len(self.model) < self.total)
        self.tick_id = GLib.timeout_add(100, self.tick)
        self.device_tick_id = GLib.timeout_add_seconds(3, self.poll_devices)
        GLib.idle_add(self.refresh_devices)
        GLib.idle_add(self.bind_shortcuts)

    def menu(self):
        bar = Gtk.MenuBar()
        menus = (("Arquivo", (("Adicionar áudios…", self.choose_files), ("Adicionar pasta…", self.choose_folder),
                                ("Importar lista…", self.import_list), ("Exportar lista…", self.export_list),
                                ("Gravar áudio…", self.record_dialog), ("Sair", self.close))),
                 ("Biblioteca", (("Editar áudio / corte…", self.edit_selected), ("Analisar volume", self.analyze_selected),
                                 ("Favoritar / desfavoritar", self.favorite_selected), ("Remover da biblioteca", self.remove_selected),
                                 ("Desfazer remoção", self.undo_remove), ("Exportar áudio…", self.export_selected))),
                 ("Reprodução", (("Reproduzir", self.play_selected), ("Pausar / continuar", self.player.pause),
                                  ("Parar", self.player.stop), ("Próximo", lambda: self.next_clip(1)),
                                  ("Anterior", lambda: self.next_clip(-1)), ("Aleatório", self.random_clip))),
                 ("Ajuda", (("Como usar", self.help), ("Diagnóstico de áudio", self.diagnose), ("Sobre", self.about))))
        for title, entries in menus:
            item, submenu = Gtk.MenuItem(label=title), Gtk.Menu()
            for label, action in entries:
                child = Gtk.MenuItem(label=label)
                child.connect("activate", lambda widget, callback=action: callback())
                submenu.append(child)
            item.set_submenu(submenu)
            bar.append(item)
        return bar

    def audio_panel(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=8)
        devices = Gtk.Grid(column_spacing=12, row_spacing=8)
        self.source, self.sink = Gtk.ComboBoxText(), Gtk.ComboBoxText()
        self.source.set_hexpand(True)
        self.sink.set_hexpand(True)
        devices.attach(Gtk.Label(label="Tua voz", xalign=0), 0, 0, 1, 1)
        devices.attach(self.source, 1, 0, 1, 1)
        devices.attach(Gtk.Label(label="Fones / saída", xalign=0), 0, 1, 1, 1)
        devices.attach(self.sink, 1, 1, 1, 1)
        devices.attach(button("Atualizar dispositivos", self.refresh_devices, "view-refresh-symbolic"), 2, 0, 1, 2)
        devices.attach(button("Ativar microfone virtual", self.enable_mic), 3, 0, 1, 1)
        devices.attach(button("Desativar microfone virtual", self.disable_mic), 3, 1, 1, 1)
        self.source.connect("changed", self.voice_source_changed)
        self.sink.connect("changed", self.sink_changed)
        box.pack_start(devices, False, False, 0)
        sliders = Gtk.Box(spacing=18)
        self.sliders = {}
        for name, label, default, maximum in (("local", "Áudio nos fones", 1, 1), ("mic", "Áudio no microfone", 1, 1), ("voice", "Tua voz", 1, 1.5)):
            column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            column.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, maximum * 100, 1)
            scale.set_value(self.library.preference("volume_" + name, default) * 100)
            scale.set_digits(0)
            scale.set_size_request(170, -1)
            scale.connect("value-changed", self.volume_changed, name)
            self.sliders[name] = scale
            column.pack_start(scale, False, False, 0)
            sliders.pack_start(column, True, True, 0)
        box.pack_start(sliders, False, False, 0)
        options = Gtk.Box(spacing=16)
        self.voice_mute = Gtk.CheckButton(label="Silenciar minha voz")
        self.block_voice = Gtk.CheckButton(label="Silenciar minha voz enquanto um som toca")
        self.block_voice.set_active(self.library.preference("block_voice", False))
        self.voice_mute.connect("toggled", lambda item: self.apply_voice())
        self.block_voice.connect("toggled", lambda item: (self.library.set_preference("block_voice", item.get_active()), self.apply_voice()))
        options.pack_start(self.voice_mute, False, False, 0)
        options.pack_start(self.block_voice, False, False, 0)
        options.pack_end(button("Enviar para um aplicativo…", self.route_app), False, False, 0)
        options.pack_end(button("Usar como entrada padrão", self.default_mic), False, False, 0)
        box.pack_start(options, False, False, 0)
        self.audio_status = Gtk.Label(label="Microfone virtual desligado. Ative e selecione SMITH Soundboard Microphone no aplicativo de voz.", xalign=0)
        self.audio_status.set_line_wrap(True)
        self.audio_status.get_style_context().add_class("muted")
        box.pack_start(self.audio_status, False, False, 0)
        self.shortcut_label = Gtk.Label(xalign=0)
        self.shortcut_label.set_ellipsize(3)
        self.shortcut_label.get_style_context().add_class("muted")
        box.pack_start(self.shortcut_label, False, False, 0)
        return box

    def transport(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=0)
        box.get_style_context().add_class("transport")
        top = Gtk.Box(spacing=10, margin_start=14, margin_end=14, margin_top=8)
        self.now = Gtk.Label(label="Selecione um áudio ou arraste arquivos para a lista", xalign=0)
        self.now.set_ellipsize(3)
        self.now.get_style_context().add_class("title")
        top.pack_start(self.now, True, True, 0)
        self.timer = Gtk.Label(label="0:00 / 0:00")
        top.pack_end(self.timer, False, False, 0)
        box.pack_start(top, False, False, 0)
        self.seekbar = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, .01)
        self.seekbar.set_draw_value(False)
        self.seekbar.connect("change-value", self.seek_changed)
        box.pack_start(self.seekbar, False, False, 0)
        controls = Gtk.Box(spacing=7, margin_start=12, margin_end=12, margin_bottom=10)
        for label, callback, icon in (("Anterior", lambda: self.next_clip(-1), "media-skip-backward-symbolic"),
                                      ("Reproduzir selecionado", self.play_selected, "media-playback-start-symbolic"),
                                      ("Pausar / continuar", self.player.pause, "media-playback-pause-symbolic"),
                                      ("Parar · Escape", self.player.stop, "media-playback-stop-symbolic"),
                                      ("Próximo", lambda: self.next_clip(1), "media-skip-forward-symbolic")):
            controls.pack_start(button(label, callback, icon), False, False, 0)
        self.mode = Gtk.ComboBoxText()
        for value, label in (("both", "Fones + microfone"), ("mic", "Somente microfone"), ("local", "Somente fones")):
            self.mode.append(value, label)
        self.mode.set_active_id(self.library.preference("mode", "both"))
        self.mode.connect("changed", self.mode_changed)
        controls.pack_start(self.mode, False, False, 10)
        self.normalize = Gtk.CheckButton(label="Normalizar volume")
        self.normalize.set_active(self.library.preference("normalize", False))
        self.normalize.set_tooltip_text("Opcional: ajusta o volume percebido. Desmarque para preservar a dinâmica original, sem equalização ou reforço de graves.")
        self.normalize.connect("toggled", lambda item: self.library.set_preference("normalize", item.get_active()))
        controls.pack_start(self.normalize, False, False, 0)
        self.repeat = Gtk.ComboBoxText()
        for value, label in (("off", "Sem repetição"), ("one", "Repetir áudio"), ("list", "Reprodução contínua"), ("random", "Aleatório contínuo")):
            self.repeat.append(value, label)
        self.repeat.set_active_id(self.library.preference("repeat", "off"))
        self.repeat.connect("changed", lambda item: self.library.set_preference("repeat", item.get_active_id()))
        controls.pack_end(self.repeat, False, False, 0)
        self.meter = Gtk.LevelBar(min_value=0, max_value=1)
        self.meter.set_size_request(80, 10)
        self.meter.set_tooltip_text("Nível do microfone virtual após o limitador")
        controls.pack_end(self.meter, False, False, 6)
        box.pack_start(controls, False, False, 0)
        return box

    def filters(self):
        return {"query": self.search.get_text(), "category": self.category, "favorite": self.filter_kind == "favorite"}

    def refresh_categories(self):
        current = (self.filter_kind, self.category)
        for child in self.sidebar.get_children():
            self.sidebar.remove(child)
        entries = [("all", None, "♫  Todos os sons"), ("favorite", None, "★  Favoritos")]
        entries += [("category", item["id"], item["name"] + "  ·  " + str(item["count"])) for item in self.library.categories()]
        for kind, category, title in entries:
            row = Gtk.ListBoxRow()
            row.kind, row.category = kind, category
            row.add(Gtk.Label(label=title, xalign=0))
            self.sidebar.add(row)
            if (kind, category) == current:
                selected = row
        self.sidebar.show_all()
        self.sidebar.select_row(locals().get("selected", self.sidebar.get_row_at_index(0)))

    def category_selected(self, box, row):
        if row:
            self.category, self.filter_kind = row.category, row.kind
            if hasattr(self, "status"):
                self.reload()

    def set_sort(self, value):
        self.sort = value
        self.reload()

    def reload(self, append=False):
        selected = set(self.selected_ids())
        if not append:
            self.model.clear()
        for clip in self.library.list(**self.filters(), offset=len(self.model), limit=500, sort=self.sort):
            row = self.model.append([clip.id, ("★  " if clip.favorite else "") + clip.title, clock(clip.length),
                                    Gtk.accelerator_get_label(*Gtk.accelerator_parse(clip.hotkey)) if clip.hotkey else "—",
                                    clip.category_name or "—", clip.codec, f"{clip.gain_db:+.1f} dB"])
            if clip.id in selected:
                self.table.get_selection().select_iter(row)
        self.total = self.library.count(**self.filters())
        self.more.set_visible(len(self.model) < self.total)
        self.status.set_text(f"{self.total:,} sons · {len(self.model):,} carregados · Enter reproduz · Escape para · Arraste arquivos ou pastas para importar")
        return False

    def selected_ids(self):
        model, paths = self.table.get_selection().get_selected_rows()
        return [model[path][0] for path in paths]

    def play_selected(self):
        ids = self.selected_ids()
        if ids:
            self.play_id(ids[0])
        elif len(self.model):
            self.play_id(self.model[0][0])

    def play_id(self, clip_id, preview=None):
        clip = preview or self.library.get(clip_id)
        if not clip or clip.deleted:
            self.error("Áudio não encontrado na biblioteca.")
            return
        mode = self.mode.get_active_id() or "both"
        try:
            if mode != "local" and not self.router.active:
                self.router.start(self.selected_voice())
            self.player.play(clip, mode, self.sink.get_active_id() or None, self.router.mix_sink,
                             self.sliders["local"].get_value() / 100, self.sliders["mic"].get_value() / 100,
                             self.normalize.get_active())
            self.last_played = clip.id
            self.library.played(clip.id)
            self.now.set_text(clip.title)
            self.seekbar.set_range(0, max(.01, clip.length))
            self.audio_status.set_text("Microfone virtual ativo: " + MIC_NAME if self.router.active else "Reproduzindo somente nos fones.")
        except Exception as exc:
            self.error(str(exc))

    def playback_state(self, player, state):
        self.apply_voice()
        self.timer.set_tooltip_text({"playing": "Reproduzindo", "paused": "Pausado", "loading": "Abrindo áudio", "stopped": "Parado"}.get(state, state))

    def apply_voice(self):
        if self.router.active:
            try:
                block = self.block_voice.get_active() and self.player.state == "playing" and self.player.mode != "local"
                self.router.set_voice(self.sliders["voice"].get_value() / 100, self.voice_mute.get_active() or block)
            except Exception as exc:
                self.audio_status.set_text(str(exc))

    def volume_changed(self, scale, name):
        value = scale.get_value() / 100
        self.library.set_preference("volume_" + name, value)
        self.player.set_volume(name, value)
        if name == "voice":
            self.apply_voice()

    def mode_changed(self, combo):
        self.library.set_preference("mode", combo.get_active_id())
        if self.player.state in ("playing", "paused"):
            clip_id = self.last_played
            self.play_id(clip_id)

    def seek_changed(self, scale, scroll_type, value):
        if not self.updating_seek:
            self.player.seek(value)
        return False

    def tick(self):
        if self.closing:
            return False
        position = self.player.position()
        duration = self.player.clip.length if self.player.clip else 0
        self.updating_seek = True
        self.seekbar.set_value(position)
        self.updating_seek = False
        self.timer.set_text(clock(position) + " / " + clock(duration))
        self.meter.set_value(self.router.meter())
        if self.router.bridge_error:
            error, self.router.bridge_error = self.router.bridge_error, None
            self.player.stop()
            self.disable_mic()
            self.error(error)
        return True

    def finished(self, player):
        mode = self.repeat.get_active_id()
        if mode == "one" and self.last_played:
            GLib.idle_add(lambda: self.play_id(self.last_played))
        elif mode == "list":
            self.next_clip(1, wrap=True)
        elif mode == "random":
            self.random_clip()

    def next_clip(self, step, wrap=False):
        ids = [row[0] for row in self.model]
        if not ids:
            return
        index = ids.index(self.last_played) + step if self.last_played in ids else 0
        if index >= len(ids) and len(ids) < self.total:
            self.reload(append=True)
            ids = [row[0] for row in self.model]
        if wrap:
            index %= len(ids)
        if 0 <= index < len(ids):
            self.play_id(ids[index])

    def random_clip(self):
        count = self.library.count(**self.filters())
        if count:
            clips = self.library.list(**self.filters(), offset=random.randrange(count), limit=1, sort=self.sort)
            self.play_id(clips[0].id)

    def refresh_devices(self):
        try:
            sources, sinks = self.router.pulse.devices()
            info = self.router.pulse.info()
            self.apply_devices(sources, sinks, info)
        except Exception as exc:
            self.audio_status.set_text(str(exc))
        return False

    def apply_devices(self, sources, sinks, info):
        if self.closing:
            return False
        snapshot = ([(item["name"], item.get("index"), item.get("mute", False)) for item in sources],
                    [item["name"] for item in sinks], info.get("default_source_name"), info.get("default_sink_name"))
        if snapshot != self.device_snapshot:
            self.device_snapshot = snapshot
            selected_source = self.library.preference("source", "@auto") or "@auto"
            selected_sink = self.library.preference("sink", info.get("default_sink_name"))
            self.refreshing_devices = True
            try:
                self.source.remove_all()
                self.sink.remove_all()
                self.source.append("@auto", "Automático · microfone padrão do sistema")
                self.source.append("@none", "Sem voz · somente os áudios")
                for item in sources:
                    self.source.append(item["name"], item.get("description", item["name"]))
                self.source.set_active_id(selected_source if selected_source in ("@auto", "@none") or any(
                    item["name"] == selected_source for item in sources) else "@auto")
                for item in sinks:
                    self.sink.append(item["name"], item.get("description", item["name"]))
                if not self.sink.set_active_id(selected_sink or info.get("default_sink_name", "")):
                    self.sink.set_active(0)
            finally:
                self.refreshing_devices = False
        if self.router.active:
            preference = self.library.preference("source", "@auto") or "@auto"
            default = info.get("default_source_name")
            preferred = self.router.voice_source if preference == "@auto" and (default or "").startswith("smith_") else preference
            chosen = None if preference == "@none" else choose_source(sources, preferred, default)
            try:
                self.router.set_voice_source(chosen)
                self.apply_voice()
                physical = next((item for item in sources if item["name"] == chosen), None)
                if physical:
                    self.audio_status.set_text("Voz + áudios ativos · " + physical.get("description", chosen) +
                                               (" · Atenção: microfone silenciado no sistema" if physical.get("mute") else ""))
                else:
                    self.audio_status.set_text("Microfone virtual ativo · " + ("sem voz por escolha" if preference == "@none" else
                                               "nenhum microfone físico disponível; reconecte-o para retomar a voz"))
            except Exception as exc:
                self.audio_status.set_text(str(exc))
        elif snapshot != getattr(self, "last_inactive_snapshot", None):
            self.last_inactive_snapshot = snapshot
            self.audio_status.set_text(f"{len(sources)} microfone(s) disponível(is). Ative o microfone virtual para enviar voz e áudios juntos.")
        return False

    def poll_devices(self):
        if self.closing:
            return False
        if not self.device_poll_pending:
            self.device_poll_pending = True
            def work():
                try:
                    sources, sinks = self.router.pulse.devices()
                    info = self.router.pulse.info()
                    GLib.idle_add(self.apply_devices, sources, sinks, info)
                except Exception:
                    pass
                finally:
                    self.device_poll_pending = False
            self.pool.submit(work)
        return True

    def selected_voice(self):
        preference = self.source.get_active_id() or "@auto"
        if preference == "@none":
            return None
        sources, sinks = self.router.pulse.devices()
        default = self.router.pulse.info().get("default_source_name")
        preferred = self.router.voice_source if preference == "@auto" and (default or "").startswith("smith_") else preference
        chosen = choose_source(sources, preferred, default)
        if not chosen:
            raise RuntimeError("Nenhum microfone físico foi encontrado. Conecte-o e atualize os dispositivos; para transmitir só áudios, escolha Sem voz.")
        return chosen

    def sink_changed(self, combo):
        if not self.refreshing_devices and combo.get_active_id():
            self.library.set_preference("sink", combo.get_active_id())

    def voice_source_changed(self, combo):
        if self.refreshing_devices or not combo.get_active_id():
            return
        self.library.set_preference("source", combo.get_active_id())
        if self.router.active:
            try:
                self.router.set_voice_source(self.selected_voice())
                self.apply_voice()
            except Exception as exc:
                self.error(str(exc))

    def enable_mic(self):
        try:
            self.router.start(self.selected_voice())
            self.apply_voice()
            self.audio_status.set_text("Voz + áudios ativos · selecione SMITH_Soundboard_Microphone no aplicativo de voz.")
        except Exception as exc:
            self.error(str(exc))

    def disable_mic(self):
        try:
            self.player.stop()
            self.router.close()
            self.audio_status.set_text("Microfone virtual desligado. Rotas alteradas pelo aplicativo foram restauradas.")
        except Exception as exc:
            self.error(str(exc))

    def default_mic(self):
        try:
            self.router.make_default()
            self.audio_status.set_text("Entrada padrão definida. Aplicativos já abertos podem precisar reabrir a captura.")
        except Exception as exc:
            self.error(str(exc))

    def route_app(self):
        try:
            if not self.router.active:
                raise RuntimeError("Ative o microfone virtual primeiro.")
            streams = self.router.capture_apps()
            if not streams:
                raise RuntimeError("Nenhum aplicativo capturando áudio. Abra o teste de microfone no aplicativo desejado e tente novamente.")
            dialog = self.dialog("Enviar microfone virtual para um aplicativo")
            combo = Gtk.ComboBoxText()
            for stream in streams:
                props = stream.get("properties", {})
                combo.append(str(stream["index"]), props.get("application.name", "Aplicativo") + " · " + props.get("media.name", "captura"))
            combo.set_active(0)
            dialog.get_content_area().pack_start(combo, False, False, 12)
            dialog.show_all()
            if dialog.run() == Gtk.ResponseType.OK:
                self.router.move_capture(int(combo.get_active_id()))
            dialog.destroy()
        except Exception as exc:
            self.error(str(exc))

    def choose_files(self):
        chooser = Gtk.FileChooserDialog(title="Adicionar áudios", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Adicionar", Gtk.ResponseType.OK)
        chooser.set_select_multiple(True)
        audio = Gtk.FileFilter()
        audio.set_name("Áudio e mídia")
        audio.add_mime_type("audio/*")
        for ext in media.EXTENSIONS:
            audio.add_pattern("*" + ext)
            audio.add_pattern("*" + ext.upper())
        chooser.add_filter(audio)
        all_files = Gtk.FileFilter()
        all_files.set_name("Todos os arquivos")
        all_files.add_pattern("*")
        chooser.add_filter(all_files)
        if chooser.run() == Gtk.ResponseType.OK:
            self.import_paths(chooser.get_filenames())
        chooser.destroy()

    def choose_folder(self):
        chooser = Gtk.FileChooserDialog(title="Adicionar pasta e subpastas", transient_for=self, action=Gtk.FileChooserAction.SELECT_FOLDER)
        chooser.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Adicionar pasta", Gtk.ResponseType.OK)
        if chooser.run() == Gtk.ResponseType.OK:
            self.import_paths([chooser.get_filename()])
        chooser.destroy()

    def drop_files(self, widget, context, x, y, selection, info, timestamp):
        paths = [Gio.File.new_for_uri(value).get_path() for value in selection.get_uris()]
        self.import_paths([path for path in paths if path])
        Gtk.drag_finish(context, True, False, timestamp)

    def import_paths(self, paths):
        category = self.category
        self.status.set_text("Importando e analisando áudio em segundo plano…")
        def work():
            count, errors = 0, []
            for selected in paths:
                path = Path(selected).expanduser()
                candidates = (Path(root) / name for root, dirs, names in os.walk(path, followlinks=False) for name in names
                              if Path(name).suffix.lower() in media.EXTENSIONS) if path.is_dir() else [path]
                for candidate in candidates:
                    if self.cancel.is_set():
                        return
                    try:
                        metadata = media.probe(candidate, self.cancel)
                        clip_id = self.library.add(metadata, category)
                        count += 1
                        GLib.idle_add(self.import_progress, count, candidate.name)
                        try:
                            values = media.analyze(candidate, metadata["duration"], self.cancel)
                            self.library.update(clip_id, **values)
                        except Exception as exc:
                            errors.append(candidate.name + ": análise: " + str(exc))
                    except Exception as exc:
                        if len(errors) < 50:
                            errors.append(candidate.name + ": " + str(exc))
            GLib.idle_add(self.import_done, count, errors)
        self.pool.submit(work)

    def import_progress(self, count, name):
        if not self.closing:
            if count == 1 or count % 10 == 0:
                self.reload()
            self.status.set_text(f"Importando: {count} áudios · {name}")
        return False

    def import_done(self, count, errors):
        if not self.closing:
            self.refresh_categories()
            self.reload()
            self.status.set_text(f"Importação concluída: {count} áudios · {len(errors)} ocorrências" + (" · " + errors[0] if errors else ""))
            self.status.set_tooltip_text("\n".join(errors) if errors else None)
        return False

    def new_category(self):
        dialog = self.dialog("Nova categoria")
        entry = Gtk.Entry(placeholder_text="Nome da categoria", activates_default=True)
        dialog.get_content_area().pack_start(entry, False, False, 10)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            try:
                self.library.add_category(entry.get_text())
                self.refresh_categories()
            except Exception as exc:
                self.error(str(exc))
        dialog.destroy()

    def context_menu(self, view, event):
        if event.button != 3:
            return False
        hit = view.get_path_at_pos(int(event.x), int(event.y))
        if hit and not view.get_selection().path_is_selected(hit[0]):
            view.get_selection().unselect_all()
            view.get_selection().select_path(hit[0])
        menu = Gtk.Menu()
        for label, action in (("Reproduzir", self.play_selected), ("Editar / corte / atalho…", self.edit_selected),
                              ("Favoritar / desfavoritar", self.favorite_selected), ("Exportar áudio…", self.export_selected),
                              ("Remover da biblioteca", self.remove_selected)):
            child = Gtk.MenuItem(label=label)
            child.connect("activate", lambda item, callback=action: callback())
            menu.append(child)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def edit_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        clip = self.library.get(ids[0])
        dialog = self.dialog("Editar áudio · " + clip.title)
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin=12)
        name = Gtk.Entry(text=clip.title)
        name.set_hexpand(True)
        category = Gtk.ComboBoxText()
        category.append("", "Sem categoria")
        for item in self.library.categories():
            category.append(str(item["id"]), item["name"])
        category.set_active_id(str(clip.category) if clip.category else "")
        hotkey = Gtk.Entry(text=clip.hotkey or "", placeholder_text="Ex.: <Control><Alt>1 ou F8")
        capture = Gtk.ToggleButton(label="Capturar tecla")
        def capture_key(entry, event):
            if not capture.get_active():
                return False
            modifiers = event.state & Gtk.accelerator_get_default_mod_mask()
            if Gdk.keyval_is_upper(event.keyval):
                keyval = Gdk.keyval_to_lower(event.keyval)
            else:
                keyval = event.keyval
            if keyval in (Gdk.KEY_Control_L, Gdk.KEY_Control_R, Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R):
                return True
            hotkey.set_text(Gtk.accelerator_name(keyval, modifiers))
            capture.set_active(False)
            return True
        hotkey.connect("key-press-event", capture_key)
        capture.connect("toggled", lambda item: hotkey.grab_focus() if item.get_active() else None)
        start = Gtk.SpinButton.new_with_range(0, max(.001, clip.duration), .01)
        end = Gtk.SpinButton.new_with_range(.001, max(.001, clip.duration), .01)
        start.set_digits(3)
        end.set_digits(3)
        start.set_value(clip.trim_start)
        end.set_value(clip.trim_end or clip.duration)
        gain = Gtk.SpinButton.new_with_range(-24, 12, .5)
        gain.set_digits(1)
        gain.set_value(clip.gain_db)
        waveform = Waveform(clip, lambda value, mouse: (start if mouse == 1 else end).set_value(value))
        def redraw(item):
            waveform.start, waveform.end = start.get_value(), end.get_value()
            waveform.queue_draw()
        start.connect("value-changed", redraw)
        end.connect("value-changed", redraw)
        for row, (label, widget) in enumerate((("Nome", name), ("Categoria", category), ("Atalho", hotkey),
                                               ("Início (segundos)", start), ("Fim (segundos)", end), ("Ganho (dB)", gain))):
            grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            grid.attach(widget, 1, row, 1, 1)
        grid.attach(capture, 2, 2, 1, 1)
        grid.attach(waveform, 0, 6, 3, 1)
        note = Gtk.Label(label="Clique esquerdo marca início; direito marca fim. O arquivo original é preservado.", xalign=0)
        note.get_style_context().add_class("muted")
        grid.attach(note, 0, 7, 3, 1)
        def preview():
            self.play_id(clip.id, replace(clip, trim_start=start.get_value(), trim_end=end.get_value(), gain_db=gain.get_value()))
        grid.attach(button("Ouvir corte", preview), 0, 8, 1, 1)
        dialog.get_content_area().add(grid)
        dialog.show_all()
        while dialog.run() == Gtk.ResponseType.OK:
            try:
                accelerator = hotkey.get_text().strip()
                if accelerator:
                    key, mask = Gtk.accelerator_parse(accelerator)
                    if not key or not Gtk.accelerator_valid(key, mask) or key == Gdk.KEY_Escape:
                        raise ValueError("Atalho inválido. Use, por exemplo, <Control><Alt>1 ou F8. Escape é reservado para parar.")
                    accelerator = Gtk.accelerator_name(key, mask)
                self.library.update(clip.id, title=name.get_text(), category=int(category.get_active_id()) if category.get_active_id() else None,
                                    hotkey=accelerator, trim_start=start.get_value(), trim_end=end.get_value(), gain_db=gain.get_value())
                self.reload()
                self.refresh_categories()
                self.bind_shortcuts()
                break
            except sqlite3.IntegrityError:
                self.error("Esse atalho já está atribuído a outro áudio.")
            except Exception as exc:
                self.error(str(exc))
        dialog.destroy()

    def favorite_selected(self):
        for clip_id in self.selected_ids():
            self.library.update(clip_id, favorite=not self.library.get(clip_id).favorite)
        self.reload()

    def remove_selected(self):
        self.undo_ids = self.selected_ids()
        for clip_id in self.undo_ids:
            self.library.update(clip_id, deleted=True)
        self.reload()
        self.refresh_categories()
        self.bind_shortcuts()

    def undo_remove(self):
        for clip_id in self.undo_ids:
            self.library.restore(clip_id)
        self.undo_ids = []
        self.reload()
        self.refresh_categories()
        self.bind_shortcuts()

    def analyze_selected(self):
        ids = self.selected_ids() or [row[0] for row in self.model]
        def work():
            errors = []
            for clip_id in ids:
                if self.cancel.is_set():
                    return
                clip = self.library.get(clip_id)
                try:
                    self.library.update(clip_id, **media.analyze(clip.path, clip.duration, self.cancel))
                except Exception as exc:
                    errors.append(clip.title + ": " + str(exc))
            GLib.idle_add(self.import_done, len(ids), errors)
        self.status.set_text("Analisando volume e forma de onda…")
        self.pool.submit(work)

    def save_path(self, title, filename, pattern):
        chooser = Gtk.FileChooserDialog(title=title, transient_for=self, action=Gtk.FileChooserAction.SAVE)
        chooser.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Salvar", Gtk.ResponseType.OK)
        chooser.set_do_overwrite_confirmation(True)
        chooser.set_current_name(filename)
        file_filter = Gtk.FileFilter()
        file_filter.set_name(pattern)
        for suffix in pattern.split(" "):
            file_filter.add_pattern(suffix)
        chooser.add_filter(file_filter)
        path = chooser.get_filename() if chooser.run() == Gtk.ResponseType.OK else None
        chooser.destroy()
        return path

    def export_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        clip = self.library.get(ids[0])
        destination = self.save_path("Exportar corte · WAV, FLAC ou MP3", clip.title.replace("/", "_") + ".wav", "*.wav *.flac *.mp3")
        if destination:
            self.status.set_text("Exportando áudio…")
            normalize = self.normalize.get_active()
            def work():
                target = Path(destination)
                temporary = target.with_name("." + target.stem + "." + uuid.uuid4().hex + target.suffix)
                try:
                    media.export_clip(clip, temporary, normalize, cancel=self.cancel)
                    temporary.replace(target)
                    GLib.idle_add(self.set_status, "Áudio exportado: " + str(target))
                except Exception as exc:
                    temporary.unlink(missing_ok=True)
                    GLib.idle_add(self.error, str(exc))
            self.pool.submit(work)

    def export_list(self):
        destination = self.save_path("Exportar lista de sons", "biblioteca-smith.json", "*.json")
        if destination:
            try:
                Path(destination).write_text(json.dumps(self.library.export_manifest(), ensure_ascii=False, indent=2), encoding="utf-8")
                self.set_status("Lista exportada; os arquivos de áudio continuam nos caminhos originais.")
            except Exception as exc:
                self.error(str(exc))

    def import_list(self):
        chooser = Gtk.FileChooserDialog(title="Importar lista SMITH", transient_for=self, action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Importar", Gtk.ResponseType.OK)
        path = chooser.get_filename() if chooser.run() == Gtk.ResponseType.OK else None
        chooser.destroy()
        if path:
            try:
                self.library.import_manifest(json.loads(Path(path).read_text(encoding="utf-8")))
                self.reload()
                self.refresh_categories()
                self.bind_shortcuts()
            except Exception as exc:
                self.error(str(exc))

    def record_dialog(self):
        if self.recording:
            return
        try:
            sources, sinks = self.router.pulse.devices()
        except Exception as exc:
            self.error(str(exc))
            return
        dialog = Gtk.Dialog(title="Gravar áudio · WAV 24 bits / 48 kHz", transient_for=self, modal=True)
        dialog.add_button("Fechar", Gtk.ResponseType.CLOSE)
        box = dialog.get_content_area()
        box.set_spacing(12)
        box.set_border_width(14)
        source = Gtk.ComboBoxText()
        for item in sources:
            source.append(item["name"], "Microfone · " + item.get("description", item["name"]))
        for item in sinks:
            source.append(item["name"] + ".monitor", "Som do sistema · " + item.get("description", item["name"]))
        source.set_active(0)
        label = Gtk.Label(label="Escolha a origem. A gravação será adicionada à biblioteca.")
        toggle = Gtk.Button(label="● Iniciar gravação")
        box.pack_start(source, False, False, 0)
        box.pack_start(label, False, False, 0)
        box.pack_start(toggle, False, False, 0)
        started = [0]
        def finish():
            recorder, self.recording = self.recording, None
            if recorder:
                try:
                    destination = recorder.stop()
                    self.import_paths([str(destination)])
                    label.set_text("Gravação salva e enviada para a biblioteca.")
                except Exception as exc:
                    self.error(str(exc))
            source.set_sensitive(True)
            toggle.set_label("● Iniciar gravação")
        def clicked(widget):
            if self.recording:
                finish()
                return
            if not source.get_active_id():
                return
            folder = data_directory() / "recordings"
            folder.mkdir(parents=True, exist_ok=True)
            destination = folder / (datetime.now().strftime("Gravação %Y-%m-%d %H-%M-%S") + "-" + uuid.uuid4().hex[:4] + ".wav")
            try:
                self.recording = media.Recorder(source.get_active_id(), destination)
                self.recording.start()
                started[0] = time.monotonic()
                toggle.set_label("■ Finalizar e adicionar")
                source.set_sensitive(False)
            except Exception as exc:
                if self.recording:
                    self.recording.pipeline.set_state(media.Gst.State.NULL)
                self.recording = None
                self.error(str(exc))
        def update():
            if self.recording:
                error = self.recording.error()
                if error:
                    self.recording.pipeline.set_state(media.Gst.State.NULL)
                    self.recording = None
                    label.set_text(error)
                    source.set_sensitive(True)
                    toggle.set_label("● Iniciar gravação")
                else:
                    label.set_text("Gravando · " + clock(time.monotonic() - started[0]))
            return True
        toggle.connect("clicked", clicked)
        watch = GLib.timeout_add(250, update)
        dialog.show_all()
        dialog.run()
        GLib.source_remove(watch)
        finish()
        dialog.destroy()

    def bind_shortcuts(self):
        self.shortcuts.bind(self.library.hotkeys())
        return False

    def shortcut_status(self, text):
        if not self.closing:
            self.shortcut_label.set_text(text)
            self.shortcut_label.set_tooltip_text(text)

    def hotkey(self, clip_id):
        now = time.monotonic()
        if now - self.hotkey_times.get(clip_id, 0) > .2:
            self.hotkey_times[clip_id] = now
            self.play_id(clip_id)

    def keypress(self, window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.player.stop()
            return True
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Entry, Gtk.SpinButton)):
            return False
        mask = event.state & Gtk.accelerator_get_default_mod_mask()
        for clip_id, title, accelerator in self.library.hotkeys():
            key, modifiers = Gtk.accelerator_parse(accelerator)
            if Gdk.keyval_to_lower(event.keyval) == key and mask == modifiers:
                self.hotkey(clip_id)
                return True
        if focus is not self.table:
            return False
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.play_selected()
            return True
        if event.keyval == Gdk.KEY_space:
            self.player.pause() if self.player.state in ("paused", "playing") else self.play_selected()
            return True
        if event.keyval == Gdk.KEY_Delete:
            self.remove_selected()
            return True
        return False

    def dialog(self, title):
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Salvar", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.get_content_area().set_border_width(12)
        return dialog

    def set_status(self, text):
        if not self.closing:
            self.status.set_text(text)
        return False

    def error(self, text):
        if self.closing:
            return False
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.CLOSE, text="Não foi possível concluir")
        dialog.format_secondary_text(str(text))
        dialog.run()
        dialog.destroy()
        return False

    def help(self):
        dialog = Gtk.MessageDialog(transient_for=self, buttons=Gtk.ButtonsType.CLOSE, text="Áudios no microfone, em três passos")
        dialog.format_secondary_text("1. Adicione arquivos ou pastas pelo botão + ou arraste para a lista.\n"
                                     "2. Em Áudio e microfone virtual, escolha teu microfone físico e ative o microfone virtual.\n"
                                     "3. No Discord, Steam ou outro aplicativo, selecione SMITH_Soundboard_Microphone como entrada.\n\n"
                                     "Dê dois cliques em um áudio para reproduzir. Clique direito para editar, cortar, definir atalho ou exportar.\n"
                                     "Use Somente fones para escutar antes de transmitir. No aplicativo de voz, desative filtros de supressão de música se cortarem o som.\n\n"
                                     "A biblioteca não impõe limite de quantidade. Os arquivos originais precisam continuar acessíveis. "
                                     "Atalhos globais no Wayland dependem do portal oferecido pelo desktop.")
        dialog.run()
        dialog.destroy()

    def diagnose(self):
        from .diagnostics import report
        text = report()
        dialog = Gtk.Dialog(title="Diagnóstico", transient_for=self, modal=True)
        dialog.add_button("Fechar", Gtk.ResponseType.CLOSE)
        view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        view.get_buffer().set_text(text)
        pane = scroll(view)
        pane.set_size_request(700, 400)
        dialog.get_content_area().add(pane)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def about(self):
        dialog = Gtk.AboutDialog(transient_for=self, modal=True, program_name="SMITH Soundboard", version=__version__,
                                 comments="Soundboard nativo para Linux · GTK, GStreamer e PipeWire/PulseAudio\nImplementação independente, sem vínculo com o Soundpad.",
                                 license_type=Gtk.License.MIT_X11, logo_icon_name="audio-speakers")
        dialog.run()
        dialog.destroy()

    def on_close(self, *args):
        self.shutdown()
        return False

    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.cancel.set()
        self.player.stop()
        self.shortcuts.close()
        GLib.source_remove(self.tick_id)
        GLib.source_remove(self.device_tick_id)
        if self.recording:
            try:
                self.recording.stop()
            except Exception:
                self.recording.pipeline.set_state(media.Gst.State.NULL)
        self.pool.shutdown(wait=True, cancel_futures=True)
        try:
            self.router.close()
        except Exception as exc:
            print("O estado de áudio será recuperado na próxima execução:", exc)


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.library = Library(data_directory() / "library.sqlite3")
        self.player = media.Player()
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", data_directory())) / "smith-soundboard"
        self.router = Router(runtime)

    def do_activate(self):
        if not self.window:
            self.window = Window(self)
        self.window.present()

    def do_command_line(self, command_line):
        import argparse
        parser = argparse.ArgumentParser(prog="smith-soundboard")
        parser.add_argument("--play", type=int)
        parser.add_argument("--stop", action="store_true")
        parser.add_argument("--pause", action="store_true")
        parser.add_argument("--import", dest="import_files", nargs="+")
        parser.add_argument("files", nargs="*")
        try:
            args = parser.parse_args(command_line.get_arguments()[1:])
        except SystemExit as exc:
            return exc.code
        if not self.window or not (args.stop or args.pause or args.play is not None):
            self.activate()
        if args.stop:
            self.player.stop()
        elif args.pause:
            self.player.pause()
        elif args.play is not None:
            self.window.play_id(args.play)
        if args.import_files or args.files:
            cwd = command_line.get_cwd() or os.getcwd()
            self.window.import_paths([str(Path(cwd) / path) if not Path(path).is_absolute() else path for path in (args.import_files or []) + args.files])
        return 0

    def do_shutdown(self):
        if self.window:
            self.window.shutdown()
        if hasattr(self, "library"):
            self.library.close()
        Gtk.Application.do_shutdown(self)
