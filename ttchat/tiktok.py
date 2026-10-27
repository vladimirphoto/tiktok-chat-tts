"""Подключение к чату TikTok LIVE с автоматическим переподключением.

Сетевую часть делает библиотека TikTokLive. Она импортируется только внутри
RealLibrary, поэтому остальной код и тесты работают без неё.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

# Причины сбоя подключения (результат classify)
OFFLINE = "offline"
NOT_FOUND = "not_found"
RATE_LIMIT = "rate_limit"
AGE = "age"
BLOCKED = "blocked"
OTHER = "other"

RETRY_DELAYS = {OFFLINE: 30.0, RATE_LIMIT: 60.0, BLOCKED: 45.0, AGE: 0.0, NOT_FOUND: 0.0}
MAX_BACKOFF = 60.0

_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{2,24}$")


def parse_username(raw: str) -> str:
    """Достать ник из «@name», «name» или ссылки https://www.tiktok.com/@name/live."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Введите ник стримера")
    if "tiktok.com" in raw.lower():
        if "://" not in raw:
            raw = "https://" + raw
        match = re.search(r"/@([^/?#]+)", urlparse(raw).path)
        if not match:
            raise ValueError("В ссылке не найден ник вида @name")
        raw = match.group(1)
    name = raw.lstrip("@").strip()
    if not _USERNAME_RE.match(name):
        raise ValueError("Ник может содержать только латинские буквы, цифры, точку и «_»")
    return name


def follower_of(event: Any) -> Optional[bool]:
    """Подписан ли автор события на канал: True/False либо None, если TikTok не сообщил."""
    user = getattr(event, "user", None)
    if user is None:
        return None
    for attr in ("is_follower", "isFollower", "is_subscriber"):
        value = getattr(user, attr, None)
        if isinstance(value, bool):
            return value
    info = getattr(user, "follow_info", None)
    if info is None:
        info = getattr(user, "followInfo", None)
    status = getattr(info, "follow_status", None) if info is not None else None
    if status is None:
        status = getattr(user, "follow_status", None)
    if isinstance(status, bool):
        return status
    if isinstance(status, int):
        # 0 — не подписан; 1 — подписан; 2 и дальше — взаимная подписка / друзья
        return status != 0
    return None


@dataclass
class Handlers:
    on_comment: Callable[[str, str, Optional[int], Optional[bool]], None]  # ник, текст, время, подписчик
    on_gift: Callable[[str, str, int], None]                # ник, подарок, количество
    on_follow: Callable[[str], None]
    on_connect: Callable[[], None]
    on_viewers: Optional[Callable[[int], None]] = None      # сколько зрителей сейчас
    on_likes: Optional[Callable[[int, int], None]] = None   # лайков в событии, всего в эфире (0, если неизвестно)
    on_join: Optional[Callable[[str], None]] = None         # ник вошедшего в эфир зрителя


class RealLibrary:
    """Обёртка над TikTokLive: создаёт клиента, вешает обработчики, классифицирует ошибки."""

    def make_client(self, username: str, euler_key: str, handlers: Handlers) -> Any:
        from TikTokLive import TikTokLiveClient
        from TikTokLive.client.web.web_settings import WebDefaults
        from TikTokLive.events import CommentEvent, ConnectEvent, FollowEvent, GiftEvent

        if euler_key:
            WebDefaults.tiktok_sign_api_key = euler_key
        client = TikTokLiveClient(unique_id="@" + username)

        def nick_of(event: Any) -> str:
            user = getattr(event, "user", None)
            return (getattr(user, "nickname", "") or getattr(user, "unique_id", "") or "")

        @client.on(ConnectEvent)
        async def _connected(event: Any) -> None:
            handlers.on_connect()

        @client.on(CommentEvent)
        async def _comment(event: Any) -> None:
            common = getattr(event, "common", None)
            created = getattr(common, "create_time", None)
            handlers.on_comment(nick_of(event), getattr(event, "comment", "") or "", created,
                                follower_of(event))

        @client.on(GiftEvent)
        async def _gift(event: Any) -> None:
            gift = event.gift
            if getattr(gift, "streakable", False):
                if getattr(event, "streaking", False):
                    return  # серия ещё идёт: озвучим один раз, когда она закончится
                count = int(getattr(event, "repeat_count", 1) or 1)
            else:
                count = 1
            handlers.on_gift(nick_of(event), getattr(gift, "name", "") or "", count)

        @client.on(FollowEvent)
        async def _follow(event: Any) -> None:
            handlers.on_follow(nick_of(event))

        # Вход зрителя в эфир — для ленты событий. Если события нет в версии библиотеки, пропускаем.
        try:
            from TikTokLive.events import JoinEvent
        except Exception:
            JoinEvent = None  # noqa: N806
        if JoinEvent is not None and handlers.on_join is not None:
            @client.on(JoinEvent)
            async def _join(event: Any) -> None:
                handlers.on_join(nick_of(event))

        # Статистика необязательна: если в версии библиотеки этих событий нет, просто пропускаем.
        try:
            from TikTokLive.events import LikeEvent, RoomUserSeqEvent
        except Exception:
            return client

        def as_int(value: Any) -> int:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return 0

        @client.on(LikeEvent)
        async def _like(event: Any) -> None:
            if handlers.on_likes is not None:
                handlers.on_likes(as_int(getattr(event, "count", 0)), as_int(getattr(event, "total", 0)))

        @client.on(RoomUserSeqEvent)
        async def _viewers(event: Any) -> None:
            if handlers.on_viewers is not None:
                number = getattr(event, "total", None)
                if number is None:
                    number = getattr(event, "total_user", None)
                handlers.on_viewers(as_int(number))

        return client

    def classify(self, error: BaseException) -> str:
        try:
            from TikTokLive.client import errors as e
        except Exception:
            return OTHER
        if isinstance(error, e.UserOfflineError):
            return OFFLINE
        if isinstance(error, e.UserNotFoundError):
            return NOT_FOUND
        if isinstance(error, e.SignatureRateLimitError):
            return RATE_LIMIT
        if isinstance(error, e.AgeRestrictedError):
            return AGE
        if isinstance(error, (e.WebcastBlockedError, e.SignAPIError)):
            return BLOCKED
        # Если эфира нет, TikTok отдаёт пустой ID комнаты, и библиотека падает на int('') —
        # ValueError «invalid literal for int()». Для пользователя это тот же случай «не в эфире».
        if isinstance(error, ValueError) and "invalid literal for int()" in str(error):
            return OFFLINE
        try:
            from TikTokLive.client.web.routes.fetch_room_id_live_html import FailedParseRoomIdError
        except Exception:
            FailedParseRoomIdError = None  # noqa: N806
        if FailedParseRoomIdError is not None and isinstance(error, FailedParseRoomIdError):
            return OFFLINE
        return OTHER


StatusCallback = Callable[[str, str], None]  # (состояние, пояснение)


class TikTokService:
    """Живёт в отдельном потоке со своим event loop; сам переподключается при обрыве."""

    def __init__(self, handlers: Handlers, on_status: StatusCallback,
                 library: Any = None, sleep_scale: float = 1.0) -> None:
        self._handlers = handlers
        self._on_status = on_status
        self._lib = library or RealLibrary()
        self._sleep_scale = sleep_scale
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._client: Any = None
        self._ready = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, username: str, euler_key: str = "") -> None:
        if self.running:
            raise RuntimeError("уже запущено")
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._thread_main, args=(username, euler_key), name="tiktok", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self, timeout: float = 6.0) -> None:
        loop, stop_event = self._loop, self._stop_event
        if loop is not None and stop_event is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                pass  # цикл уже закрылся — поток и так завершается
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._thread = None

    # ---- внутреннее -------------------------------------------------------
    def _thread_main(self, username: str, euler_key: str) -> None:
        try:
            asyncio.run(self._main(username, euler_key))
        except Exception as ex:  # на всякий случай: поток не должен падать молча
            self._on_status("error", f"Сбой модуля TikTok: {type(ex).__name__}: {ex}")
        finally:
            self._on_status("stopped", "Отключено")

    async def _sleep(self, seconds: float) -> bool:
        """Спать, пока не попросят остановиться. True — остановка запрошена."""
        assert self._stop_event is not None
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds * self._sleep_scale)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run_until_stop(self, client: Any) -> bool:
        """Держать соединение, пока оно живо или пока не попросили остановиться.

        Возвращает True, если остановка запрошена; исключение подключения пробрасывается.
        """
        assert self._stop_event is not None
        connect_task = asyncio.ensure_future(client.connect())
        stop_task = asyncio.ensure_future(self._stop_event.wait())
        try:
            done, _ = await asyncio.wait({connect_task, stop_task},
                                         return_when=asyncio.FIRST_COMPLETED)
            if connect_task in done:
                connect_task.result()  # пробросит ошибку подключения, если она была
                return self._stop_event.is_set()
            try:
                await asyncio.wait_for(client.disconnect(), timeout=3)
            except Exception:
                pass
            return True
        finally:
            stop_task.cancel()
            if not connect_task.done():
                connect_task.cancel()
            await asyncio.gather(connect_task, stop_task, return_exceptions=True)

    async def _main(self, username: str, euler_key: str) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._ready.set()
        failures = 0
        while not self._stop_event.is_set():
            connected = {"value": False}

            def on_connect() -> None:
                connected["value"] = True
                self._on_status("connected", f"Подключено к @{username}")
                self._handlers.on_connect()

            handlers = Handlers(self._handlers.on_comment, self._handlers.on_gift,
                                self._handlers.on_follow, on_connect,
                                self._handlers.on_viewers, self._handlers.on_likes,
                                self._handlers.on_join)
            self._on_status("connecting", f"Подключаюсь к @{username}…")
            delay: float
            try:
                self._client = self._lib.make_client(username, euler_key, handlers)
                if await self._run_until_stop(self._client):
                    break
                self._on_status("reconnecting", "Соединение закрыто, переподключаюсь…")
                delay = 3.0 if connected["value"] else min(MAX_BACKOFF, 5.0 * 2 ** failures)
                failures = 0 if connected["value"] else failures + 1
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                if self._stop_event.is_set():
                    break
                kind = self._lib.classify(ex)
                if kind == NOT_FOUND:
                    self._on_status("fatal", f"Пользователь @{username} не найден или у него нет LIVE")
                    return
                if kind == AGE:
                    self._on_status("fatal", "Эфир с ограничением по возрасту — подключиться нельзя")
                    return
                failures += 1
                delay = RETRY_DELAYS.get(kind) or min(MAX_BACKOFF, 5.0 * 2 ** (failures - 1))
                self._on_status(kind, self._describe(kind, ex, delay))
            finally:
                self._client = None
            if await self._sleep(delay):
                break

    @staticmethod
    def _describe(kind: str, ex: BaseException, delay: float) -> str:
        wait = f"Повтор через {int(delay)} с."
        if kind == OFFLINE:
            return f"Стример сейчас не в эфире (или TikTok не отдаёт эфир). Проверьте ник. {wait}"
        if kind == RATE_LIMIT:
            return f"Сервис подписи ограничил число подключений. {wait}"
        if kind == BLOCKED:
            return f"TikTok или сервис подписи отклонил подключение ({type(ex).__name__}). {wait}"
        return f"Ошибка подключения: {type(ex).__name__}: {ex}. {wait}"


def now_ms() -> int:
    return int(time.time() * 1000)
