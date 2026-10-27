"""Временный прогонщик для локальной проверки нового окна; заменяет ttchat.new_gui.run_gui
на безопасный вариант, не требующий tkinter в этой машине."""

from __future__ import annotations

import sys
import traceback
from typing import List, Optional

from ttchat import APP_NAME

# Прячем настоящий tkinter, если его нет, чтобы старый код не падал при импорте.
try:
    import tkinter as _tk  # noqa: F401
except Exception:  # pragma: no cover
    class _FakeTkinter:
        """Минимальный фиктив tkinter, чтобы импорт модулей не падал без дисплея."""

        def __getattr__(self, name):
            raise ModuleNotFoundError("tkinter недоступен на этой машине")

        def __bool__(self):
            return False

    sys.modules["tkinter"] = _FakeTkinter()

try:
    from ttchat.new_gui import App, _enable_dpi_awareness
except Exception:
    APP = None


def run_demo(argv: Optional[List[str]] = None) -> int:
    if APP is None:
        print(f"{APP_NAME}: Tkinter недоступен — окно не запустить локально.")
        return 2
    try:
        _enable_dpi_awareness()
        import tkinter as tk
        root = tk.Tk()
        app = App(root)

        def on_error(exc, val, tb):
            app._log(
                "error",
                "Внутренняя ошибка: " + "".join(traceback.format_exception_only(exc, val)).strip(),
            )

        root.report_callback_exception = on_error
        root.mainloop()
        return 0
    except Exception:
        print(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(run_demo())
