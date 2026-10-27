import threading
import time
import unittest
from dataclasses import replace

from tests.helpers import wait_for
from ttchat.engine import Engine
from ttchat.player import NullPlayer
from ttchat.settings import Settings


class RecordingSynth:
    def __init__(self, delay=0.0, fail_on=None):
        self.calls = []
        self.delay = delay
        self.fail_on = fail_on
        self.gate = None

    def __call__(self, text, voice, rate, pitch):
        if self.gate is not None:
            self.gate.wait(3)
        time.sleep(self.delay)
        self.calls.append((text, voice, rate, pitch))
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("синтез сломался")
        return text.encode("utf-8")


def make_engine(settings=None, synth=None, player=None):
    settings = settings or Settings()
    events = []
    synth = synth or RecordingSynth()
    player = player or NullPlayer()
    engine = Engine(lambda: settings, synth, player, lambda lvl, msg: events.append((lvl, msg)))
    return engine, synth, player, events


class EngineTests(unittest.TestCase):
    def test_speaks_in_order(self):
        engine, synth, player, events = make_engine()
        engine.start()
        try:
            for text in ("раз", "два", "три"):
                engine.enqueue(text)
            self.assertTrue(wait_for(lambda: len(player.played) == 3))
        finally:
            engine.stop()
        self.assertEqual([c[0] for c in synth.calls], ["раз", "два", "три"])
        self.assertEqual([m for lvl, m in events if lvl == "speak"], ["раз", "два", "три"])

    def test_uses_current_voice_rate_pitch_volume(self):
        s = replace(Settings(), voice="ru-RU-DariyaNeural", rate=20, pitch=-3, volume=55)
        engine, synth, player, _ = make_engine(settings=s)
        engine.start()
        try:
            engine.enqueue("привет")
            self.assertTrue(wait_for(lambda: player.played))
        finally:
            engine.stop()
        self.assertEqual(synth.calls[0], ("привет", "ru-RU-DariyaNeural", 20, -3))
        self.assertEqual(player.played[0][1], 55)

    def test_empty_text_not_queued(self):
        engine, *_ = make_engine()
        self.assertFalse(engine.enqueue("   "))
        self.assertEqual(engine.pending(), 0)

    def test_overflow_drops_oldest(self):
        s = replace(Settings(), queue_limit=3)
        engine, synth, player, events = make_engine(settings=s)
        # поток синтеза не запущен: очередь только копится
        for n in range(6):
            engine.enqueue(f"сообщение {n}")
        self.assertEqual(engine.pending(), 3)
        self.assertEqual(sum(1 for lvl, _ in events if lvl == "warn"), 3)
        engine.start()
        try:
            self.assertTrue(wait_for(lambda: len(player.played) == 3))
        finally:
            engine.stop()
        self.assertEqual([c[0] for c in synth.calls], ["сообщение 3", "сообщение 4", "сообщение 5"])

    def test_synth_error_is_reported_and_queue_continues(self):
        engine, synth, player, events = make_engine(synth=RecordingSynth(fail_on="плохо"))
        engine.start()
        try:
            engine.enqueue("плохо")
            engine.enqueue("хорошо")
            self.assertTrue(wait_for(lambda: len(player.played) == 1))
        finally:
            engine.stop()
        self.assertTrue(any(lvl == "error" and "синтез сломался" in m for lvl, m in events))

    def test_player_error_is_reported_and_queue_continues(self):
        class FlakyPlayer(NullPlayer):
            def play(self, mp3, volume):
                if b"bad" in mp3:
                    raise RuntimeError("нет звуковой карты")
                super().play(mp3, volume)

        engine, _, player, events = make_engine(player=FlakyPlayer())
        engine.start()
        try:
            engine.enqueue("bad")
            engine.enqueue("good")
            self.assertTrue(wait_for(lambda: len(player.played) == 1))
        finally:
            engine.stop()
        self.assertTrue(any(lvl == "error" and "нет звуковой карты" in m for lvl, m in events))

    def test_clear_drops_pending_and_in_flight(self):
        synth = RecordingSynth()
        synth.gate = threading.Event()  # держим синтез, чтобы фраза «зависла» в полёте
        engine, _, player, _ = make_engine(synth=synth)
        engine.start()
        try:
            engine.enqueue("устаревшая")
            engine.enqueue("тоже устаревшая")
            self.assertTrue(wait_for(lambda: engine.pending() >= 1))
            engine.clear()
            synth.gate.set()
            time.sleep(0.3)
            self.assertEqual(player.played, [])
            engine.enqueue("новая")
            self.assertTrue(wait_for(lambda: len(player.played) == 1))
        finally:
            engine.stop()

    def test_skip_interrupts_current_and_continues(self):
        player = NullPlayer(delay=5)
        engine, _, _, _ = make_engine(player=player)
        engine.start()
        try:
            engine.enqueue("длинная")
            engine.enqueue("следующая")
            self.assertTrue(wait_for(lambda: len(player.played) == 1))
            started = time.monotonic()
            engine.skip()
            self.assertTrue(wait_for(lambda: len(player.played) == 2, timeout=2))
            self.assertLess(time.monotonic() - started, 2)
        finally:
            player.stop()
            engine.stop()

    def test_stop_is_idempotent_and_prompt(self):
        engine, *_ = make_engine()
        engine.start()
        started = time.monotonic()
        engine.stop()
        engine.stop()
        self.assertLess(time.monotonic() - started, 2)

    def test_pending_counts_synth_and_ready(self):
        engine, *_ = make_engine()
        engine.enqueue("а")
        engine.enqueue("б")
        self.assertEqual(engine.pending(), 2)


if __name__ == "__main__":
    unittest.main()
