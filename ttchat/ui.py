"""Виджеты со скруглёнными углами и иконки.

Tk не умеет сглаживать полигоны, поэтому скругление рисуется так: угловые четверти
растеризуются в Python с суперсэмплингом (получается плавная кромка), а прямые участки
дорисовываются обычными прямоугольниками. Угловые картинки кэшируются на холсте.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---- палитра ---------------------------------------------------------------

BG = "#0f1113"
PANEL = "#17191c"
PANEL_SOFT = "#1e2126"
FIELD = "#0b0d0f"
BORDER = "#292d33"
BTN = "#23262b"
BTN_HOVER = "#2e3238"
FG = "#eceef0"
MUTED = "#8a9097"
ACCENT = "#f43f5e"
ACCENT_HOVER = "#e11d48"
GREEN = "#3ddc84"
ORANGE = "#f0a13a"
RED = "#ef5350"

FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_H1 = ("Segoe UI", 14, "bold")
FONT_H2 = ("Segoe UI", 11, "bold")
FONT_STAT = ("Segoe UI", 14, "bold")


# ---- сглаженные скругления -------------------------------------------------

def _rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _hex(rgb: Sequence[float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in rgb)


def _corner_matrix(radius: int, fill: str, outline: str, bg: str,
                   samples: int = 4) -> List[List[str]]:
    """Цвета пикселей левого верхнего угла: заливка, обводка 1 px и фон вокруг."""
    fr, fg_, fb = _rgb(fill)
    orr, og, ob = _rgb(outline)
    br, bg_, bb = _rgb(bg)
    step = 1.0 / samples
    total = samples * samples
    rows: List[List[str]] = []
    for y in range(radius):
        row: List[str] = []
        for x in range(radius):
            inner = edge = 0
            for sy in range(samples):
                for sx in range(samples):
                    dx = x + (sx + 0.5) * step - radius
                    dy = y + (sy + 0.5) * step - radius
                    distance = math.hypot(dx, dy)
                    if distance <= radius - 1.0:
                        inner += 1
                    elif distance <= radius:
                        edge += 1
            outside = total - inner - edge
            row.append(_hex((
                (fr * inner + orr * edge + br * outside) / total,
                (fg_ * inner + og * edge + bg_ * outside) / total,
                (fb * inner + ob * edge + bb * outside) / total,
            )))
        rows.append(row)
    return rows


def _photo(canvas: tk.Misc, rows: List[List[str]], master_key: str) -> "tk.PhotoImage":
    height = len(rows)
    width = len(rows[0]) if rows else 0
    image = tk.PhotoImage(master=canvas, width=width, height=height)
    data = " ".join("{" + " ".join(row) + "}" for row in rows)
    image.put(data)
    return image


def _tiles(canvas: tk.Misc, radius: int, fill: str, outline: str, bg: str) -> Dict[str, Any]:
    cache: Dict[Any, Dict[str, Any]] = canvas.__dict__.setdefault("_rr_cache", {})
    key = (radius, fill, outline, bg)
    cached = cache.get(key)
    if cached is not None:
        return cached
    base = _corner_matrix(radius, fill, outline, bg)
    flipped_x = [list(reversed(row)) for row in base]
    flipped_y = list(reversed(base))
    flipped_xy = list(reversed(flipped_x))
    tiles = {
        "nw": _photo(canvas, base, "nw"),
        "ne": _photo(canvas, flipped_x, "ne"),
        "sw": _photo(canvas, flipped_y, "sw"),
        "se": _photo(canvas, flipped_xy, "se"),
    }
    cache[key] = tiles
    return tiles


def draw_round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                    radius: float, fill: str, outline: Optional[str] = None,
                    bg: str = BG, tags: str = "rounded") -> None:
    """Нарисовать прямоугольник со сглаженными скруглениями."""
    x1, y1, x2, y2 = round(x1), round(y1), round(x2), round(y2)
    width, height = x2 - x1, y2 - y1
    if width < 2 or height < 2:
        return
    radius = int(max(0, min(radius, width / 2, height / 2)))
    if radius == 0:
        canvas.create_rectangle(x1, y1, x2, y2, fill=fill,
                                outline=outline or fill, tags=tags)
        return
    tiles = _tiles(canvas, radius, fill, outline or fill, bg)
    canvas.create_image(x1, y1, image=tiles["nw"], anchor="nw", tags=tags)
    canvas.create_image(x2, y1, image=tiles["ne"], anchor="ne", tags=tags)
    canvas.create_image(x1, y2, image=tiles["sw"], anchor="sw", tags=tags)
    canvas.create_image(x2, y2, image=tiles["se"], anchor="se", tags=tags)
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline="", tags=tags)
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline="", tags=tags)
    if outline:
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y1 + 1, fill=outline,
                                outline="", tags=tags)
        canvas.create_rectangle(x1 + radius, y2 - 1, x2 - radius, y2, fill=outline,
                                outline="", tags=tags)
        canvas.create_rectangle(x1, y1 + radius, x1 + 1, y2 - radius, fill=outline,
                                outline="", tags=tags)
        canvas.create_rectangle(x2 - 1, y1 + radius, x2, y2 - radius, fill=outline,
                                outline="", tags=tags)


def text_width(font: Sequence[Any], text: str) -> int:
    return tkfont.Font(font=font).measure(text or " ")


def fit_text(font: Sequence[Any], text: str, limit: int) -> str:
    """Обрезать текст по ширине, добавив многоточие."""
    if limit <= 0:
        return ""
    if text_width(font, text) <= limit:
        return text
    cut = text
    while cut and text_width(font, cut + "…") > limit:
        cut = cut[:-1]
    return (cut.rstrip() + "…") if cut else "…"


# ---- иконки ----------------------------------------------------------------
# Все иконки рисуются линиями в квадрате size×size с левым верхним углом (x, y).

def icon_viewers(c: tk.Canvas, x: float, y: float, color: str, size: float = 18,
                 tags: str = "icon") -> None:
    r = size / 5.0
    c.create_oval(x + size * 0.05, y + size * 0.12, x + size * 0.05 + 2 * r,
                  y + size * 0.12 + 2 * r, outline=color, width=1.2, tags=tags)
    c.create_oval(x + size * 0.47, y + size * 0.2, x + size * 0.47 + 1.6 * r,
                  y + size * 0.2 + 1.6 * r, outline=color, width=1.2, tags=tags)
    c.create_arc(x - size * 0.08, y + size * 0.44, x + size * 0.42, y + size * 0.98,
                 start=20, extent=140, style="arc", outline=color, width=1.2, tags=tags)
    c.create_arc(x + size * 0.42, y + size * 0.5, x + size * 1.02, y + size * 0.98,
                 start=25, extent=130, style="arc", outline=color, width=1.2, tags=tags)


def icon_heart(c: tk.Canvas, x: float, y: float, color: str, size: float = 18,
               tags: str = "icon") -> None:
    w = h = size
    points = [x + w * 0.5, y + h * 0.92,
              x + w * 0.08, y + h * 0.5,
              x + w * 0.08, y + h * 0.26,
              x + w * 0.5, y + h * 0.2,
              x + w * 0.92, y + h * 0.26,
              x + w * 0.92, y + h * 0.5]
    c.create_polygon(points, smooth=True, fill="", outline=color, width=1.2, tags=tags)


def icon_gift(c: tk.Canvas, x: float, y: float, color: str, size: float = 18,
              tags: str = "icon") -> None:
    w = h = size
    c.create_rectangle(x + w * 0.1, y + h * 0.42, x + w * 0.9, y + h * 0.92,
                       outline=color, width=1.2, tags=tags)
    c.create_rectangle(x + w * 0.06, y + h * 0.28, x + w * 0.94, y + h * 0.44,
                       outline=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.5, y + h * 0.28, x + w * 0.5, y + h * 0.92,
                  fill=color, width=1.2, tags=tags)
    c.create_arc(x + w * 0.24, y + h * 0.06, x + w * 0.52, y + h * 0.3,
                 start=0, extent=200, style="arc", outline=color, width=1.2, tags=tags)
    c.create_arc(x + w * 0.48, y + h * 0.06, x + w * 0.76, y + h * 0.3,
                 start=-20, extent=200, style="arc", outline=color, width=1.2, tags=tags)


def icon_follow(c: tk.Canvas, x: float, y: float, color: str, size: float = 18,
                tags: str = "icon") -> None:
    w = h = size
    r = w * 0.17
    c.create_oval(x + w * 0.12, y + h * 0.1, x + w * 0.12 + 2 * r, y + h * 0.1 + 2 * r,
                  outline=color, width=1.2, tags=tags)
    c.create_arc(x + w * 0.02, y + h * 0.46, x + w * 0.56, y + h * 1.02,
                 start=15, extent=150, style="arc", outline=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.76, y + h * 0.36, x + w * 0.76, y + h * 0.78,
                  fill=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.55, y + h * 0.57, x + w * 0.97, y + h * 0.57,
                  fill=color, width=1.2, tags=tags)


def icon_skip(c: tk.Canvas, x: float, y: float, color: str, size: float = 14,
              tags: str = "icon") -> None:
    w = h = size
    c.create_polygon(x + w * 0.2, y + h * 0.18, x + w * 0.2, y + h * 0.82,
                     x + w * 0.68, y + h * 0.5, fill=color, outline=color, tags=tags)
    c.create_rectangle(x + w * 0.74, y + h * 0.18, x + w * 0.88, y + h * 0.82,
                       fill=color, outline=color, tags=tags)


def icon_trash(c: tk.Canvas, x: float, y: float, color: str, size: float = 14,
               tags: str = "icon") -> None:
    w = h = size
    c.create_line(x + w * 0.12, y + h * 0.24, x + w * 0.88, y + h * 0.24,
                  fill=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.38, y + h * 0.24, x + w * 0.42, y + h * 0.12,
                  fill=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.62, y + h * 0.24, x + w * 0.58, y + h * 0.12,
                  fill=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.42, y + h * 0.12, x + w * 0.58, y + h * 0.12,
                  fill=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.24, y + h * 0.24, x + w * 0.3, y + h * 0.9,
                  fill=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.76, y + h * 0.24, x + w * 0.7, y + h * 0.9,
                  fill=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.3, y + h * 0.9, x + w * 0.7, y + h * 0.9,
                  fill=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.5, y + h * 0.38, x + w * 0.5, y + h * 0.78,
                  fill=color, width=1.2, tags=tags)


def icon_power(c: tk.Canvas, x: float, y: float, color: str, size: float = 14,
               tags: str = "icon") -> None:
    w = h = size
    c.create_arc(x + w * 0.14, y + h * 0.2, x + w * 0.86, y + h * 0.92,
                 start=300, extent=300, style="arc", outline=color, width=1.2, tags=tags)
    c.create_line(x + w * 0.5, y + h * 0.08, x + w * 0.5, y + h * 0.46,
                  fill=color, width=1.4, tags=tags)


def icon_play(c: tk.Canvas, x: float, y: float, color: str, size: float = 14,
              tags: str = "icon") -> None:
    w = h = size
    c.create_polygon(x + w * 0.25, y + h * 0.16, x + w * 0.25, y + h * 0.84,
                     x + w * 0.8, y + h * 0.5, fill=color, outline=color, tags=tags)


def icon_download(c: tk.Canvas, x: float, y: float, color: str, size: float = 14,
                  tags: str = "icon") -> None:
    w = h = size
    c.create_line(x + w * 0.5, y + h * 0.1, x + w * 0.5, y + h * 0.62,
                  fill=color, width=1.4, tags=tags)
    c.create_line(x + w * 0.26, y + h * 0.4, x + w * 0.5, y + h * 0.64,
                  fill=color, width=1.4, tags=tags)
    c.create_line(x + w * 0.74, y + h * 0.4, x + w * 0.5, y + h * 0.64,
                  fill=color, width=1.4, tags=tags)
    c.create_line(x + w * 0.16, y + h * 0.8, x + w * 0.84, y + h * 0.8,
                  fill=color, width=1.4, tags=tags)


ICON_WIDTH = 14


# ---- карточка --------------------------------------------------------------

class Card(tk.Frame):
    """Панель со сглаженными скруглёнными углами и вложенным фреймом содержимого."""

    def __init__(self, parent: tk.Misc, pad: int = 13, radius: int = 12,
                 fill: str = PANEL, outline: str = BORDER, bg: str = BG,
                 **kwargs: Any) -> None:
        super().__init__(parent, bg=bg, **kwargs)
        self._fill = fill
        self._outline = outline
        self._radius = radius
        self._bg = bg
        self._back = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, takefocus=0)
        self._back.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg=fill)
        self.body.pack(fill="both", expand=True, padx=pad, pady=pad)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event: Optional[tk.Event] = None) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width < 8 or height < 8:
            return
        self._back.delete("all")
        draw_round_rect(self._back, 0, 0, width, height, self._radius,
                        self._fill, self._outline, self._bg)


# ---- кнопка ----------------------------------------------------------------

class RoundButton(tk.Canvas):
    """Кнопка со скруглёнными краями: текст и (необязательно) иконка на холсте."""

    KINDS = {
        "primary": (ACCENT, ACCENT_HOVER, "#ffffff"),
        "secondary": (BTN, BTN_HOVER, FG),
        "ghost": (BG, PANEL_SOFT, MUTED),
    }

    def __init__(self, parent: tk.Misc, text: str, command: Optional[Callable[[], None]] = None,
                 kind: str = "secondary", icon: Optional[Callable[..., None]] = None,
                 padx: int = 12, pady: int = 6, radius: int = 9, font: Sequence[Any] = FONT,
                 bg: str = BG, **kwargs: Any) -> None:
        self._text = text
        self._command = command
        self._kind = kind
        self._icon = icon
        self._padx = padx
        self._pady = pady
        self._radius = radius
        self._font = tuple(font)
        self._bg = bg
        self._enabled = True
        self._hover = False
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, takefocus=0,
                         cursor="hand2", **kwargs)
        self._resize()
        self.bind("<Configure>", lambda e: self._render())
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    # ---- совместимость с tk.Button ----
    def cget(self, key: str) -> Any:
        if key == "text":
            return self._text
        if key == "state":
            return "normal" if self._enabled else "disabled"
        return super().cget(key)

    def configure(self, **kwargs: Any) -> None:  # type: ignore[override]
        if "text" in kwargs:
            self._text = str(kwargs.pop("text"))
            self._resize()
            self._render()
        if "state" in kwargs:
            self._enabled = str(kwargs.pop("state")) != "disabled"
            self._render()
        if kwargs:
            super().configure(**kwargs)

    config = configure

    # ---- внутреннее ----
    def _colors(self) -> Tuple[str, str, str]:
        fill, hover, fg = self.KINDS.get(self._kind, self.KINDS["secondary"])
        if not self._enabled:
            return BTN, BTN, MUTED
        return (hover if self._hover else fill), hover, fg

    def _resize(self) -> None:
        width = text_width(self._font, self._text) + 2 * self._padx
        if self._icon is not None:
            width += ICON_WIDTH + 7
        height = max(tkfont.Font(font=self._font).metrics("linespace") + 2 * self._pady, 30)
        super().configure(width=width, height=height)

    def _render(self) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width < 4 or height < 4:
            width, height = int(self.cget("width")), int(self.cget("height"))
        self.delete("all")
        fill, _hover, fg = self._colors()
        draw_round_rect(self, 0, 0, width, height, self._radius, fill, None, self._bg)
        inner = width - 2 * self._padx
        if self._icon is not None:
            inner -= ICON_WIDTH + 7
        label = fit_text(self._font, self._text, max(0, inner))
        if self._icon is None:
            self.create_text(width / 2, height / 2 + 1, text=label, fill=fg,
                             font=self._font)
        else:
            block = ICON_WIDTH + (7 if label else 0) + text_width(self._font, label)
            left = (width - block) / 2
            self._icon(self, left, (height - ICON_WIDTH) / 2, fg, ICON_WIDTH)
            self.create_text(left + ICON_WIDTH + 7, height / 2 + 1, text=label,
                             anchor="w", fill=fg, font=self._font)

    def _click(self, _event: tk.Event) -> None:
        if self._enabled and self._command is not None:
            self._command()

    def _enter(self, _event: tk.Event) -> None:
        self._hover = True
        self._render()

    def _leave(self, _event: tk.Event) -> None:
        self._hover = False
        self._render()


# ---- выпадающий список ------------------------------------------------------

class Select(tk.Canvas):
    """Выбор из списка: скруглённое поле и всплывающий список с полными названиями."""

    def __init__(self, parent: tk.Misc, var: tk.StringVar, values: Sequence[str],
                 command: Optional[Callable[[], None]] = None, font: Sequence[Any] = FONT,
                 height: int = 34, bg: str = PANEL, **kwargs: Any) -> None:
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=height,
                         takefocus=0, cursor="hand2", **kwargs)
        self.var = var
        self._values = list(values)
        self._command = command
        self._font = tuple(font)
        self._bg = bg
        self._popup: Optional[tk.Toplevel] = None
        self.bind("<Configure>", lambda e: self._render())
        self.bind("<Button-1>", lambda e: self._toggle())
        self.bind("<Destroy>", lambda e: self._close())

    # ---- совместимость с ttk.Combobox ----
    def get(self) -> str:
        return self.var.get()

    def set(self, value: str) -> None:
        self.var.set(value)
        self._render()

    def bind_selected(self, callback: Callable[[], None]) -> None:
        self._command = callback

    # ---- рисование ----
    def _render(self) -> None:
        width, height = self.winfo_width(), self.winfo_height()
        if width < 10 or height < 10:
            return
        self.delete("all")
        draw_round_rect(self, 0, 0, width, height, 8, FIELD, BORDER, self._bg)
        text = fit_text(self._font, self.var.get(), max(0, width - 44))
        self.create_text(12, height / 2, text=text, anchor="w", fill=FG, font=self._font)
        cx, cy = width - 18, height / 2
        self.create_line(cx - 4, cy - 2, cx, cy + 2, cx + 4, cy - 2,
                         fill=MUTED, width=1.4, capstyle=tk.ROUND)

    # ---- список ----
    def _toggle(self) -> None:
        if self._popup is not None:
            self._close()
        else:
            self._open()

    def _close(self) -> None:
        popup, self._popup = self._popup, None
        if popup is not None:
            try:
                popup.destroy()
            except tk.TclError:
                pass

    def _open(self) -> None:
        if not self._values:
            return
        row_height = 28
        padding = 8
        width = max(self.winfo_width(), max(text_width(self._font, v) for v in self._values) + 40)
        height = len(self._values) * row_height + 2 * padding
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.configure(bg=BG)
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 4
        top.geometry(f"{width}x{height}+{x}+{y}")
        canvas = tk.Canvas(top, width=width, height=height, bg=BG, highlightthickness=0,
                           bd=0)
        canvas.pack()
        draw_round_rect(canvas, 0, 0, width, height, 10, PANEL, BORDER, BG)
        for index, value in enumerate(self._values):
            top_y = padding + index * row_height
            tag = f"row{index}"
            selected = value == self.var.get()
            if selected:
                canvas.create_rectangle(6, top_y, width - 6, top_y + row_height - 2,
                                        fill=PANEL_SOFT, outline="", tags=tag)
            canvas.create_text(14, top_y + row_height / 2 - 1, text=value, anchor="w",
                               fill=FG if not selected else ACCENT, font=self._font,
                               tags=tag)
            canvas.tag_bind(tag, "<Button-1>", lambda e, v=value: self._choose(v))
            canvas.tag_bind(tag, "<Enter>", lambda e, t=tag, s=selected: (
                None if s else canvas.configure(cursor="hand2")))
        top.bind("<Escape>", lambda e: self._close())
        top.bind("<FocusOut>", lambda e: self._close())
        top.focus_set()
        self._popup = top

    def _choose(self, value: str) -> None:
        self.var.set(value)
        self._close()
        self._render()
        if self._command is not None:
            self._command()


# ---- флажок ----------------------------------------------------------------

class Checkbox(tk.Frame):
    """Флажок со сглаженным квадратом и галочкой."""

    SIZE = 17

    def __init__(self, parent: tk.Misc, text: str, var: tk.BooleanVar,
                 command: Optional[Callable[[], None]] = None, bg: str = PANEL,
                 **kwargs: Any) -> None:
        super().__init__(parent, bg=bg, **kwargs)
        self.var = var
        self.command = command
        self._bg = bg
        self._box = tk.Canvas(self, width=self.SIZE + 3, height=self.SIZE + 3, bg=bg,
                              highlightthickness=0, takefocus=0)
        self._box.grid(row=0, column=0, sticky="w", padx=(0, 7))
        self._label = tk.Label(self, text=text, bg=bg, fg=FG, font=FONT, anchor="w",
                               cursor="hand2")
        self._label.grid(row=0, column=1, sticky="w")
        self.configure(cursor="hand2")
        for widget in (self, self._box, self._label):
            widget.bind("<Button-1>", self._toggle)
            widget.bind("<Enter>", lambda e: self._label.configure(fg="#ffffff"))
            widget.bind("<Leave>", lambda e: self._label.configure(fg=FG))
        self._draw()

    def _toggle(self, _event: tk.Event) -> None:
        self.var.set(not self.var.get())
        self._draw()
        if self.command is not None:
            self.command()

    def _draw(self) -> None:
        on = bool(self.var.get())
        box = self._box
        box.delete("all")
        size = self.SIZE
        draw_round_rect(box, 0, 0, size + 2, size + 2, 5,
                        ACCENT if on else FIELD, ACCENT if on else "#3a3f46", self._bg)
        if on:
            box.create_line(4.5, size * 0.55, size * 0.42, size - 3, size - 1.5, 4,
                            fill="#ffffff", width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)

    def refresh(self) -> None:
        self._draw()
