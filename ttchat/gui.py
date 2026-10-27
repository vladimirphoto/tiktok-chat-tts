"""Окно приложения (tkinter): тёмный интерфейс в стиле современных desktop-утилит.

Раскладка: шапка с названием и статусом эфира, четыре карточки статистики, слева — подключение,
озвучка и статус модулей, справа — лента событий, снизу — панель «Что озвучивать».

Скруглённые карточки рисуются на Canvas по точкам дуг (без smooth=True), поэтому края ровные,
а содержимое карточки вложено так, чтобы не перекрывать скругления.
"""

from __future__ import annotations

import math
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, Optional, Tuple

from . import APP_NAME, GITHUB_REPO, __version__, updater
from .controller import Controller
from .engine import Engine
from .player import create_player
from .settings import DEFAULT_VOICE, Settings, SettingsStore
from .tts import SAMPLE_PHRASE, VOICES, Synthesizer

# ---- палитра ---------------------------------------------------------------

BG = "#0f1113"          # фон окна
PANEL = "#17191c"       # поверхность карточки
FIELD = "#0b0d0f"       # поля ввода и лента
BORDER = "#292d33"      # тонкая обводка карточек и полей
BTN = "#23262b"         # обычная кнопка
BTN_HOVER = "#2e3238"
FG = "#eceef0"          # основной текст
MUTED = "#8a9097"       # второстепенный текст
ACCENT = "#f43f5e"      # акцент (кнопка подключения)
ACCENT_HOVER = "#e11d48"
GREEN = "#3ddc84"
ORANGE = "#f0a13a"
RED = "#ef5350"
CHECK_BORDER = "#3a3f46"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_TINY = ("Segoe UI", 8)
FONT_H1 = ("Segoe UI", 14, "bold")
FONT_H2 = ("Segoe UI", 11, "bold")
FONT_STAT = ("Segoe UI", 14, "bold")

RADIUS = 12             # радиус скругления карточек
CARD_PAD = 13           # внутренние поля обычных карточек
STAT_PAD = 10           # внутренние поля карточек статистики
GAP = 8                 # отступ между карточками
SIDE_GAP = 10           # отступ между колонками

STATE_VIEW: Dict[str, tuple] = {
    "connecting": ("Подключение…", ORANGE),
    "connected": ("Подключено", GREEN),
    "reconnecting": ("Переподключение…", ORANGE),
    "offline": ("Ожидание эфира", ORANGE),
    "rate_limit": ("Лимит сервиса, жду…", ORANGE),
    "blocked": ("Отклонено, повторяю…", ORANGE),
    "other": ("Ошибка, повторяю…", ORANGE),
    "fatal": ("Не удалось подключиться", RED),
    "error": ("Ошибка", RED),
    "stopped": ("Не подключено", MUTED),
}

LOG_COLORS = {
    "chat": FG, "skip": "#71767c", "gift": "#c084fc", "info": "#6aa9f4",
    "warn": ORANGE, "error": "#f4807f", "speak": "#5cdb95", "follow": "#c084fc",
    "join": "#6aa9f4",
}
LOG_LIMIT_LINES = 1500
SCREEN_RESERVE = 80  # px высоты экрана под заголовок окна и панель задач

# Сверху — четыре карточки: зрители, лайки, подарки, новые подписки.
# Значения берутся по факту событий (сколько реально подарили/подписались).
STAT_CARDS: Tuple[Tuple[str, str, str], ...] = (
    ("viewers", "Зрители сейчас", "◉"),
    ("likes", "Лайки", "♥"),
    ("gifts", "Подарки", "◆"),
    ("follows", "Новые подписки", "＋"),
)


def format_count(value: Optional[int]) -> str:
    """12345 -> «12 345»; None -> «—»."""
    if value is None:
        return "—"
    return f"{int(value):,}".replace(",", "\u00a0")


def format_uptime(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def rounded_points(x1: float, y1: float, x2: float, y2: float, radius: float,
                   steps: int = 8) -> list:
    """Точки прямоугольника со скруглёнными углами (точные дуги, без сглаживания)."""
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    if r <= 0:
        return [x1, y1, x2, y1, x2, y2, x1, y2]
    points: list = []
    corners = ((x2 - r, y1 + r, -90.0), (x2 - r, y2 - r, 0.0),
               (x1 + r, y2 - r, 90.0), (x1 + r, y1 + r, 180.0))
    for cx, cy, start in corners:
        for index in range(steps + 1):
            angle = math.radians(start + 90.0 * index / steps)
            points.extend([cx + r * math.cos(angle), cy + r * math.sin(angle)])
    return points


class Card(tk.Frame):
    """Карточка со скруглёнными краями: фон рисуется на холсте, содержимое — во внутреннем фрейме.

    Внутренний фрейм отступает от края больше радиуса, поэтому его прямые углы не выглядывают
    из-под скругления, а обводка видна по всему периметру.
    """

    def __init__(self, parent: tk.Misc, pad: int = CARD_PAD, radius: int = RADIUS,
                 fill: str = PANEL, outline: str = BORDER, **kwargs: Any) -> None:
        super().__init__(parent, bg=BG, **kwargs)
        self._fill = fill
        self._outline = outline
        self._radius = radius
        self._back = tk.Canvas(self, bg=BG, highlightthickness=0, bd=0, takefocus=0)
        self._back.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=pad, pady=pad)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event: Optional[tk.Event] = None) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width < 8 or height < 8:
            return
        self._back.delete("all")
        self._back.create_polygon(
            rounded_points(0.5, 0.5, width - 0.5, height - 0.5, self._radius),
            fill=self._fill, outline=self._outline, width=1)


class StatusChip:
    """Статус эфира в шапке: скруглённая «пилюля» с точкой и подписью."""

    def __init__(self, parent: tk.Misc, text: str, color: str) -> None:
        self.color = color
        self.canvas = tk.Canvas(parent, bg=BG, highlightthickness=0, bd=0, width=180, height=30)
        self.label = tk.Label(self.canvas, text=text, bg=PANEL, fg=color, font=FONT_BOLD)
        self.label.place(x=24, rely=0.5, anchor="w")
        self._resize()

    def _resize(self) -> None:
        self.canvas.update_idletasks()
        width = self.label.winfo_reqwidth() + 36
        height = max(28, self.label.winfo_reqheight() + 10)
        self.canvas.configure(width=width, height=height)
        self.draw()

    def draw(self) -> None:
        width = int(self.canvas.cget("width"))
        height = int(self.canvas.cget("height"))
        self.canvas.delete("pill")
        self.canvas.create_polygon(
            rounded_points(0.5, 0.5, width - 0.5, height - 0.5, (height - 1) / 2),
            fill=PANEL, outline=self.color, width=1, tags="pill")
        cy = height / 2
        self.canvas.create_oval(10, cy - 3.5, 17, cy + 3.5, fill=self.color, outline="",
                                tags="pill")

    def set(self, text: str, color: str) -> None:
        self.color = color
        self.label.configure(text=text, fg=color)
        self._resize()


class Checkbox(tk.Frame):
    """Аккуратный флажок: квадрат с галочкой, без «крестиков» ttk."""

    SIZE = 16

    def __init__(self, parent: tk.Misc, text: str, var: tk.BooleanVar,
                 command: Optional[Callable[[], None]] = None, **kwargs: Any) -> None:
        super().__init__(parent, bg=PANEL, **kwargs)
        self.var = var
        self.command = command
        self._box = tk.Canvas(self, width=self.SIZE + 2, height=self.SIZE + 2, bg=PANEL,
                              highlightthickness=0, takefocus=0)
        self._box.grid(row=0, column=0, sticky="w", padx=(0, 7))
        self._label = tk.Label(self, text=text, bg=PANEL, fg=FG, font=FONT, anchor="w",
                               cursor="hand2")
        self._label.grid(row=0, column=1, sticky="w")
        self.configure(cursor="hand2")
        for widget in (self, self._box, self._label):
            widget.bind("<Button-1>", self._toggle)
            widget.bind("<Enter>", self._hover_on)
            widget.bind("<Leave>", self._hover_off)
        self._draw()

    def _toggle(self, _event: tk.Event) -> None:
        self.var.set(not self.var.get())
        self._draw()
        if self.command is not None:
            self.command()

    def _hover_on(self, _event: tk.Event) -> None:
        self._label.configure(fg="#ffffff")

    def _hover_off(self, _event: tk.Event) -> None:
        self._label.configure(fg=FG)

    def _draw(self) -> None:
        on = bool(self.var.get())
        box = self._box
        box.delete("all")
        size = self.SIZE
        box.create_rectangle(1, 1, size + 1, size + 1,
                             fill=ACCENT if on else FIELD,
                             outline=ACCENT if on else CHECK_BORDER, width=1)
        if on:
            box.create_line(4, size * 0.55, size * 0.42, size - 3, size - 2, 4,
                            fill="#ffffff", width=2)

    def refresh(self) -> None:
        self._draw()


class App:
    def __init__(self, root: tk.Tk, store: Optional[SettingsStore] = None, synth: Any = None,
                 player: Any = None, library: Any = None,
                 update_check: Optional[Callable[[], Any]] = updater.check_for_update) -> None:
        self.root = root
        self.store = store or SettingsStore()
        self.settings: Settings = self.store.load()
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self._save_job: Optional[str] = None
        self._closing = False
        self._busy_disconnect = False
        self._live_since: Optional[float] = None
        self._conn_state = "stopped"
        self._update_check = update_check
        self._update_info: Optional[updater.UpdateInfo] = None
        self._updating = False

        self._synth = synth or Synthesizer()
        self.engine = Engine(lambda: self.settings, self._synth, player or create_player(),
                             self._emit)
        self.controller = Controller(lambda: self.settings, self.engine, self._emit,
                                     self._on_state, library=library)
        self.voice_ids = list(VOICES.keys())
        self._build()
        self.engine.start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._drain)
        if self._update_check is not None:
            self.root.after(1500, lambda: self._start_update_check(manual=False))
        self._log("info", f"{APP_NAME} {__version__}. Введите ник стримера и нажмите «Подключиться».")

    # ---- потокобезопасные уведомления ---------------------------------------

    def _emit(self, level: str, message: str) -> None:
        self.ui_queue.put(("log", level, message))

    def _on_state(self, state: str, message: str) -> None:
        self.ui_queue.put(("state", state, message))

    # ---- стиль ttk ----------------------------------------------------------

    def _setup_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TCombobox", fieldbackground=FIELD, background=BTN, foreground=FG,
                        arrowcolor=MUTED, bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD,
                        selectbackground=FIELD, selectforeground=FG, padding=4)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
                  bordercolor=[("focus", ACCENT)],
                  selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        style.configure("Dark.Vertical.TScrollbar", gripcount=0, arrowsize=11, width=9,
                        background=BTN, darkcolor=BTN, lightcolor=BTN, bordercolor=FIELD,
                        troughcolor=FIELD, arrowcolor=MUTED, relief="flat")
        style.map("Dark.Vertical.TScrollbar",
                  background=[("active", BTN_HOVER), ("pressed", BTN_HOVER)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # ---- строительные блоки -------------------------------------------------

    def _card(self, parent: tk.Misc, title: Optional[str] = None, subtitle: str = "",
              pad: int = CARD_PAD,
              actions: Optional[Tuple[Tuple[str, Callable[[], None]], ...]] = None,
              title_font: tuple = FONT_H2, title_color: str = FG) -> Tuple[Card, tk.Frame]:
        """Карточка со скруглёнными краями. Возвращает (карточка, фрейм содержимого)."""
        card = Card(parent, pad=pad)
        body = card.body
        if not title and not actions:
            return card, body
        head = tk.Frame(body, bg=PANEL)
        head.pack(fill="x")
        head.columnconfigure(0, weight=1)
        titles = tk.Frame(head, bg=PANEL)
        titles.grid(row=0, column=0, sticky="w")
        if title:
            tk.Label(titles, text=title, bg=PANEL, fg=title_color, font=title_font,
                     anchor="w").pack(anchor="w")
        if subtitle:
            tk.Label(titles, text=subtitle, bg=PANEL, fg=MUTED, font=FONT_SMALL,
                     anchor="w").pack(anchor="w")
        if actions:
            buttons = tk.Frame(head, bg=PANEL)
            buttons.grid(row=0, column=1, sticky="e")
            for text, command in actions:
                self._button(buttons, text, command, small=True).pack(side="left", padx=(6, 0))
        return card, body

    def _button(self, parent: tk.Misc, text: str, command: Callable[[], None],
                primary: bool = False, small: bool = False, **kwargs: Any) -> tk.Button:
        bg, hover = (ACCENT, ACCENT_HOVER) if primary else (BTN, BTN_HOVER)
        button = tk.Button(parent, text=text, command=command, bg=bg,
                           fg="#ffffff" if primary else FG,
                           activebackground=hover, activeforeground=("#ffffff" if primary else FG),
                           relief="flat", bd=0, font=FONT_BOLD if primary else FONT,
                           padx=12 if small else 14, pady=3 if small else 7,
                           cursor="hand2", disabledforeground=MUTED, **kwargs)

        def on_enter(_event: Any) -> None:
            if str(button.cget("state")) != "disabled":
                button.configure(bg=hover)

        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", lambda e: button.configure(bg=bg))
        return button

    def _info_row(self, body: tk.Misc, title: str, value: str,
                  color: str = FG) -> tk.Label:
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x", pady=(5, 0))
        row.columnconfigure(1, weight=1)
        tk.Label(row, text=title, bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w").grid(
            row=0, column=0, sticky="w")
        label = tk.Label(row, text=value, bg=PANEL, fg=color, font=FONT_BOLD, anchor="e")
        label.grid(row=0, column=1, sticky="e")
        return label

    def _field(self, parent: tk.Misc, textvariable: tk.StringVar,
               prefix: str = "") -> Tuple[tk.Frame, tk.Entry]:
        """Поле ввода с тонкой обводкой и необязательным префиксом (@)."""
        frame = tk.Frame(parent, bg=FIELD, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=BORDER)
        if prefix:
            tk.Label(frame, text=prefix, bg=FIELD, fg=MUTED, font=FONT).pack(side="left",
                                                                            padx=(10, 0))
        entry = tk.Entry(frame, textvariable=textvariable, bg=FIELD, fg=FG,
                         insertbackground=FG, relief="flat", bd=0, font=FONT,
                         highlightthickness=0, disabledbackground=FIELD,
                         disabledforeground=MUTED)
        entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(8 if prefix else 10, 8))
        return frame, entry

    # ---- построение интерфейса ----------------------------------------------

    def _build(self) -> None:
        s = self.settings
        root = self.root
        root.title(f"{APP_NAME} {__version__}")
        root.configure(bg=BG)
        self._setup_style()

        self.v_user = tk.StringVar(value=s.username)
        voice = s.voice if s.voice in VOICES else DEFAULT_VOICE
        self.v_voice = tk.StringVar(value=VOICES[voice])
        self.v_manual = tk.StringVar()
        self.flags = {
            "read_nick": ("Читать ник перед сообщением", s.read_nick),
            "skip_commands": ("Пропускать команды (! и /)", s.skip_commands),
            "skip_links": ("Пропускать ссылки", s.skip_links),
            "only_russian": ("Только русские сообщения", s.only_russian),
            "ignore_repeats": ("Не читать повторы и спам", s.ignore_repeats),
            "only_new": ("Не читать старые при входе", s.only_new),
            "read_gifts": ("Озвучивать подарки", s.read_gifts),
            "read_follows": ("Озвучивать подписки", s.read_follows),
        }
        self.flag_vars = {k: tk.BooleanVar(value=v[1]) for k, v in self.flags.items()}
        self._flag_widgets: Dict[str, Checkbox] = {}

        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="both", expand=True, padx=16, pady=8)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)  # лента событий забирает свободную высоту
        self._outer = outer

        self._build_header(outer)
        self._build_stats(outer)
        self._build_main(outer)
        self._build_filters(outer)
        self._build_footer(outer)

        self.v_user.trace_add("write", lambda *a: self._changed())
        self._fit_window()

    def _fit_window(self) -> None:
        """Размер окна по содержимому, но не больше экрана."""
        root = self.root
        root.update_idletasks()
        need_w, need_h = root.winfo_reqwidth(), root.winfo_reqheight()
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        max_w, max_h = screen_w - 40, screen_h - SCREEN_RESERVE
        width = min(max(need_w, 1000), max_w)
        height = min(max(need_h, 620), max_h)
        left = max(0, (screen_w - width) // 2)
        top = max(0, (screen_h - height) // 3)
        root.geometry(f"{width}x{height}+{left}+{top}")
        root.minsize(min(need_w, max_w), min(need_h, max_h))

    def _build_header(self, parent: tk.Frame) -> None:
        head = tk.Frame(parent, bg=BG)
        head.grid(row=0, column=0, sticky="ew", pady=(0, GAP))
        head.columnconfigure(0, weight=1)
        titles = tk.Frame(head, bg=BG)
        titles.grid(row=0, column=0, sticky="w")
        tk.Label(titles, text="Чат TikTok LIVE — озвучка", bg=BG, fg=FG, font=FONT_H1,
                 anchor="w").pack(anchor="w")
        tk.Label(titles, text="Лайв-комната TikTok, очередь сообщений и озвучка",
                 bg=BG, fg=MUTED, font=FONT_SMALL, anchor="w").pack(anchor="w")
        self._chip = StatusChip(head, "Не подключено", MUTED)
        self._chip.canvas.grid(row=0, column=1, sticky="e")
        self.lbl_state = self._chip.label

    def _build_stats(self, parent: tk.Frame) -> None:
        """Сверху — четыре карточки: зрители, лайки, подарки, новые подписки."""
        row = tk.Frame(parent, bg=BG)
        row.grid(row=1, column=0, sticky="ew", pady=(0, GAP))
        self.stat_values: Dict[str, tk.Label] = {}
        for index, (key, title, glyph) in enumerate(STAT_CARDS):
            row.columnconfigure(index, weight=1, uniform="stats")
            card = Card(row, pad=STAT_PAD)
            card.grid(row=0, column=index, sticky="nsew",
                      padx=(0 if index == 0 else GAP // 2,
                            0 if index == len(STAT_CARDS) - 1 else GAP // 2))
            head = tk.Frame(card.body, bg=PANEL)
            head.pack(fill="x")
            head.columnconfigure(0, weight=1)
            tk.Label(head, text=title, bg=PANEL, fg=MUTED, font=FONT_SMALL,
                     anchor="w").grid(row=0, column=0, sticky="w")
            tk.Label(head, text=glyph, bg=PANEL, fg=MUTED, font=FONT_SMALL,
                     anchor="e").grid(row=0, column=1, sticky="e")
            value = tk.Label(card.body, text="—" if key == "viewers" else "0", bg=PANEL, fg=FG,
                             font=FONT_STAT, anchor="w")
            value.pack(anchor="w", pady=(2, 0))
            self.stat_values[key] = value

    def _build_main(self, parent: tk.Frame) -> None:
        """Слева — подключение, озвучка и статус модулей, справа — лента событий."""
        main = tk.Frame(parent, bg=BG)
        main.grid(row=2, column=0, sticky="nsew", pady=(0, GAP))
        main.columnconfigure(0, weight=0, minsize=376)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = tk.Frame(main, bg=BG)
        left.grid(row=0, column=0, sticky="new", padx=(0, SIDE_GAP))
        left.columnconfigure(0, weight=1)
        self._build_connect(left).grid(row=0, column=0, sticky="ew")
        self._build_voice(left).grid(row=1, column=0, sticky="ew", pady=(GAP, 0))

        self._build_feed(main).grid(row=0, column=1, sticky="nsew")

    def _build_connect(self, parent: tk.Misc) -> Card:
        """Подключение: ник, кнопка, подключённый ник и статус."""
        card, body = self._card(parent, "Подключение")
        tk.Label(body, text="Ник стримера", bg=PANEL, fg=MUTED, font=FONT_SMALL,
                 anchor="w").pack(anchor="w", pady=(6, 3))
        frame, entry = self._field(body, self.v_user, prefix="@")
        frame.pack(fill="x")
        entry.bind("<Return>", lambda e: self.toggle_connect())
        self.entry_user = entry

        self.btn_connect = self._button(body, "Подключиться", self.toggle_connect, primary=True)
        self.btn_connect.pack(fill="x", pady=(6, 2))

        self._nick_label = self._info_row(body, "Подключённый ник", "—", ACCENT)
        self._status_label = self._info_row(body, "Статус подключения", "Не подключено", ORANGE)
        return card

    def _build_voice(self, parent: tk.Misc) -> Card:
        """Озвучка: выбор голоса, проверка и своя фраза."""
        card, body = self._card(parent, "Озвучка")
        picker = tk.Frame(body, bg=PANEL)
        picker.pack(fill="x", pady=(6, 0))
        picker.columnconfigure(0, weight=1)
        self.cmb_voice = ttk.Combobox(picker, textvariable=self.v_voice, state="readonly",
                                      style="Dark.TCombobox", font=FONT, width=1,
                                      values=[VOICES[v] for v in self.voice_ids])
        self.cmb_voice.grid(row=0, column=0, sticky="ew")
        self.cmb_voice.bind("<<ComboboxSelected>>", lambda e: self._changed())
        self._button(picker, "▶ Проверить", self.test_voice).grid(row=0, column=1, padx=(6, 0))

        manual = tk.Frame(body, bg=PANEL)
        manual.pack(fill="x", pady=(5, 0))
        manual.columnconfigure(0, weight=1)
        frame, field = self._field(manual, self.v_manual)
        frame.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        field.bind("<Return>", lambda e: self.speak_manual())
        self._button(manual, "Озвучить текст", self.speak_manual).grid(row=0, column=1)
        return card

    def _build_feed(self, parent: tk.Misc) -> Card:
        """Справа — лента событий: чат, подарки, подписки, входы зрителей."""
        card, body = self._card(
            parent, "Лента событий", "▶ — что озвучивается сейчас",
            actions=(("⏭ Пропустить", self.engine.skip), ("✕ Очистить", self.engine.clear)))
        area = tk.Frame(body, bg=PANEL)
        area.pack(fill="both", expand=True, pady=(10, 0))
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)

        self.log = tk.Text(area, wrap="word", state="disabled", font=FONT, bg=FIELD, fg=FG,
                           height=8, width=20, relief="flat", bd=0, highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=BORDER, padx=12, pady=10,
                           insertbackground=FG, spacing1=1, spacing3=3)
        scroll = ttk.Scrollbar(area, command=self.log.yview, style="Dark.Vertical.TScrollbar")
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        for level, color in LOG_COLORS.items():
            self.log.tag_configure(level, foreground=color)

        # Пустое состояние поверх ленты — показывается, только когда событий нет.
        self._placeholder = tk.Frame(area, bg=FIELD)
        icon = tk.Canvas(self._placeholder, width=52, height=52, bg=FIELD, highlightthickness=0)
        icon.pack()
        icon.create_oval(2, 2, 50, 50, fill="#1b1e22", outline=BORDER)
        icon.create_text(26, 26, text="✉", fill=MUTED, font=("Segoe UI", 16))
        tk.Label(self._placeholder, text="Событий пока нет", bg=FIELD, fg=FG,
                 font=FONT_BOLD).pack(pady=(10, 4))
        tk.Label(self._placeholder, text="Эфир ещё не начался — подключимся автоматически,\n"
                                         "как только он пойдёт.",
                 bg=FIELD, fg=MUTED, font=FONT_SMALL, justify="center").pack()
        return card

    def _build_filters(self, parent: tk.Frame) -> None:
        """«Что озвучивать» — отрисованные флажки вместо крестиков."""
        card, body = self._card(parent, "Что озвучивать")
        card.grid(row=3, column=0, sticky="ew", pady=(0, GAP - 2))
        area = tk.Frame(body, bg=PANEL)
        area.pack(fill="x", pady=(6, 0))
        columns = 4
        for index, (key, (text, _)) in enumerate(self.flags.items()):
            row, column = divmod(index, columns)
            box = Checkbox(area, text, self.flag_vars[key], self._changed)
            box.grid(row=row, column=column, sticky="w", pady=1)
            self._flag_widgets[key] = box
        for column in range(columns):
            area.columnconfigure(column, weight=1, uniform="flags")

    def _build_footer(self, parent: tk.Frame) -> None:
        foot = tk.Frame(parent, bg=BG)
        foot.grid(row=4, column=0, sticky="ew")
        foot.columnconfigure(1, weight=1)
        tk.Label(foot, text=f"{APP_NAME} · версия {__version__}", bg=BG, fg=MUTED,
                 font=FONT_SMALL).grid(row=0, column=0, sticky="w")
        live = tk.Frame(foot, bg=BG)
        live.grid(row=0, column=2, sticky="e", padx=(0, 16))
        tk.Label(live, text="В эфире", bg=BG, fg=MUTED, font=FONT_SMALL).pack(side="left",
                                                                           padx=(0, 5))
        self.stat_values["uptime"] = tk.Label(live, text="—", bg=BG, fg=FG, font=FONT_SMALL)
        self.stat_values["uptime"].pack(side="left")
        self.btn_check = tk.Button(foot, text="Проверить обновления",
                                   command=lambda: self._start_update_check(manual=True),
                                   bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG,
                                   relief="flat", bd=0, font=FONT_SMALL, cursor="hand2")
        self.btn_check.grid(row=0, column=3, sticky="e", padx=(0, 16))
        self.btn_update = self._button(foot, "⬆ Обновить", self.apply_update, primary=True)
        self.btn_update.grid(row=0, column=4, sticky="e", padx=(0, 16))
        self.btn_update.grid_remove()
        self.lbl_queue = tk.Label(foot, text="В очереди: 0", bg=BG, fg=MUTED, font=FONT_SMALL)
        self.lbl_queue.grid(row=0, column=5, sticky="e")

    # ---- настройки -----------------------------------------------------------

    def _collect(self) -> Settings:
        label = self.v_voice.get()
        voice = next((vid for vid, text in VOICES.items() if text == label), self.settings.voice)
        current = self.settings
        new = replace(
            current,
            username=self.v_user.get().strip(),
            voice=voice,
            **{key: bool(var.get()) for key, var in self.flag_vars.items()},
        ).validated()
        self.settings = new
        return new

    def _changed(self) -> None:
        self._collect()
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(600, self._save)

    def _save(self) -> None:
        self._save_job = None
        self.store.save(self.settings)

    # ---- действия ------------------------------------------------------------

    def toggle_connect(self) -> None:
        self._collect()
        if self.controller.running:
            self._disconnect_async()
            return
        if self.controller.connect():
            self.btn_connect.configure(text="Отключиться")
            self._apply_state("connecting")

    def _disconnect_async(self) -> None:
        if self._busy_disconnect:
            return
        self._busy_disconnect = True
        self.btn_connect.configure(state="disabled")

        def work() -> None:
            self.controller.disconnect()
            self.ui_queue.put(("disconnected",))

        threading.Thread(target=work, daemon=True).start()

    def test_voice(self) -> None:
        self._collect()
        self.engine.clear()
        self.controller.speak(SAMPLE_PHRASE)

    def speak_manual(self) -> None:
        text = self.v_manual.get().strip()
        if text:
            self._collect()
            self.controller.speak(text)
            self.v_manual.set("")

    # ---- обновления -----------------------------------------------------------

    def _start_update_check(self, manual: bool) -> None:
        check = self._update_check
        if check is None or self._updating or self._closing:
            return
        if manual:
            self._log("info", "Проверяю обновления…")

        def work() -> None:
            try:
                result: Any = check()
            except Exception as ex:  # сеть, лимиты GitHub и т. п.
                result = ex
            self.ui_queue.put(("update", result, manual))

        threading.Thread(target=work, daemon=True).start()

    def _on_update_result(self, result: Any, manual: bool) -> None:
        if isinstance(result, Exception):
            if manual:
                self._log("warn", str(result))
            return
        if result is None:
            if manual:
                self._log("info", f"Установлена последняя версия ({__version__}).")
            return
        self._update_info = result
        self.btn_update.configure(text=f"⬆ Обновить до {result.version}", state="normal")
        self.btn_update.grid()
        self._log("info", f"Доступна новая версия {result.version}. Нажмите «Обновить» внизу окна.")

    def apply_update(self) -> None:
        info = self._update_info
        if info is None or self._updating:
            return
        if not updater.can_self_update():
            messagebox.showinfo(
                APP_NAME,
                "Автообновление работает только в собранной программе (exe) для Windows.\n\n"
                f"Скачать новую версию вручную: github.com/{GITHUB_REPO}/releases")
            return
        if not messagebox.askyesno(APP_NAME, f"Скачать версию {info.version} и перезапустить программу?"):
            return
        self._updating = True
        self.btn_update.configure(state="disabled", text="Скачиваю… 0%")
        target = Path(sys.executable)

        def work() -> None:
            try:
                path = updater.download_update(info, target,
                                               lambda p: self.ui_queue.put(("progress", p)))
                self.ui_queue.put(("update_ready", path, target))
            except Exception as ex:
                self.ui_queue.put(("update_failed", str(ex)))

        threading.Thread(target=work, daemon=True).start()

    def _finish_update(self, path: Path, target: Path) -> bool:
        """Запустить замену файла и закрыть окно. Возвращает True, если окно закрыто."""
        try:
            updater.launch_replacer(path, target)
        except Exception as ex:
            self._updating = False
            self.btn_update.configure(state="normal", text="⬆ Повторить обновление")
            self._log("error", f"Не удалось запустить обновление: {ex}")
            return False
        self._log("info", "Обновление скачано. Перезапускаюсь…")
        self.close()
        return True

    # ---- состояние, лента и статистика ---------------------------------------

    def _apply_state(self, state: str) -> None:
        """Статус в шапке, в левой колонке и в блоке «Статус модулей»."""
        text, color = STATE_VIEW.get(state, (state, MUTED))
        self._conn_state = state
        self._chip.set(text, color)
        nick = self.v_user.get().strip()
        connected = state in ("connected", "reconnecting", "offline")
        self._nick_label.configure(text=f"@{nick}" if (connected and nick) else "—")
        self._status_label.configure(text=text, fg=color)
        if state == "connected":
            if self._live_since is None:
                self._live_since = time.monotonic()
        else:
            self._live_since = None

    def _log(self, level: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        prefix = "▶ " if level == "speak" else ""
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stamp}] {prefix}{message}\n", level)
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > LOG_LIMIT_LINES:
            self.log.delete("1.0", f"{lines - LOG_LIMIT_LINES}.0")
        self.log.configure(state="disabled")
        self.log.see("end")
        self._update_placeholder()

    def _update_placeholder(self) -> None:
        """Подсказка «Событий пока нет» видна только в пустой ленте."""
        if self.log.get("1.0", "end-1c").strip():
            self._placeholder.place_forget()
        else:
            self._placeholder.place(relx=0.5, rely=0.5, anchor="center")

    def _refresh_stats(self) -> None:
        stats = self.controller.stats
        self.stat_values["viewers"].configure(text=format_count(stats.viewers))
        self.stat_values["likes"].configure(text=format_count(stats.likes))
        self.stat_values["gifts"].configure(text=format_count(stats.gifts))
        self.stat_values["follows"].configure(text=format_count(stats.follows))
        uptime = "—" if self._live_since is None else format_uptime(time.monotonic() - self._live_since)
        self.stat_values["uptime"].configure(text=uptime)

    # ---- очередь событий из рабочих потоков -----------------------------------

    def _drain(self) -> None:
        if self._closing:
            return
        try:
            for _ in range(200):
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._log(item[1], item[2])
                elif kind == "state":
                    self._apply_state(item[1])
                    if item[1] == "stopped":
                        self.btn_connect.configure(state="normal", text="Подключиться")
                elif kind == "disconnected":
                    self._busy_disconnect = False
                    self.btn_connect.configure(state="normal", text="Подключиться")
                    self._apply_state("stopped")
                elif kind == "update":
                    self._on_update_result(item[1], item[2])
                elif kind == "progress":
                    self.btn_update.configure(text=f"Скачиваю… {item[1]}%")
                elif kind == "update_failed":
                    self._updating = False
                    self.btn_update.configure(state="normal", text="⬆ Повторить обновление")
                    self._log("error", f"Обновление не удалось: {item[1]}")
                elif kind == "update_ready":
                    if self._finish_update(item[1], item[2]):
                        return
        except queue.Empty:
            pass
        self._refresh_stats()
        self.lbl_queue.configure(text=f"В очереди: {self.engine.pending()}")
        self.root.after(100, self._drain)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._collect()
            self.store.save(self.settings)
        finally:
            self.root.withdraw()
            try:
                self.controller.shutdown()
                getattr(self._synth, "close", lambda: None)()
            finally:
                self.root.destroy()


def _enable_dpi_awareness() -> None:
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # type: ignore[attr-defined]
        except Exception:
            pass


def run_gui() -> int:
    _enable_dpi_awareness()
    root = tk.Tk()
    app = App(root)

    def on_error(exc, val, tb):  # ошибки в обработчиках Tk не должны молча убивать окно
        import traceback
        app._log("error", "Внутренняя ошибка: "
                           + "".join(traceback.format_exception_only(exc, val)).strip())

    root.report_callback_exception = on_error
    root.mainloop()
    return 0
