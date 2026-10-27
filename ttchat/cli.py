"""Точка входа: без аргументов открывает окно, с --selftest прогоняет проверки."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import List, Optional

from . import APP_NAME, __version__


def _fix_streams() -> None:
    """В exe без консоли (--windowed) sys.stdout равен None — print упал бы."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def _crash_log(text: str) -> Path:
    from .settings import default_path

    path = default_path().parent / "error.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        pass
    return path


def main(argv: Optional[List[str]] = None) -> int:
    _fix_streams()
    parser = argparse.ArgumentParser(prog="TikTokChatTTS", description=f"{APP_NAME}: озвучка чата TikTok LIVE")
    parser.add_argument("--version", action="store_true", help="показать версию и выйти")
    parser.add_argument("--selftest", action="store_true", help="проверить все модули и синтез речи")
    parser.add_argument("--report", metavar="ФАЙЛ", help="куда записать JSON-отчёт самопроверки")
    parser.add_argument("--samples", metavar="ПАПКА", help="куда сохранить mp3-образцы голосов")
    parser.add_argument("--probe", metavar="НИК", help="в самопроверке сделать пробное подключение к TikTok")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return 0
    if args.selftest:
        from .selftest import run_selftest

        return run_selftest(args.report, args.probe, args.samples)

    try:
        from .gui import run_gui

        return run_gui()
    except Exception:
        details = traceback.format_exc()
        path = _crash_log(details)
        try:
            from tkinter import Tk, messagebox

            root = Tk()
            root.withdraw()
            messagebox.showerror(APP_NAME, f"Приложение не запустилось.\n\n{details[-800:]}\n\nЖурнал: {path}")
        except Exception:
            pass
        return 1
