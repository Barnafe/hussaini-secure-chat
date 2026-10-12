"""Custom Tkinter widgets that give the window its rounded, anti-aliased ChatSphere look.

Plain Tk cannot draw rounded corners, gradients or smooth circles, so these widgets are
small canvases that place Pillow-rendered images (see client/images.py) under real text and
real ``Entry`` fields. Typing, copy/paste, focus and the IME all still work normally.
"""
import tkinter as tk

from client import images
from client import theme as T


def _wheel_units(event):
    if getattr(event, "num", None) == 4:
        return -3
    if getattr(event, "num", None) == 5:
        return 3
    delta = getattr(event, "delta", 0)
    if abs(delta) >= 120:
        return -3 * (delta // 120)
    return -3 if delta > 0 else 3 if delta < 0 else 0


def bind_wheel(widget, handler):
    """Mouse-wheel support on Windows, macOS and Linux (only while the pointer is over ``widget``)."""
    def on(_e=None):
        widget.bind_all("<MouseWheel>", handler)
        widget.bind_all("<Button-4>", handler)
        widget.bind_all("<Button-5>", handler)

    def off(_e=None):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.unbind_all(seq)

    widget.bind("<Enter>", on, add="+")
    widget.bind("<Leave>", off, add="+")


# --------------------------------------------------------------------------- #
# canvas with clickable regions
# --------------------------------------------------------------------------- #
class HitCanvas(tk.Canvas):
    """A canvas on which you draw everything and register clickable rectangles."""

    def __init__(self, parent, bg, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(parent, bg=bg, **kw)
        self._hits = []
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self.configure(cursor=""))

    def reset(self):
        self.delete("all")
        self._hits = []

    def hit(self, x1, y1, x2, y2, callback):
        self._hits.append((x1, y1, x2, y2, callback))

    def _find(self, event):
        x, y = event.x, self.canvasy(event.y)
        for x1, y1, x2, y2, cb in reversed(self._hits):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return cb
        return None

    def _click(self, event):
        cb = self._find(event)
        if cb:
            cb()

    def _motion(self, event):
        self.configure(cursor="hand2" if self._find(event) else "")


# --------------------------------------------------------------------------- #
# buttons
# --------------------------------------------------------------------------- #
class IconButton(tk.Canvas):
    """A round or square icon button with a hover highlight."""

    def __init__(self, parent, name, command=None, size=22, box=40, color=T.TEXT, hover_bg=T.HOVER,
                 bg=T.BG, ring=None, fill=None, stroke=2.0):
        super().__init__(parent, width=box, height=box, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.command, self.box, self.size, self.color, self.stroke = command, box, size, color, stroke
        self.hover_bg, self.ring, self.fill = hover_bg, ring, fill
        self._bg_item = self.create_image(box // 2, box // 2, image=self._shape(False))
        self._icon_item = self.create_image(box // 2, box // 2, image=T.icon(name, size, color, stroke))
        self.bind("<Enter>", lambda _e: self._hover(True))
        self.bind("<Leave>", lambda _e: self._hover(False))
        self.bind("<Button-1>", lambda _e: self.command() if self.command else None)

    def _shape(self, hovered):
        fill = self.hover_bg if hovered else self.fill
        key = ("iconbtn", self.box, fill, self.ring)
        if not fill and not self.ring:
            return T.photo(("blank", 1), lambda: images.circle_shape(1, fill=None))
        return T.photo(key, lambda: images.circle_shape(self.box, fill=fill, outline=self.ring, ow=2))

    def _hover(self, on):
        self.itemconfigure(self._bg_item, image=self._shape(on))

    def set_icon(self, name, color=None):
        self.color = color or self.color
        self.itemconfigure(self._icon_item, image=T.icon(name, self.size, self.color, self.stroke))


_BUTTON_STYLES = {
    "primary": {"fill": ("#1a74ff", "#0a55e0"), "hover": ("#3a8aff", "#1768f0"), "text": "#ffffff", "outline": None},
    "secondary": {"fill": (T.FIELD, T.FIELD), "hover": ("#102446", "#102446"), "text": T.TEXT, "outline": T.FIELD_LINE},
    "danger": {"fill": (T.BG, T.BG), "hover": ("#2a0f1d", "#2a0f1d"), "text": T.RED, "outline": "#5a1c2c"},
    "success": {"fill": ("#2bd16a", "#16a34a"), "hover": ("#3ee07c", "#22c55e"), "text": "#ffffff", "outline": None},
    "end": {"fill": ("#f05252", "#cf2b3d"), "hover": ("#ff6a6a", "#e0364a"), "text": "#ffffff", "outline": None},
    "disabled": {"fill": ("#0d1830", "#0d1830"), "hover": ("#0d1830", "#0d1830"), "text": T.DIM, "outline": None},
}


class PillButton(tk.Canvas):
    """A rounded, optionally gradient button. Supports ``configure(state=..., text=...)``."""

    def __init__(self, parent, text="", command=None, style="primary", width=200, height=46, icon=None,
                 bg=T.CARD, radius=None, font_px=15):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
        self.command, self.style, self._text, self._state = command, style, text, "normal"
        self._hover, self._icon, self._radius, self._font_px = False, icon, radius, font_px
        self._pw, self._ph = width, height
        self.bind("<Configure>", self._resize)
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<Button-1>", self._click)
        self._draw()

    def _resize(self, event):
        if (event.width, event.height) != (self._pw, self._ph) and event.width > 4:
            self._pw, self._ph = event.width, event.height
            self._draw()

    def _set_hover(self, on):
        self._hover = on and self._state == "normal"
        self.configure(cursor="hand2" if self._hover else "")
        self._draw()

    def _click(self, _e):
        if self._state == "normal" and self.command:
            self.command()

    def configure(self, cnf=None, **kw):
        redraw = False
        if "state" in kw:
            self._state = str(kw.pop("state"))
            redraw = True
        if "text" in kw:
            self._text = kw.pop("text")
            redraw = True
        if "command" in kw:
            self.command = kw.pop("command")
        if cnf or kw:
            super().configure(cnf, **kw)
        if redraw:
            self._draw()

    config = configure

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)

    def _draw(self):
        self.delete("all")
        w, h = self._pw, self._ph
        st = _BUTTON_STYLES["disabled" if self._state == "disabled" else self.style]
        left, right = st["hover" if self._hover else "fill"]
        r = self._radius if self._radius is not None else h // 2
        img = T.photo(("pill", w, h, r, left, right, st["outline"]),
                      lambda: images.gradient_rrect(w, h, r, left, right, outline=st["outline"]))
        self.create_image(0, 0, image=img, anchor="nw")
        fnt = T.font(self._font_px, "semibold")
        tw = fnt.measure(self._text)
        iw = 0
        if self._icon:
            iw = 20 + 8
        x = (w - tw - iw) // 2
        if self._icon:
            self.create_image(x + 10, h // 2, image=T.icon(self._icon, 20, st["text"], 2.0))
        self.create_text(x + iw, h // 2, text=self._text, font=fnt, fill=st["text"], anchor="w")


class Toggle(tk.Canvas):
    def __init__(self, parent, value=False, command=None, bg=T.RIGHT):
        super().__init__(parent, width=46, height=26, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self.value, self.command = bool(value), command
        self._item = self.create_image(0, 0, anchor="nw", image=self._img())
        self.bind("<Button-1>", self._flip)

    def _img(self):
        return T.photo(("toggle", self.value), lambda: images.toggle(self.value))

    def set(self, value):
        self.value = bool(value)
        self.itemconfigure(self._item, image=self._img())

    def _flip(self, _e):
        self.set(not self.value)
        if self.command:
            self.command(self.value)


# --------------------------------------------------------------------------- #
# text field
# --------------------------------------------------------------------------- #
class PillEntry(tk.Canvas):
    """A rounded input field. The real ``tk.Entry`` is ``.entry``."""

    def __init__(self, parent, textvariable=None, show=None, placeholder="", icon=None, width=300, height=46,
                 radius=None, bg=T.CARD, field=T.FIELD, border=T.FIELD_LINE, font_px=15, state="normal"):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
        self.var = textvariable or tk.StringVar()
        self._pw, self._ph = width, height
        self._radius = radius if radius is not None else height // 2
        self._icon, self._placeholder, self._focused = icon, placeholder, False
        self._field, self._border = field, border
        pad = 18 + (30 if icon else 0)
        self._pad = pad
        self.entry = tk.Entry(self, textvariable=self.var, show=show, bd=0, relief="flat", font=T.font(font_px),
                              bg=field, fg=T.TEXT, insertbackground=T.TEXT, disabledbackground=field,
                              disabledforeground=T.MUTED, readonlybackground=field, selectbackground=T.BLUE,
                              selectforeground="#ffffff", highlightthickness=0, state=state)
        self._hint = tk.Label(self, text=placeholder, font=T.font(font_px), bg=field, fg=T.DIM, anchor="w", bd=0,
                            cursor="xterm")
        self._hint.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda _e: self._focus(True))
        self.entry.bind("<FocusOut>", lambda _e: self._focus(False))
        self.var.trace_add("write", lambda *_: self._placeholder_visibility())
        self.bind("<Configure>", self._resize)
        self.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self._draw()

    def focus_set(self):
        self.entry.focus_set()

    def _resize(self, event):
        if (event.width, event.height) != (self._pw, self._ph) and event.width > 4:
            self._pw, self._ph = event.width, event.height
            self._draw()

    def _focus(self, on):
        self._focused = on
        self._draw()

    def _placeholder_visibility(self):
        if self._placeholder:
            self.itemconfigure("ph", state="hidden" if self.var.get() else "normal")

    def _draw(self):
        self.delete("all")
        w, h, r = self._pw, self._ph, self._radius
        border = T.BLUE if self._focused else self._border
        img = T.photo(("pillentry", w, h, r, self._field, border),
                      lambda: images.rounded_rect(w, h, r, self._field, outline=border, ow=2 if self._focused else 1))
        self.create_image(0, 0, image=img, anchor="nw")
        if self._icon:
            self.create_image(24, h // 2, image=T.icon(self._icon, 20, T.MUTED, 2.0))
        self.create_window(self._pad, h // 2, window=self.entry, anchor="w", width=max(10, w - self._pad - 16),
                           height=h - 14, tags="entry")
        if self._placeholder:                         # a label above the entry (canvas text would be hidden by it)
            self.create_window(self._pad, h // 2, window=self._hint, anchor="w", width=max(10, w - self._pad - 16),
                               height=h - 14, tags="ph", state="hidden" if self.var.get() else "normal")


# --------------------------------------------------------------------------- #
# virtual list (chat list, contacts, groups ...)
# --------------------------------------------------------------------------- #
class ListCanvas(tk.Canvas):
    """A scrolling list of custom-drawn rows.

    ``render(canvas, row, y, width, hover, selected, regions)`` draws one row at vertical
    offset ``y`` and appends ``(x1, x2, name)`` to ``regions`` for clickable parts.
    ``on_select(row)`` / ``on_action(row, name)`` are called on clicks.
    """

    def __init__(self, parent, bg, render, on_select=None, on_action=None):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0)
        self.render, self.on_select, self.on_action = render, on_select, on_action
        self.rows, self._ys, self._regions = [], [], {}
        self.hover, self.selected_key = -1, None
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda _e: self._set_hover(-1))
        self.bind("<Button-1>", self._click)
        bind_wheel(self, self._wheel)

    def _wheel(self, event):
        if self.bbox("all") and self._total() > self.winfo_height():
            self.yview_scroll(_wheel_units(event), "units")

    def _total(self):
        return self._ys[-1] + self.rows[-1]["height"] if self.rows else 0

    def set_rows(self, rows, selected_key=None):
        top = self.canvasy(0)
        self.rows, self.selected_key = rows, selected_key
        self.redraw()
        if top:
            self.yview_moveto(min(top, max(0, self._total() - self.winfo_height())) / max(1, self._total()))

    def redraw(self):
        self.delete("all")
        self._ys, y = [], 0
        for row in self.rows:
            self._ys.append(y)
            y += row["height"]
        self._regions = {}
        for i in range(len(self.rows)):
            self._paint(i)
        self.configure(scrollregion=(0, 0, max(1, self.winfo_width()), max(self._total(), 1)))
        if self._total() <= self.winfo_height():
            self.yview_moveto(0)

    def _paint(self, i):
        self.delete(f"r{i}")
        before = self.find_all()
        start = before[-1] if before else 0
        regions = []
        row = self.rows[i]
        self.render(self, row, self._ys[i], max(1, self.winfo_width()), i == self.hover,
                    row.get("key") is not None and row.get("key") == self.selected_key, regions)
        for item in self.find_all():
            if item > start:
                self.addtag_withtag(f"r{i}", item)
        self._regions[i] = regions

    def _index_at(self, event):
        y = self.canvasy(event.y)
        for i, top in enumerate(self._ys):
            if top <= y < top + self.rows[i]["height"]:
                return i, y
        return -1, y

    def _action_at(self, event):
        i, _y = self._index_at(event)
        if i < 0:
            return i, None
        for x1, x2, name in self._regions.get(i, []):
            if x1 <= event.x <= x2:
                return i, name
        return i, None

    def _set_hover(self, i):
        if i == self.hover:
            return
        old, self.hover = self.hover, i
        for j in (old, i):
            if 0 <= j < len(self.rows):
                self._paint(j)

    def _motion(self, event):
        i, name = self._action_at(event)
        selectable = i >= 0 and (self.rows[i].get("key") is not None or name)
        self._set_hover(i if selectable else -1)
        self.configure(cursor="hand2" if selectable else "")

    def _click(self, event):
        i, name = self._action_at(event)
        if i < 0:
            return
        row = self.rows[i]
        if name and self.on_action:
            self.on_action(row, name)
        elif row.get("key") is not None and self.on_select:
            self.on_select(row)


# --------------------------------------------------------------------------- #
# message view
# --------------------------------------------------------------------------- #
def wrap_text(fnt, text, max_px):
    """Word-wrap ``text`` into lines no wider than ``max_px`` (long words are split)."""
    lines = []
    for para in str(text).split("\n"):
        words, cur = para.split(" "), ""
        for word in words:
            trial = word if not cur else cur + " " + word
            if fnt.measure(trial) <= max_px:
                cur = trial
                continue
            if cur:
                lines.append(cur)
                cur = ""
            while fnt.measure(word) > max_px and len(word) > 1:
                cut = len(word)
                while cut > 1 and fnt.measure(word[:cut]) > max_px:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            cur = word
        lines.append(cur)
    return lines or [""]


class MessageView(tk.Canvas):
    """The conversation: day chips, incoming/outgoing bubbles, avatars, times and ticks."""

    PAD = 24
    AV = 38

    def __init__(self, parent, on_copy=None):
        super().__init__(parent, bg=T.BG, highlightthickness=0, bd=0, yscrollcommand=self._on_scroll)
        self.items, self.wallpaper_kind = [], "doodle"
        self._bg_size = (0, 0)
        self._bg_item = None
        self._menu_text = ""
        self.on_copy = on_copy
        self.menu = tk.Menu(self, tearoff=0, bg=T.CARD, fg=T.TEXT, activebackground=T.BLUE,
                            activeforeground="#ffffff", bd=0, relief="flat", font=T.font(13))
        self.menu.add_command(label="Copy message", command=self._copy)
        self._after = None
        self.bind("<Configure>", self._on_configure)
        bind_wheel(self, lambda e: self.yview_scroll(_wheel_units(e), "units"))

    # ---- public --------------------------------------------------------- #
    def set_wallpaper(self, kind):
        self.wallpaper_kind = kind
        self._bg_size = (0, 0)
        self._paint_background()

    def set_items(self, items, stick=True):
        """items: dicts with type 'day' / 'msg' / 'empty' (see ChatPane for the fields)."""
        near_bottom = self.yview()[1] > 0.97 or not self.items
        self.items = items
        self._layout()
        if stick or near_bottom:
            self.yview_moveto(1.0)

    def at_bottom(self):
        return self.yview()[1] > 0.97

    # ---- internals ------------------------------------------------------- #
    def _on_scroll(self, *_a):
        if self._bg_item is not None:
            self.coords(self._bg_item, 0, self.canvasy(0))

    def _on_configure(self, _event):
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(60, self._relayout)

    def _relayout(self):
        self._after = None
        self._paint_background()
        if self.items:
            stick = self.at_bottom()
            self._layout()
            if stick:
                self.yview_moveto(1.0)

    def _paint_background(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        bw, bh = -(-w // 300) * 300, -(-h // 300) * 300
        if (bw, bh) != self._bg_size or self._bg_item is None:
            self._bg_size = (bw, bh)
            kind = self.wallpaper_kind
            img = T.photo(("wall", kind, bw, bh), lambda: images.wallpaper(kind, bw, bh))
            if self._bg_item is None:
                self._bg_item = self.create_image(0, self.canvasy(0), image=img, anchor="nw", tags="bg")
            else:
                self.itemconfigure(self._bg_item, image=img)
        self.tag_lower("bg")

    def _layout(self):
        self.delete("all")
        self._bg_item = None
        self._paint_background()
        W = max(self.winfo_width(), 300)
        fnt, small, name_f = T.font(15), T.font(12), T.font(13, "semibold")
        y = 18
        max_text = max(150, min(int(W * 0.56), 560) - 44)
        for idx, it in enumerate(self.items):
            kind = it["type"]
            if kind == "day":
                label = it["text"]
                tw = T.font(14).measure(label) + 44
                img = T.photo(("chip", tw), lambda tw=tw: images.rounded_rect(tw, 34, 17, "#08162b", outline=T.LINE))
                self.create_image((W - tw) // 2, y, image=img, anchor="nw")
                self.create_text(W // 2, y + 17, text=label, fill=T.TEXT2, font=T.font(14))
                y += 34 + 22
            elif kind == "empty":
                self.create_text(W // 2, y + 80, text=it["text"], fill=T.MUTED, font=T.font(15), justify="center",
                                 width=W - 80)
                y += 160
            else:
                y = self._message(idx, it, y, W, fnt, small, name_f, max_text)
        self.configure(scrollregion=(0, 0, W, y + 8))

    def _message(self, idx, it, y, W, fnt, small, name_f, max_text):
        mine = it["mine"]
        lines = wrap_text(fnt, it["text"], max_text)
        text_w = max(fnt.measure(line) for line in lines)
        line_h = fnt.metrics("linespace") + 3
        stamp = it["time"]
        stamp_w = small.measure(stamp) + (26 if mine else 0)
        name = it.get("sender") if (not mine and it.get("sender")) else None
        name_w = name_f.measure(name) if name else 0
        inner_w = max(text_w, stamp_w, name_w, 48)
        bw = inner_w + 40
        bh = 14 + (name_f.metrics("linespace") + 4 if name else 0) + line_h * len(lines) + 4 + small.metrics("linespace") + 12
        tag = f"m{idx}"
        if mine:
            color = T.BLUE
            img = T.photo(("bubble", bw, bh, True, color), lambda: images.bubble(bw, bh, color, tail=True))
            x = W - self.PAD - bw - 9
            self.create_image(x, y, image=img, anchor="nw", tags=tag)
        else:
            color = T.BUBBLE_IN
            img = T.photo(("bubble", bw, bh, False, color), lambda: images.bubble(bw, bh, color, tail=False))
            x = self.PAD + self.AV + 10
            self.create_image(x, y, image=img, anchor="nw", tags=tag)
            av = it.get("avatar")
            if av is not None:
                self.create_image(self.PAD, y + bh - self.AV, image=av, anchor="nw")
        ty = y + 14
        if name:
            self.create_text(x + 20, ty, text=name, anchor="nw", fill=it.get("name_color", T.LINK), font=name_f, tags=tag)
            ty += name_f.metrics("linespace") + 4
        fg = "#ff9aa8" if not it["ok"] else T.TEXT
        for line in lines:
            self.create_text(x + 20, ty, text=line, anchor="nw", fill=fg, font=fnt, tags=tag)
            ty += line_h
        sy = y + bh - 12 - small.metrics("linespace")
        if mine:
            self.create_text(x + bw - 20 - 22, sy, text=stamp, anchor="ne", fill="#cfe0ff", font=small, tags=tag)
            delivered = it.get("delivered")             # two ticks once the other person has received it
            self.create_image(x + bw - 20, sy + small.metrics("linespace") // 2, anchor="e", tags=tag,
                              image=T.icon("check2" if delivered else "check", 17,
                                           "#67e8f9" if delivered else "#9fd0ff", 2.2))
        else:
            self.create_text(x + bw - 20, sy, text=stamp, anchor="ne", fill=T.MUTED, font=small, tags=tag)
        for seq in ("<Button-3>", "<Button-2>"):
            self.tag_bind(tag, seq, lambda e, t=it["text"]: self._popup(e, t))
        return y + bh + 14

    def _popup(self, event, text):
        self._menu_text = text
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._menu_text)
        if self.on_copy:
            self.on_copy()


# --------------------------------------------------------------------------- #
# scrolling frame + toast
# --------------------------------------------------------------------------- #
class ScrollFrame(tk.Frame):
    """A vertically scrolling container: put widgets into ``.body``."""

    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._fit)
        bind_wheel(self.canvas, self._wheel)
        bind_wheel(self.body, self._wheel)

    def _fit(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)

    def _wheel(self, event):
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(_wheel_units(event), "units")


class Toast(tk.Canvas):
    """A short message that floats near the bottom of the window and fades away."""

    def __init__(self, parent):
        super().__init__(parent, height=46, bg=T.BG, highlightthickness=0, bd=0)
        self._after = None

    def show(self, text, bad=False, ms=3200):
        fnt = T.font(14, "semibold")
        w = min(fnt.measure(text) + 70, max(260, self.master.winfo_width() - 40))
        text = T.ellipsize(fnt, text, w - 60)
        self.configure(width=w)
        self.delete("all")
        edge = "#7a2336" if bad else "#1d4fb3"
        fill = "#2a0f1d" if bad else "#0b2150"
        img = T.photo(("toast", w, fill, edge), lambda: images.rounded_rect(w, 46, 23, fill, outline=edge, ow=1))
        self.create_image(0, 0, image=img, anchor="nw")
        self.create_image(26, 23, image=T.icon("x" if bad else "check", 16, T.RED if bad else T.BLUE_HI, 2.4))
        self.create_text(46, 23, text=text, anchor="w", fill=T.TEXT, font=fnt)
        self.place(relx=0.5, rely=1.0, y=-26, anchor="s")
        self.tk.call("raise", self._w)
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(ms, self.hide)

    def hide(self):
        self._after = None
        self.place_forget()
