"""Самопроверка: прогоняет всё важное «вживую» и пишет отчёт.

Запускается как `TikTokChatTTS.exe --selftest --report report.json` (в том числе из CI),
чтобы убедиться, что собранный exe действительно умеет импортировать библиотеки,
синтезировать русскую речь и обращаться к TikTok.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import __version__
from .tts import SAMPLE_PHRASE, VOICES, Synthesizer, estimate_seconds, looks_like_mp3

REQUIRED_VOICES = ("ru-RU-SvetlanaNeural",)
MIN_AUDIO_BYTES = 4000


class Report:
    def __init__(self) -> None:
        self.steps: List[Dict[str, Any]] = []

    def run(self, name: str, func: Callable[[], str], required: bool = True) -> bool:
        started = time.monotonic()
        try:
            detail, ok = func(), True
        except Exception as ex:
            detail = f"{type(ex).__name__}: {ex}"
            ok = False
            if os.environ.get("TTCHAT_TRACE"):
                detail += "\n" + traceback.format_exc()
        self.steps.append({
            "name": name, "ok": ok, "required": required,
            "seconds": round(time.monotonic() - started, 2), "detail": detail,
        })
        mark = "OK  " if ok else ("FAIL" if required else "warn")
        print(f"[{mark}] {name} ({self.steps[-1]['seconds']} c) — {detail}", flush=True)
        return ok

    @property
    def passed(self) -> bool:
        return all(s["ok"] for s in self.steps if s["required"])

    def to_json(self) -> Dict[str, Any]:
        return {
            "version": __version__,
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "frozen": bool(getattr(sys, "frozen", False)),
            "passed": self.passed,
            "steps": self.steps,
        }


def _check_imports() -> str:
    import importlib

    modules = ["ttchat.settings", "ttchat.textproc", "ttchat.tts", "ttchat.player", "ttchat.engine",
               "ttchat.tiktok", "ttchat.controller", "ttchat.gui", "edge_tts", "TikTokLive",
               "TikTokLive.events", "TikTokLive.client.errors",
               "TikTokLive.client.web.web_settings", "tkinter"]
    for name in modules:
        importlib.import_module(name)
    return f"импортировано модулей: {len(modules)}"


def _check_settings() -> str:
    from .settings import Settings, SettingsStore
    from dataclasses import replace

    with tempfile.TemporaryDirectory() as folder:
        store = SettingsStore(Path(folder) / "s.json")
        original = replace(Settings(), username="тест", rate=10)
        if not store.save(original) or store.load() != original:
            raise RuntimeError("настройки не сохраняются или читаются неверно")
    return "сохранение и чтение настроек работают"


def _check_text() -> str:
    from .settings import Settings
    from .textproc import prepare_chat

    ok = prepare_chat("Маша", "Привет, всем 😍 приветттт!!!", Settings())
    if ok.phrase is None or "😍" in ok.phrase:
        raise RuntimeError(f"неверная очистка: {ok}")
    if prepare_chat("a", "!cmd", Settings()).phrase is not None:
        raise RuntimeError("команда не отфильтрована")
    return f"фраза: «{ok.phrase}»"


def _synth_voice(voice: str, samples_dir: Optional[Path]) -> str:
    synth = Synthesizer()
    try:
        data = synth(SAMPLE_PHRASE, voice, 0, 0)
    finally:
        synth.close()
    if not looks_like_mp3(data):
        raise RuntimeError("ответ не похож на mp3")
    if len(data) < MIN_AUDIO_BYTES:
        raise RuntimeError(f"слишком короткий звук: {len(data)} байт")
    if samples_dir is not None:
        samples_dir.mkdir(parents=True, exist_ok=True)
        (samples_dir / f"{voice}.mp3").write_bytes(data)
    return f"{len(data)} байт, ≈{estimate_seconds(data):.1f} с речи"


def _check_voice_list() -> str:
    import asyncio
    import edge_tts

    voices = asyncio.run(edge_tts.list_voices())
    russian = sorted(v["ShortName"] for v in voices
                     if v["Locale"].startswith("ru-") and v["Gender"] == "Female")
    missing = [v for v in VOICES if v not in {x["ShortName"] for x in voices}]
    if missing:
        raise RuntimeError(f"в каталоге Microsoft нет голосов: {', '.join(missing)}")
    return f"всего голосов: {len(voices)}; русские женские: {', '.join(russian)}"


def _check_playback() -> str:
    from .player import MciPlayer, create_player

    if not sys.platform.startswith("win"):
        return "пропущено: воспроизведение проверяется только в Windows"
    synth = Synthesizer()
    try:
        data = synth("Проверка звука.", REQUIRED_VOICES[0], 0, 0)
    finally:
        synth.close()
    player = create_player()
    if not isinstance(player, MciPlayer):
        raise RuntimeError("не создан MCI-проигрыватель")
    started = time.monotonic()
    player.play(data, 0)  # громкость 0: проверяем только саму цепочку MCI
    return f"MCI отработал за {time.monotonic() - started:.1f} с"


def _check_engine_end_to_end() -> str:
    from .engine import Engine
    from .player import create_player
    from .settings import Settings

    spoken: List[str] = []
    errors: List[str] = []

    def on_event(level: str, message: str) -> None:
        (spoken if level == "speak" else errors if level == "error" else []).append(message)

    synth = Synthesizer()
    engine = Engine(lambda: Settings(volume=0), synth, create_player(), on_event)
    engine.start()
    try:
        engine.enqueue("Это проверка очереди озвучки.")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and not spoken and not errors:
            time.sleep(0.1)
        # дожидаемся конца воспроизведения
        while time.monotonic() < deadline and engine.pending() and not errors:
            time.sleep(0.1)
        time.sleep(0.3)
    finally:
        engine.stop()
        synth.close()
    if errors:
        raise RuntimeError(errors[0])
    if not spoken:
        raise RuntimeError("очередь ничего не озвучила за 45 с")
    return "очередь синтезировала и воспроизвела фразу"


def probe_tiktok(username: str, timeout: float = 75.0) -> str:
    """Одна попытка подключения: достаточно любого осмысленного ответа TikTok/подписи."""
    from .tiktok import TikTokService, Handlers, parse_username

    name = parse_username(username)
    states: List[tuple] = []
    done = threading.Event()
    comments: List[str] = []

    def on_status(state: str, message: str) -> None:
        if state in ("connecting", "stopped"):
            return
        states.append((state, message))
        done.set()

    handlers = Handlers(lambda n, t, ts: comments.append(t), lambda n, g, c: None,
                        lambda n: None, lambda: None)
    service = TikTokService(handlers, on_status)
    service.start(name)
    try:
        done.wait(timeout)
    finally:
        service.stop()
    if not states:
        raise RuntimeError(f"нет ответа за {timeout:.0f} с")
    state, message = states[0]
    if state not in ("connected", "offline"):
        raise RuntimeError(f"{state}: {message}")
    return f"{state}: {message}"


def run_selftest(report_path: Optional[str] = None, probe_user: Optional[str] = None,
                 samples_dir: Optional[str] = None) -> int:
    report = Report()
    samples = Path(samples_dir) if samples_dir else None
    report.run("Импорт библиотек и модулей", _check_imports)
    report.run("Настройки", _check_settings)
    report.run("Фильтры текста", _check_text)
    report.run("Каталог голосов Microsoft", _check_voice_list, required=False)
    for voice in VOICES:
        report.run(f"Синтез: {voice}", lambda v=voice: _synth_voice(v, samples),
                   required=voice in REQUIRED_VOICES)
    playback_ok = report.run("Воспроизведение (MCI)", _check_playback, required=False)
    # Без аудиоустройства (например, на CI-раннере) MCI не открывается — это не ошибка кода,
    # поэтому сквозная проверка очереди обязательна, только если базовое воспроизведение работает.
    report.run("Очередь: синтез → воспроизведение", _check_engine_end_to_end,
               required=sys.platform.startswith("win") and playback_ok)
    if probe_user:
        report.run(f"TikTok: попытка подключения к @{probe_user.lstrip('@')}",
                   lambda: probe_tiktok(probe_user), required=False)
    summary = report.to_json()
    print("ИТОГ:", "ВСЁ В ПОРЯДКЕ" if report.passed else "ЕСТЬ ОШИБКИ", flush=True)
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report.passed else 1
