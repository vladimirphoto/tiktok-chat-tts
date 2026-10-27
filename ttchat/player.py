"""Воспроизведение mp3: в Windows через встроенный MCI (без внешних библиотек)."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from typing import List, Optional, Protocol, Tuple

MAX_PLAY_SECONDS = 90.0


class Player(Protocol):
    def play(self, mp3: bytes, volume: int) -> None:
        """Проиграть звук и вернуться, когда он закончился (или был остановлен)."""

    def stop(self) -> None:
        """Прервать текущее воспроизведение (если оно идёт)."""


class PlayerError(RuntimeError):
    pass


class MciPlayer:
    """Проигрыватель Windows MCI: open -> setaudio -> play -> опрос статуса -> close."""

    def __init__(self, mci_send=None, mci_error=None, poll: float = 0.05) -> None:
        if mci_send is None:
            mci_send, mci_error = self._load_winmm()
        self._send = mci_send
        self._error_text = mci_error
        self._poll = poll
        self._lock = threading.Lock()
        self._playing = False
        self._stop_flag = threading.Event()
        self._counter = 0
        self._dir = tempfile.mkdtemp(prefix="ttchat-")

    @staticmethod
    def _load_winmm():
        import ctypes

        winmm = ctypes.windll.winmm  # type: ignore[attr-defined]

        def send(command: str, want_result: bool = False) -> Tuple[int, str]:
            buf = ctypes.create_unicode_buffer(256)
            code = winmm.mciSendStringW(command, buf, 255, 0)
            return code, buf.value

        def error_text(code: int) -> str:
            buf = ctypes.create_unicode_buffer(256)
            winmm.mciGetErrorStringW(code, buf, 255)
            return buf.value

        return send, error_text

    def _command(self, command: str) -> str:
        code, result = self._send(command, True)
        if code != 0:
            detail = self._error_text(code) if self._error_text else f"код {code}"
            raise PlayerError(f"MCI «{command.split()[0]}»: {detail}")
        return result

    def play(self, mp3: bytes, volume: int) -> None:
        with self._lock:
            self._counter += 1
            alias = f"tts{self._counter}"
            self._stop_flag.clear()
            self._playing = True
        path = os.path.join(self._dir, f"{alias}.mp3")
        opened = False
        try:
            with open(path, "wb") as fh:
                fh.write(mp3)
            self._command(f'open "{path}" type mpegvideo alias {alias}')
            opened = True
            level = max(0, min(100, int(volume))) * 10
            try:
                self._command(f"setaudio {alias} volume to {level}")
            except PlayerError:
                pass  # громкость не критична: играем с системной
            self._command(f"play {alias}")
            deadline = time.monotonic() + MAX_PLAY_SECONDS
            while time.monotonic() < deadline:
                if self._stop_flag.is_set():
                    break
                try:
                    mode = self._command(f"status {alias} mode").strip().lower()
                except PlayerError:
                    break
                if mode != "playing":
                    break
                time.sleep(self._poll)
        finally:
            if opened:
                try:
                    self._command(f"stop {alias}")
                except PlayerError:
                    pass
                try:
                    self._command(f"close {alias}")
                except PlayerError:
                    pass
            try:
                os.remove(path)
            except OSError:
                pass
            with self._lock:
                self._playing = False

    def stop(self) -> None:
        with self._lock:
            if self._playing:
                self._stop_flag.set()


class NullPlayer:
    """Для не-Windows систем и тестов: ничего не воспроизводит, но запоминает вызовы."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.played: List[Tuple[int, int]] = []  # (размер данных, громкость)
        self._stop_flag = threading.Event()
        self._lock = threading.Lock()
        self._playing = False

    def play(self, mp3: bytes, volume: int) -> None:
        with self._lock:
            self._playing = True
            self._stop_flag.clear()
        self.played.append((len(mp3), volume))
        end = time.monotonic() + self.delay
        while time.monotonic() < end and not self._stop_flag.is_set():
            time.sleep(0.005)
        with self._lock:
            self._playing = False

    def stop(self) -> None:
        with self._lock:
            if self._playing:
                self._stop_flag.set()


def create_player() -> Player:
    if sys.platform.startswith("win"):
        return MciPlayer()
    return NullPlayer()
