"""Связывает чат TikTok, фильтры и очередь озвучки. Не зависит от GUI."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .engine import Engine
from .settings import Settings
from .textproc import RecentTracker, follow_phrase, gift_phrase, prepare_chat
from .tiktok import Handlers, TikTokService, now_ms, parse_username

EmitCallback = Callable[[str, str], None]    # (уровень, текст) -> журнал
StateCallback = Callable[[str, str], None]   # (состояние, пояснение) -> строка статуса

@dataclass
class Stats:
    viewers: Optional[int] = None   # None — TikTok ещё не прислал
    likes: int = 0
    gifts: int = 0
    follows: int = 0


BACKLOG_WINDOW_S = 5.0      # сразу после подключения TikTok присылает «хвост» старых комментариев
BACKLOG_TOLERANCE_MS = 10_000


class Controller:
    def __init__(self, get_settings: Callable[[], Settings], engine: Engine,
                 emit: EmitCallback, on_state: StateCallback,
                 library: Any = None, clock_ms: Callable[[], int] = now_ms) -> None:
        self._get_settings = get_settings
        self.engine = engine
        self._emit = emit
        self._on_state = on_state
        self._clock_ms = clock_ms
        self._recent = RecentTracker()
        self._connected_at_ms = 0
        self._lock = threading.Lock()
        self.stats = Stats()
        self._service = TikTokService(
            Handlers(self.on_comment, self.on_gift, self.on_follow, self._on_connect,
                     self.on_viewers, self.on_likes, self.on_join),
            self._on_status, library=library,
        )

    # ---- управление подключением ----------------------------------------
    @property
    def running(self) -> bool:
        return self._service.running

    def connect(self) -> bool:
        settings = self._get_settings()
        try:
            username = parse_username(settings.username)
        except ValueError as ex:
            self._emit("error", str(ex))
            return False
        if self._service.running:
            return False
        self._connected_at_ms = 0
        self.stats = Stats()
        self._service.start(username, settings.euler_key.strip())
        return True

    def disconnect(self) -> None:
        self._service.stop()

    def shutdown(self) -> None:
        self._service.stop()
        self.engine.stop()

    # ---- события TikTok (приходят из потока сервиса) -----------------------
    def _on_connect(self) -> None:
        with self._lock:
            self._connected_at_ms = self._clock_ms()

    def _on_status(self, state: str, message: str) -> None:
        level = "error" if state in ("error", "fatal") else "info"
        self._emit(level, message)
        self._on_state(state, message)

    def _is_backlog(self, created_ms: Optional[int]) -> bool:
        with self._lock:
            connected = self._connected_at_ms
        if not created_ms or not connected:
            return False
        now = self._clock_ms()
        if now - connected > BACKLOG_WINDOW_S * 1000:
            return False
        return created_ms < connected - BACKLOG_TOLERANCE_MS

    def on_comment(self, nick: str, text: str, created_ms: Optional[int] = None) -> None:
        settings = self._get_settings()
        if settings.only_new and self._is_backlog(created_ms):
            return
        result = prepare_chat(nick, text, settings, self._recent)
        if result.phrase is None:
            self._emit("skip", f"{nick}: {text}  — пропущено ({result.reason})")
            return
        self._emit("chat", f"{nick}: {text}")
        self.engine.enqueue(result.phrase, "chat")

    def on_viewers(self, count: int) -> None:
        self.stats.viewers = count

    def on_likes(self, count: int, total: int) -> None:
        # «total» — сумма лайков по всему эфиру; если её нет, копим сами
        self.stats.likes = total if total > self.stats.likes else self.stats.likes + count

    def on_gift(self, nick: str, gift: str, count: int) -> None:
        self.stats.gifts += max(1, int(count))
        # В ленту событий попадает всё, что подарили, — независимо от озвучки подарков.
        self._emit("gift", f"{nick} отправил подарок {gift} ×{count}")
        if self._get_settings().read_gifts:
            self.engine.enqueue(gift_phrase(nick, gift, count), "gift")

    def on_follow(self, nick: str) -> None:
        self.stats.follows += 1
        self._emit("follow", f"{nick} подписался")
        if self._get_settings().read_follows:
            self.engine.enqueue(follow_phrase(nick), "follow")

    def on_join(self, nick: str) -> None:
        """Зритель вошёл в эфир. В ленте видно, в слух не читаем."""
        if not (nick or "").strip():
            return
        self._emit("join", f"{nick} зашёл в эфир")

    # ---- ручная озвучка ----------------------------------------------------
    def speak(self, text: str) -> bool:
        """Озвучить произвольный текст без фильтров (проверка голоса, ручной ввод)."""
        return self.engine.enqueue(text, "manual")
