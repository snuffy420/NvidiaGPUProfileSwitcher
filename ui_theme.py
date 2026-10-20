"""Standard-library dark/light styling and local display preferences."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk

from driver_profiles import write_json

PALETTES = {
    "Dark": dict(bg="#0f1217", panel="#171c24", raised="#222a35", hover="#2e3946",
                 text="#eef2f7", muted="#9ba9ba", border="#303a47", accent="#76b900",
                 selected="#294315", danger="#ff8992", field="#12171e"),
    "Light": dict(bg="#f1f4f8", panel="#ffffff", raised="#e4eaf0", hover="#d7e1eb",
                  text="#172434", muted="#526477", border="#cbd5df", accent="#76b900",
                  selected="#def0bf", danger="#b42336", field="#ffffff"),
}


def enable_dpi_awareness():
    if os.name == "nt":
        try:
            function = ctypes.windll.user32.SetProcessDpiAwarenessContext
            function.argtypes = [ctypes.c_void_p]
            function.restype = ctypes.c_bool
            function(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass


class ScrollPane(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.content = ttk.Frame(self.canvas)
        window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window, width=event.width))
        self.bind_all("<MouseWheel>", self.wheel, add="+")

    def wheel(self, event):
        widget = event.widget
        while widget is not None:
            if widget is self:
                if self.content.winfo_height() > self.canvas.winfo_height():
                    self.canvas.yview_scroll(int(-event.delta / 120), "units")
                return
            widget = getattr(widget, "master", None)


class Theme:
    def __init__(self, root: tk.Tk, settings_path: Path):
        self.root = root
        self.settings_path = settings_path
        self.name = "Dark"
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("theme") in PALETTES:
                self.name = data["theme"]
        except (OSError, ValueError):
            pass
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self.apply(self.name)

    def apply(self, name: str):
        if name not in PALETTES:
            raise ValueError("Choose Dark or Light.")
        self.name = name
        p = PALETTES[name]
        s = self.style
        self.root.configure(background=p["bg"])
        s.configure(".", background=p["bg"], foreground=p["text"], font=("Segoe UI", 10),
                    bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"])
        s.configure("TFrame", background=p["bg"])
        s.configure("Card.TFrame", background=p["panel"], borderwidth=1, relief="solid")
        s.configure("TLabel", background=p["bg"], foreground=p["text"])
        s.configure("Muted.TLabel", foreground=p["muted"])
        s.configure("Title.TLabel", font=("Segoe UI", 21, "bold"))
        s.configure("Section.TLabel", font=("Segoe UI", 16, "bold"))
        s.configure("Card.TLabel", background=p["panel"], foreground=p["text"])
        s.configure("CardTitle.TLabel", background=p["panel"], foreground=p["text"], font=("Segoe UI", 15, "bold"))
        s.configure("CardMuted.TLabel", background=p["panel"], foreground=p["muted"])
        s.configure("Kicker.TLabel", background=p["panel"], foreground=p["accent"] if name == "Dark" else "#426900",
                    font=("Segoe UI", 9, "bold"))
        s.configure("Badge.TLabel", background=p["raised"], foreground=p["muted"], padding=(10, 5), font=("Segoe UI", 9))
        s.configure("Logo.TLabel", background=p["bg"], padding=0)
        s.configure("TButton", background=p["raised"], foreground=p["text"], borderwidth=1,
                    padding=(11, 8), relief="flat", focusthickness=1, focuscolor=p["accent"])
        s.map("TButton", background=[("disabled", p["panel"]), ("pressed", p["hover"]), ("active", p["hover"])],
              foreground=[("disabled", p["muted"])], bordercolor=[("focus", p["accent"])])
        s.configure("Accent.TButton", background=p["accent"], foreground="#112000", font=("Segoe UI", 10, "bold"))
        s.map("Accent.TButton", background=[("disabled", p["raised"]), ("active", "#8dce20"), ("pressed", "#69a300")],
              foreground=[("disabled", p["muted"]), ("!disabled", "#112000")])
        s.configure("Danger.TButton", foreground=p["danger"])
        s.configure("TMenubutton", background=p["raised"], foreground=p["text"], padding=(11, 8),
                    arrowcolor=p["muted"], borderwidth=1, relief="flat")
        s.map("TMenubutton", background=[("active", p["hover"])], foreground=[("disabled", p["muted"])])
        s.configure("TNotebook", background=p["bg"], borderwidth=0, tabmargins=(0, 8, 0, 0))
        s.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"], padding=(20, 11), borderwidth=0,
                    font=("Segoe UI", 10, "bold"))
        s.map("TNotebook.Tab", background=[("selected", p["panel"]), ("active", p["raised"])],
              foreground=[("selected", p["text"]), ("active", p["text"])])
        s.configure("Treeview", background=p["panel"], fieldbackground=p["panel"], foreground=p["text"],
                    borderwidth=0, rowheight=35)
        s.configure("Treeview.Heading", background=p["raised"], foreground=p["muted"],
                    padding=(10, 9), font=("Segoe UI", 9, "bold"), relief="flat")
        s.map("Treeview", background=[("selected", p["selected"])], foreground=[("selected", p["text"])])
        s.map("Treeview.Heading", background=[("active", p["hover"])])
        for widget in ("TCombobox", "TEntry"):
            s.configure(widget, fieldbackground=p["field"], background=p["raised"], foreground=p["text"],
                        arrowcolor=p["text"], insertcolor=p["text"], padding=7)
            s.map(widget, fieldbackground=[("readonly", p["field"]), ("disabled", p["panel"])],
                  foreground=[("disabled", p["muted"]), ("readonly", p["text"])],
                  selectbackground=[("!disabled", p["selected"])], selectforeground=[("!disabled", p["text"])])
        s.configure("TCheckbutton", background=p["bg"], foreground=p["text"], padding=4)
        s.configure("TRadiobutton", background=p["bg"], foreground=p["text"], padding=4)
        for widget in ("TCheckbutton", "TRadiobutton"):
            s.map(widget, background=[("active", p["bg"])], indicatorbackground=[("selected", p["accent"])])
        s.configure("TScrollbar", background=p["raised"], troughcolor=p["bg"], arrowcolor=p["muted"], borderwidth=0)
        s.configure("TSeparator", background=p["border"])
        s.configure("TProgressbar", troughcolor=p["panel"], background=p["accent"], borderwidth=0)
        for key, value in {"*Text.background": p["panel"], "*Text.foreground": p["text"],
                           "*Text.insertBackground": p["text"], "*Text.selectBackground": p["selected"],
                           "*Listbox.background": p["panel"], "*Listbox.foreground": p["text"],
                           "*Listbox.selectBackground": p["selected"], "*Listbox.selectForeground": p["text"]}.items():
            self.root.option_add(key, value)
        self.refresh_widgets(self.root)

    def refresh_widgets(self, parent):
        p = PALETTES[self.name]
        if isinstance(parent, (tk.Tk, tk.Toplevel)):
            parent.configure(background=p["bg"])
            if os.name == "nt":
                try:
                    value = ctypes.c_int(self.name == "Dark")
                    handle = ctypes.windll.user32.GetParent(parent.winfo_id())
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(handle, 20, ctypes.byref(value), ctypes.sizeof(value))
                except (AttributeError, OSError):
                    pass
        elif isinstance(parent, tk.Text):
            parent.configure(background=p["panel"], foreground=p["text"], insertbackground=p["text"],
                             selectbackground=p["selected"], selectforeground=p["text"], relief="flat",
                             borderwidth=0, highlightthickness=1, highlightbackground=p["border"],
                             highlightcolor=p["accent"], padx=14, pady=12)
        elif isinstance(parent, tk.Menu):
            parent.configure(background=p["panel"], foreground=p["text"], activebackground=p["selected"],
                             activeforeground=p["text"], disabledforeground=p["muted"], borderwidth=0,
                             font=("Segoe UI", 10))
        elif isinstance(parent, tk.Canvas):
            parent.configure(background=p["bg"])
        for child in parent.winfo_children():
            self.refresh_widgets(child)

    def save(self):
        write_json(self.settings_path, {"schema": 1, "theme": self.name})
