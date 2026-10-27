"""Qt interface for NVIDIA GPU Driver Profile Switcher."""
from __future__ import annotations

from dataclasses import asdict, replace
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import webbrowser

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDialog, QFileDialog as QtFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox as QtMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QSizePolicy, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

import cached_drivers as cache
import driver_updates
import gpu_manager as gm
from driver_profiles import (DriverProfile, capture_working_profile, is_nvidia, nvidia_version,
                             save_profiles, verify_profile, write_json)
from gpu_errors import PROBLEMS, SOURCE as ERROR_SOURCE, problem_details, problem_info
from ui_theme import ScrollPane, Theme, apply_title_bar


def label(text, style=None, wrap=False):
    widget = QLabel(text)
    widget.setWordWrap(wrap)
    if style:
        widget.setObjectName(style)
    return widget


def button(text, callback, style=None):
    widget = QPushButton(text)
    widget.clicked.connect(lambda _checked=False: callback())
    if style:
        widget.setObjectName(style)
    return widget


def text_view(height=None):
    view = QPlainTextEdit()
    view.setReadOnly(True)
    if height:
        view.setMinimumHeight(height)
    return view


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setShowGrid(False)
    widget.verticalHeader().setVisible(False)
    widget.verticalHeader().setDefaultSectionSize(38)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return widget


class OperationDialog(QDialog):
    """Frameless dialog which cannot be dismissed during a save."""
    saving = False

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.windowHandle():
            position = event.position().toPoint()
            edges = Qt.Edge(0)
            if position.x() < 8:
                edges |= Qt.Edge.LeftEdge
            elif position.x() >= self.width() - 8:
                edges |= Qt.Edge.RightEdge
            if position.y() < 8:
                edges |= Qt.Edge.TopEdge
            elif position.y() >= self.height() - 8:
                edges |= Qt.Edge.BottomEdge
            if edges and self.windowHandle().startSystemResize(edges):
                event.accept()
                return
        super().mousePressEvent(event)

    def reject(self):
        if not self.saving:
            super().reject()

    def closeEvent(self, event):
        if self.saving:
            event.ignore()
        else:
            super().closeEvent(event)


def dialog(parent, title, width=700, height=500):
    window = OperationDialog(parent)
    window.setWindowTitle(title)
    screen = (parent.screen() if parent is not None else QApplication.primaryScreen()) or QApplication.primaryScreen()
    available = screen.availableGeometry()
    window.resize(min(width, available.width() - 32), min(height, available.height() - 32))
    window.setModal(True)
    outer = QVBoxLayout(window)
    outer.setContentsMargins(16, 10, 16, 16)
    outer.setSpacing(8)
    header = WindowHeader(window)
    header_layout = QHBoxLayout(header)
    header_layout.setContentsMargins(0, 0, 0, 0)
    header_layout.addWidget(label(title, "dialogTitle"), 1)
    close = button("×", window.reject, "windowClose")
    close.setFixedSize(34, 30)
    close.setToolTip("Close")
    header_layout.addWidget(close)
    outer.addWidget(header)
    layout = QVBoxLayout()
    layout.setSpacing(8)
    outer.addLayout(layout, 1)
    return window, layout


def confirm(parent, title, message):
    return QMessageBox.question(parent, title, message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


class QMessageBox:
    """Themed application messages and safety confirmations."""
    StandardButton = QtMessageBox.StandardButton

    @staticmethod
    def _show(parent, title, message, question=False):
        window, layout = dialog(parent, title, 640, 280)
        view = text_view(90)
        view.setPlainText(message)
        layout.addWidget(view, 1)
        row = QHBoxLayout()
        row.addStretch()
        if question:
            no = button("No", window.reject)
            row.addWidget(no)
            yes = button("Yes", window.accept, "accent")
            row.addWidget(yes)
            no.setFocus()
        else:
            row.addWidget(button("OK", window.accept, "accent"))
        layout.addLayout(row)
        return window.exec() == QDialog.DialogCode.Accepted

    @classmethod
    def question(cls, parent, title, message, *_args):
        return cls.StandardButton.Yes if cls._show(parent, title, message, True) else cls.StandardButton.No

    @classmethod
    def information(cls, parent, title, message):
        cls._show(parent, title, message)

    @classmethod
    def critical(cls, parent, title, message):
        cls._show(parent, title, message)


class QFileDialog:
    """Use Qt's non-native picker inside the same integrated dialog frame."""
    @staticmethod
    def _show(parent, title, directory, file_filter="", mode="open"):
        window, layout = dialog(parent, title, 900, 620)
        picker = QtFileDialog(window, title, directory, file_filter)
        picker.setOption(QtFileDialog.Option.DontUseNativeDialog, True)
        picker.setWindowFlags(Qt.WindowType.Widget)
        if mode == "directory":
            picker.setFileMode(QtFileDialog.FileMode.Directory)
            picker.setOption(QtFileDialog.Option.ShowDirsOnly, True)
        elif mode == "save":
            picker.setAcceptMode(QtFileDialog.AcceptMode.AcceptSave)
            picker.setFileMode(QtFileDialog.FileMode.AnyFile)
            if file_filter == "JSON (*.json)":
                picker.setDefaultSuffix("json")
        else:
            picker.setFileMode(QtFileDialog.FileMode.ExistingFile)
        picker.accepted.connect(window.accept)
        picker.rejected.connect(window.reject)
        layout.addWidget(picker)
        if window.exec() != QDialog.DialogCode.Accepted:
            return "", ""
        files = picker.selectedFiles()
        return (files[0] if files else ""), picker.selectedNameFilter()

    @staticmethod
    def getOpenFileName(parent, title, directory="", file_filter=""):
        return QFileDialog._show(parent, title, directory, file_filter)

    @staticmethod
    def getSaveFileName(parent, title, directory="", file_filter=""):
        return QFileDialog._show(parent, title, directory, file_filter, "save")

    @staticmethod
    def getExistingDirectory(parent, title, directory=""):
        return QFileDialog._show(parent, title, directory, mode="directory")[0]


class WindowHeader(QWidget):
    """Integrated title area with native Qt window move and resize behavior."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window.windowHandle():
            self.window.windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and hasattr(self.window, "toggle_maximize"):
            self.window.toggle_maximize()
        super().mouseDoubleClickEvent(event)


class App(QMainWindow):
    def __init__(self):
        self._application = QApplication.instance() or QApplication(sys.argv[:1])
        self._application.setApplicationName(gm.APP_NAME)
        self._application.setApplicationVersion(gm.APP_VERSION)
        super().__init__()
        icon = QIcon(str(gm.RESOURCE_DIR / "assets" / "app-icon.ico"))
        self._application.setWindowIcon(icon)
        self.setWindowIcon(icon)
        if os.name == "nt":
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("zer0software.NvidiaGPUProfileSwitcher")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(f"{gm.APP_NAME} v{gm.APP_VERSION}")
        screen = self._application.primaryScreen().availableGeometry()
        width = min(1320, screen.width())
        height = min(920, screen.height())
        self.setMinimumSize(min(900, width), min(650, height))
        self.resize(width, height)
        self.move(screen.center() - self.rect().center())
        self.theme = Theme(self, gm.SETTINGS_FILE)
        apply_title_bar(self, self.theme.name)
        self.devices = []
        self.events = queue.Queue()
        self.busy = False
        self.profiles = []
        self.profile_load_error = None
        self.dialogs = []
        try:
            self.profiles = gm.load_profiles(gm.PROFILES_FILE)
        except Exception as error:
            self.profile_load_error = str(error)
        gm.configure_cache(gm.SETTINGS_FILE)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(5)
        self.header = WindowHeader(self)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        self.logo_label = QLabel()
        header.addWidget(self.logo_label)
        header.addWidget(label("GPU Driver Profile Switcher", "title"))
        header.addWidget(label(f"v{gm.APP_VERSION}", "muted"))
        header.addStretch(1)
        self.minimize_button = button("−", self.showMinimized, "windowControl")
        self.maximize_button = button("□", self.toggle_maximize, "windowControl")
        self.close_button = button("×", self.close, "windowClose")
        for control, tip in ((self.minimize_button, "Minimize"), (self.maximize_button, "Maximize or restore"), (self.close_button, "Close")):
            control.setToolTip(tip)
            control.setFixedSize(38, 36)
            header.addWidget(control, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.header)
        self.load_logo()
        status_row = QHBoxLayout()
        status_row.addWidget(label("Administrator: device controls available" if gm.is_admin() else "Preview mode · Device changes require administrator", "muted"), 1)
        self.admin_button = button("Restart as administrator", self.elevate)
        status_row.addWidget(self.admin_button)
        layout.addLayout(status_row)
        self.notebook = QTabWidget()
        layout.addWidget(self.notebook, 1)
        self.build_gpu_tab()
        self.build_driver_tab()
        self.build_error_tab()
        self.notebook.setCurrentIndex(1)
        self.notebook.currentChanged.connect(lambda index: self.driver_top_actions.setVisible(index == 1))
        footer = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(100)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        footer.addWidget(self.progress)
        self.status = label("Ready", "muted", True)
        footer.addWidget(self.status, 1)
        footer.addWidget(label("Appearance", "muted"))
        self.theme_choice = QComboBox()
        self.theme_choice.addItems(["Dark", "Light"])
        self.theme_choice.setCurrentText(self.theme.name)
        self.theme_choice.currentTextChanged.connect(self.change_theme)
        footer.addWidget(self.theme_choice)
        layout.addLayout(footer)
        self.reload_profile_choices()
        if gm.PENDING_FILE.exists():
            try:
                pending = json.loads(gm.PENDING_FILE.read_text(encoding="utf-8"))
                self.select_profile(pending.get("profile", ""))
                self.status.setText(f"Previous switch: {pending.get('profile', 'unknown')} / {pending.get('state', 'verification pending')}. Finish setup and restart, then verify.")
            except (OSError, ValueError, TypeError, AttributeError):
                self.status.setText("Previous switch record could not be read. Refresh and verify your loaded drivers.")
        if self.profile_load_error:
            self.append_log("Profile file could not be loaded; it was preserved. " + self.profile_load_error)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        self.refresh()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
            position = event.position().toPoint()
            margin = 8
            edges = Qt.Edge(0)
            if position.x() < margin:
                edges |= Qt.Edge.LeftEdge
            elif position.x() >= self.width() - margin:
                edges |= Qt.Edge.RightEdge
            if position.y() < margin:
                edges |= Qt.Edge.TopEdge
            elif position.y() >= self.height() - margin:
                edges |= Qt.Edge.BottomEdge
            if edges and self.windowHandle() and self.windowHandle().startSystemResize(edges):
                event.accept()
                return
        super().mousePressEvent(event)

    def change_theme(self, name):
        self.theme.apply(name)
        apply_title_bar(self, name)
        self.load_logo()
        try:
            self.theme.save()
        except OSError as error:
            self.append_log(f"Theme applied, but its preference could not be saved: {error}")

    def load_logo(self):
        pixmap = QPixmap(str(gm.RESOURCE_DIR / "assets" / f"nvidia-logo-{self.theme.name.lower()}.png"))
        self.logo_label.setPixmap(pixmap.scaled(115, 32, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def open_dialog(self, window):
        self.dialogs.append(window)
        window.finished.connect(lambda _result: self.dialogs.remove(window) if window in self.dialogs else None)
        window.open()

    def build_gpu_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(label("GPU inventory", "section"))
        layout.addWidget(label("Move monitor cables to another working GPU before disabling an adapter. Disable keeps the device installed; GPU numbering does not identify the primary desktop.", "muted", True))
        bar = QHBoxLayout()
        self.refresh_button = button("Refresh GPUs", self.refresh)
        self.export_button = button("Export diagnosis", self.export)
        for widget in (self.refresh_button, self.export_button):
            bar.addWidget(widget)
        bar.addStretch()
        bar.addWidget(button("NVIDIA drivers", lambda: webbrowser.open("https://www.nvidia.com/en-us/drivers/")))
        bar.addWidget(button("Display settings", lambda: os.startfile("ms-settings:display")))
        layout.addLayout(bar)
        panels = QSplitter(Qt.Orientation.Vertical)
        panels.setChildrenCollapsible(False)
        self.tree = table(["GPU", "Present", "Windows state", "Driver version", "Desktop connection"])
        self.tree.setMinimumHeight(100)
        self.tree.itemSelectionChanged.connect(self.selection_changed)
        panels.addWidget(self.tree)
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(6)
        self.details = text_view(60)
        self.details.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        detail_layout.addWidget(self.details, 1)
        controls = QHBoxLayout()
        self.disable_button = button("Disable selected GPU...", lambda: self.prepare("disable"), "danger")
        self.enable_button = button("Enable selected GPU...", lambda: self.prepare("enable"))
        controls.addWidget(self.disable_button)
        controls.addWidget(self.enable_button)
        controls.addStretch()
        detail_layout.addLayout(controls)
        detail_layout.addWidget(label("A recovery CMD file is saved before disabling. Desktop connection can be unknown; confirm monitor cabling yourself. Driver installers can still change device state.", "muted", True))
        detail_panel.setMinimumHeight(185)
        panels.addWidget(detail_panel)
        self.log = text_view(40)
        panels.addWidget(self.log)
        panels.setStretchFactor(0, 4)
        panels.setStretchFactor(1, 4)
        panels.setStretchFactor(2, 2)
        panels.setSizes([280, 270, 120])
        layout.addWidget(panels, 1)
        self.notebook.addTab(page, "GPUs")

    def build_driver_tab(self):
        scroll = ScrollPane()
        frame = scroll.content.layout()
        frame.setContentsMargins(0, 8, 4, 0)
        frame.setSpacing(6)
        self.cards = QGridLayout()
        self.cards.setHorizontalSpacing(8)
        self.cards.setVerticalSpacing(8)
        self.cards.setColumnStretch(0, 1)
        self.cards.setColumnStretch(1, 1)
        frame.addLayout(self.cards)
        self.selected_name = ""
        self.reenable_check = QCheckBox("Re-enable this profile's GPUs if they are disabled (Code 22)")
        self.reenable_check.setChecked(True)
        frame.addWidget(self.reenable_check)
        actions = QHBoxLayout()
        self.cached_switch_button = button("Switch using cached package...", self.launch_cached_profile, "accent")
        self.install_profile_button = button("Use full installer...", self.launch_profile)
        self.verify_profile_button = button("Verify loaded profile", self.verify_selected_profile)
        for widget in (self.cached_switch_button, self.install_profile_button, self.verify_profile_button):
            actions.addWidget(widget)
        self.driver_top_actions = QWidget()
        top_actions = QHBoxLayout(self.driver_top_actions)
        top_actions.setContentsMargins(0, 0, 0, 0)
        top_actions.setSpacing(6)
        self.profile_buttons = [button("Check NVIDIA updates...", self.check_driver_updates),
                                button("Cache NVIDIA installer (.exe)...", self.cache_nvidia_installer)]
        for widget in self.profile_buttons:
            top_actions.addWidget(widget)
        self.notebook.setCornerWidget(self.driver_top_actions, Qt.Corner.TopRightCorner)
        for title, entries in (
            ("Manage profiles", [("Capture working drivers...", self.capture_profile), ("Add profile...", self.edit_profile),
                                 ("View selected details...", self.show_profile_details), ("Edit selected...", lambda: self.edit_profile(self.current_profile())),
                                 ("Remove profile...", self.remove_profile)]),
            ("More cache options", [("Cache saved profiles", self.cache_saved_profiles), ("Cache installed version...", self.cache_installed_version),
                                    ("Import extracted INF...", self.import_cached_inf)])):
            control = QPushButton(title)
            menu = QMenu(control)
            for name, callback in entries:
                menu.addAction(name, lambda _checked=False, callback=callback: callback())
            control.setMenu(menu)
            self.profile_buttons.append(control)
            actions.addWidget(control)
        actions.addStretch()
        frame.addLayout(actions)
        cache_row = QHBoxLayout()
        self.cache_location = label("", "muted")
        self.cache_location.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        cache_row.addWidget(self.cache_location, 1)
        self.cache_manager_button = button("Manage caches...", self.manage_caches)
        cache_row.addWidget(self.cache_manager_button)
        self.cache_folder_button = button("Change cache folder...", self.change_cache_folder)
        cache_row.addWidget(self.cache_folder_button)
        frame.addLayout(cache_row)
        self.profile_detail = text_view(160)
        frame.addWidget(self.profile_detail, 1)
        self.notebook.addTab(scroll, "Driver library")

    def current_profile(self):
        return next((p for p in self.profiles if p.name == self.selected_name), None)

    def select_profile(self, name):
        if not self.busy and any(p.name == name for p in self.profiles):
            self.selected_name = name
            self.profile_summary()

    def reload_profile_choices(self, selected=None):
        names = [p.name for p in self.profiles]
        current = selected or self.selected_name
        self.selected_name = current if current in names else names[0] if names else ""
        while self.cards.count():
            item = self.cards.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)
                widget.deleteLater()
        self.profile_cards = {}
        if not self.profiles:
            self.cards.addWidget(label("No saved profiles. Cache an installer or capture working drivers to get started.", "muted", True), 0, 0, 1, 2)
        for index, profile in enumerate(self.profiles):
            kind = "MULTI-GPU" if len(profile.required_gpu_ids) > 1 else "SINGLE GPU"
            card = button(f"{kind}\n{profile.name}\nDriver {nvidia_version(profile.expected_version)}  ·  {'Cached' if profile.cache_manifest else 'Not cached'}  ·  {len(profile.required_gpu_ids)} enabled / {len(profile.disabled_gpu_ids)} disabled", lambda checked=False, name=profile.name: self.select_profile(name))
            card.setObjectName("profileCard")
            card.setCheckable(True)
            self.cards.addWidget(card, index // 2, index % 2, 1, 2 if len(self.profiles) == 1 else 1)
            self.profile_cards[profile.name] = card
        self.profile_summary()

    def profile_controls(self):
        profile = self.current_profile()
        for control in self.profile_buttons:
            control.setEnabled(not self.busy)
        for name, card in self.profile_cards.items():
            card.setEnabled(not self.busy)
            card.setChecked(bool(profile and profile.name == name))
        self.reenable_check.setEnabled(not self.busy)
        self.cache_folder_button.setEnabled(not self.busy)
        self.cache_manager_button.setEnabled(not self.busy)
        self.admin_button.setEnabled(not self.busy and not gm.is_admin())
        self.install_profile_button.setEnabled(bool(not self.busy and gm.is_admin() and profile and profile.installer and profile.sha256))
        self.cached_switch_button.setEnabled(bool(not self.busy and gm.is_admin() and profile and profile.cache_manifest))
        self.verify_profile_button.setEnabled(bool(not self.busy and profile))
        self.cache_location.setText(f"Driver cache: {gm.CACHE_ROOT}")
        self.cache_location.setToolTip(str(gm.CACHE_ROOT))

    def profile_information(self, profile):
        devices = {gpu.instance_id.upper(): gpu for gpu in self.devices}
        def describe(device_id):
            gpu = devices.get(device_id.upper())
            if gpu is None:
                return f"{device_id}\n  Not detected in the current inventory."
            return (f"{gpu.name}\n  Instance ID: {device_id}\n  Current driver: {self.version_label(gpu)} | Windows: {gpu.driver_version or 'Unknown'}\n"
                    f"  Driver INF: {gpu.driver_inf or 'Unknown'} | State: {gpu.state}\n  Location: {gpu.location or 'Unknown'}")
        return (f"Profile: {profile.name}\nExpected NVIDIA driver: {nvidia_version(profile.expected_version)}\n"
                f"Cached package: {profile.cache_manifest or 'Not cached — cache an NVIDIA installer or installed version'}\n"
                f"Full installer fallback: {profile.installer or 'Not attached'}\n\nGPUs required to work:\n"
                + "\n".join(map(describe, profile.required_gpu_ids)) + "\n\nGPUs saved as disabled:\n"
                + ("\n".join(map(describe, profile.disabled_gpu_ids)) or "None"))

    def profile_summary(self):
        profile = self.current_profile()
        self.profile_detail.setPlainText(self.profile_information(profile) if profile else "Cache an NVIDIA installer or capture your working drivers to create a profile.")
        self.profile_controls()

    def show_profile_details(self):
        profile = self.current_profile()
        if not profile:
            return
        window, layout = dialog(self, "Profile details", 800, 500)
        view = text_view()
        view.setPlainText(self.profile_information(profile))
        layout.addWidget(view)
        layout.addWidget(button("Close", window.accept))
        self.open_dialog(window)

    def cache_saved_profiles(self):
        if self.busy or self.profile_load_error:
            return
        def done(result):
            self.profiles, report = result
            self.reload_profile_choices()
            self.append_log(report)
            self.status.setText("Cache operation complete. Read the result for unavailable packages.")
            QMessageBox.information(self, "Cached driver packages", report)
        self.start_job("Copying and hashing saved driver versions; active drivers remain unchanged...", lambda: gm.cache_profiles(self.profiles), done)

    def cache_installed_version(self):
        if self.busy or self.profile_load_error:
            return
        def done(devices):
            self.display_inventory(devices)
            cards = [gpu for gpu in devices if is_nvidia(gpu) and gpu.present is True and gpu.problem_code == 0]
            if not cards:
                QMessageBox.critical(self, "No working NVIDIA GPU", "A present NVIDIA GPU must report Code 0 before its installed version can be captured.")
                return
            window, layout = dialog(self, "Cache installed driver version", 650, 230)
            layout.addWidget(label("Choose the GPU whose currently installed version you want to keep:", wrap=True))
            choice = QComboBox()
            choice.addItems([f"{gpu.name} - {self.version_label(gpu)}" for gpu in cards])
            choice.setCurrentIndex(cards.index(gm.preferred_gpu(cards)))
            layout.addWidget(choice)
            layout.addWidget(label("A new version gets its own profile and cache. Older cached versions remain available.", "muted", True))
            def selected():
                gpu = cards[choice.currentIndex()]
                try:
                    version = nvidia_version(gpu.driver_version)
                    targets = [item.instance_id for item in cards if nvidia_version(item.driver_version) == version]
                except ValueError as error:
                    QMessageBox.critical(window, "Invalid driver version", str(error))
                    return
                profile = next((item for item in self.profiles if nvidia_version(item.expected_version) == version and {key.upper() for key in item.required_gpu_ids} == {key.upper() for key in targets}), None)
                if profile is None:
                    base = ("AI multi-GPU" if len(targets) > 1 else gpu.name) + " " + version
                    name, index = base, 2
                    while any(item.name.casefold() == name.casefold() for item in self.profiles):
                        name, index = f"{base} {index}", index + 1
                    profile = DriverProfile(name, version, required_gpu_ids=targets)
                profile = replace(profile, disabled_gpu_ids=[item.instance_id for item in devices if is_nvidia(item) and item.present is True and item.problem_code == 22 and item.instance_id.upper() not in {key.upper() for key in targets}])
                window.accept()
                def job():
                    if not verify_profile(profile, gm.inventory())[0]:
                        raise RuntimeError("The selected GPU/driver state changed. Refresh and capture it again.")
                    manifest = cache.cache_profile(profile, gm.CACHE_ROOT)
                    updated = replace(profile, cache_manifest=str(manifest))
                    profiles = [updated if item.name == profile.name else item for item in self.profiles]
                    if not any(item.name == profile.name for item in profiles):
                        profiles.append(updated)
                    save_profiles(gm.PROFILES_FILE, profiles)
                    return profiles, updated
                def saved(result):
                    self.profiles, updated = result
                    self.reload_profile_choices(updated.name)
                    self.status.setText(f"Cached installed version {version}. No driver was switched.")
                    self.append_log(f"Saved installed-version cache: {updated.name}\n{updated.cache_manifest}")
                self.start_job("Caching the installed driver version...", job, saved)
            layout.addWidget(button("Cache this installed version", selected, "accent"))
            self.open_dialog(window)
        self.start_job("Reading installed NVIDIA driver versions...", gm.inventory, done)

    def check_driver_updates(self):
        if self.busy or self.profile_load_error:
            return
        def job():
            import ctypes
            if ctypes.sizeof(ctypes.c_void_p) != 8:
                raise RuntimeError("Driver update checks require 64-bit Python on Windows 10/11 x64.")
            devices = gm.inventory()
            os_id = 135 if sys.getwindowsversion().build >= 22000 else 57
            results = driver_updates.check_updates(devices, os_id)
            write_json(gm.APP_DIR / "last-driver-update-check.json", {"checked_at": datetime.now().astimezone().isoformat(), "os_id": os_id, "results": [asdict(item) for item in results]})
            return devices, results
        def done(result):
            self.display_inventory(result[0])
            self.show_driver_updates(result[1])
        self.start_job("Checking NVIDIA's official catalogue for every present NVIDIA GPU...", job, done)

    def show_driver_updates(self, results):
        window, layout = dialog(self, "NVIDIA driver updates — choose which GPUs", 950, 650)
        report = []
        for gpu in results:
            report.append(f"{gpu.name} | Installed package: {gpu.installed}" + (" | DEVICE DISABLED" if gpu.disabled else ""))
            if gpu.error:
                report.append("Could not confirm updates: " + gpu.error)
            elif gpu.newer():
                offer = gpu.newer()[0]
                report.append(f"Update available: {offer.version} — {offer.title} — {offer.date}")
            elif gpu.offers:
                report.append(f"No newer compatible WHQL package found. Newest returned: {gpu.offers[0].version}.")
            report.append("")
        view = text_view(120)
        view.setPlainText("\n".join(report) or "No present NVIDIA GPUs were detected.")
        layout.addWidget(view)
        choices = driver_updates.update_choices(results)
        group = QButtonGroup(window)
        for index, choice in enumerate(choices):
            radio = QRadioButton(choice.label)
            group.addButton(radio, index)
            layout.addWidget(radio)
        layout.addWidget(label("Your tested AI profile is kept. A single-GPU update is not a shared multi-GPU upgrade. A common update is offered only when NVIDIA lists the same newer package for every target. Different generations cannot use separate simultaneously active NVIDIA stacks. Unsupported GPUs must be disabled before switching. Selected disabled GPUs are re-enabled only with your approval.", "muted", True))
        row = QHBoxLayout()
        details = button("NVIDIA release details", lambda: webbrowser.open(choices[group.checkedId()].offer.details_url))
        download = button("Download, cache and review switch...", lambda: submit(), "accent")
        details.setEnabled(False)
        download.setEnabled(False)
        group.idToggled.connect(lambda index, checked: (details.setEnabled(group.checkedId() >= 0), download.setEnabled(group.checkedId() >= 0)))
        row.addWidget(details)
        row.addWidget(download)
        row.addStretch()
        row.addWidget(button("Keep current drivers", window.reject))
        layout.addLayout(row)
        def submit():
            if group.checkedId() < 0:
                return
            choice = choices[group.checkedId()]
            prompt = (f"Download NVIDIA {choice.offer.version} and cache a profile for:\n{choice.label}\n\n{choice.offer.download_url}\n\n"
                      "The EXE will not be executed. Signature and compatibility will be checked. Installation requires a separate confirmation.")
            if not confirm(window, "Download and cache update", prompt):
                return
            window.accept()
            def job():
                path = driver_updates.download_offer(choice.offer, gm.APP_DIR / "downloads")
                return gm.cache_downloaded_installer(path, list(choice.device_ids), self.profiles, expected_version=choice.offer.version)
            self.start_job("Downloading from NVIDIA, checking signature, and caching the selected update...", job, lambda result: self.installer_cached(result, True))
        self.append_log("NVIDIA update check:\n" + "\n".join(report))
        self.open_dialog(window)

    def cache_nvidia_installer(self):
        if self.busy or self.profile_load_error:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Cache a downloaded NVIDIA driver EXE (setup will not run)", "", "NVIDIA offline driver installer (*.exe)")
        if not filename:
            return
        def loaded(devices):
            self.display_inventory(devices)
            cards = [gpu for gpu in devices if is_nvidia(gpu) and gpu.present is True]
            if not cards:
                QMessageBox.critical(self, "No NVIDIA GPU", "No present NVIDIA GPU could be found.")
                return
            window, layout = dialog(self, "Cache NVIDIA installer (.exe)", 800, 440)
            layout.addWidget(label(filename, "muted", True))
            layout.addWidget(label("Choose the GPUs for this cached profile:"))
            checks = []
            for gpu in cards:
                check = QCheckBox(f"{gpu.name} — {gpu.state}\n{gpu.instance_id}")
                check.setChecked(gpu.instance_id == gm.preferred_gpu(cards).instance_id)
                layout.addWidget(check)
                checks.append((gpu.instance_id, check))
            layout.addWidget(label("The version is detected from the EXE and selected GPUs are checked for compatibility. Unselected GPUs are excluded. Caching does not install drivers or enable devices. Disabled unselected GPUs are saved as Keep disabled.", "muted", True))
            def submit():
                ids = [key for key, check in checks if check.isChecked()]
                if not ids:
                    QMessageBox.critical(window, "Choose a GPU", "Select at least one GPU for the profile.")
                    return
                window.accept()
                self.start_job("Checking NVIDIA signature, extracting EXE, and caching the compatible display package...", lambda: gm.cache_downloaded_installer(Path(filename), ids, self.profiles), self.installer_cached)
            layout.addWidget(button("Extract and cache — leave active drivers unchanged", submit, "accent"))
            self.open_dialog(window)
        self.start_job("Reading GPUs for the downloaded driver profile...", gm.inventory, loaded)

    def installer_cached(self, result, offer_switch=False):
        self.profiles, profile = result
        self.reload_profile_choices(profile.name)
        self.status.setText(f"Cached NVIDIA {profile.expected_version}. No drivers were installed or GPUs enabled.")
        self.append_log(f"Cached EXE profile: {profile.name}\n{profile.cache_manifest}")
        if offer_switch and gm.is_admin():
            self.launch_cached_profile()
        else:
            QMessageBox.information(self, "NVIDIA EXE cached", f"Saved {profile.name}.\n\nActive drivers are unchanged. Restart as administrator if needed, then click Switch using cached package.")

    def import_cached_inf(self):
        profile = self.current_profile()
        if self.busy or profile is None or self.profile_load_error:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "Choose an INF in the extracted NVIDIA Display.Driver folder", "", "NVIDIA display driver INF (*.inf)")
        if not filename:
            return
        def job():
            source = Path(filename)
            metadata = cache.inf_metadata(source)
            if metadata.version != nvidia_version(profile.expected_version):
                raise ValueError(f"This package is {metadata.version}, but the selected profile expects {profile.expected_version}.")
            cache.preflight(source, metadata.version, profile.required_gpu_ids)
            manifest = cache.cache_package(source, gm.CACHE_ROOT)
            updated = replace(profile, cache_manifest=str(manifest))
            profiles = [updated if item.name == profile.name else item for item in self.profiles]
            save_profiles(gm.PROFILES_FILE, profiles)
            return profiles, updated
        def done(result):
            self.profiles, updated = result
            self.reload_profile_choices(updated.name)
            self.append_log(f"Imported cached package: {updated.cache_manifest}")
            self.status.setText("Extracted driver package cached. Windows validates its signature when staging a switch.")
        self.start_job("Checking and copying the complete extracted package...", job, done)

    def launch_cached_profile(self):
        profile = self.current_profile()
        if self.busy or profile is None or not gm.is_admin():
            return
        reenable = self.reenable_check.isChecked()
        prompt = (f"Switch to cached profile: {profile.name}\nExpected driver: {profile.expected_version}\nCache: {profile.cache_manifest}\n\nExact target devices:\n"
                  + "\n".join(profile.required_gpu_ids) + "\n\nGPUs to disable/keep disabled:\n" + ("\n".join(profile.disabled_gpu_ids) or "None")
                  + f"\n\nRe-enable disabled profile GPUs: {'Yes' if reenable else 'No'}\n\nStop AI jobs and games first. This replaces the active display driver, including a downgrade. Displays may briefly go black. Windows may require a restart; it will not restart automatically. Other NVIDIA GPUs using a different version must be disabled first.")
        if not confirm(self, "Confirm cached driver switch", prompt):
            return
        def done(result):
            devices, report, passed = result
            self.display_inventory(devices)
            self.append_log(report)
            self.status.setText("Cached profile loaded and verified. Test your AI workflow next." if passed else "Cached switch needs attention. Read the result and restart/verify if requested.")
            QMessageBox.information(self, "Cached switch result", report)
        self.start_job("Validating cached files, preserving previous packages, then switching...", lambda: gm.switch_cached_profile(profile, reenable), done)

    def capture_profile(self):
        if self.busy:
            return
        def done(devices):
            self.display_inventory(devices)
            try:
                profile = capture_working_profile(devices)
            except ValueError as error:
                QMessageBox.critical(self, "Cannot capture working drivers", str(error))
                return
            base, index = profile.name, 2
            while any(item.name.casefold() == profile.name.casefold() for item in self.profiles):
                profile = replace(profile, name=f"{base} {index}")
                index += 1
            self.edit_profile(profile, creating=True)
        self.start_job("Reading current working drivers...", gm.inventory, done)

    def edit_profile(self, profile=None, creating=False):
        if self.busy:
            return
        if self.profile_load_error:
            QMessageBox.critical(self, "Profile file needs repair", "The existing profile file was preserved. Repair or rename driver_profiles.json before saving.\n" + self.profile_load_error)
            return
        original = profile if profile and not creating else None
        if profile is None:
            nvidia_gpus = [gpu for gpu in self.devices if is_nvidia(gpu) and gpu.present is True]
            healthy = [gpu for gpu in nvidia_gpus if gpu.problem_code == 0]
            versions = set()
            for gpu in healthy:
                try:
                    versions.add(nvidia_version(gpu.driver_version))
                except ValueError:
                    versions.add("")
            profile = DriverProfile("", next(iter(versions)) if len(versions) == 1 else "", required_gpu_ids=[gpu.instance_id for gpu in healthy], disabled_gpu_ids=[gpu.instance_id for gpu in nvidia_gpus if gpu.problem_code == 22])
        window, outer = dialog(self, "Driver profile", 850, 720)
        scroll = ScrollPane()
        scroll.setMinimumHeight(100)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        outer.addWidget(scroll, 1)
        layout = scroll.content.layout()
        layout.setSpacing(6)
        name = QLineEdit(profile.name)
        version = QLineEdit(profile.expected_version)
        installer = QLineEdit(profile.installer)
        layout.addWidget(label("Profile name"))
        layout.addWidget(name)
        layout.addWidget(label("Driver package"))
        packages = gm.discover_all_cached_packages()
        manual = "Manual / current installed version (no cached package)"
        picker = QComboBox()
        picker.addItem(manual, "")
        for item in packages:
            picker.addItem(f"{item.version} — {item.inf.name} — {item.manifest.parent.name}", str(item.manifest))
        index = picker.findData(profile.cache_manifest) if profile.cache_manifest else 0
        if index < 0:
            picker.addItem("Saved cached package unavailable — select another package or manual", profile.cache_manifest)
            index = picker.count() - 1
        picker.setCurrentIndex(index)
        layout.addWidget(picker)
        layout.addWidget(label("Cached packages are checked for integrity and GPU compatibility when saved.", "muted", True))
        layout.addWidget(label("Expected driver version (NVIDIA or full Windows version)"))
        layout.addWidget(version)
        version.setReadOnly(bool(profile.cache_manifest))
        layout.addWidget(label("Downloaded official NVIDIA installer (.exe)"))
        installer_row = QHBoxLayout()
        installer_row.addWidget(installer, 1)
        def browse():
            path, _ = QFileDialog.getOpenFileName(window, "Choose NVIDIA driver installer", "", "NVIDIA installer (*.exe)")
            if path:
                installer.setText(path)
        installer_row.addWidget(button("Browse...", browse))
        layout.addLayout(installer_row)
        layout.addWidget(label("A cached selection supplies the package's actual version. Saving does not switch drivers; an installer is checked for NVIDIA signature and fingerprint.", "muted", True))
        layout.addWidget(label("Saved GPU states (applied only after confirming a switch):"))
        known = {gpu.instance_id: gpu.name for gpu in self.devices if is_nvidia(gpu)}
        for device_id in profile.required_gpu_ids + profile.disabled_gpu_ids:
            known.setdefault(device_id, device_id)
        required = {item.upper() for item in profile.required_gpu_ids}
        disabled = {item.upper() for item in profile.disabled_gpu_ids}
        disabled |= {gpu.instance_id.upper() for gpu in self.devices if gpu.problem_code == 22 and gpu.instance_id.upper() not in required}
        targets = []
        for device_id, gpu_name in known.items():
            row = QHBoxLayout()
            row.addWidget(label(f"{gpu_name}\n{device_id}", wrap=True), 1)
            choice = QComboBox()
            choice.addItems(["Use profile driver", "Keep disabled", "Leave unchanged"])
            choice.setCurrentText("Use profile driver" if device_id.upper() in required else "Keep disabled" if device_id.upper() in disabled else "Leave unchanged")
            row.addWidget(choice)
            layout.addLayout(row)
            targets.append((device_id, choice))
        def choose_package(_index):
            path = picker.currentData()
            selected = next((item for item in packages if str(item.manifest) == path), None)
            version.setReadOnly(bool(path))
            if selected:
                version.setText(selected.version)
                installer.clear()
        picker.currentIndexChanged.connect(choose_package)
        row = QHBoxLayout()
        save_button = button("Save profile", lambda: save(), "accent")
        cancel_button = button("Cancel", window.reject)
        row.addWidget(save_button)
        row.addWidget(cancel_button)
        row.addStretch()
        outer.addLayout(row)
        def save():
            candidate = DriverProfile(name.text().strip(), version.text().strip(), installer.text().strip(),
                [device_id for device_id, choice in targets if choice.currentText() == "Use profile driver"],
                cache_manifest=picker.currentData() or "", disabled_gpu_ids=[device_id for device_id, choice in targets if choice.currentText() == "Keep disabled"])
            try:
                candidate.validate()
                other = [item for item in self.profiles if item is not original]
                if any(item.name.casefold() == candidate.name.casefold() for item in other):
                    raise ValueError("Choose a unique profile name.")
            except ValueError as error:
                QMessageBox.critical(window, "Check profile", str(error))
                return
            def job():
                try:
                    if candidate.cache_manifest:
                        _, inf = cache.validate_manifest(Path(candidate.cache_manifest), gm.cache_root_for_manifest(Path(candidate.cache_manifest)), candidate.expected_version)
                        cache.preflight(inf, nvidia_version(candidate.expected_version), candidate.required_gpu_ids)
                    checked = replace(candidate, sha256=gm.check_installer(candidate)) if candidate.installer else candidate
                    profiles = other + [checked]
                    save_profiles(gm.PROFILES_FILE, profiles)
                    return profiles, checked
                except Exception as error:
                    return error
            def done(result):
                window.saving = False
                window.setEnabled(True)
                if isinstance(result, Exception):
                    self.append_log(str(result))
                    self.status.setText("Profile was not saved: " + str(result))
                    QMessageBox.critical(window, "Check driver package", str(result))
                    return
                window.accept()
                self.profiles, checked = result
                self.reload_profile_choices(checked.name)
                self.append_log(f"Saved driver profile: {checked.name} ({nvidia_version(checked.expected_version)})")
                self.status.setText("Profile saved; cached package and target GPU compatibility verified." if checked.cache_manifest else "Profile saved; NVIDIA installer signature and fingerprint checked." if checked.installer else "Profile saved. Cache a matching package or attach an installer before switching.")
            window.saving = True
            window.setEnabled(False)
            self.start_job("Checking driver package and saving profile...", job, done)
        self.open_dialog(window)

    def remove_profile(self):
        profile = self.current_profile()
        if self.busy or profile is None or self.profile_load_error:
            return
        if confirm(self, "Remove profile", f"Remove '{profile.name}' from the saved profiles? Its installer and backups remain on disk."):
            profiles = [item for item in self.profiles if item is not profile]
            def job():
                save_profiles(gm.PROFILES_FILE, profiles)
                return profiles
            def done(result):
                self.profiles = result
                self.reload_profile_choices()
            self.start_job("Saving profile list...", job, done)

    def launch_profile(self):
        profile = self.current_profile()
        if self.busy or profile is None:
            return
        if not gm.is_admin():
            QMessageBox.information(self, "Administrator required", "Restart as administrator first.")
            return
        reenable = self.reenable_check.isChecked()
        names = {gpu.instance_id.upper(): gpu.name for gpu in self.devices}
        prompt = (f"Install profile: {profile.name}\nExpected driver: {nvidia_version(profile.expected_version)}\nInstaller: {profile.installer}\n\nRequired GPUs:\n"
                  + "\n".join(names.get(key.upper(), key) for key in profile.required_gpu_ids) + "\n\nGPUs to disable/keep disabled:\n"
                  + ("\n".join(names.get(key.upper(), key) for key in profile.disabled_gpu_ids) or "None")
                  + f"\n\nRe-enable disabled profile GPUs: {'Yes' if reenable else 'No'}\n\nStop AI jobs and games first. This reinstalls the system NVIDIA driver. Complete the visible installer; a restart may be required. Current packages and inventory are backed up. Windows will not restart automatically.")
        if confirm(self, "Confirm driver profile installation", prompt):
            def done(result):
                devices, report, passed = result
                self.display_inventory(devices)
                self.append_log(report)
                self.status.setText("Loaded driver matches this profile. Test your AI workflow next." if passed else "Switch is not fully verified. Finish setup/restart, then verify.")
                QMessageBox.information(self, "Driver profile result", report)
            self.start_job("Backing up driver, then running NVIDIA setup...", lambda: gm.install_profile(profile, reenable), done)

    def verify_selected_profile(self):
        profile = self.current_profile()
        if self.busy or profile is None:
            return
        def job():
            devices = gm.inventory()
            passed, report = verify_profile(profile, devices)
            if passed and gm.PENDING_FILE.exists():
                pending = json.loads(gm.PENDING_FILE.read_text(encoding="utf-8"))
                if pending.get("profile") == profile.name:
                    gm.PENDING_FILE.unlink()
                    report += "\nPrevious switch record cleared. Complete any installer-requested restart."
            return devices, passed, report
        def done(result):
            devices, passed, report = result
            self.display_inventory(devices)
            self.append_log(report)
            self.status.setText("Loaded driver/device verification passed. Test your AI workflow next." if passed else "Loaded driver/device verification did not pass. See the result for each GPU.")
            QMessageBox.information(self, "Profile verification", report)
        self.start_job("Checking loaded versions and GPU error codes...", job, done)

    def build_error_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(label("Device Manager problem code reference", "section"))
        layout.addWidget(label("Offline meanings and next steps for Codes 0–57. Legacy/internal SDK codes are labeled.", "muted", True))
        search = QLineEdit()
        search.setPlaceholderText("Search codes, meanings, or next steps...")
        layout.addWidget(search)
        panels = QSplitter(Qt.Orientation.Vertical)
        panels.setChildrenCollapsible(False)
        codes = table(["Code", "Meaning", "Category"])
        codes.setMinimumHeight(140)
        panels.addWidget(codes)
        details = text_view(100)
        panels.addWidget(details)
        panels.setStretchFactor(0, 3)
        panels.setStretchFactor(1, 2)
        panels.setSizes([360, 240])
        layout.addWidget(panels, 1)
        def selected():
            row = codes.currentRow()
            if row >= 0:
                details.setPlainText(problem_details(int(codes.item(row, 0).text())))
        codes.itemSelectionChanged.connect(selected)
        def filter_codes():
            term = search.text().strip().casefold()
            codes.setRowCount(0)
            for code, info in PROBLEMS.items():
                if term in f"{code} {info.symbol} {info.title} {info.meaning} {info.next_step}".casefold():
                    row = codes.rowCount()
                    codes.insertRow(row)
                    for column, value in enumerate((code, info.title, "Legacy / internal SDK" if info.legacy else "No problem" if code == 0 else "Device Manager")):
                        codes.setItem(row, column, QTableWidgetItem(str(value)))
        search.textChanged.connect(filter_codes)
        filter_codes()
        codes.selectRow(32)
        def source():
            row = codes.currentRow()
            code = int(codes.item(row, 0).text()) if row >= 0 else None
            webbrowser.open(problem_info(code).url if code not in (0, None) else ERROR_SOURCE)
        layout.addWidget(button("Microsoft documentation for selected code", source))
        self.notebook.addTab(page, "Error reference")

    def selected(self):
        row = self.tree.currentRow()
        return self.devices[row] if 0 <= row < len(self.devices) else None

    def append_log(self, value):
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {value}\n")

    def selection_changed(self):
        gpu = self.selected()
        text = "Select a GPU to see its exact device ID."
        if gpu:
            text = (f"{gpu.name}\nInstance ID: {gpu.instance_id}\nDriver: {self.version_label(gpu)} | Windows: {gpu.driver_version or 'Unknown'}\n"
                    f"Driver INF: {gpu.driver_inf or 'Unknown'} | Location: {gpu.location or 'Unknown'}\n" + problem_details(gpu.problem_code))
        self.details.setPlainText(text)
        available = gpu is not None and gpu.present is True and gm.is_admin() and not self.busy
        self.disable_button.setEnabled(bool(available and gpu.problem_code != 22))
        self.enable_button.setEnabled(bool(available and gpu.problem_code == 22))

    def display_inventory(self, devices):
        previous = self.selected()
        self.tree.blockSignals(True)
        self.devices = devices
        self.tree.setRowCount(len(devices))
        for row, gpu in enumerate(devices):
            for column, value in enumerate((gpu.name, {True: "Yes", False: "No", None: "Unknown"}[gpu.present], gpu.state, self.version_label(gpu), gpu.desktop)):
                self.tree.setItem(row, column, QTableWidgetItem(value))
        match = next((index for index, gpu in enumerate(devices) if previous and gpu.instance_id == previous.instance_id), None)
        if match is not None:
            self.tree.selectRow(match)
        self.tree.blockSignals(False)
        self.selection_changed()
        self.profile_summary()

    @staticmethod
    def version_label(gpu):
        if is_nvidia(gpu) and gpu.driver_version:
            try:
                return nvidia_version(gpu.driver_version)
            except ValueError:
                pass
        return gpu.driver_version or "Unknown"

    def start_job(self, title, function, done):
        if self.busy:
            return
        self.busy = True
        self.progress.setRange(0, 0)
        self.status.setText(title)
        self.refresh_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.profile_controls()
        self.selection_changed()
        def worker():
            try:
                self.events.put((done, function(), None))
            except Exception as error:
                self.events.put((done, None, str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            done, result, error = self.events.get_nowait()
        except queue.Empty:
            return
        self.busy = False
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText("Ready" if not error else "Action failed: " + error)
        self.refresh_button.setEnabled(True)
        self.export_button.setEnabled(True)
        self.selection_changed()
        self.profile_controls()
        if error:
            self.append_log(error)
            QMessageBox.critical(self, "GPU Manager", error)
        else:
            done(result)

    def refresh(self):
        def done(devices):
            self.display_inventory(devices)
            self.append_log(f"Found {len(devices)} display adapter(s). Select a device for its problem-code explanation and next steps.")
        self.start_job("Reading Windows GPU inventory...", gm.inventory, done)

    def prepare(self, action):
        gpu = self.selected()
        if self.busy or gpu is None or not gm.is_admin():
            return
        def job():
            devices = gm.inventory()
            target = gm.validate_action(devices, gpu.instance_id, action)
            return target, devices, gm.recovery_file(target, devices) if action == "disable" else None
        def done(result):
            target, devices, path = result
            self.display_inventory(devices)
            prompt = f"{action.capitalize()} {target.name}?\n\nExact device:\n{target.instance_id}"
            if path:
                self.append_log(f"Recovery saved: {path}")
                prompt += f"\n\nRecovery file:\n{path}\n\nConfirm all monitor cables are connected to a different working GPU. Displays attached to this GPU may go black. Run the recovery CMD as administrator to restore it."
            if confirm(self, "Confirm GPU change", prompt):
                self.start_job(f"{action.capitalize()} operation and verification...", lambda: gm.change_device(target.instance_id, action), self.changed)
        self.start_job("Checking selected GPU and preparing recovery...", job, done)

    def changed(self, result):
        devices, detail = result
        self.display_inventory(devices)
        self.append_log(detail)
        QMessageBox.information(self, "Windows device result", detail)

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export diagnosis", "gpu-diagnosis.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps([asdict(gpu) for gpu in self.devices], indent=2), encoding="utf-8")
                self.append_log(f"Diagnosis exported to {path}")
            except OSError as error:
                QMessageBox.critical(self, "Export failed", str(error))

    def manage_caches(self):
        if self.busy:
            return
        window, layout = dialog(self, "Manage cached drivers", 1000, 550)
        layout.addWidget(label("Cached driver packages", "section"))
        layout.addWidget(label("New drivers do not delete older caches. Select a package to review its location and references. Saved profiles protect their packages from deletion.", "muted", True))
        packages_table = table(["Driver", "Saved profiles", "Recovery history", "Cache folder"])
        layout.addWidget(packages_table, 1)
        detail = label("Select a package to review it.", "muted", True)
        layout.addWidget(detail)
        row = QHBoxLayout()
        delete_button = button("Delete selected cache...", lambda: delete_selected(), "danger")
        delete_button.setEnabled(False)
        row.addWidget(delete_button)
        row.addStretch()
        row.addWidget(button("Close", window.reject))
        layout.addLayout(row)
        packages = []
        references = []

        def refresh_list():
            nonlocal packages, references
            packages = gm.discover_all_cached_packages()
            references = []
            packages_table.setRowCount(0)
            for package in packages:
                try:
                    refs = gm.cache_references(package.manifest, gm.load_profiles(gm.PROFILES_FILE))
                    warning = ""
                except (OSError, ValueError, TypeError) as error:
                    refs = ([], [])
                    warning = str(error)
                references.append((refs[0], refs[1], warning))
                row_index = packages_table.rowCount()
                packages_table.insertRow(row_index)
                values = (package.version, ", ".join(refs[0]) or "None",
                          "Cannot check" if warning else str(len(refs[1])), str(package.manifest.parent))
                for column, value in enumerate(values):
                    packages_table.setItem(row_index, column, QTableWidgetItem(value))
            detail.setText("Select a package to review it." if packages else "No readable cached packages were found in the configured cache folders.")
            delete_button.setEnabled(False)

        def selected():
            index = packages_table.currentRow()
            if index < 0 or index >= len(packages):
                delete_button.setEnabled(False)
                return
            package = packages[index]
            names, history, warning = references[index]
            message = f"Package: {package.manifest.parent}"
            if names:
                message += "\nUsed by saved profiles: " + ", ".join(names) + ". Edit those profiles before deleting."
            if history:
                message += f"\nReferenced by {len(history)} recovery history record(s). Deleting removes this cached recovery copy."
            if warning:
                message += "\nCannot safely check references: " + warning
            detail.setText(message)
            delete_button.setEnabled(not names and not warning)

        def delete_selected():
            index = packages_table.currentRow()
            if index < 0 or index >= len(packages):
                return
            package = packages[index]
            names, history, warning = references[index]
            if names or warning:
                return
            prompt = (f"Permanently delete cached NVIDIA driver {package.version}?\n\n"
                      f"{package.manifest.parent}\n\nThis removes only this cached package, not the installed driver or original installer.")
            if history:
                prompt += (f"\n\nWARNING: This package is referenced by {len(history)} recovery history record(s). "
                           "Deleting it removes that cached recovery copy; the history records will remain but point to a missing package.")
            if not confirm(window, "Delete cached driver", prompt):
                return
            delete_button.setEnabled(False)
            def done(_result):
                self.append_log(f"Deleted cached package: {package.manifest.parent}")
                self.status.setText(f"Deleted cached driver {package.version}; installed drivers were not changed.")
                refresh_list()
            def job():
                try:
                    gm.delete_cached_package(package.manifest, allow_history=bool(history))
                    return None
                except (OSError, ValueError) as error:
                    return str(error)
            def completed(error):
                if error:
                    QMessageBox.critical(window, "Cache not deleted", error)
                    refresh_list()
                    return
                done(None)
            self.start_job("Deleting the selected cached driver...", job, completed)
        packages_table.itemSelectionChanged.connect(selected)
        refresh_list()
        self.open_dialog(window)

    def change_cache_folder(self):
        if self.busy:
            return
        chosen = QFileDialog.getExistingDirectory(self, "Choose driver cache folder", str(gm.CACHE_ROOT))
        if not chosen:
            return
        new_root = Path(chosen).resolve()
        if new_root != gm.CACHE_ROOT:
            if not confirm(self, "Change driver cache folder", f"New cached packages will be saved to:\n{new_root}\n\nThe folder will be scanned for existing app-cached packages. Existing packages remain in their original folders and saved profiles will continue to use them. No files will be moved or deleted. Continue?"):
                return
            try:
                gm.set_cache_root(new_root, gm.SETTINGS_FILE)
            except (OSError, ValueError) as error:
                QMessageBox.critical(self, "Cache folder not changed", str(error))
                return
        self.profile_controls()
        def done(packages):
            count = len(packages)
            message = (f"Cache scan found {count} cached driver package(s) in {new_root}. "
                       "Available in Manage caches and the profile package picker. "
                       "No drivers were installed or profiles changed.")
            self.status.setText(message)
            self.append_log(message)
        self.start_job("Scanning the selected cache folder for existing driver packages...",
                       lambda: cache.discover_cached_packages(new_root), done)

    def elevate(self):
        if self.busy or gm.is_admin():
            return
        import ctypes
        from ctypes import wintypes
        shell = ctypes.windll.shell32.ShellExecuteW
        shell.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        shell.restype = ctypes.c_ssize_t
        result = shell(None, "runas", sys.executable, "" if getattr(sys, "frozen", False) else subprocess.list2cmdline([str(Path(gm.__file__).resolve())]), str(gm.APP_DIR), 1)
        if result <= 32:
            QMessageBox.critical(self, "Elevation canceled or failed", "No GPU settings were changed.")
        else:
            self.close()

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, "Operation in progress", "Wait for the current operation to finish.")
            event.ignore()
        else:
            event.accept()
