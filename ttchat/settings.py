"""Настройки приложения: значения по умолчанию, проверка границ, хранение в JSON."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, Dict

DEFAULT_VOICE = "ru-RU-SvetlanaNeural"

PLAYBACK_VOLUME = 80  # громкость воспроизведения, %: регулируется в системе, в окне её нет

# (минимум, максимум) для числовых настроек
LIMITS: Dict[str, tuple] = {
    "max_len": (20, 500),     # максимум символов в одном сообщении
    "queue_limit": (1, 100),  # максимум сообщений в очереди
}


@dataclass(frozen=True)
class Settings:
    username: str = ""
    voice: str = DEFAULT_VOICE
    read_nick: bool = True
    max_len: int = 150
    queue_limit: int = 15
    skip_commands: bool = True
    skip_links: bool = True
    only_russian: bool = False
    ignore_repeats: bool = True
    only_new: bool = True
    read_gifts: bool = True
    read_follows: bool = True
    followers_only: bool = False   # озвучивать только сообщения подписчиков канала
    blacklist: str = ""
    euler_key: str = ""

    def validated(self) -> "Settings":
        """Копия настроек с числами, приведёнными к допустимым границам."""
        changes: Dict[str, Any] = {}
        for name, (low, high) in LIMITS.items():
            value = getattr(self, name)
            changes[name] = max(low, min(high, int(value)))
        return replace(self, **changes)


def default_path() -> Path:
    """Папка настроек: %APPDATA%\\TikTokChatTTS в Windows, ~/.config/... в остальных ОС."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "TikTokChatTTS" / "settings.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "tiktok-chat-tts" / "settings.json"


def _coerce(default: Any, value: Any) -> Any:
    """Привести значение из файла к типу значения по умолчанию или вернуть default."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return int(value)
        return default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return default


def from_dict(data: Any) -> Settings:
    """Собрать Settings из словаря; неизвестные ключи и значения неверного типа игнорируются."""
    base = Settings()
    if not isinstance(data, dict):
        return base
    values = {}
    for field in fields(Settings):
        if field.name in data:
            values[field.name] = _coerce(getattr(base, field.name), data[field.name])
    return replace(base, **values).validated()


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_path()

    def load(self) -> Settings:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return from_dict(json.load(fh))
        except (OSError, ValueError):
            return Settings()

    def save(self, settings: Settings) -> bool:
        """Атомарная запись: сначала во временный файл, затем замена. Возвращает успех."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(asdict(settings.validated()), fh, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return True
        except OSError:
            return False
