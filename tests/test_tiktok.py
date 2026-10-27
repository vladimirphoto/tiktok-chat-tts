import asyncio
import threading
import unittest

from tests.helpers import wait_for
from ttchat import tiktok
from ttchat.tiktok import Handlers, TikTokService, parse_username


class ParseUsernameTests(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(parse_username("@isaackogz"), "isaackogz")
        self.assertEqual(parse_username("isaackogz"), "isaackogz")
        self.assertEqual(parse_username("  @some.user_1  "), "some.user_1")
        self.assertEqual(parse_username("https://www.tiktok.com/@abc.def/live"), "abc.def")
        self.assertEqual(parse_username("tiktok.com/@abc?lang=ru"), "abc")

    def test_invalid(self):
        for bad in ("", "   ", "@", "привет", "a", "https://www.tiktok.com/video/1", "bad name"):
            with self.assertRaises(ValueError, msg=bad):
                parse_username(bad)


class FakeError(Exception):
    def __init__(self, kind, text="ошибка"):
        super().__init__(text)
        self.kind = kind


class FakeClient:
    def __init__(self, script, handlers):
        self.script = script
        self.handlers = handlers
        self.release = asyncio.Event() if False else None
        self.disconnected = False
        self._event = None

    async def connect(self):
        self._event = asyncio.Event()
        await self.script(self)

    async def disconnect(self):
        self.disconnected = True
        if self._event is not None:
            self._event.set()

    async def hold(self):
        await self._event.wait()


class FakeLibrary:
    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.made = []
        self.keys = []

    def make_client(self, username, euler_key, handlers):
        self.keys.append(euler_key)
        script = self.scripts.pop(0) if len(self.scripts) > 1 else self.scripts[0]
        client = FakeClient(script, handlers)
        self.made.append((username, client))
        return client

    def classify(self, error):
        return getattr(error, "kind", tiktok.OTHER)


class Recorder:
    def __init__(self):
        self.statuses = []
        self.comments = []
        self.gifts = []
        self.follows = []

    def handlers(self):
        return Handlers(
            lambda n, t, ts: self.comments.append((n, t, ts)),
            lambda n, g, c: self.gifts.append((n, g, c)),
            lambda n: self.follows.append(n),
            lambda: None,
        )

    def status(self, state, message):
        self.statuses.append((state, message))

    def states(self):
        return [s for s, _ in self.statuses]


async def connect_and_hold(client):
    client.handlers.on_connect()
    await client.hold()


def make_service(lib, rec):
    return TikTokService(rec.handlers(), rec.status, library=lib, sleep_scale=0.001)


class ServiceTests(unittest.TestCase):
    def test_connects_forwards_events_and_stops(self):
        async def script(client):
            client.handlers.on_connect()
            client.handlers.on_comment("Маша", "привет", 123)
            client.handlers.on_gift("Аня", "Rose", 3)
            client.handlers.on_follow("Оля")
            await client.hold()

        rec = Recorder()
        lib = FakeLibrary(script)
        svc = make_service(lib, rec)
        svc.start("streamer", "KEY")
        self.assertTrue(wait_for(lambda: "connected" in rec.states()))
        self.assertTrue(wait_for(lambda: rec.follows))
        svc.stop()
        self.assertFalse(svc.running)
        self.assertEqual(rec.comments, [("Маша", "привет", 123)])
        self.assertEqual(rec.gifts, [("Аня", "Rose", 3)])
        self.assertEqual(rec.follows, ["Оля"])
        self.assertEqual(lib.made[0][0], "streamer")
        self.assertEqual(lib.keys, ["KEY"])
        self.assertTrue(lib.made[0][1].disconnected)
        self.assertEqual(rec.states()[-1], "stopped")

    def test_offline_retries_until_live(self):
        async def offline(client):
            raise FakeError(tiktok.OFFLINE)

        rec = Recorder()
        lib = FakeLibrary(offline, offline, connect_and_hold)
        svc = make_service(lib, rec)
        svc.start("streamer")
        self.assertTrue(wait_for(lambda: "connected" in rec.states()))
        svc.stop()
        self.assertEqual(rec.states().count("offline"), 2)
        self.assertGreaterEqual(len(lib.made), 3)
        offline_msg = next(m for s, m in rec.statuses if s == "offline")
        self.assertIn("не в эфире", offline_msg)

    def test_not_found_is_fatal_no_retry(self):
        async def missing(client):
            raise FakeError(tiktok.NOT_FOUND)

        rec = Recorder()
        lib = FakeLibrary(missing)
        svc = make_service(lib, rec)
        svc.start("nobody")
        self.assertTrue(wait_for(lambda: not svc.running))
        self.assertEqual(len(lib.made), 1)
        self.assertIn("fatal", rec.states())

    def test_age_restricted_is_fatal(self):
        async def aged(client):
            raise FakeError(tiktok.AGE)

        rec = Recorder()
        svc = make_service(FakeLibrary(aged), rec)
        svc.start("x1")
        self.assertTrue(wait_for(lambda: not svc.running))
        self.assertIn("fatal", rec.states())

    def test_unknown_error_backs_off_and_retries(self):
        async def boom(client):
            raise FakeError(tiktok.OTHER, "что-то странное")

        rec = Recorder()
        lib = FakeLibrary(boom, connect_and_hold)
        svc = make_service(lib, rec)
        svc.start("streamer")
        self.assertTrue(wait_for(lambda: "connected" in rec.states()))
        svc.stop()
        msg = next(m for s, m in rec.statuses if s == tiktok.OTHER)
        self.assertIn("что-то странное", msg)

    def test_reconnects_after_clean_disconnect(self):
        async def drop(client):
            client.handlers.on_connect()  # подключились и сразу оборвалось

        rec = Recorder()
        lib = FakeLibrary(drop, connect_and_hold)
        svc = make_service(lib, rec)
        svc.start("streamer")
        self.assertTrue(wait_for(lambda: rec.states().count("connected") == 2))
        svc.stop()
        self.assertIn("reconnecting", rec.states())

    def test_stop_during_retry_sleep_is_prompt(self):
        async def offline(client):
            raise FakeError(tiktok.OFFLINE)

        rec = Recorder()
        svc = TikTokService(rec.handlers(), rec.status, library=FakeLibrary(offline), sleep_scale=1.0)
        svc.start("streamer")  # реальная пауза 30 с
        self.assertTrue(wait_for(lambda: "offline" in rec.states()))
        import time
        started = time.monotonic()
        svc.stop()
        self.assertLess(time.monotonic() - started, 2)
        self.assertFalse(svc.running)

    def test_stop_while_connecting_cancels(self):
        async def never(client):
            await asyncio.sleep(3600)  # зависшее подключение, disconnect не помогает

        rec = Recorder()
        lib = FakeLibrary(never)
        svc = make_service(lib, rec)
        svc.start("streamer")
        self.assertTrue(wait_for(lambda: "connecting" in rec.states()))
        svc.stop()
        self.assertFalse(svc.running)

    def test_double_start_rejected(self):
        rec = Recorder()
        svc = make_service(FakeLibrary(connect_and_hold), rec)
        svc.start("streamer")
        with self.assertRaises(RuntimeError):
            svc.start("streamer")
        svc.stop()

    def test_restart_after_stop(self):
        rec = Recorder()
        lib = FakeLibrary(connect_and_hold)
        svc = make_service(lib, rec)
        svc.start("a1")
        self.assertTrue(wait_for(lambda: rec.states().count("connected") == 1))
        svc.stop()
        svc.start("b2")
        self.assertTrue(wait_for(lambda: rec.states().count("connected") == 2))
        svc.stop()
        self.assertEqual([u for u, _ in lib.made], ["a1", "b2"])


class DescribeTests(unittest.TestCase):
    def test_messages_mention_retry_delay(self):
        text = TikTokService._describe(tiktok.RATE_LIMIT, RuntimeError("x"), 60)
        self.assertIn("60", text)
        self.assertIn("ограничил", text)


if __name__ == "__main__":
    unittest.main()


class RealClassifyTests(unittest.TestCase):
    """Классификация настоящих ошибок TikTokLive (пропускается, если библиотеки нет)."""

    def setUp(self):
        try:
            import TikTokLive  # noqa: F401
        except Exception:
            self.skipTest("TikTokLive не установлена")
        self.lib = tiktok.RealLibrary()

    def test_empty_room_id_means_offline(self):
        try:
            int("")
        except ValueError as ex:
            self.assertEqual(self.lib.classify(ex), tiktok.OFFLINE)

    def test_other_value_error_stays_other(self):
        self.assertEqual(self.lib.classify(ValueError("что-то другое")), tiktok.OTHER)

    def test_failed_parse_room_id_means_offline(self):
        from TikTokLive.client.web.routes.fetch_room_id_live_html import FailedParseRoomIdError

        self.assertEqual(self.lib.classify(FailedParseRoomIdError("нет SIGI_STATE")), tiktok.OFFLINE)
