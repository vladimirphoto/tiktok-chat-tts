import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from ttchat import updater
from ttchat.updater import UpdateError, UpdateInfo

EXE = b"MZ" + b"\x90" * 5000


def release(tag="v1.0.5", with_exe=True, with_sha=True, exe_hash=None):
    assets = []
    if with_exe:
        assets.append({"name": "TikTokChatTTS.exe", "size": len(EXE),
                       "browser_download_url": "https://example.test/TikTokChatTTS.exe"})
    if with_sha:
        assets.append({"name": "sha256.txt", "browser_download_url": "https://example.test/sha256.txt"})
    return {"tag_name": tag, "body": "заметки", "assets": assets}


class FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_opener(exe=EXE, sha_text=None):
    sha_text = sha_text if sha_text is not None else hashlib.sha256(EXE).hexdigest() + "  TikTokChatTTS.exe\n"

    def opener(request, timeout=None):
        url = request.full_url
        if url.endswith("sha256.txt"):
            return FakeResponse(sha_text.encode())
        return FakeResponse(exe)

    return opener


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(updater.parse_version("v1.0.12"), (1, 0, 12))
        self.assertEqual(updater.parse_version("1.2"), (1, 2))
        self.assertEqual(updater.parse_version(""), ())
        self.assertEqual(updater.parse_version("beta"), ())

    def test_is_newer_compares_numbers_not_text(self):
        self.assertTrue(updater.is_newer("v1.0.10", "1.0.9"))
        self.assertFalse(updater.is_newer("v1.0.9", "1.0.10"))
        self.assertFalse(updater.is_newer("v1.0.5", "1.0.5"))
        self.assertFalse(updater.is_newer("", "1.0.0"))
        self.assertTrue(updater.is_newer("v1.1.0", "1.0.99"))


class CheckTests(unittest.TestCase):
    def test_new_release_found(self):
        info = updater.check_for_update("1.0.0", fetch=lambda url: release())
        self.assertEqual(info.version, "1.0.5")
        self.assertEqual(info.tag, "v1.0.5")
        self.assertTrue(info.url.endswith("TikTokChatTTS.exe"))
        self.assertTrue(info.sha_url.endswith("sha256.txt"))

    def test_same_or_older_means_none(self):
        self.assertIsNone(updater.check_for_update("1.0.5", fetch=lambda url: release()))
        self.assertIsNone(updater.check_for_update("1.0.9", fetch=lambda url: release()))

    def test_release_without_exe_is_error(self):
        with self.assertRaises(UpdateError):
            updater.check_for_update("1.0.0", fetch=lambda url: release(with_exe=False))

    def test_network_error_is_wrapped(self):
        def boom(url):
            raise OSError("нет сети")

        with self.assertRaises(UpdateError):
            updater.check_for_update("1.0.0", fetch=boom)

    def test_without_sha_still_ok(self):
        info = updater.check_for_update("1.0.0", fetch=lambda url: release(with_sha=False))
        self.assertEqual(info.sha_url, "")


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name) / "TikTokChatTTS.exe"
        self.info = UpdateInfo("1.0.5", "v1.0.5", "https://example.test/TikTokChatTTS.exe",
                               size=len(EXE), sha_url="https://example.test/sha256.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def test_good_download(self):
        seen = []
        path = updater.download_update(self.info, self.target, seen.append, opener=make_opener())
        self.assertEqual(path.name, "TikTokChatTTS.exe.new")
        self.assertEqual(path.read_bytes(), EXE)
        self.assertEqual(seen[-1], 100)

    def test_bad_hash_removes_file(self):
        opener = make_opener(sha_text="0" * 64 + "  TikTokChatTTS.exe\n")
        with self.assertRaises(UpdateError):
            updater.download_update(self.info, self.target, opener=opener)
        self.assertFalse(Path(str(self.target) + ".new").exists())

    def test_not_an_exe_is_rejected(self):
        info = UpdateInfo("1.0.5", "v1.0.5", "https://example.test/x.exe", size=0)
        with self.assertRaises(UpdateError):
            updater.download_update(info, self.target, opener=make_opener(exe=b"<html>oops</html>"))
        self.assertFalse(Path(str(self.target) + ".new").exists())

    def test_truncated_download_is_rejected(self):
        info = UpdateInfo("1.0.5", "v1.0.5", "https://example.test/x.exe", size=len(EXE) + 100)
        with self.assertRaises(UpdateError):
            updater.download_update(info, self.target, opener=make_opener())

    def test_expected_hash_parsing(self):
        digest = "ab" * 32
        self.assertEqual(updater.expected_hash(f"{digest}  TikTokChatTTS.exe\n"), digest)
        self.assertEqual(updater.expected_hash(digest), digest)
        self.assertIsNone(updater.expected_hash("мусор"))


class ScriptTests(unittest.TestCase):
    def test_script_moves_and_restarts(self):
        text = updater.build_replace_script(Path(r"C:\Apps\T.exe.new"), Path(r"C:\Apps\T.exe"))
        self.assertIn(r'move /y "C:\Apps\T.exe.new" "C:\Apps\T.exe"', text)
        self.assertIn(r'start "" "C:\Apps\T.exe"', text)
        self.assertIn("PYINSTALLER_RESET_ENVIRONMENT", text)
        self.assertIn("\r\n", text)

    def test_self_update_only_for_frozen_windows(self):
        self.assertFalse(updater.can_self_update())  # в тестах приложение не «заморожено»


if __name__ == "__main__":
    unittest.main()
