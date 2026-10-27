"""Окно приложения (tkinter): подключение к стриму, голос, фильтры, журнал чата."""

from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import replace
from tkinter import messagebox, ttk
from typing import Any, Dict, Optional

from . import APP_NAME, __version__
from .controller import Controller
from .engine import Engine
from .player import create_player
from .settings import DEFAULT_VOICE, Settings, SettingsStore
from .tts import SAMPLE_PHRASE, VOICES, Synthesizer

# состояние -> (текст, цвет)
STATE_VIEW: Dict[str, tuple] = {
    "connecting": ("Подключение…", "#d98200"),
    "connected": ("● Подключено", "#1a8f3c"),
    "reconnecting": ("Переподключение…", "#d98200"),
    "offline": ("Стример не в эфире, жду…", "#d98200"),
    "rate_limit": ("Лимит сервиса, жду…", "#d98200"),
    "blocked": ("Отклонено, повторяю…", "#d98200"),
    "other": ("Ошибка, повторяю…", "#d98200"),
    "fatal": ("Не удалось подключиться", "#c62828"),
    "error": ("Ошибка", "#c62828"),
    "stopped": ("Не подключено", "#666666"),
}

LOG_COLORS = {
    "chat": "#111111", "skip": "#8a8a8a", "gift": "#8e24aa", "info": "#1565c0",
    "warn": "#d98200", "error": "#c62828", "speak": "#1a8f3c",
}
LOG_LIMIT_LINES = 1500


class App:
    def __init__(self, root: tk.Tk, store: Optional[SettingsStore] = None, synth: Any = None,
                 player: Any = None, library: Any = None) -> None:
        self.root = root
        self.store = store or SettingsStore()
        self.settings: Settings = self.store.load()
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self._save_job: Optional[str] = None
        self._closing = False
        self._busy_disconnect = False

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
        self._log("info", f"{APP_NAME} {__version__}. Введите ник стримера и нажмите «Подключиться».")

    # ---- потокобезопасные уведомления -----------------------------------------
    def _emit(self, level: str, message: str) -> None:
        self.ui_queue.put(("log", level, message))

    def _on_state(self, state: str, message: str) -> None:
        self.ui_queue.put(("state", state, message))

    # ---- построение интерфейса ------------------------------------------------
    def _build(self) -> None:
        s = self.settings
        root = self.root
        root.title(f"{APP_NAME} {__version__}")
        root.minsize(760, 640)
        try:
            style = ttk.Style()
            style.theme_use("vista" if "vista" in style.theme_names() else "clam")
        except tk.TclError:
            pass

        self.v_user = tk.StringVar(value=s.username)
        voice = s.voice if s.voice in VOICES else DEFAULT_VOICE
        self.v_voice = tk.StringVar(value=VOICES[voice])
        self.v_rate = tk.IntVar(value=s.rate)
        self.v_pitch = tk.IntVar(value=s.pitch)
        self.v_volume = tk.IntVar(value=s.volume)
        self.v_max_len = tk.IntVar(value=s.max_len)
        self.v_queue = tk.IntVar(value=s.queue_limit)
        self.v_blacklist = tk.StringVar(value=s.blacklist)
        self.v_key = tk.StringVar(value=s.euler_key)
        self.flags = {
            "read_nick": ("Читать ник перед сообщением", s.read_nick),
            "skip_commands": ("Пропускать команды (! и /)", s.skip_commands),
            "skip_links": ("Пропускать сообщения со ссылками", s.skip_links),
            "only_russian": ("Только сообщения с русскими буквами", s.only_russian),
            "ignore_repeats": ("Не читать повторы и спам", s.ignore_repeats),
            "only_new": ("Не читать старые комментарии при входе", s.only_new),
            "read_gifts": ("Озвучивать подарки", s.read_gifts),
            "read_follows": ("Озвучивать новые подписки", s.read_follows),
        }
        self.flag_vars = {k: tk.BooleanVar(value=v[1]) for k, v in self.flags.items()}

        pad = {"padx": 10, "pady": 4}
        main = ttk.Frame(root, padding=6)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=1)

        # --- стрим
        box = ttk.LabelFrame(main, text=" Стрим TikTok ")
        box.grid(row=0, column=0, sticky="ew", **pad)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Ник стримера:").grid(row=0, column=0, padx=8, pady=8)
        entry = ttk.Entry(box, textvariable=self.v_user)
        entry.grid(row=0, column=1, sticky="ew", pady=8)
        entry.bind("<Return>", lambda e: self.toggle_connect())
        self.btn_connect = ttk.Button(box, text="Подключиться", command=self.toggle_connect, width=16)
        self.btn_connect.grid(row=0, column=2, padx=8)
        self.lbl_state = ttk.Label(box, text="Не подключено", foreground="#666666", width=26)
        self.lbl_state.grid(row=0, column=3, padx=(0, 8))
        ttk.Label(box, text="Можно вставить @ник или ссылку на эфир. Стример должен быть в прямом эфире.",
                  foreground="#666666").grid(row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(0, 6))

        # --- голос
        vbox = ttk.LabelFrame(main, text=" Голос ")
        vbox.grid(row=1, column=0, sticky="ew", **pad)
        vbox.columnconfigure(1, weight=1)
        ttk.Label(vbox, text="Голос:").grid(row=0, column=0, padx=8, pady=6, sticky="w")
        self.cmb_voice = ttk.Combobox(vbox, textvariable=self.v_voice, state="readonly",
                                      values=[VOICES[v] for v in self.voice_ids])
        self.cmb_voice.grid(row=0, column=1, sticky="ew", pady=6)
        self.cmb_voice.bind("<<ComboboxSelected>>", lambda e: self._changed())
        ttk.Button(vbox, text="▶ Проверить голос", command=self.test_voice).grid(row=0, column=2, padx=8)
        self._slider(vbox, 1, "Скорость", self.v_rate, -50, 100, "%")
        self._slider(vbox, 2, "Тон", self.v_pitch, -30, 30, " Гц")
        self._slider(vbox, 3, "Громкость", self.v_volume, 0, 100, "%")

        # --- что читать
        fbox = ttk.LabelFrame(main, text=" Что озвучивать ")
        fbox.grid(row=2, column=0, sticky="ew", **pad)
        fbox.columnconfigure(0, weight=1)
        fbox.columnconfigure(1, weight=1)
        for index, (key, (text, _)) in enumerate(self.flags.items()):
            ttk.Checkbutton(fbox, text=text, variable=self.flag_vars[key],
                            command=self._changed).grid(row=index // 2, column=index % 2,
                                                         sticky="w", padx=10, pady=2)
        lim = ttk.Frame(fbox)
        lim.grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=(4, 6))
        ttk.Label(lim, text="Макс. длина сообщения (символов):").pack(side="left")
        ttk.Spinbox(lim, from_=20, to=500, width=5, textvariable=self.v_max_len,
                    command=self._changed).pack(side="left", padx=(6, 18))
        ttk.Label(lim, text="Макс. очередь (сообщений):").pack(side="left")
        ttk.Spinbox(lim, from_=1, to=100, width=5, textvariable=self.v_queue,
                    command=self._changed).pack(side="left", padx=6)

        # --- чёрный список и ключ
        obox = ttk.LabelFrame(main, text=" Дополнительно ")
        obox.grid(row=3, column=0, sticky="ew", **pad)
        obox.columnconfigure(1, weight=1)
        ttk.Label(obox, text="Не читать слова:").grid(row=0, column=0, padx=8, pady=4, sticky="w")
        ttk.Entry(obox, textvariable=self.v_blacklist).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(obox, text="через запятую", foreground="#666666").grid(row=0, column=2, padx=(0, 8))
        ttk.Label(obox, text="Ключ Euler Stream:").grid(row=1, column=0, padx=8, pady=4, sticky="w")
        ttk.Entry(obox, textvariable=self.v_key, show="•").grid(row=1, column=1, sticky="ew", padx=(0, 8))
        ttk.Label(obox, text="необязательно", foreground="#666666").grid(row=1, column=2, padx=(0, 8))

        # --- ручные действия
        abox = ttk.Frame(main)
        abox.grid(row=4, column=0, sticky="ew", **pad)
        abox.columnconfigure(2, weight=1)
        ttk.Button(abox, text="⏭ Пропустить", command=self.engine.skip).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(abox, text="🗑 Очистить очередь", command=self.engine.clear).grid(row=0, column=1, padx=(0, 12))
        self.v_manual = tk.StringVar()
        manual = ttk.Entry(abox, textvariable=self.v_manual)
        manual.grid(row=0, column=2, sticky="ew")
        manual.bind("<Return>", lambda e: self.speak_manual())
        ttk.Button(abox, text="Озвучить текст", command=self.speak_manual).grid(row=0, column=3, padx=(6, 0))

        # --- журнал
        lbox = ttk.LabelFrame(main, text=" Журнал чата ")
        lbox.grid(row=5, column=0, sticky="nsew", **pad)
        lbox.columnconfigure(0, weight=1)
        lbox.rowconfigure(0, weight=1)
        self.log = tk.Text(lbox, height=9, wrap="word", state="disabled", font=("Segoe UI", 10))
        scroll = ttk.Scrollbar(lbox, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for level, color in LOG_COLORS.items():
            self.log.tag_configure(level, foreground=color)

        self.lbl_queue = ttk.Label(main, text="В очереди: 0", foreground="#666666")
        self.lbl_queue.grid(row=6, column=0, sticky="e", padx=12)

        for var in (self.v_user, self.v_blacklist, self.v_key, self.v_max_len, self.v_queue):
            var.trace_add("write", lambda *a: self._changed())

    def _slider(self, parent: ttk.Frame, row: int, title: str, var: tk.IntVar,
                low: int, high: int, unit: str) -> None:
        ttk.Label(parent, text=title + ":").grid(row=row, column=0, padx=8, sticky="w")
        label = ttk.Label(parent, width=9, anchor="e")

        def on_move(value: str) -> None:
            number = int(round(float(value)))
            var.set(number)
            label.configure(text=f"{number:+d}{unit}" if low < 0 else f"{number}{unit}")
            self._changed()

        scale = ttk.Scale(parent, from_=low, to=high, orient="horizontal", command=on_move)
        scale.set(var.get())
        scale.grid(row=row, column=1, sticky="ew", pady=2)
        label.grid(row=row, column=2, padx=8)
        number = var.get()
        label.configure(text=f"{number:+d}{unit}" if low < 0 else f"{number}{unit}")

    # ---- настройки ------------------------------------------------------------
    def _collect(self) -> Settings:
        """Собрать настройки из элементов окна (только в главном потоке)."""
        def number(var: tk.IntVar, default: int) -> int:
            try:
                return int(var.get())
            except (tk.TclError, ValueError):
                return default

        label = self.v_voice.get()
        voice = next((vid for vid, text in VOICES.items() if text == label), self.settings.voice)
        current = self.settings
        new = replace(
            current,
            username=self.v_user.get().strip(),
            voice=voice,
            rate=number(self.v_rate, current.rate),
            pitch=number(self.v_pitch, current.pitch),
            volume=number(self.v_volume, current.volume),
            max_len=number(self.v_max_len, current.max_len),
            queue_limit=number(self.v_queue, current.queue_limit),
            blacklist=self.v_blacklist.get(),
            euler_key=self.v_key.get().strip(),
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

    # ---- обновление окна из очереди --------------------------------------------
    def _set_state(self, state: str) -> None:
        text, color = STATE_VIEW.get(state, (state, "#666666"))
        self.lbl_state.configure(text=text, foreground=color)

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
        except queue.Empty:
            pass
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
