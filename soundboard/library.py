"""Disk-backed, uncapped sound library. Original media files are referenced."""
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import sqlite3
import threading
import unicodedata


def searchable(value):
    return "".join(character for character in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(character))


@dataclass(frozen=True)
class Clip:
    id: int
    path: str
    title: str
    duration: float = 0
    rate: int = 0
    channels: int = 0
    codec: str = ""
    category: int | None = None
    category_name: str = ""
    favorite: bool = False
    hotkey: str | None = None
    gain_db: float = 0
    trim_start: float = 0
    trim_end: float | None = None
    replay_gain: float | None = None
    peak: float | None = None
    waveform: str | None = None
    played: int = 0
    deleted: bool = False

    @property
    def length(self):
        return max(0, (self.trim_end if self.trim_end is not None else self.duration) - self.trim_start)


class Library:
    def __init__(self, filename):
        self.path = Path(filename)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS categories(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
            CREATE TABLE IF NOT EXISTS clips(
                id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, title TEXT NOT NULL, search TEXT NOT NULL,
                duration REAL NOT NULL DEFAULT 0, rate INTEGER NOT NULL DEFAULT 0, channels INTEGER NOT NULL DEFAULT 0,
                codec TEXT NOT NULL DEFAULT '', category INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                favorite INTEGER NOT NULL DEFAULT 0, hotkey TEXT, gain_db REAL NOT NULL DEFAULT 0,
                trim_start REAL NOT NULL DEFAULT 0, trim_end REAL, replay_gain REAL, peak REAL, waveform TEXT,
                played INTEGER NOT NULL DEFAULT 0, last_played TEXT, deleted INTEGER NOT NULL DEFAULT 0,
                created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS active_hotkey ON clips(hotkey) WHERE deleted=0 AND hotkey IS NOT NULL;
            CREATE INDEX IF NOT EXISTS clip_category ON clips(category,deleted);
            CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        self.connection.commit()

    @staticmethod
    def clip(row):
        if row is None:
            return None
        data = {name: row[name] for name in Clip.__dataclass_fields__ if name in row.keys()}
        data["favorite"] = bool(data.get("favorite"))
        data["deleted"] = bool(data.get("deleted"))
        return Clip(**data)

    def get(self, clip_id):
        with self.lock:
            row = self.connection.execute("SELECT clips.*,COALESCE(categories.name,'') category_name FROM clips "
                                          "LEFT JOIN categories ON categories.id=clips.category WHERE clips.id=?", (clip_id,)).fetchone()
            return self.clip(row)

    def add_many(self, records, category=None):
        ids = []
        with self.lock, self.connection:
            for item in records:
                path = str(Path(item["path"]).expanduser().resolve())
                title = str(item.get("title") or Path(path).stem)
                duration = float(item.get("duration", 0))
                if not math.isfinite(duration) or duration < 0:
                    raise ValueError("Duração inválida.")
                existing = self.connection.execute("SELECT id,deleted FROM clips WHERE path=?", (path,)).fetchone()
                if existing and existing["deleted"]:
                    self.restore(existing["id"])
                self.connection.execute("""
                    INSERT INTO clips(path,title,search,duration,rate,channels,codec,category)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET deleted=0,
                    duration=excluded.duration,rate=excluded.rate,channels=excluded.channels,codec=excluded.codec
                """, (path, title, searchable(title + " " + path), duration,
                      int(item.get("rate", 0)), int(item.get("channels", 0)), str(item.get("codec", "")), category))
                ids.append(self.connection.execute("SELECT id FROM clips WHERE path=?", (path,)).fetchone()[0])
        return ids

    def add(self, record, category=None):
        return self.add_many([record], category)[0]

    def _where(self, query="", category=None, favorite=False, deleted=False):
        clauses, values = ["clips.deleted=?"], [int(deleted)]
        if query:
            query = searchable(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("clips.search LIKE ? ESCAPE '\\'")
            values.append("%" + query + "%")
        if category is not None:
            clauses.append("clips.category=?")
            values.append(category)
        if favorite:
            clauses.append("clips.favorite=1")
        return " AND ".join(clauses), values

    def list(self, query="", category=None, favorite=False, deleted=False, offset=0, limit=500, sort="title"):
        order = {"title": "clips.title COLLATE NOCASE,clips.id", "recent": "clips.last_played DESC,clips.id",
                 "added": "clips.id DESC", "duration": "clips.duration,clips.id"}.get(sort, "clips.id")
        where, values = self._where(query, category, favorite, deleted)
        with self.lock:
            rows = self.connection.execute("SELECT clips.*,COALESCE(categories.name,'') category_name FROM clips "
                                           "LEFT JOIN categories ON categories.id=clips.category WHERE " + where +
                                           " ORDER BY " + order + " LIMIT ? OFFSET ?", (*values, int(limit), int(offset))).fetchall()
            return [self.clip(row) for row in rows]

    def count(self, **filters):
        where, values = self._where(**filters)
        with self.lock:
            return self.connection.execute("SELECT COUNT(*) FROM clips WHERE " + where, values).fetchone()[0]

    def update(self, clip_id, **values):
        allowed = {"title", "category", "favorite", "hotkey", "gain_db", "trim_start", "trim_end",
                   "replay_gain", "peak", "waveform", "deleted"}
        if not values or set(values) - allowed:
            raise ValueError("Campos de áudio inválidos.")
        clip = self.get(clip_id)
        if not clip:
            raise KeyError(clip_id)
        start, end = values.get("trim_start", clip.trim_start), values.get("trim_end", clip.trim_end)
        if not math.isfinite(start) or start < 0 or (end is not None and (not math.isfinite(end) or end <= start)):
            raise ValueError("O fim do corte deve ser posterior ao início.")
        if end is not None and clip.duration and end > clip.duration + 0.05:
            raise ValueError("O corte ultrapassa a duração do áudio.")
        if clip.duration and start >= clip.duration:
            raise ValueError("O início do corte deve ficar dentro do áudio.")
        if "gain_db" in values and (not math.isfinite(values["gain_db"]) or not -24 <= values["gain_db"] <= 12):
            raise ValueError("Ganho deve ficar entre -24 e +12 dB.")
        if "title" in values:
            if not str(values["title"]).strip():
                raise ValueError("Nome não pode ficar vazio.")
            values["search"] = searchable(values["title"] + " " + clip.path)
        if "hotkey" in values:
            values["hotkey"] = values["hotkey"] or None
        with self.lock, self.connection:
            self.connection.execute("UPDATE clips SET " + ",".join(name + "=?" for name in values) + " WHERE id=?",
                                    (*values.values(), clip_id))

    def played(self, clip_id):
        with self.lock, self.connection:
            self.connection.execute("UPDATE clips SET played=played+1,last_played=CURRENT_TIMESTAMP WHERE id=?", (clip_id,))

    def restore(self, clip_id):
        clip = self.get(clip_id)
        with self.lock, self.connection:
            if clip and clip.hotkey and self.connection.execute(
                "SELECT id FROM clips WHERE hotkey=? AND deleted=0 AND id<>?", (clip.hotkey, clip_id)
            ).fetchone():
                self.connection.execute("UPDATE clips SET hotkey=NULL WHERE id=?", (clip_id,))
            self.connection.execute("UPDATE clips SET deleted=0 WHERE id=?", (clip_id,))

    def categories(self):
        with self.lock:
            return [dict(row) for row in self.connection.execute(
                "SELECT categories.id,categories.name,COUNT(clips.id) count FROM categories LEFT JOIN clips "
                "ON clips.category=categories.id AND clips.deleted=0 GROUP BY categories.id ORDER BY categories.name COLLATE NOCASE")]

    def add_category(self, name):
        name = name.strip()
        if not name:
            raise ValueError("Nome de categoria vazio.")
        with self.lock, self.connection:
            return self.connection.execute("INSERT INTO categories(name) VALUES(?)", (name,)).lastrowid

    def hotkeys(self):
        with self.lock:
            return [(row["id"], row["title"], row["hotkey"]) for row in self.connection.execute(
                "SELECT id,title,hotkey FROM clips WHERE deleted=0 AND hotkey IS NOT NULL")]

    def preference(self, key, default=None):
        with self.lock:
            row = self.connection.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_preference(self, key, value):
        with self.lock, self.connection:
            self.connection.execute("INSERT INTO preferences(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                    (key, json.dumps(value, ensure_ascii=False)))

    def export_manifest(self):
        with self.lock:
            rows = self.connection.execute("SELECT clips.*,COALESCE(categories.name,'') category_name FROM clips "
                                           "LEFT JOIN categories ON categories.id=clips.category WHERE deleted=0 ORDER BY clips.id").fetchall()
        return {"format": "smith-soundboard", "version": 1, "clips": [asdict(self.clip(row)) for row in rows]}

    def import_manifest(self, data):
        if not isinstance(data, dict) or data.get("format") != "smith-soundboard" or data.get("version") != 1:
            raise ValueError("Lista de sons incompatível.")
        ids = []
        categories = {item["name"].casefold(): item["id"] for item in self.categories()}
        for item in data.get("clips", []):
            name = item.get("category_name", "").strip()
            if name and name.casefold() not in categories:
                categories[name.casefold()] = self.add_category(name)
            clip_id = self.add(item, categories.get(name.casefold()))
            values = {key: item[key] for key in ("favorite", "gain_db", "trim_start", "trim_end", "replay_gain", "peak", "waveform") if key in item}
            if values:
                self.update(clip_id, **values)
            if item.get("hotkey"):
                try:
                    self.update(clip_id, hotkey=item["hotkey"])
                except sqlite3.IntegrityError:
                    pass
            ids.append(clip_id)
        return ids

    def close(self):
        with self.lock:
            self.connection.close()
