"""Окно приложения (tkinter): тёмный интерфейс — подключение, статистика эфира, озвучка, лента событий.

Раскладка как на скриншоте: сверху — карточки статистики (зрители, лайки, подарки, подписки),
слева — карточка подключения с ником, статусом и кнопкой «Подключиться/Отключиться», справа —
лента событий, внизу — панель «Что озвучивать» с аккуратными флажками. Все блоки (карточки)
внутри окна со скруглёнными краями, слева никакого меню нет.
"""

from __future__ import annotations

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

# ---- палитра --------------------------------------------------------------

BG = "#0f0f10"
PANEL = "#17181a"
FIELD = "#0b0b0c"
BORDER = "#2a2c30"
BTN = "#26282c"
BTN_HOVER = "#33363b"
FG = "#f1f1f2"
MUTED = "#8b8f94"
ACCENT = "#f43f5e"
ACCENT_HOVER = "#e11d48"
GREEN = "#22c55e"
ORANGE = "#f59e0b"
RED = "#ef4444"
CHECK_BORDER = "#3a3e44"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_H1 = ("Segoe UI", 16, "bold")
FONT_H2 = ("Segoe UI", 12, "bold")
FONT_STAT = ("Segoe UI", 16, "bold")

CARD_RADIUS = 10            # радиус скругления карточек
STAT_PAD = 12               # внутренние поля карточек статистики
CARD_PAD = 14               # внутренние поля обычных карточек

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
    "chat": FG, "skip": "#6f7378", "gift": "#c084fc", "info": "#60a5fa",
    "warn": ORANGE, "error": "#f87171", "speak": "#4ade80", "follow": "#c084fc",
    "join": "#60a5fa",
}
LOG_LIMIT_LINES = 1500
SCREEN_RESERVE = 110  # px высоты экрана под заголовок окна и панель задач

# Сверху оставляем четыре карточки: зрители, лайки, подарки, новые подписки.
# Значения берутся по факту событий (сколько реально подарили/подписались).
STAT_CARDS: Tuple[Tuple[str, str], ...] = (
    ("viewers", "Зрители сейчас"),
    ("likes", "Лайки"),
    ("gifts", "Подарки"),
    ("follows", "Новые подписки"),
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


def rounded_points(x1: float, y1: float, x2: float, y2: float, radius: float) -> list:
    """Точки для create_polygon(smooth=True): прямоугольник со скруглёнными углами."""
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    k = 0.5522847498  # коэффициент Безье для четверти круга
    points: list = []
    for idx, (x, y) in enumerate((
            (x1 + r, y1), (x2 - r, y1), (x2, y1 + r), (x2, y2 - r),
            (x2 - r, y2), (x1 + r, y2), (x1, y2 - r), (x1, y1 + r))):
        if idx % 2 == 0:
            points.extend([x, y, x + r * k, y])
        else:
            points.extend([x - r + r * k, y, x, y + r * k, x])
    return points


class RoundedCanvas(tk.Canvas):
    """Холст, который рисует прямоугольник со скруглёнными краями заданного размера.

    Используется как фон карточки: холст кладётся через place() под содержимым, поэтому
    не влияет на размеры, а просто повторяет размер карточки.
    """

    def __init__(self, parent: tk.Misc, fill: str = PANEL, outline: str = BORDER,
                 radius: int = CARD_RADIUS, **kwargs: Any) -> None:
        super().__init__(parent, bg=BG, highlightthickness=0, bd=0, relief="flat", **kwargs)
        self._fill = fill
        self._outline = outline
        self._radius = radius
        self.bind("<Configure>", lambda e: self.draw())

    def draw(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.delete("shape")
        self.create_polygon(rounded_points(0, 0, w - 1, h - 1, self._radius),
                            smooth=True, fill=self._fill, outline=self._outline,
                            width=1, tags="shape")

    def set_colors(self, fill: str, outline: str) -> None:
        self._fill, self._outline = fill, outline
        self.draw()


class StatusChip:
    """Статус эфира в шапке: скруглённая «пилюля» с точкой и подписью.

    Подпись — обычная tk.Label (доступна как .label), пилюля нарисована на холсте.
    """

    def __init__(self, parent: tk.Misc, text: str, color: str) -> None:
        self.holder = tk.Frame(parent, bg=BG)
        self.canvas = tk.Canvas(self.holder, bg=BG, highlightthickness=0, bd=0,
                                width=160, height=30)
        self.canvas.pack()
        self.color = color
        self.label = tk.Label(self.canvas, text=text, bg=PANEL, fg=color, font=FONT_BOLD,
                              padx=10, pady=3)
        self.label.bind("<Configure>", lambda e: self._resize())
        self._place_label()
        self._resize()

    def _place_label(self) -> None:
        # Держим место слева под цветную точку.
        self.label.place(x=26, rely=0.5, anchor="w")

    def _resize(self) -> None:
        w = self.label.winfo_reqwidth() + 38
        h = self.label.winfo_reqheight() + 10
        if (int(self.canvas.cget("width")), int(self.canvas.cget("height"))) != (w, h):
            self.canvas.configure(width=w, height=h)
        self.draw()

    def draw(self) -> None:
        w = int(self.canvas.cget("width"))
        h = int(self.canvas.cget("height"))
        self.canvas.delete("pill")
        self.canvas.create_polygon(rounded_points(0, 0, w - 1, h - 1, (h - 1) / 2),
                                   smooth=True, fill=PANEL, outline=self.color,
                                   width=1, tags="pill")
        cy = h / 2
        self.canvas.create_oval(10, cy - 4, 18, cy + 4, fill=self.color, outline="",
                                tags="pill")

    def set(self, text: str, color: str) -> None:
        self.color = color
        self.label.configure(text=text, fg=color)
        self._resize()


class Checkbox(tk.Frame):
    """Аккуратный флажок: квадрат с галочкой, без «крестиков» ttk."""

    SIZE = 17

    def __init__(self, parent: tk.Misc, text: str, var: tk.BooleanVar,
                 command: Callable[[], None], **kwargs: Any) -> None:
        super().__init__(parent, bg=PANEL, **kwargs)
        self.var = var
        self.command = command
        self._box = tk.Canvas(self, width=self.SIZE + 2, height=self.SIZE + 2, bg=PANEL,
                              highlightthickness=0, takefocus=0)
        self._box.grid(row=0, column=0, sticky="w", padx=(0, 8))
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
        if self.command:
            self.command()

    def _hover_on(self, _event: tk.Event) -> None:
        self._label.configure(fg="#fff7ed")

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
            box.create_line(4, size * 0.55, size * 0.42, size - 4, size - 3, 4,
                            fill="#ffffff", width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)

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

    # ---- потокобезопасные уведомления ---------------------------------------------

    def _emit(self, level: str, message: str) -> None:
        self.ui_queue.put(("log", level, message))

    def _on_state(self, state: str, message: str) -> None:
        self.ui_queue.put(("state", state, message))

    # ---- стиль ttk ---------------------------------------------------------------

    def _setup_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TCombobox", fieldbackground=FIELD, background=BTN, foreground=FG,
                        arrowcolor=FG, bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD,
                        selectbackground=FIELD, selectforeground=FG, padding=6)
        style.map("Dark.TCombobox",
                  fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
                  selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        style.configure("Dark.Vertical.TScrollbar", background=BTN, troughcolor=FIELD,
                        bordercolor=FIELD, arrowcolor=FG, lightcolor=BTN, darkcolor=BTN)
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    # ---- строительные блоки --------------------------------------------------------

    def _card(self, parent: tk.Misc, title: Optional[str] = None, subtitle: str = "",
              pad: int = CARD_PAD,
              actions: Optional[Tuple[Tuple[str, Callable[[], None]], ...]] = None,
              title_font: tuple = FONT_H2, title_color: str = FG
              ) -> Tuple[tk.Frame, tk.Frame]:
        """Карточка со скруглёнными краями. Возвращает (внешний фрейм, фрейм содержимого)."""
        outer = tk.Frame(parent, bg=BG)
        back = RoundedCanvas(outer, radius=CARD_RADIUS)
        back.place(x=0, y=0, relwidth=1, relheight=1)
        body = tk.Frame(outer, bg=PANEL)
        body.pack(fill="both", expand=True, padx=pad, pady=pad)

        if not title and not actions:
            return outer, body
        head = tk.Frame(body, bg=PANEL)
        head.pack(fill="x", anchor="w")
        head.columnconfigure(0, weight=1)
        titles = tk.Frame(head, bg=PANEL)
        titles.grid(row=0, column=0, sticky="w")
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
        return outer, body

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
        row.pack(fill="x", padx=(0, 4), pady=(6, 0))
        row.columnconfigure(1, weight=1)
        tk.Label(row, text=title, bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        label = tk.Label(row, text=value, bg=PANEL, fg=color, font=FONT_BOLD, anchor="e")
        label.grid(row=0, column=1, sticky="e")
        return label

    # ---- построение интерфейса ------------------------------------------------------

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
        outer.pack(fill="both", expand=True, padx=14, pady=10)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)  # лента событий забирает свободную высоту
        self._outer = outer

        self._build_header(outer)
        self._build_stats(outer)
        self._build_main(outer)
        self._build_voice(outer)
        self._build_filters(outer)
        self._build_footer(outer)

        self.v_user.trace_add("write", lambda *a: self._changed())
        self._fit_window()

    def _fit_window(self) -> None:
        """Размер окна = то, что нужно содержимому (с запасом под ленту), но не больше экрана."""
        root = self.root
        root.update_idletasks()
        need_w, need_h = root.winfo_reqwidth(), root.winfo_reqheight()
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        max_w, max_h = screen_w - 40, screen_h - SCREEN_RESERVE
        width = min(max(need_w, 900), max_w)
        height = min(need_h + 170, max_h)
        root.geometry(f"{width}x{height}+{max(0, (screen_w - width) // 2)}"
                      f"+{max(0, (max_h - height) // 2)}")
        root.minsize(min(need_w, max_w), min(need_h, max_h))

    def _build_header(self, parent: tk.Frame) -> None:
        head = tk.Frame(parent, bg=BG)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.columnconfigure(0, weight=1)
        tk.Label(head, text="Чат TikTok LIVE — озвучка", bg=BG, fg=FG, font=FONT_H1,
                 anchor="w").grid(row=0, column=0, sticky="w")
        self._chip = StatusChip(head, "Не подключено", MUTED)
        self._chip.holder.grid(row=0, column=1, sticky="e")
        self.lbl_state = self._chip.label

    def _build_stats(self, parent: tk.Frame) -> None:
        """Сверху — четыре карточки: зрители, лайки, подарки, новые подписки."""
        row = tk.Frame(parent, bg=BG)
        row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.stat_values: Dict[str, tk.Label] = {}
        for index, (key, title) in enumerate(STAT_CARDS):
            row.columnconfigure(index, weight=1, uniform="stats")
            outer, body = self._card(row, title, pad=STAT_PAD, title_font=FONT_SMALL,
                                     title_color=MUTED)
            outer.grid(row=0, column=index, sticky="nsew",
                       padx=(0 if index == 0 else 4,
                             0 if index == len(STAT_CARDS) - 1 else 4))
            value = tk.Label(body, text="—" if key == "viewers" else "0", bg=PANEL, fg=FG,
                             font=FONT_STAT, anchor="w")
            value.pack(anchor="w", pady=(2, 0))
            self.stat_values[key] = value

    def _build_main(self, parent: tk.Frame) -> None:
        """Слева — страница подключения, справа — лента событий."""
        main = tk.Frame(parent, bg=BG)
        main.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        main.columnconfigure(0, weight=0, minsize=330)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        self._build_connect(main).grid(row=0, column=0, sticky="new", padx=(0, 10))
        self._build_feed(main).grid(row=0, column=1, sticky="nsew", padx=(10, 0))

    def _build_connect(self, parent: tk.Frame) -> tk.Frame:
        """Слева — страница подключения: ник, кнопка, статус и подключённый ник."""
        outer, body = self._card(parent, "Подключение", "Введите ник стримера TikTok")

        tk.Label(body, text="Ник стримера", bg=PANEL, fg=MUTED, font=FONT_SMALL,
                 anchor="w").pack(anchor="w", pady=(8, 3))

        line = tk.Frame(body, bg=FIELD, highlightthickness=1, highlightbackground=BORDER)
        line.pack(fill="x")
        tk.Label(line, text="@", bg=FIELD, fg=MUTED, font=FONT_BOLD).pack(side="left", padx=(10, 0))
        entry = tk.Entry(line, textvariable=self.v_user, bg=FIELD, fg=FG, insertbackground=FG,
                         relief="flat", bd=0, font=FONT, highlightthickness=0)
        entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(6, 8))
        entry.bind("<Return>", lambda e: self.toggle_connect())
        self.entry_user = entry

        self.btn_connect = self._button(body, "Подключиться", self.toggle_connect, primary=True)
        self.btn_connect.pack(fill="x", pady=(8, 0))

        self._nick_label = self._info_row(body, "Подключённый ник", "—", ACCENT)
        self._status_label = self._info_row(body, "Статус подключения", "Не подключено", ORANGE)
        self.stat_values["uptime"] = self._info_row(body, "Время в эфире", "—")
        return outer

    def _build_voice(self, parent: tk.Frame) -> tk.Frame:
        """Аккуратный выбор озвучки: одна строка — голос, проверка и своя фраза."""
        outer, body = self._card(parent, pad=10)
        outer.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        row = tk.Frame(body, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="Озвучка", bg=PANEL, fg=FG, font=FONT_H2).pack(side="left",
                                                                           padx=(4, 14))
        tk.Label(row, text="Голос", bg=PANEL, fg=MUTED, font=FONT_SMALL).pack(side="left",
                                                                               padx=(0, 8))
        self.cmb_voice = ttk.Combobox(row, textvariable=self.v_voice, state="readonly",
                                      style="Dark.TCombobox", font=FONT, width=30,
                                      values=[VOICES[v] for v in self.voice_ids])
        self.cmb_voice.pack(side="left")
        self.cmb_voice.bind("<<ComboboxSelected>>", lambda e: self._changed())
        self._button(row, "▶ Проверить", self.test_voice).pack(side="left", padx=(8, 0))
        self._button(row, "Озвучить свой текст", self.speak_manual).pack(side="right")
        field = tk.Entry(row, textvariable=self.v_manual, bg=FIELD, fg=FG, insertbackground=FG,
                         relief="flat", bd=0, font=FONT, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=BORDER)
        field.pack(side="right", fill="x", expand=True, ipady=5, padx=(16, 8))
        field.bind("<Return>", lambda e: self.speak_manual())
        return outer

    def _build_feed(self, parent: tk.Frame) -> tk.Frame:
        """Справа — лента событий: чат, подарки, подписки, входы зрителей."""
        outer, body = self._card(
            parent, "Лента событий (▶ — что озвучивается сейчас)", pad=12,
            actions=(("⏭ Пропустить", self.engine.skip), ("🗑 Очистить", self.engine.clear)))
        # Внутри карточки заголовок уже упакован через pack, поэтому сетка — в отдельном фрейме.
        area = tk.Frame(body, bg=PANEL)
        area.pack(fill="both", expand=True)
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)

        self.log = tk.Text(area, wrap="word", state="disabled", font=FONT, bg=FIELD, fg=FG,
                           height=6, relief="flat", bd=0, highlightthickness=1,
                           highlightbackground=BORDER, padx=10, pady=8, insertbackground=FG,
                           spacing3=2)
        scroll = ttk.Scrollbar(area, command=self.log.yview, style="Dark.Vertical.TScrollbar")
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for level, color in LOG_COLORS.items():
            self.log.tag_configure(level, foreground=color)

        self._placeholder = tk.Label(area, text="Событий пока нет\n\nЭфир ещё не начался — "
                                                 "подключимся автоматически, как только он пойдёт.",
                                     bg=FIELD, fg=MUTED, font=FONT, justify="center",
                                     wraplength=380)
        self._placeholder.place(relx=0.5, rely=0.5, anchor="center")
        self._placeholder.place_forget()
        return outer

    def _build_filters(self, parent: tk.Frame) -> None:
        """«Что озвучивать» — отрисованные флажки вместо крестиков."""
        outer, body = self._card(parent, "Что озвучивать")
        outer.grid(row=4, column=0, sticky="ew", pady=(0, 4))
        # Заголовок карточки упакован через pack, поэтому флажки-сетка — в отдельном фрейме.
        area = tk.Frame(body, bg=PANEL)
        area.pack(fill="x", pady=(4, 0))
        columns = 4
        for index, (key, (text, _)) in enumerate(self.flags.items()):
            row, column = divmod(index, columns)
            box = Checkbox(area, text, self.flag_vars[key], self._changed)
            box.grid(row=row, column=column, sticky="w", padx=(0, 16), pady=2)
            self._flag_widgets[key] = box
        for column in range(columns):
            area.columnconfigure(column, weight=1, uniform="flags")

    def _build_footer(self, parent: tk.Frame) -> None:
        foot = tk.Frame(parent, bg=BG)
        foot.grid(row=5, column=0, sticky="ew")
        foot.columnconfigure(1, weight=1)
        tk.Label(foot, text=f"{APP_NAME} · версия {__version__}", bg=BG, fg=MUTED,
                 font=FONT_SMALL).grid(row=0, column=0, sticky="w")
        self.btn_check = tk.Button(foot, text="Проверить обновления",
                                   command=lambda: self._start_update_check(manual=True),
                                   bg=BG, fg=MUTED, activebackground=BG, activeforeground=FG,
                                   relief="flat", bd=0, font=FONT_SMALL, cursor="hand2")
        self.btn_check.grid(row=0, column=1, sticky="w", padx=14)
        self.btn_update = self._button(foot, "⬆ Обновить", self.apply_update, primary=True)
        self.btn_update.grid(row=0, column=2, sticky="e", padx=(0, 14))
        self.btn_update.grid_remove()
        self.lbl_queue = tk.Label(foot, text="В очереди: 0", bg=BG, fg=MUTED, font=FONT_SMALL)
        self.lbl_queue.grid(row=0, column=3, sticky="e")

    # ---- настройки ---------------------------------------------------------------

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

    # ---- действия ---------------------------------------------------------------

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

    # ---- обновления ----------------------------------------------------------------

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

    # ---- состояние, лента и статистика ---------------------------------------------

    def _apply_state(self, state: str) -> None:
        """Статус в шапке и в левой колонке (статус подключения + подключённый ник)."""
        text, color = STATE_VIEW.get(state, (state, MUTED))
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

    # ---- очередь событий из рабочих потоков ------------------------------------------

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
