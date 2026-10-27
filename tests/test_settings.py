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
        s = replace(Settings(), rate=999, pitch=-999, volume=500, max_len=1, queue_limit=0).validated()
        self.assertEqual((s.rate, s.pitch, s.volume, s.max_len, s.queue_limit), (100, -30, 100, 20, 1))

    def test_from_dict_ignores_junk(self):
        s = from_dict({"username": 5, "volume": "громко", "rate": 12.7, "read_nick": "yes",
                       "unknown": 1, "voice": "en-US-AvaMultilingualNeural"})
        self.assertEqual(s.username, "")
        self.assertEqual(s.volume, 80)
        self.assertEqual(s.rate, 12)
        self.assertTrue(s.read_nick)
        self.assertEqual(s.voice, "en-US-AvaMultilingualNeural")

    def test_bool_is_not_int(self):
        self.assertEqual(from_dict({"volume": True}).volume, 80)

    def test_from_dict_non_dict(self):
        self.assertEqual(from_dict(None), Settings())
        self.assertEqual(from_dict([1, 2]), Settings())


class StoreTests(unittest.TestCase):
    def test_roundtrip_and_unicode(self):
        with tempfile.TemporaryDirectory() as d:
            store = SettingsStore(Path(d) / "sub" / "settings.json")
            original = replace(Settings(), username="тест", blacklist="слово", rate=15)
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
