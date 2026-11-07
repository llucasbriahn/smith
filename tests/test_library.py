import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from soundboard.library import Library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Library(Path(self.temp.name) / "library.sqlite3")

    def tearDown(self):
        self.library.close()
        self.temp.cleanup()

    def add(self, name="teste.wav", **values):
        return self.library.add({"path": str(Path(self.temp.name) / name), "duration": 10, **values})

    def test_no_library_cap_and_paging(self):
        self.library.add_many({"path": f"/audio/clip-{index}.mp3", "duration": 1} for index in range(12000))
        self.assertEqual(self.library.count(), 12000)
        self.assertEqual(len(self.library.list(limit=500, offset=11500)), 500)
        self.assertEqual(len(self.library.export_manifest()["clips"]), 12000)

    def test_unicode_search_and_literal_sql_wildcards(self):
        clip_id = self.add(title="Explosão 100%_A")
        self.assertEqual(self.library.list(query="EXPLOSAO")[0].id, clip_id)
        self.assertEqual(self.library.count(query="100%_"), 1)
        self.assertEqual(self.library.count(query="100_A"), 0)
        self.assertEqual(self.library.count(query="' OR 1=1 --"), 0)

    def test_dedup_and_keep_edits(self):
        clip_id = self.add()
        self.library.update(clip_id, title="Novo nome", trim_start=1, trim_end=3)
        self.assertEqual(self.add(), clip_id)
        self.assertEqual(self.library.count(), 1)
        self.assertEqual(self.library.get(clip_id).length, 2)
        self.assertEqual(self.library.get(clip_id).title, "Novo nome")

    def test_soft_delete_restore_and_hotkey_conflict(self):
        first, second = self.add("a.wav"), self.add("b.wav")
        self.library.update(first, hotkey="<Control>1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.library.update(second, hotkey="<Control>1")
        self.library.update(first, deleted=True)
        self.library.update(second, hotkey="<Control>1")
        self.add("a.wav")
        self.assertIsNone(self.library.get(first).hotkey)
        self.assertEqual(self.library.count(), 2)

    def test_trim_and_gain_validation(self):
        clip_id = self.add()
        for values in ({"trim_start": -1}, {"trim_start": 11}, {"trim_start": 4, "trim_end": 3},
                       {"trim_end": 20}, {"gain_db": float("nan")}, {"gain_db": 40}, {"title": " "}):
            with self.assertRaises(ValueError):
                self.library.update(clip_id, **values)

    def test_categories_favorites_and_manifest_roundtrip(self):
        category = self.library.add_category("Memes")
        clip_id = self.library.add({"path": "/audio/a.wav", "duration": 10}, category)
        self.library.update(clip_id, favorite=True, trim_start=2, trim_end=4, gain_db=-3, replay_gain=-2, peak=.7, hotkey="F8")
        manifest = json.loads(json.dumps(self.library.export_manifest()))
        other = Library(Path(self.temp.name) / "other.sqlite3")
        try:
            new_id = other.import_manifest(manifest)[0]
            clip = other.get(new_id)
            self.assertEqual(clip.category_name, "Memes")
            self.assertEqual(clip.length, 2)
            self.assertEqual(clip.hotkey, "F8")
            self.assertEqual(clip.replay_gain, -2)
            self.assertEqual(other.count(favorite=True), 1)
            self.assertEqual(other.count(category=clip.category), 1)
        finally:
            other.close()

    def test_preferences_and_persistence(self):
        self.library.set_preference("devices", {"source": "mic", "gain": .5})
        clip_id = self.add()
        self.library.close()
        self.library = Library(Path(self.temp.name) / "library.sqlite3")
        self.assertEqual(self.library.preference("devices")["source"], "mic")
        self.assertEqual(self.library.get(clip_id).duration, 10)

    def test_empty_optional_manifest_metadata(self):
        self.library.import_manifest({"format": "smith-soundboard", "version": 1, "clips": [{"path": "/tmp/test.wav"}]})
        self.assertEqual(self.library.count(), 1)

    def test_rename_updates_search(self):
        clip_id = self.add(title="Inicial")
        self.library.update(clip_id, title="Pássaro")
        self.assertEqual(self.library.count(query="passaro"), 1)
        self.assertEqual(self.library.count(query="Inicial"), 0)
