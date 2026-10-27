"""Dark/light Qt styling and local display preferences."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QScrollArea, QWidget, QVBoxLayout
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


def apply_title_bar(widget, name: str):
    """Match the native Windows caption to the selected palette when supported."""
    if os.name != "nt":
        return
    try:
        hwnd = ctypes.c_void_p(int(widget.winId()))
        set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        set_attribute.restype = ctypes.c_long
        dark = ctypes.c_int(name == "Dark")
        set_attribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        # Windows 11 supports explicit caption, border and text colors; Windows 10
        # ignores these attributes and uses its native immersive dark caption.
        def colorref(color):
            return int(color[5:7] + color[3:5] + color[1:3], 16)
        palette = PALETTES[name]
        for attribute, color in ((34, palette["bg"]), (35, palette["bg"]), (36, palette["text"])):
            value = ctypes.c_uint(colorref(color))
            set_attribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (AttributeError, OSError):
        pass


def load_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(path: Path, **changes):
    data = load_settings(path)
    data.update(changes)
    data.setdefault("schema", 1)
    write_json(path, data)


class ScrollPane(QScrollArea):
    def __init__(self, parent=None, fill_height=False):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.content = QWidget()
        self.content.setLayout(QVBoxLayout())
        self.content.layout().setContentsMargins(0, 0, 0, 0)
        self.setWidget(self.content)


class Theme:
    def __init__(self, root, settings_path: Path):
        self.root = root
        self.settings_path = settings_path
        saved = load_settings(settings_path).get("theme")
        self.name = saved if saved in PALETTES else "Dark"
        self.apply(self.name)

    def apply(self, name: str):
        if name not in PALETTES:
            raise ValueError("Choose Dark or Light.")
        self.name = name
        p = PALETTES[name]
        QApplication.instance().setStyleSheet(f"""
            QWidget {{ background: {p['bg']}; color: {p['text']}; font: 9pt 'Segoe UI'; }}
            QMainWindow, QDialog {{ background: {p['bg']}; }}
            QLabel {{ background: transparent; }}
            QLabel#title {{ font-size: 15pt; font-weight: 700; }}
            QLabel#dialogTitle {{ font-size: 11pt; font-weight: 700; }}
            QLabel#section {{ font-size: 14pt; font-weight: 700; }}
            QLabel#muted {{ color: {p['muted']}; }}
            QLabel#kicker {{ color: {p['accent'] if name == 'Dark' else '#426900'}; font-size: 9pt; font-weight: 700; }}
            QFrame#card {{ background: {p['panel']}; border: 1px solid {p['border']}; border-radius: 10px; }}
            QPushButton {{ background: {p['raised']}; color: {p['text']}; border: 1px solid {p['border']};
                border-radius: 7px; padding: 7px 12px; font-weight: 600; }}
            QPushButton:hover {{ background: {p['hover']}; }}
            QPushButton:focus {{ border: 1px solid {p['accent']}; }}
            QPushButton:disabled {{ color: {p['muted']}; background: {p['panel']}; }}
            QPushButton#accent {{ background: {p['accent']}; color: #112000; border: 0; }}
            QPushButton#accent:hover {{ background: #8dce20; }}
            QPushButton#accent:disabled {{ background: {p['raised']}; color: {p['muted']}; }}
            QPushButton#danger {{ color: {p['danger']}; }}
            QPushButton#windowControl, QPushButton#windowClose {{ background: transparent; border: 0;
                border-radius: 5px; padding: 0; font-size: 15pt; font-weight: 400; }}
            QPushButton#windowControl:hover {{ background: {p['hover']}; }}
            QPushButton#windowClose:hover {{ background: {p['danger']}; color: {p['bg']}; }}
            QSplitter::handle {{ background: {p['border']}; margin: 2px 0; }}
            QPushButton#profileCard {{ text-align: left; padding: 6px 12px; background: {p['panel']}; }}
            QPushButton#profileCard:checked {{ background: {p['selected']}; border: 2px solid {p['accent']}; }}
            QTabWidget::pane {{ border: 0; top: 0; }}
            QTabBar::tab {{ background: {p['raised']}; color: {p['text']}; border: 0;
                border-radius: 7px; margin-right: 6px; padding: 8px 18px; font-weight: 600; }}
            QTabBar::tab:selected {{ background: {p['accent']}; color: #112000; }}
            QTabBar::tab:hover:!selected {{ background: {p['hover']}; }}
            QLineEdit, QComboBox, QTextEdit, QPlainTextEdit, QTableWidget {{ background: {p['field']};
                color: {p['text']}; border: 1px solid {p['border']}; border-radius: 7px; padding: 7px;
                selection-background-color: {p['selected']}; selection-color: {p['text']}; }}
            QComboBox::drop-down {{ border: 0; width: 22px; }}
            QComboBox QAbstractItemView, QMenu {{ background: {p['panel']}; color: {p['text']};
                border: 1px solid {p['border']}; selection-background-color: {p['selected']}; }}
            QMenu::item {{ padding: 8px 24px; }}
            QMenu::item:selected {{ background: {p['selected']}; }}
            QHeaderView::section {{ background: {p['raised']}; color: {p['muted']}; padding: 9px;
                border: 0; font-weight: 700; }}
            QTableWidget {{ gridline-color: {p['border']}; }}
            QTableWidget::item {{ padding: 6px; }}
            QTableWidget::item:selected {{ background: {p['selected']}; color: {p['text']}; }}
            QCheckBox, QRadioButton {{ spacing: 8px; padding: 4px; }}
            QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px;
                border: 1px solid {p['border']}; background: {p['field']}; }}
            QCheckBox::indicator {{ border-radius: 4px; }}
            QRadioButton::indicator {{ border-radius: 8px; }}
            QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}
            QScrollBar:vertical {{ background: {p['bg']}; width: 10px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {p['border']}; border-radius: 5px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QProgressBar {{ border: 0; border-radius: 3px; background: {p['raised']}; height: 6px; }}
            QProgressBar::chunk {{ border-radius: 3px; background: {p['accent']}; }}
        """)

    def save(self):
        save_settings(self.settings_path, theme=self.name)
