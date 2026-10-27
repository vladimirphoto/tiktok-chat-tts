"""Синтез речи через бесплатный Microsoft Edge TTS (нейросетевые голоса, без ключей)."""

from __future__ import annotations

import asyncio
from typing import Dict

# Только женские голоса. Первые два — родные русские, остальные — мультиязычные
# нейроголоса Microsoft, которые тоже читают по-русски (качество слегка отличается).
VOICES: Dict[str, str] = {
    "ru-RU-SvetlanaNeural": "Светлана — мягкий и тёплый (рекомендуется)",
    "ru-RU-DariyaNeural": "Дарья — молодой и звонкий",
    "en-US-AvaMultilingualNeural": "Ава — мультиязычный, читает по-русски",
    "en-US-EmmaMultilingualNeural": "Эмма — мультиязычный, читает по-русски",
    "fr-FR-VivienneMultilingualNeural": "Вивьен — мультиязычный, читает по-русски",
    "de-DE-SeraphinaMultilingualNeural": "Серафина — мультиязычный, читает по-русски",
}

SAMPLE_PHRASE = "Привет! Я буду читать ваш чат вслух. Как вам мой голос?"

REQUEST_TIMEOUT = 25.0
ATTEMPTS = 3


class TTSError(RuntimeError):
    """Не удалось получить звук от сервиса синтеза."""


def format_rate(percent: int) -> str:
    return f"{int(percent):+d}%"


def format_pitch(hertz: int) -> str:
    return f"{int(hertz):+d}Hz"


def looks_like_mp3(data: bytes) -> bool:
    """Проверка заголовка: ID3-тег или синхрослово MPEG-кадра (11 единичных бит)."""
    if len(data) < 4:
        return False
    if data[:3] == b"ID3":
        return True
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def estimate_seconds(data: bytes, bitrate_bps: int = 48_000) -> float:
    """Длительность для CBR-потока (Edge отдаёт audio-24khz-48kbitrate-mono-mp3)."""
    return len(data) * 8 / bitrate_bps


async def synth_async(text: str, voice: str, rate: int = 0, pitch: int = 0) -> bytes:
    import edge_tts  # импорт внутри функции: модуль не нужен для тестов логики

    communicate = edge_tts.Communicate(
        text, voice, rate=format_rate(rate), pitch=format_pitch(pitch)
    )
    audio = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            audio += chunk["data"]
    if not audio:
        raise TTSError("сервис вернул пустой звук")
    return bytes(audio)


class Synthesizer:
    """Синхронная обёртка для рабочего потока: один event loop на весь поток."""

    def __init__(self, attempts: int = ATTEMPTS, timeout: float = REQUEST_TIMEOUT,
                 backend=synth_async) -> None:
        self.attempts = attempts
        self.timeout = timeout
        self._backend = backend
        self._loop: asyncio.AbstractEventLoop | None = None

    def __call__(self, text: str, voice: str, rate: int = 0, pitch: int = 0) -> bytes:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        last: Exception | None = None
        for attempt in range(self.attempts):
            try:
                return self._loop.run_until_complete(
                    asyncio.wait_for(self._backend(text, voice, rate, pitch), self.timeout)
                )
            except Exception as ex:  # сеть, таймаут, пустой ответ — пробуем ещё раз
                last = ex
                if attempt + 1 < self.attempts:
                    self._loop.run_until_complete(asyncio.sleep(0.6 * (attempt + 1)))
        raise TTSError(f"{type(last).__name__}: {last}") from last

    def close(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
