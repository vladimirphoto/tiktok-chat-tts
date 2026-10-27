import tempfile
import time
import unittest
from pathlib import Path

try:
    import tkinter as tk
except Exception:  # pragma: no cover - нет tkinter
    tk = None

from tests.test_engine import RecordingSynth
from tests.test_tiktok import FakeLibrary, connect_and_hold
from ttchat import updater
from ttchat.player import NullPlayer
from ttchat.settings import SettingsStore


def pump(root, condition, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        root.update()
        if condition():
            return True
        time.sleep(0.01)
    root.update()
    return bool(condition())


def log_text(app):
    return app.log.get("1.0", "end")


@unittest.skipIf(tk is None, "tkinter недоступен")
class GuiTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as ex:
            self.skipTest(f"нет дисплея: {ex}")
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SettingsStore(Path(self.tmp.name) / "settings.json")
        self.player = NullPlayer()
        self.synth = RecordingSynth()
        self.lib = FakeLibrary(connect_and_hold)
        from ttchat.gui import App

        self.app = App(self.root, self.store, synth=self.synth, player=self.player, library=self.lib,
                       update_check=None)

    def tearDown(self):
        try:
            if not self.app._closing:
                self.app.close()
        except tk.TclError:
            pass
        self.tmp.cleanup()

    def test_builds_with_defaults(self):
        self.assertEqual(self.app.btn_connect.cget("text"), "Подключиться")
        self.assertIn("Светлана", self.app.v_voice.get())
        self.assertIn("Введите ник", log_text(self.app))

    def test_connect_chat_flow_and_disconnect(self):
        self.app.v_user.set("@streamer")
        self.app.toggle_connect()
        self.assertEqual(self.app.btn_connect.cget("text"), "Отключиться")
        self.assertTrue(pump(self.root, lambda: "Подключено" in self.app.lbl_state.cget("text")))
        self.app.controller.on_comment("Маша", "Привет всем", None)
        self.assertTrue(pump(self.root, lambda: self.player.played))
        self.assertTrue(pump(self.root, lambda: "Маша: Привет всем" in log_text(self.app)))
        self.assertEqual(self.synth.calls[0][0], "Маша: Привет всем")
        self.app.toggle_connect()  # отключаемся
        self.assertTrue(pump(self.root, lambda: self.app.btn_connect.cget("text") == "Подключиться"))
        self.assertIn("Не подключено", self.app.lbl_state.cget("text"))

    def test_invalid_username_is_reported(self):
        self.app.v_user.set("")
        self.app.toggle_connect()
        self.assertTrue(pump(self.root, lambda: "Введите ник" in log_text(self.app).split("Введите ник стримера", 1)[-1]
                             or log_text(self.app).count("Введите ник") >= 2))
        self.assertEqual(self.app.btn_connect.cget("text"), "Подключиться")

    def test_settings_are_collected_and_saved(self):
        self.app.flag_vars["read_nick"].set(False)
        self.app.v_blacklist.set("слово")
        self.app.cmb_voice.set("Ава — мультиязычный, читает по-русски")
        self.app._changed()
        self.assertFalse(self.app.settings.read_nick)
        self.assertEqual(self.app.settings.voice, "en-US-AvaMultilingualNeural")
        self.assertTrue(pump(self.root, lambda: self.store.path.exists(), timeout=3))
        self.assertEqual(self.store.load().blacklist, "слово")

    def test_invalid_spinbox_value_keeps_previous(self):
        self.app.v_max_len.set(100)
        self.app._changed()
        self.root.tk.globalsetvar(str(self.app.v_max_len), "abc")
        self.app._changed()
        self.assertEqual(self.app.settings.max_len, 100)

    def test_test_voice_and_manual_speech(self):
        self.app.test_voice()
        self.assertTrue(pump(self.root, lambda: len(self.player.played) == 1))
        self.app.v_manual.set("Ручной текст")
        self.app.speak_manual()
        self.assertTrue(pump(self.root, lambda: len(self.player.played) == 2))
        self.assertEqual(self.app.v_manual.get(), "")

    def test_log_is_trimmed(self):
        from ttchat import gui

        for n in range(gui.LOG_LIMIT_LINES + 50):
            self.app._log("chat", f"сообщение {n}")
        lines = int(self.app.log.index("end-1c").split(".")[0])
        self.assertLessEqual(lines, gui.LOG_LIMIT_LINES + 1)
        self.assertIn(f"сообщение {gui.LOG_LIMIT_LINES + 49}", log_text(self.app))

    def test_close_saves_and_shuts_down(self):
        self.app.v_max_len.set(222)
        self.app.close()
        self.assertEqual(self.store.load().max_len, 222)
        self.assertFalse(self.app.controller.running)

    def test_no_voice_tuning_widgets(self):
        for name in ("v_rate", "v_pitch", "v_volume"):
            self.assertFalse(hasattr(self.app, name), name)

    def test_stats_cards_follow_controller(self):
        self.assertEqual(self.app.stat_values["viewers"].cget("text"), "—")
        self.app.controller.on_viewers(1234)
        self.app.controller.on_gift("Аня", "Rose", 2)
        self.app.controller.on_follow("Оля")
        self.app._refresh_stats()
        self.assertEqual(self.app.stat_values["viewers"].cget("text"), "1\u00a0234")
        self.assertEqual(self.app.stat_values["gifts"].cget("text"), "2")
        self.assertEqual(self.app.stat_values["follows"].cget("text"), "1")

    def test_update_button_hidden_until_new_version(self):
        self.assertFalse(self.app.btn_update.winfo_ismapped())
        info = updater.UpdateInfo("1.0.9", "v1.0.9", "https://example.test/a.exe")
        self.app.ui_queue.put(("update", info, False))
        self.assertTrue(pump(self.root, lambda: "1.0.9" in self.app.btn_update.cget("text")))
        self.assertIn("новая версия", log_text(self.app))

    def test_update_check_errors_are_quiet_unless_manual(self):
        self.app.ui_queue.put(("update", RuntimeError("нет сети"), False))
        pump(self.root, lambda: False, timeout=0.3)
        self.assertNotIn("нет сети", log_text(self.app))
        self.app.ui_queue.put(("update", RuntimeError("нет сети"), True))
        self.assertTrue(pump(self.root, lambda: "нет сети" in log_text(self.app)))

    def test_update_failure_re_enables_button(self):
        self.app._update_info = updater.UpdateInfo("1.0.9", "v1.0.9", "https://example.test/a.exe")
        self.app._updating = True
        self.app.ui_queue.put(("update_failed", "контрольная сумма не совпала"))
        self.assertTrue(pump(self.root, lambda: not self.app._updating))
        self.assertIn("контрольная сумма", log_text(self.app))

    def test_format_helpers(self):
        from ttchat import gui

        self.assertEqual(gui.format_count(None), "—")
        self.assertEqual(gui.format_count(0), "0")
        self.assertEqual(gui.format_uptime(3725), "1:02:05")


if __name__ == "__main__":
    unittest.main()
