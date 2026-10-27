import unittest
from dataclasses import replace

from tests.helpers import wait_for
from tests.test_engine import RecordingSynth
from tests.test_tiktok import FakeLibrary, connect_and_hold
from ttchat.controller import Controller
from ttchat.engine import Engine
from ttchat.player import NullPlayer
from ttchat.settings import Settings


class Harness:
    def __init__(self, **overrides):
        self.settings = replace(Settings(), **overrides)
        self.log = []
        self.states = []
        self.player = NullPlayer()
        self.synth = RecordingSynth()
        self.engine = Engine(lambda: self.settings, self.synth, self.player)
        self.now = [1_000_000]
        self.lib = FakeLibrary(connect_and_hold)
        self.ctl = Controller(lambda: self.settings, self.engine,
                              lambda lvl, msg: self.log.append((lvl, msg)),
                              lambda st, msg: self.states.append(st),
                              library=self.lib, clock_ms=lambda: self.now[0])

    def spoken(self):
        return [c[0] for c in self.synth.calls]


class ControllerTests(unittest.TestCase):
    def test_comment_is_filtered_logged_and_queued(self):
        h = Harness()
        h.engine.start()
        try:
            h.ctl.on_comment("Маша", "Привет всем!")
            self.assertTrue(wait_for(lambda: h.player.played))
            self.assertEqual(h.spoken(), ["Маша: Привет всем!"])
            self.assertIn(("chat", "Маша: Привет всем!"), h.log)
        finally:
            h.ctl.shutdown()

    def test_skipped_comment_is_logged_with_reason_not_spoken(self):
        h = Harness()
        h.ctl.on_comment("Вася", "!команда")
        h.ctl.on_comment("Вася", "😍😍")
        reasons = [m for lvl, m in h.log if lvl == "skip"]
        self.assertEqual(len(reasons), 2)
        self.assertIn("команда", reasons[0])
        self.assertEqual(h.engine.pending(), 0)

    def test_gifts_and_follows_respect_switches(self):
        h = Harness(read_gifts=False, read_follows=True)
        h.ctl.on_gift("Аня", "Rose", 1)
        self.assertEqual(h.engine.pending(), 0)
        h.ctl.on_follow("Оля")
        self.assertEqual(h.engine.pending(), 1)
        h.settings = replace(h.settings, read_gifts=True, read_follows=False)
        h.ctl.on_gift("Аня", "Rose", 2)
        h.ctl.on_follow("Оля")
        self.assertEqual(h.engine.pending(), 2)

    def test_followers_only_filter(self):
        h = Harness(followers_only=True, read_follows=False)
        h.ctl.on_comment("Вася", "привет", None, False)
        self.assertEqual(h.engine.pending(), 0)
        h.ctl.on_comment("Петя", "привет", None, True)
        self.assertEqual(h.engine.pending(), 1)
        h.ctl.on_comment("Аня", "привет", None, None)  # статус не известен
        self.assertEqual(h.engine.pending(), 1)
        h.ctl.on_follow("Маша")  # подписалась в этом эфире
        h.ctl.on_comment("Маша", "новое сообщение", None)
        self.assertEqual(h.engine.pending(), 2)
        reasons = [m for lvl, m in h.log if lvl == "skip"]
        self.assertTrue(any("не подписчик" in r for r in reasons))
        self.assertTrue(any("не определён" in r for r in reasons))

    def test_backlog_comments_right_after_connect_are_dropped(self):
        h = Harness()
        h.ctl._on_connect()  # подключились в момент now=1_000_000
        h.now[0] += 1000     # через секунду приходит «хвост» старых комментариев
        h.ctl.on_comment("Старый", "давнее сообщение", 1_000_000 - 60_000)
        self.assertEqual(h.engine.pending(), 0)
        h.ctl.on_comment("Новый", "свежее сообщение", 1_000_000 + 500)
        self.assertEqual(h.engine.pending(), 1)

    def test_backlog_filter_expires_and_can_be_disabled(self):
        h = Harness()
        h.ctl._on_connect()
        h.now[0] += 6000  # окно «хвоста» закрыто: старые отметки времени уже не отбрасываем
        h.ctl.on_comment("A", "сообщение раз", 1_000_000 - 60_000)
        self.assertEqual(h.engine.pending(), 1)
        h2 = Harness(only_new=False)
        h2.ctl._on_connect()
        h2.now[0] += 1000
        h2.ctl.on_comment("A", "сообщение", 1_000_000 - 60_000)
        self.assertEqual(h2.engine.pending(), 1)

    def test_comment_without_timestamp_is_kept(self):
        h = Harness()
        h.ctl._on_connect()
        h.ctl.on_comment("A", "сообщение", None)
        self.assertEqual(h.engine.pending(), 1)

    def test_connect_validates_username(self):
        h = Harness(username="  ")
        self.assertFalse(h.ctl.connect())
        self.assertTrue(any(lvl == "error" for lvl, _ in h.log))
        self.assertFalse(h.ctl.running)

    def test_connect_passes_settings_to_library_and_reports_state(self):
        h = Harness(username="@streamer", euler_key=" KEY ")
        self.assertTrue(h.ctl.connect())
        try:
            self.assertTrue(wait_for(lambda: "connected" in h.states))
            self.assertEqual(h.lib.made[0][0], "streamer")
            self.assertEqual(h.lib.keys, ["KEY"])
            self.assertFalse(h.ctl.connect())  # повторное подключение игнорируется
        finally:
            h.ctl.shutdown()
        self.assertEqual(h.states[-1], "stopped")

    def test_speak_bypasses_filters(self):
        h = Harness()
        h.engine.start()
        try:
            self.assertTrue(h.ctl.speak("!тест"))
            self.assertTrue(wait_for(lambda: h.player.played))
            self.assertEqual(h.spoken(), ["!тест"])
        finally:
            h.ctl.shutdown()


class StatsTests(unittest.TestCase):
    def test_counters(self):
        h = Harness()
        self.assertIsNone(h.ctl.stats.viewers)
        h.ctl.on_viewers(42)
        h.ctl.on_gift("Аня", "Rose", 3)
        h.ctl.on_gift("Аня", "Rose", 1)
        h.ctl.on_follow("Оля")
        self.assertEqual((h.ctl.stats.viewers, h.ctl.stats.gifts, h.ctl.stats.follows), (42, 4, 1))

    def test_likes_use_total_when_known_else_sum(self):
        h = Harness()
        h.ctl.on_likes(5, 0)
        h.ctl.on_likes(3, 0)
        self.assertEqual(h.ctl.stats.likes, 8)
        h.ctl.on_likes(2, 1000)
        self.assertEqual(h.ctl.stats.likes, 1000)

    def test_gift_counted_even_when_not_spoken(self):
        h = Harness(read_gifts=False, read_follows=False)
        h.ctl.on_gift("Аня", "Rose", 1)
        h.ctl.on_follow("Оля")
        self.assertEqual((h.ctl.stats.gifts, h.ctl.stats.follows), (1, 1))

    def test_stats_reset_on_connect(self):
        h = Harness()
        h.ctl.on_gift("Аня", "Rose", 2)
        h.settings = replace(h.settings, username="streamer")
        try:
            self.assertTrue(h.ctl.connect())
            self.assertEqual(h.ctl.stats.gifts, 0)
        finally:
            h.ctl.shutdown()


if __name__ == "__main__":
    unittest.main()
