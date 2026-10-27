import os
import threading
import unittest

from tests.helpers import wait_for
from ttchat.player import MciPlayer, NullPlayer, PlayerError


class FakeMci:
    """Имитация winmm.mciSendStringW: хранит журнал команд и «играет» N опросов статуса."""

    def __init__(self, polls_playing=2, fail_on=None):
        self.log = []
        self.polls_playing = polls_playing
        self.fail_on = fail_on
        self.files = []

    def send(self, command, want_result=False):
        self.log.append(command)
        verb = command.split()[0]
        if verb == "open":
            path = command.split('"')[1]
            self.files.append((path, os.path.exists(path)))
        if self.fail_on == verb:
            return 263, ""
        if command.startswith("status") and "mode" in command:
            if self.polls_playing > 0:
                self.polls_playing -= 1
                return 0, "playing"
            return 0, "stopped"
        return 0, ""

    def error_text(self, code):
        return f"ошибка {code}"

    def verbs(self):
        return [c.split()[0] for c in self.log]


class MciPlayerTests(unittest.TestCase):
    def make(self, **kw):
        fake = FakeMci(**kw)
        return fake, MciPlayer(mci_send=fake.send, mci_error=fake.error_text, poll=0.001)

    def test_command_sequence_and_volume(self):
        fake, player = self.make()
        player.play(b"\xff\xf3data", 80)
        verbs = fake.verbs()
        self.assertEqual(verbs[:3], ["open", "setaudio", "play"])
        self.assertEqual(verbs[-2:], ["stop", "close"])
        self.assertIn("volume to 800", fake.log[1])
        self.assertIn("type mpegvideo", fake.log[0])

    def test_temp_file_exists_during_open_and_removed_after(self):
        fake, player = self.make()
        player.play(b"data", 50)
        path, existed = fake.files[0]
        self.assertTrue(existed)
        self.assertFalse(os.path.exists(path))

    def test_volume_is_clamped(self):
        fake, player = self.make()
        player.play(b"d", 999)
        self.assertIn("volume to 1000", fake.log[1])
        fake2, player2 = self.make()
        player2.play(b"d", -5)
        self.assertIn("volume to 0", fake2.log[1])

    def test_open_failure_raises_and_cleans_up(self):
        fake, player = self.make(fail_on="open")
        with self.assertRaises(PlayerError):
            player.play(b"d", 50)
        path, _ = fake.files[0]
        self.assertFalse(os.path.exists(path))
        self.assertNotIn("close", fake.verbs())  # нечего закрывать

    def test_setaudio_failure_is_not_fatal(self):
        fake, player = self.make(fail_on="setaudio")
        player.play(b"d", 50)
        self.assertIn("play", fake.verbs())

    def test_stop_interrupts_playback(self):
        fake, player = self.make(polls_playing=10_000)
        done = threading.Event()

        def run():
            player.play(b"d", 50)
            done.set()

        threading.Thread(target=run, daemon=True).start()
        self.assertTrue(wait_for(lambda: "play" in [c.split()[0] for c in fake.log]))
        player.stop()
        self.assertTrue(done.wait(2))

    def test_stop_while_idle_does_not_affect_next_play(self):
        fake, player = self.make(polls_playing=3)
        player.stop()
        player.play(b"d", 50)
        self.assertGreaterEqual(fake.verbs().count("status"), 3)

    def test_unique_aliases(self):
        fake, player = self.make(polls_playing=0)
        player.play(b"a", 50)
        player.play(b"b", 50)
        opens = [c for c in fake.log if c.startswith("open")]
        self.assertNotEqual(opens[0].split()[-1], opens[1].split()[-1])


class NullPlayerTests(unittest.TestCase):
    def test_records_calls(self):
        p = NullPlayer()
        p.play(b"abc", 70)
        self.assertEqual(p.played, [(3, 70)])

    def test_stop_cuts_delay(self):
        p = NullPlayer(delay=5)
        done = threading.Event()
        threading.Thread(target=lambda: (p.play(b"a", 1), done.set()), daemon=True).start()
        self.assertTrue(wait_for(lambda: p.played))
        p.stop()
        self.assertTrue(done.wait(2))


if __name__ == "__main__":
    unittest.main()
