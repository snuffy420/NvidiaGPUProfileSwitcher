"""Theme persistence and Qt widget contrast without changing GPU state."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QPlainTextEdit, QWidget
from ui_theme import Theme, ScrollPane, PALETTES

APP = QApplication.instance() or QApplication([])


class ThemeChecks(unittest.TestCase):
    def test_new_scroll_area_uses_current_theme_and_updates_when_changed(self):
        with tempfile.TemporaryDirectory(prefix="gpu-scroll-theme-test-") as tmp:
            root = QWidget()
            theme = Theme(root, Path(tmp) / "settings.json")
            pane = ScrollPane(root)
            self.assertIsNotNone(pane.widget())
            self.assertIn(PALETTES["Dark"]["bg"], APP.styleSheet())
            theme.apply("Light")
            self.assertIn(PALETTES["Light"]["bg"], APP.styleSheet())
            root.close()

    def test_app_logo_is_transparent_and_changes_with_theme(self):
        import gpu_manager as gm
        with tempfile.TemporaryDirectory(prefix="gpu-logo-theme-test-") as tmp:
            root = Path(tmp)
            with (patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=False),
                  patch.object(gm, "SETTINGS_FILE", root / "settings.json"),
                  patch.object(gm, "PROFILES_FILE", root / "profiles.json"),
                  patch.object(gm, "PENDING_FILE", root / "pending.json")):
                app = gm.App()
                try:
                    for mode in ("Light", "Dark"):
                        app.theme_choice.setCurrentText(mode)
                        image = app.logo_label.pixmap().toImage()
                        self.assertFalse(image.isNull())
                        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
                        self.assertIn(PALETTES[mode]["bg"], APP.styleSheet())
                finally:
                    app.close()

    def test_theme_switch_updates_existing_widgets_and_persists_preference(self):
        with tempfile.TemporaryDirectory(prefix="gpu-theme-test-") as tmp:
            root = QWidget()
            path = Path(tmp) / "settings.json"
            theme = Theme(root, path)
            self.assertEqual(theme.name, "Dark")
            text = QPlainTextEdit(root)
            for mode in ("Light", "Dark"):
                theme.apply(mode)
                self.assertIn(PALETTES[mode]["field"], APP.styleSheet())
                self.assertIn(PALETTES[mode]["text"], APP.styleSheet())
                self.assertNotEqual(PALETTES[mode]["field"], PALETTES[mode]["text"])
            theme.apply("Light")
            theme.save()
            self.assertEqual(Theme(root, path).name, "Light")
            with self.assertRaises(ValueError):
                theme.apply("invalid")
            root.close()

    def test_integrated_window_controls_and_gpu_panels(self):
        import gpu_manager as gm
        from PyQt6.QtCore import Qt
        from test_gpu_manager import A, B
        with tempfile.TemporaryDirectory(prefix="gpu-window-test-") as tmp:
            root = Path(tmp)
            with (patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=False),
                  patch.object(gm, "SETTINGS_FILE", root / "settings.json"),
                  patch.object(gm, "PROFILES_FILE", root / "profiles.json"),
                  patch.object(gm, "PENDING_FILE", root / "pending.json")):
                window = gm.App()
                try:
                    window.resize(900, 650)
                    window.show()
                    window.notebook.setCurrentIndex(0)
                    window.display_inventory([A, B])
                    window.tree.selectRow(0)
                    APP.processEvents()
                    self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
                    self.assertTrue(window.minimize_button.isVisible())
                    self.assertTrue(window.close_button.isVisible())
                    bottom = window.details.mapTo(window, window.details.rect().bottomLeft()).y()
                    top = window.disable_button.mapTo(window, window.disable_button.rect().topLeft()).y()
                    self.assertLess(bottom, top)
                    window.maximize_button.click()
                    APP.processEvents()
                    self.assertTrue(window.isMaximized())
                    window.maximize_button.click()
                    APP.processEvents()
                    self.assertFalse(window.isMaximized())
                finally:
                    window.close()

    def test_profile_editor_buttons_fit_available_screen(self):
        import gpu_manager as gm
        from PyQt6.QtWidgets import QPushButton
        from test_gpu_manager import A, B
        with tempfile.TemporaryDirectory(prefix="gpu-profile-dialog-test-") as tmp:
            root = Path(tmp)
            with (patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=False),
                  patch.object(gm, "SETTINGS_FILE", root / "settings.json"),
                  patch.object(gm, "PROFILES_FILE", root / "profiles.json"),
                  patch.object(gm, "PENDING_FILE", root / "pending.json")):
                window = gm.App()
                try:
                    window.show()
                    window.devices = [A, B]
                    window.edit_profile()
                    APP.processEvents()
                    editor = window.dialogs[-1]
                    buttons = {item.text(): item for item in editor.findChildren(QPushButton)}
                    bounds = editor.screen().availableGeometry()
                    self.assertLessEqual(editor.frameGeometry().bottom(), bounds.bottom())
                    self.assertLessEqual(editor.frameGeometry().right(), bounds.right())
                    for name in ("Save profile", "Cancel"):
                        bottom = buttons[name].mapToGlobal(buttons[name].rect().bottomLeft()).y()
                        self.assertLessEqual(bottom, bounds.bottom())
                        self.assertLessEqual(bottom, editor.frameGeometry().bottom())
                finally:
                    window.close()

    def test_corrupt_preferences_fall_back_without_overwriting_file(self):
        with tempfile.TemporaryDirectory(prefix="gpu-theme-test-") as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("broken", encoding="utf-8")
            root = QWidget()
            self.assertEqual(Theme(root, path).name, "Dark")
            self.assertEqual(path.read_text(), "broken")
            root.close()


if __name__ == "__main__":
    unittest.main()
