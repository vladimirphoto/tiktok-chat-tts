"""Обновление из GitHub Releases: проверка новой версии, скачивание exe, замена при перезапуске.

Репозиторий публичный, поэтому токены не нужны. Скачанный файл проверяется по sha256.txt из релиза
(если он там есть) и по заголовку «MZ» (признак exe). Сетевые функции принимают подменяемые
fetch/opener, чтобы тесты работали без интернета.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.request import Request, urlopen

from . import GITHUB_REPO, __version__

API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
EXE_NAME = "TikTokChatTTS.exe"
SHA_NAME = "sha256.txt"
TIMEOUT = 20
CHUNK = 64 * 1024
USER_AGENT = "TikTokChatTTS-updater"

Progress = Callable[[int], None]  # проценты 0..100


class UpdateError(RuntimeError):
    """Обновление не удалось (сеть, контрольная сумма, права на запись)."""


@dataclass(frozen=True)
class UpdateInfo:
    version: str          # «1.0.12»
    tag: str              # «v1.0.12»
    url: str              # ссылка на exe
    size: int = 0
    sha_url: str = ""     # ссылка на sha256.txt (может не быть)
    notes: str = ""


def parse_version(text: str) -> Tuple[int, ...]:
    """«v1.0.12» -> (1, 0, 12); без цифр -> ()."""
    return tuple(int(n) for n in re.findall(r"\d+", text or ""))


def is_newer(candidate: str, current: str) -> bool:
    new, old = parse_version(candidate), parse_version(current)
    return bool(new) and new > old


def _http_json(url: str) -> Dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - только https к api.github.com
        return json.loads(response.read().decode("utf-8"))


def check_for_update(current: str = __version__,
                     fetch: Callable[[str], Dict[str, Any]] = _http_json) -> Optional[UpdateInfo]:
    """Вернуть описание новой версии или None, если обновлений нет. Ошибки сети — UpdateError."""
    try:
        data = fetch(API_URL)
    except Exception as ex:
        raise UpdateError(f"не удалось проверить обновления: {type(ex).__name__}: {ex}") from ex
    tag = str(data.get("tag_name") or "")
    if not is_newer(tag, current):
        return None
    assets = [a for a in data.get("assets") or [] if isinstance(a, dict)]
    exe = next((a for a in assets if a.get("name") == EXE_NAME), None)
    if exe is None:
        exe = next((a for a in assets if str(a.get("name", "")).lower().endswith(".exe")), None)
    if exe is None or not exe.get("browser_download_url"):
        raise UpdateError(f"в релизе {tag} нет файла {EXE_NAME}")
    sha = next((a for a in assets if a.get("name") == SHA_NAME), None)
    return UpdateInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        url=str(exe["browser_download_url"]),
        size=int(exe.get("size") or 0),
        sha_url=str((sha or {}).get("browser_download_url") or ""),
        notes=str(data.get("body") or ""),
    )


def can_self_update() -> bool:
    """Заменять файл можно только у собранного exe в Windows."""
    return sys.platform.startswith("win") and bool(getattr(sys, "frozen", False))


def expected_hash(text: str) -> Optional[str]:
    """Достать sha256 из строки вида «<64 hex>  TikTokChatTTS.exe» (формат sha256sum)."""
    for line in text.splitlines():
        match = re.search(r"\b([0-9a-fA-F]{64})\b", line)
        if match and (EXE_NAME.lower() in line.lower() or len(text.strip().splitlines()) == 1):
            return match.group(1).lower()
    return None


def download_update(info: UpdateInfo, target: Path, progress: Optional[Progress] = None,
                    opener: Callable[..., Any] = urlopen) -> Path:
    """Скачать новый exe рядом с target (файл «<target>.new»), проверить и вернуть его путь."""
    target = Path(target)
    new_path = target.with_name(target.name + ".new")
    digest = hashlib.sha256()
    total = info.size
    done = 0
    try:
        request = Request(info.url, headers={"User-Agent": USER_AGENT})
        with opener(request, timeout=TIMEOUT) as response, open(new_path, "wb") as out:
            if not total:
                try:
                    total = int(response.headers.get("Content-Length") or 0)
                except (AttributeError, TypeError, ValueError):
                    total = 0
            first = True
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                if first:
                    first = False
                    if chunk[:2] != b"MZ":
                        raise UpdateError("скачанный файл не похож на exe")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(99, done * 100 // total))
        if done == 0:
            raise UpdateError("скачан пустой файл")
        if total and done != total:
            raise UpdateError(f"файл скачан не полностью ({done} из {total} байт)")
        if info.sha_url:
            try:
                with opener(Request(info.sha_url, headers={"User-Agent": USER_AGENT}), timeout=TIMEOUT) as resp:
                    wanted = expected_hash(resp.read().decode("utf-8", "replace"))
            except Exception as ex:
                raise UpdateError(f"не удалось получить контрольную сумму: {ex}") from ex
            if wanted and wanted != digest.hexdigest():
                raise UpdateError("контрольная сумма не совпала, файл повреждён")
        if progress:
            progress(100)
        return new_path
    except UpdateError:
        _remove(new_path)
        raise
    except Exception as ex:
        _remove(new_path)
        raise UpdateError(f"ошибка скачивания: {type(ex).__name__}: {ex}") from ex


def _remove(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def build_replace_script(new_path: Path, target: Path) -> str:
    """Текст .bat: ждёт выхода приложения, подменяет exe и запускает новую версию."""
    return "\r\n".join([
        "@echo off",
        "chcp 65001 >nul",
        "set PYINSTALLER_RESET_ENVIRONMENT=1",
        "set /a tries=0",
        ":retry",
        "ping -n 2 127.0.0.1 >nul",
        f'move /y "{new_path}" "{target}" >nul 2>&1',
        "if not errorlevel 1 goto done",
        "set /a tries+=1",
        "if %tries% lss 60 goto retry",
        "exit /b 1",
        ":done",
        f'start "" "{target}"',
        '(goto) 2>nul & del "%~f0"',
        "",
    ])


def launch_replacer(new_path: Path, target: Optional[Path] = None) -> Path:
    """Запустить фоновый .bat-скрипт замены. После этого приложению нужно сразу закрыться."""
    target = Path(target or sys.executable)
    script = Path(tempfile.gettempdir()) / "ttchat-update.bat"
    script.write_text(build_replace_script(Path(new_path), target), encoding="utf-8", newline="")
    flags = 0
    if sys.platform.startswith("win"):
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return script
