"""Окно приложения (tkinter): тёмный интерфейс — подключение, статистика эфира, озвучка, лента событий."""

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

# ---- палитра (тёмная тема) --------------------------------------------------------------
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

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_H1 = ("Segoe UI", 16, "bold")
FONT_H2 = ("Segoe UI", 12, "bold")
FONT_STAT = ("Segoe UI", 16, "bold")

# состояние -> (текст, цвет)
STATE_VIEW: Dict[str, tuple] = {
    "connecting": ("Подключение…", ORANGE),
    "connected": ("● Подключено", GREEN),
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
    "warn": ORANGE, "error": "#f87171", "speak": "#4ade80",
}
LOG_LIMIT_LINES = 1500

STAT_CARDS: Tuple[Tuple[str, str], ...] = (
    ("viewers", "Зрители сейчас"), ("likes", "Лайки"), ("gifts", "Подарки"), ("follows", "Новые подписки"),
    ("uptime", "Время в эфире"),
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

    # ---- потокобезопасные уведомления -----------------------------------------
    def _emit(self, level: str, message: str) -> None:
        self.ui_queue.put(("log", level, message))

    def _on_state(self, state: str, message: str) -> None:
        self.ui_queue.put(("state", state, message))

    # ---- мелкие строительные блоки ----------------------------------------------
    def _setup_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dark.TCheckbutton", background=PANEL, foreground=FG, font=FONT,
                        focuscolor=PANEL, indicatorbackground=FIELD, indicatorforeground=FG)
        style.map("Dark.TCheckbutton",
                  background=[("active", PANEL)], foreground=[("active", FG)],
                  indicatorbackground=[("selected", ACCENT), ("!selected", FIELD)])
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

    def _card(self, parent: tk.Misc, title: str, subtitle: str = "") -> Tuple[tk.Frame, tk.Frame]:
        frame = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=BORDER)
        head = tk.Frame(frame, bg=PANEL)
        head.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(head, text=title, bg=PANEL, fg=FG, font=FONT_H2, anchor="w").pack(anchor="w")
        if subtitle:
            tk.Label(head, text=subtitle, bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w").pack(anchor="w")
        body = tk.Frame(frame, bg=PANEL)
        body.pack(fill="both", expand=True, padx=14, pady=(6, 12))
        return frame, body

    def _entry(self, parent: tk.Misc, var: tk.Variable, **kwargs: Any) -> tk.Entry:
        return tk.Entry(parent, textvariable=var, bg=FIELD, fg=FG, insertbackground=FG,
                        relief="flat", bd=0, font=FONT, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=ACCENT, **kwargs)

    def _button(self, parent: tk.Misc, text: str, command: Callable[[], None],
                primary: bool = False, **kwargs: Any) -> tk.Button:
        bg, hover = (ACCENT, ACCENT_HOVER) if primary else (BTN, BTN_HOVER)
        button = tk.Button(parent, text=text, command=command, bg=bg, fg="#ffffff" if primary else FG,
                           activebackground=hover, activeforeground="#ffffff", relief="flat", bd=0,
                           font=FONT_BOLD if primary else FONT, padx=14, pady=7, cursor="hand2",
                           disabledforeground=MUTED, **kwargs)

        def on_enter(_event: Any) -> None:
            if str(button.cget("state")) != "disabled":
                button.configure(bg=hover)

        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", lambda e: button.configure(bg=bg))
        return button

    # ---- построение интерфейса ------------------------------------------------
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

        # Окно собирается сверху вниз: фиксированные блоки, а лента событий занимает остаток.
        # Размеры не заданы в пикселях, поэтому при масштабе Windows 125–200 % ничего не обрезается.
        outer = tk.Frame(root, bg=BG)
        outer.pack(fill="both", expand=True, padx=18, pady=10)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        self._outer = outer

        self._build_header(outer)
        self._build_controls(outer)
        self._build_stats(outer)
        self._build_feed(outer)
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
        width = min(max(need_w, 900), screen_w - 40)
        height = min(need_h + 170, screen_h - 60)
        root.geometry(f"{width}x{height}+{max(0, (screen_w - width) // 2)}+{max(0, (screen_h - height) // 3)}")
        root.minsize(min(need_w, screen_w - 40), min(need_h, screen_h - 60))

    def _build_header(self, parent: tk.Frame) -> None:
        head = tk.Frame(parent, bg=BG)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.columnconfigure(0, weight=1)
        tk.Label(head, text="Чат TikTok LIVE — озвучка", bg=BG, fg=FG, font=FONT_H1, anchor="w").grid(
            row=0, column=0, sticky="w")
        self.lbl_state = tk.Label(head, text="Не подключено", bg=PANEL, fg=MUTED, font=FONT_BOLD,
                                  padx=16, pady=6, highlightthickness=1, highlightbackground=MUTED)
        self.lbl_state.grid(row=0, column=1, sticky="e")

    def _build_controls(self, parent: tk.Frame) -> None:
        """Две карточки рядом: подключение и озвучка."""
        row = tk.Frame(parent, bg=BG)
        row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        row.columnconfigure(0, weight=1, uniform="controls")
        row.columnconfigure(1, weight=1, uniform="controls")
        self._build_connect(row).grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self._build_voice(row).grid(row=0, column=1, sticky="nsew", padx=(5, 0))

    def _build_stats(self, parent: tk.Frame) -> None:
        row = tk.Frame(parent, bg=BG)
        row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.stat_values: Dict[str, tk.Label] = {}
        for index, (key, title) in enumerate(STAT_CARDS):
            row.columnconfigure(index, weight=1, uniform="stats")
            card = tk.Frame(row, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
            card.grid(row=0, column=index, sticky="ew",
                      padx=(0 if index == 0 else 4, 0 if index == len(STAT_CARDS) - 1 else 4))
            tk.Label(card, text=title, bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w").pack(
                anchor="w", padx=12, pady=(5, 0))
            value = tk.Label(card, text="—" if key in ("viewers", "uptime") else "0", bg=PANEL, fg=FG,
                             font=FONT_STAT, anchor="w")
            value.pack(anchor="w", padx=12, pady=(0, 4))
            self.stat_values[key] = value

    def _build_connect(self, parent: tk.Frame) -> tk.Frame:
        card, body = self._card(parent, "Подключение")
        hint = tk.Label(body, text="Ник стримера (или ссылка на эфир). Стример должен быть в прямом эфире.",
                 bg=PANEL, fg=MUTED, font=FONT_SMALL, anchor="w", justify="left", wraplength=380)
        hint.pack(fill="x", pady=(0, 6))
        hint.bind("<Configure>", lambda e: hint.configure(wraplength=max(200, e.width)))
        line = tk.Frame(body, bg=FIELD, highlightthickness=1, highlightbackground=BORDER)
        line.pack(fill="x")
        tk.Label(line, text="@", bg=FIELD, fg=MUTED, font=FONT).pack(side="left", padx=(10, 0))
        entry = tk.Entry(line, textvariable=self.v_user, bg=FIELD, fg=FG, insertbackground=FG,
                         relief="flat", bd=0, font=FONT, highlightthickness=0)
        entry.pack(side="left", fill="x", expand=True, ipady=7, padx=6)
        entry.bind("<Return>", lambda e: self.toggle_connect())
        self.entry_user = entry
        self.btn_connect = self._button(body, "Подключиться", self.toggle_connect, primary=True)
        self.btn_connect.pack(fill="x", pady=(8, 0))
        return card

    def _build_voice(self, parent: tk.Frame) -> tk.Frame:
        card, body = self._card(parent, "Озвучка")
        body.columnconfigure(0, weight=1)
        self.cmb_voice = ttk.Combobox(body, textvariable=self.v_voice, state="readonly",
                                      style="Dark.TCombobox", font=FONT,
                                      values=[VOICES[v] for v in self.voice_ids])
        self.cmb_voice.grid(row=0, column=0, sticky="ew")
        self.cmb_voice.bind("<<ComboboxSelected>>", lambda e: self._changed())
        buttons = tk.Frame(body, bg=PANEL)
        buttons.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        for col, (text, command) in enumerate((("▶ Проверить", self.test_voice),
                                               ("⏭ Пропустить", self.engine.skip),
                                               ("🗑 Очистить", self.engine.clear))):
            buttons.columnconfigure(col, weight=1, uniform="voice_buttons")
            self._button(buttons, text, command).grid(
                row=0, column=col, sticky="ew", padx=(0 if col == 0 else 3, 0 if col == 2 else 3))
        manual_line = tk.Frame(body, bg=PANEL)
        manual_line.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        manual_line.columnconfigure(0, weight=1)
        manual = self._entry(manual_line, self.v_manual)
        manual.grid(row=0, column=0, sticky="ew", ipady=6, padx=(0, 6))
        manual.bind("<Return>", lambda e: self.speak_manual())
        self._button(manual_line, "Озвучить свой текст", self.speak_manual).grid(row=0, column=1)
        return card

    def _build_feed(self, parent: tk.Frame) -> None:
        card, body = self._card(parent, "Лента событий  (▶ — что сейчас озвучивается)")
        card.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.log = tk.Text(body, wrap="word", state="disabled", font=FONT, bg=FIELD, fg=FG, height=4,
                           relief="flat", bd=0, highlightthickness=1, highlightbackground=BORDER,
                           padx=10, pady=8, insertbackground=FG, spacing3=2)
        scroll = ttk.Scrollbar(body, command=self.log.yview, style="Dark.Vertical.TScrollbar")
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for level, color in LOG_COLORS.items():
            self.log.tag_configure(level, foreground=color)

    def _build_filters(self, parent: tk.Frame) -> None:
        card, body = self._card(parent, "Что озвучивать")
        card.grid(row=4, column=0, sticky="ew", pady=(0, 4))
        for col in range(4):
            body.columnconfigure(col, weight=1, uniform="flags")
        for index, (key, (text, _)) in enumerate(self.flags.items()):
            ttk.Checkbutton(body, text=text, variable=self.flag_vars[key], style="Dark.TCheckbutton",
                            command=self._changed).grid(
                row=index // 4, column=index % 4, sticky="w", padx=(0, 10), pady=2)

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

    # ---- настройки ------------------------------------------------------------
    def _collect(self) -> Settings:
        """Собрать настройки из элементов окна (только в главном потоке)."""
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
            self._set_state("connecting")

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

    # ---- обновления ---------------------------------------------------------------
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
                path = updater.download_update(info, target, lambda p: self.ui_queue.put(("progress", p)))
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

    # ---- обновление окна из очереди --------------------------------------------
    def _set_state(self, state: str) -> None:
        text, color = STATE_VIEW.get(state, (state, MUTED))
        self.lbl_state.configure(text=text, fg=color, highlightbackground=color)
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

    def _refresh_stats(self) -> None:
        stats = self.controller.stats
        self.stat_values["viewers"].configure(text=format_count(stats.viewers))
        self.stat_values["likes"].configure(text=format_count(stats.likes))
        self.stat_values["gifts"].configure(text=format_count(stats.gifts))
        self.stat_values["follows"].configure(text=format_count(stats.follows))
        uptime = "—" if self._live_since is None else format_uptime(time.monotonic() - self._live_since)
        self.stat_values["uptime"].configure(text=uptime)

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
                    self._set_state(item[1])
                    if item[1] == "stopped":
                        self.btn_connect.configure(text="Подключиться")
                elif kind == "disconnected":
                    self._busy_disconnect = False
                    self.btn_connect.configure(state="normal", text="Подключиться")
                    self._set_state("stopped")
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
        app._log("error", "Внутренняя ошибка: " + "".join(traceback.format_exception_only(exc, val)).strip())

    root.report_callback_exception = on_error
    root.mainloop()
    return 0
