import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ttchat.settings import DEFAULT_VOICE, Settings, SettingsStore, from_dict


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        s = Settings()
        self.assertEqual(s.voice, DEFAULT_VOICE)
        self.assertTrue(s.read_nick)

    def test_validated_clamps(self):
        s = replace(Settings(), max_len=1, queue_limit=0).validated()
        self.assertEqual((s.max_len, s.queue_limit), (20, 1))
        s = replace(Settings(), max_len=9999, queue_limit=9999).validated()
        self.assertEqual((s.max_len, s.queue_limit), (500, 100))

    def test_from_dict_ignores_junk(self):
        s = from_dict({"username": 5, "max_len": "много", "queue_limit": 12.7, "read_nick": "yes",
                       "unknown": 1, "voice": "en-US-AvaMultilingualNeural"})
        self.assertEqual(s.username, "")
        self.assertEqual(s.max_len, Settings().max_len)
        self.assertEqual(s.queue_limit, 12)
        self.assertTrue(s.read_nick)
        self.assertEqual(s.voice, "en-US-AvaMultilingualNeural")

    def test_bool_is_not_int(self):
        self.assertEqual(from_dict({"max_len": True}).max_len, Settings().max_len)

    def test_old_settings_with_voice_tuning_still_load(self):
        # в прежних версиях были скорость, тон и громкость: такие файлы не должны ломаться
        s = from_dict({"rate": 20, "pitch": -5, "volume": 50, "username": "abc"})
        self.assertEqual(s.username, "abc")
        self.assertFalse(hasattr(s, "rate"))

    def test_from_dict_non_dict(self):
        self.assertEqual(from_dict(None), Settings())
        self.assertEqual(from_dict([1, 2]), Settings())


class StoreTests(unittest.TestCase):
    def test_roundtrip_and_unicode(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "sub" / "settings.json")
            original = replace(Settings(), username="тест", blacklist="слово", max_len=77)
            self.assertTrue(store.save(original))
            self.assertEqual(store.load(), original)
            raw = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(raw["blacklist"], "слово")

    def test_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(SettingsStore(Path(d) / "nope.json").load(), Settings())

    def test_corrupt_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_text("{ не json", encoding="utf-8")
            self.assertEqual(SettingsStore(p).load(), Settings())

    def test_no_temp_files_left(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "settings.json")
            store.save(Settings())
            self.assertEqual([p.name for p in Path(d).iterdir()], ["settings.json"])


if __name__ == "__main__":
    unittest.main()
