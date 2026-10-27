"""Очистка и фильтрация сообщений чата перед озвучкой."""

from __future__ import annotations

import re
import time
from collections import OrderedDict
from typing import Callable, List, NamedTuple, Optional

from .settings import Settings

_URL_RE = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:com|ru|net|org|io|me|tv|gg|ly|kz|ua|by|su|info|xyz|app|cc|to)\b(?:/\S*)?",
    re.IGNORECASE,
)

_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # смайлы, символы, флаги, пиктограммы
    "\U00002600-\U000027BF"  # разные символы и dingbats
    "\U00002B00-\U00002BFF"
    "\U00002190-\U000021FF"
    "\U00002300-\U000023FF"
    "\U00002900-\U0000297F"
    "\U0000FE00-\U0000FE0F"  # селекторы вариантов
    "\U0000200D\U000020E3"
    "]+"
)

# Эмодзи TikTok в тексте комментария выглядят как [rose], [smile]
_TIKTOK_EMOTE_RE = re.compile(r"\[[A-Za-z0-9_ ]{1,24}\]")
_LETTER_REPEAT_RE = re.compile(r"([^\W\d_])\1{2,}")
_PUNCT_REPEAT_RE = re.compile(r"([!?.,\-])\1{1,}")
_SPACES_RE = re.compile(r"\s+")
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_LONG_DIGITS_RE = re.compile(r"\d{5,}")

DEFAULT_NICK = "Зритель"


def clean_text(text: str) -> str:
    """Убрать ссылки, эмодзи и растянутые буквы; оставить читаемый текст."""
    text = _URL_RE.sub(" ", text)
    text = _TIKTOK_EMOTE_RE.sub(" ", text)
    text = _EMOJI_RE.sub(" ", text)
    text = text.replace("@", " ")
    text = _LETTER_REPEAT_RE.sub(r"\1", text)  # «приветттт» -> «привет» (цифры не трогаем)
    text = _PUNCT_REPEAT_RE.sub(lambda m: m.group(1), text)
    return _SPACES_RE.sub(" ", text).strip()


def clean_nick(nick: str) -> str:
    nick = _URL_RE.sub(" ", nick or "")
    nick = _EMOJI_RE.sub(" ", nick)
    nick = _LONG_DIGITS_RE.sub(" ", nick)
    nick = nick.replace("_", " ").replace(".", " ").replace("@", " ")
    nick = _SPACES_RE.sub(" ", nick).strip()
    return nick if any(ch.isalnum() for ch in nick) else DEFAULT_NICK


def has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC_RE.search(text))


def has_link(text: str) -> bool:
    return bool(_URL_RE.search(text))


def truncate(text: str, limit: int) -> str:
    """Обрезать по границе слова, чтобы не читать огрызки."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;:-")


def normalize(text: str) -> str:
    """Нормальная форма для сравнения: нижний регистр, ё=е, только буквы и цифры."""
    text = text.lower().replace("ё", "е")
    return "".join(ch for ch in text if ch.isalnum())


def parse_blacklist(raw: str) -> List[str]:
    parts = re.split(r"[,;\n]+", raw or "")
    words = (p.strip().lower().replace("ё", "е") for p in parts)
    return [w for w in words if w]


class RecentTracker:
    """Помнит недавние сообщения, чтобы не озвучивать спам-повторы."""

    def __init__(self, window: float = 20.0, capacity: int = 500,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.window = window
        self.capacity = capacity
        self._clock = clock
        self._seen: "OrderedDict[str, float]" = OrderedDict()

    def seen(self, key: str) -> bool:
        """True, если ключ уже встречался в пределах окна. Всегда запоминает ключ."""
        now = self._clock()
        while self._seen and (len(self._seen) > self.capacity
                              or now - next(iter(self._seen.values())) > self.window):
            self._seen.popitem(last=False)
        previous = self._seen.get(key)
        self._seen.pop(key, None)
        self._seen[key] = now
        return previous is not None and now - previous <= self.window


class Prepared(NamedTuple):
    phrase: Optional[str]   # что озвучивать (None — пропустить)
    reason: Optional[str]   # причина пропуска


def prepare_chat(nick: str, text: str, settings: Settings,
                 recent: Optional[RecentTracker] = None,
                 is_follower: Optional[bool] = None) -> Prepared:
    """Применить фильтры и собрать фразу для озвучки комментария.

    is_follower: True — автор подписан на канал, False — нет, None — TikTok не сообщил.
    """
    raw = (text or "").strip()
    if not raw:
        return Prepared(None, "пустое сообщение")
    if settings.skip_commands and raw[0] in "!/":
        return Prepared(None, "команда")
    if settings.skip_links and has_link(raw):
        return Prepared(None, "ссылка")
    if settings.followers_only and is_follower is not True:
        return Prepared(None, "не подписчик" if is_follower is False else "подписчик не определён")

    cleaned = clean_text(raw)
    if not any(ch.isalnum() for ch in cleaned):
        return Prepared(None, "нет текста для чтения")
    if settings.only_russian and not has_cyrillic(cleaned):
        return Prepared(None, "не по-русски")

    lowered = cleaned.lower().replace("ё", "е")
    for word in parse_blacklist(settings.blacklist):
        if word in lowered:
            return Prepared(None, "чёрный список")

    if settings.ignore_repeats and recent is not None and recent.seen(normalize(cleaned)):
        return Prepared(None, "повтор")

    cleaned = truncate(cleaned, settings.max_len)
    if settings.read_nick:
        return Prepared(f"{clean_nick(nick)}: {cleaned}", None)
    return Prepared(cleaned, None)


def gift_phrase(nick: str, gift: str, count: int) -> str:
    gift = clean_text(gift) or "подарок"
    who = clean_nick(nick)
    if count > 1:
        return f"{who}, спасибо за подарки {gift}, {count} штук"
    return f"{who}, спасибо за подарок {gift}"


def follow_phrase(nick: str) -> str:
    return f"{clean_nick(nick)}, спасибо за подписку"
