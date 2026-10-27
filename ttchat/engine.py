"""Очередь озвучки: один поток синтезирует, другой играет — следующая фраза готовится заранее."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional

from .player import Player
from .settings import PLAYBACK_VOLUME, Settings

EventCallback = Callable[[str, str], None]  # (уровень, сообщение)


@dataclass
class Item:
    text: str
    kind: str
    generation: int
    created: float


class Engine:
    def __init__(self, get_settings: Callable[[], Settings], synth: Callable[..., bytes],
                 player: Player, on_event: Optional[EventCallback] = None,
                 ready_size: int = 2, volume: int = PLAYBACK_VOLUME) -> None:
        self._get_settings = get_settings
        self._volume = volume
        self._synth = synth
        self._player = player
        self._on_event = on_event or (lambda level, message: None)
        self._cv = threading.Condition()
        self._pending: Deque[Item] = deque()
        self._ready: "queue.Queue[tuple[Item, bytes]]" = queue.Queue(maxsize=ready_size)
        self._generation = 0
        self._stopping = False
        self._threads: list[threading.Thread] = []

    # ---- управление ----------------------------------------------------
    def start(self) -> None:
        if self._threads:
            return
        self._stopping = False
        for name, target in (("tts-synth", self._synth_loop), ("tts-play", self._play_loop)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        with self._cv:
            self._stopping = True
            self._pending.clear()
            self._cv.notify_all()
        self._player.stop()
        for thread in self._threads:
            thread.join(timeout=3)
        self._threads.clear()

    def enqueue(self, text: str, kind: str = "chat") -> bool:
        """Поставить фразу в очередь. При переполнении выбрасывается самая старая."""
        text = (text or "").strip()
        if not text:
            return False
        limit = max(1, self._get_settings().queue_limit)
        with self._cv:
            while len(self._pending) >= limit:
                dropped = self._pending.popleft()
                self._emit("warn", f"Очередь переполнена, пропущено: {dropped.text[:40]}")
            self._pending.append(Item(text, kind, self._generation, time.monotonic()))
            self._cv.notify()
        return True

    def pending(self) -> int:
        with self._cv:
            return len(self._pending) + self._ready.qsize()

    def skip(self) -> None:
        """Пропустить фразу, которая звучит сейчас."""
        self._player.stop()

    def clear(self) -> None:
        """Сбросить всю очередь и прервать текущую фразу."""
        with self._cv:
            self._generation += 1
            self._pending.clear()
        while True:
            try:
                self._ready.get_nowait()
            except queue.Empty:
                break
        self._player.stop()

    # ---- рабочие потоки --------------------------------------------------
    def _emit(self, level: str, message: str) -> None:
        try:
            self._on_event(level, message)
        except Exception:
            pass

    def _next_pending(self) -> Optional[Item]:
        with self._cv:
            while not self._pending and not self._stopping:
                self._cv.wait(0.5)
            if self._stopping:
                return None
            return self._pending.popleft()

    def _synth_loop(self) -> None:
        while True:
            item = self._next_pending()
            if item is None:
                return
            if item.generation != self._generation:
                continue
            settings = self._get_settings()
            try:
                audio = self._synth(item.text, settings.voice)
            except Exception as ex:
                self._emit("error", f"Не удалось озвучить: {ex}")
                continue
            while not self._stopping:
                try:
                    self._ready.put((item, audio), timeout=0.2)
                    break
                except queue.Full:
                    continue

    def _play_loop(self) -> None:
        while not self._stopping:
            try:
                item, audio = self._ready.get(timeout=0.2)
            except queue.Empty:
                continue
            if item.generation != self._generation:
                continue
            self._emit("speak", item.text)
            try:
                self._player.play(audio, self._volume)
            except Exception as ex:
                self._emit("error", f"Ошибка воспроизведения: {ex}")
