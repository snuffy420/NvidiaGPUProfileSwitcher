"""Cache integrity and switch orchestration tests. Driver APIs are mocked."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import cached_drivers as cd
from driver_profiles import DriverProfile, load_profiles
import gpu_manager as gm
from test_gpu_manager import A, B, ID_A, ID_B


class CacheChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gpu-manager-cache-test-")
        self.root = Path(self.temp.name).resolve()
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.cache_root = self.root / "cache"
        self.source = self.root / "extracted" / "Display.Driver"
        self.source.mkdir(parents=True)
        self.inf = self.source / "nv_test.inf"
        self.inf.write_text(
            '[Version]\nClass=Display\nClassGUID={4d36e968-e325-11ce-bfc1-08002be10318}\n'
            'Provider=%NVIDIA%\nDriverVer=08/20/2024,32.0.15.6094\nCatalogFile=nv_test.cat\n'
            '[Strings]\nNVIDIA="NVIDIA"\n', encoding="utf-16")
        (self.source / "nv_test.cat").write_bytes(b"synthetic catalog, never staged on Windows")
        (self.source / "driver.sys").write_bytes(b"synthetic driver, never installed")
        self.manifest = cd.cache_package(self.inf, self.cache_root)
        self.profile = DriverProfile("AI cached", "560.94", required_gpu_ids=[ID_A, ID_B],
                                     cache_manifest=str(self.manifest))

    def tearDown(self):
        self.temp.cleanup()

    def backend(self, before=None, after=None, stage_exit=0, restart=False):
        patches = []
        self.addCleanup(lambda: [item.stop() for item in reversed(patches)])
        mocks = {}
        for name, value in (("APP_DIR", self.root), ("CACHE_ROOT", self.cache_root),
                            ("PENDING_FILE", self.root / "pending.json"), ("PROFILES_FILE", self.root / "profiles.json")):
            item = patch.object(gm, name, value)
            item.start()
            patches.append(item)
        for obj, name, kwargs in (
            (gm, "is_admin", {"return_value": True}),
            (gm, "inventory", {"side_effect": [before or [replace(A, problem_code=22), replace(B, driver_version="32.0.16.1742")], after or [A, B]]}),
            (gm, "run_command", {"return_value": subprocess.CompletedProcess([], stage_exit, b"stage result", b"")}),
            (gm, "change_device", {"return_value": ([A, B], "enabled")}),
            (cd, "preflight", {}),
            (cd, "discover_packages", {"return_value": []}),
            (cd, "cache_profile", {"return_value": self.manifest}),
            (cd, "DeviceDriver", {}),
        ):
            item = patch.object(obj, name, **kwargs)
            mocks[name] = item.start()
            patches.append(item)
        mocks["DeviceDriver"].return_value.__enter__.return_value.install.return_value = restart
        return mocks

    def test_complete_cache_round_trip_and_source_untouched(self):
        metadata, copied_inf = cd.validate_manifest(self.manifest, self.cache_root, "560.94")
        self.assertEqual(metadata["version"], "560.94")
        self.assertEqual(copied_inf.read_bytes(), self.inf.read_bytes())
        self.assertTrue((copied_inf.parent / "nv_test.cat").exists())
        self.assertTrue((self.source / "driver.sys").exists())

    def test_change_cache_folder_keeps_existing_packages_and_persists_roots_and_theme(self):
        settings = self.root / "settings.json"
        settings.write_text(json.dumps({"schema": 1, "theme": "Light"}), encoding="utf-8")
        new_root = self.root / "new-cache"
        new_root.mkdir()
        with patch.object(gm, "CACHE_ROOT", self.cache_root), patch.object(gm, "CACHE_ROOTS", []):
            gm.set_cache_root(new_root, settings)
            self.assertEqual(gm.CACHE_ROOT, new_root)
            self.assertEqual(gm.cache_root_for_manifest(self.manifest), self.cache_root)
            self.assertEqual(cd.validate_manifest(self.manifest, gm.cache_root_for_manifest(self.manifest))[0]["version"], "560.94")
            self.assertIn(self.manifest, [item.manifest for item in gm.discover_all_cached_packages()])
            self.assertEqual(json.loads(settings.read_text())["theme"], "Light")
            from ui_theme import Theme
            from PyQt6.QtWidgets import QApplication, QWidget
            app = QApplication.instance() or QApplication([])
            widget = QWidget()
            theme = Theme(widget, settings)
            theme.apply("Dark")
            theme.save()
            self.assertEqual(json.loads(settings.read_text())["cache_root"], str(new_root))
            widget.close()
            self.assertTrue(self.manifest.exists())
            gm.CACHE_ROOT = self.root / "other"
            gm.CACHE_ROOTS = []
            gm.configure_cache(settings)
            self.assertEqual(gm.CACHE_ROOT, new_root)
            self.assertEqual(gm.cache_root_for_manifest(self.manifest), self.cache_root)
            with self.assertRaises(ValueError):
                gm.cache_root_for_manifest(self.root / "untrusted" / "cache-manifest.json")

    def test_cache_folder_selection_persists_without_moving_existing_packages(self):
        settings = self.root / "settings.json"
        new_root = self.root / "new-cache"
        new_root.mkdir()
        with (patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=False),
              patch.object(gm, "SETTINGS_FILE", settings), patch.object(gm, "PROFILES_FILE", self.root / "profiles.json"),
              patch.object(gm, "PENDING_FILE", self.root / "pending.json"),
              patch.object(gm, "CACHE_ROOT", self.cache_root), patch.object(gm, "CACHE_ROOTS", []),
              patch("qt_ui.QFileDialog.getExistingDirectory", return_value=str(new_root)),
              patch("qt_ui.confirm", return_value=True)):
            app = gm.App()
            try:
                with patch.object(app, "start_job", side_effect=lambda label, worker, done: done(worker())):
                    app.change_cache_folder()
                self.assertEqual(gm.CACHE_ROOT, new_root)
                self.assertIn(str(new_root), app.cache_location.text())
                self.assertEqual(gm.cache_root_for_manifest(self.manifest), self.cache_root)
                self.assertTrue(self.manifest.is_file())
            finally:
                app.close()

    def test_selecting_cache_folder_scans_and_exposes_existing_packages(self):
        new_root = self.root / "new-cache"
        new_root.mkdir()
        existing = cd.cache_package(self.inf, new_root)
        settings = self.root / "settings.json"
        with (patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=False),
              patch.object(gm, "SETTINGS_FILE", settings), patch.object(gm, "PROFILES_FILE", self.root / "profiles.json"),
              patch.object(gm, "PENDING_FILE", self.root / "pending.json"),
              patch.object(gm, "CACHE_ROOT", self.cache_root), patch.object(gm, "CACHE_ROOTS", []),
              patch("qt_ui.QFileDialog.getExistingDirectory", return_value=str(new_root)),
              patch("qt_ui.confirm", return_value=True)):
            app = gm.App()
            try:
                with patch.object(app, "start_job", side_effect=lambda label, worker, done: done(worker())):
                    app.change_cache_folder()
                self.assertIn("found 1 cached driver package", app.status.text())
                self.assertIn(existing, [item.manifest for item in gm.discover_all_cached_packages()])
                self.assertTrue(self.manifest.exists())
                self.assertEqual(app.profiles, [])
                from PyQt6.QtWidgets import QComboBox, QTableWidget
                app.edit_profile()
                picker = app.dialogs[-1].findChildren(QComboBox)[0]
                self.assertNotEqual(picker.findData(str(existing)), -1)
                app.dialogs[-1].reject()
                app.manage_caches()
                listed = app.dialogs[-1].findChild(QTableWidget)
                self.assertEqual(listed.rowCount(), 2)
            finally:
                app.close()

    def test_cache_folder_failure_leaves_existing_configuration_unchanged(self):
        settings = self.root / "settings.json"
        with patch.object(gm, "CACHE_ROOT", self.cache_root), patch.object(gm, "CACHE_ROOTS", []):
            with self.assertRaises(ValueError):
                gm.set_cache_root(self.root / "missing", settings)
            self.assertEqual(gm.CACHE_ROOT, self.cache_root)
            self.assertFalse(settings.exists())

    def test_cache_manager_protects_profiles_and_requires_history_confirmation(self):
        from driver_profiles import save_profiles
        history = self.root / "driver_history" / "switch1"
        history.mkdir(parents=True)
        (history / "previous-cached-drivers.json").write_text(json.dumps([{"cache_manifest": str(self.manifest)}]))
        profile_path = self.root / "profiles.json"
        save_profiles(profile_path, [self.profile])
        with (patch.object(gm, "APP_DIR", self.root), patch.object(gm, "CACHE_ROOT", self.cache_root),
              patch.object(gm, "CACHE_ROOTS", []), patch.object(gm, "PROFILES_FILE", profile_path)):
            names, records = gm.cache_references(self.manifest, [self.profile])
            self.assertEqual(names, [self.profile.name])
            self.assertEqual(records, [history / "previous-cached-drivers.json"])
            with self.assertRaisesRegex(ValueError, "used by saved profiles"):
                gm.delete_cached_package(self.manifest, allow_history=True)
            self.assertTrue(self.manifest.is_file())
            save_profiles(profile_path, [])
            with self.assertRaisesRegex(ValueError, "recovery history"):
                gm.delete_cached_package(self.manifest)
            self.assertTrue(self.manifest.is_file())
            gm.delete_cached_package(self.manifest, allow_history=True)
            self.assertFalse(self.manifest.parent.exists())
            self.assertTrue((history / "previous-cached-drivers.json").exists())
            self.assertTrue(self.inf.exists())

    def test_cache_manager_rejects_untrusted_or_modified_packages(self):
        with (patch.object(gm, "APP_DIR", self.root), patch.object(gm, "CACHE_ROOT", self.cache_root),
              patch.object(gm, "CACHE_ROOTS", []), patch.object(gm, "PROFILES_FILE", self.root / "profiles.json")):
            with self.assertRaises(ValueError):
                gm.delete_cached_package(self.source / "cache-manifest.json")
            (self.manifest.parent / "package" / "driver.sys").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "changed"):
                gm.delete_cached_package(self.manifest)
            self.assertTrue(self.manifest.is_file())

    def test_cache_manager_refuses_unreadable_history(self):
        history = self.root / "driver_history" / "switch1"
        history.mkdir(parents=True)
        (history / "previous-cached-drivers.json").write_text("invalid")
        with (patch.object(gm, "APP_DIR", self.root), patch.object(gm, "CACHE_ROOT", self.cache_root),
              patch.object(gm, "CACHE_ROOTS", []), patch.object(gm, "PROFILES_FILE", self.root / "profiles.json")):
            with self.assertRaisesRegex(ValueError, "Cannot check recovery history"):
                gm.delete_cached_package(self.manifest, allow_history=True)
            self.assertTrue(self.manifest.is_file())

    def test_cache_manager_panel_shows_and_protects_profile_package(self):
        from driver_profiles import save_profiles
        from PyQt6.QtWidgets import QPushButton, QTableWidget
        profile_path = self.root / "profiles.json"
        save_profiles(profile_path, [self.profile])
        with (patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=False),
              patch.object(gm, "APP_DIR", self.root), patch.object(gm, "CACHE_ROOT", self.cache_root),
              patch.object(gm, "CACHE_ROOTS", []), patch.object(gm, "SETTINGS_FILE", self.root / "settings.json"),
              patch.object(gm, "PROFILES_FILE", profile_path), patch.object(gm, "PENDING_FILE", self.root / "pending.json")):
            app = gm.App()
            try:
                app.manage_caches()
                window = app.dialogs[-1]
                table = window.findChild(QTableWidget)
                delete = next(item for item in window.findChildren(QPushButton) if item.text().startswith("Delete selected"))
                self.assertEqual(table.rowCount(), 1)
                self.assertEqual(table.item(0, 1).text(), self.profile.name)
                table.selectRow(0)
                self.assertFalse(delete.isEnabled())
            finally:
                app.close()

    def test_cache_manager_confirmation_controls_deletion_of_history_copy(self):
        from PyQt6.QtWidgets import QPushButton, QTableWidget
        history = self.root / "driver_history" / "switch1"
        history.mkdir(parents=True)
        (history / "previous-cached-drivers.json").write_text(json.dumps([{"cache_manifest": str(self.manifest)}]))
        with (patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=False),
              patch.object(gm, "APP_DIR", self.root), patch.object(gm, "CACHE_ROOT", self.cache_root),
              patch.object(gm, "CACHE_ROOTS", []), patch.object(gm, "SETTINGS_FILE", self.root / "settings.json"),
              patch.object(gm, "PROFILES_FILE", self.root / "profiles.json"),
              patch.object(gm, "PENDING_FILE", self.root / "pending.json"),
              patch("qt_ui.confirm", side_effect=[False, True]) as confirm):
            app = gm.App()
            try:
                app.manage_caches()
                window = app.dialogs[-1]
                table = window.findChild(QTableWidget)
                delete = next(item for item in window.findChildren(QPushButton) if item.text().startswith("Delete selected"))
                table.selectRow(0)
                self.assertTrue(delete.isEnabled())
                delete.click()
                self.assertTrue(self.manifest.exists())
                self.assertIn("recovery history", confirm.call_args.args[2])
                with patch.object(app, "start_job", side_effect=lambda label, worker, done: done(worker())):
                    delete.click()
                self.assertFalse(self.manifest.exists())
                self.assertEqual(table.rowCount(), 0)
                self.assertTrue(self.inf.exists())
            finally:
                app.close()

    def test_cache_picker_discovers_versions_from_metadata_and_skips_bad_entries(self):
        self.inf.write_text(self.inf.read_text(encoding="utf-16").replace("32.0.15.6094", "32.0.16.1742"), encoding="utf-16")
        newer = cd.cache_package(self.inf, self.cache_root)
        broken = self.cache_root / "broken"
        broken.mkdir()
        (broken / "cache-manifest.json").write_text('{"schema": 1, "version": "999.99"}')
        packages = cd.discover_cached_packages(self.cache_root)
        self.assertEqual([item.version for item in packages], ["617.42", "560.94"])
        self.assertEqual(packages[0].manifest, newer)
        # A changed payload remains a candidate but must fail full validation before save.
        (newer.parent / "package/driver.sys").write_bytes(b"changed")
        self.assertEqual(len(cd.discover_cached_packages(self.cache_root)), 2)
        with self.assertRaisesRegex(ValueError, "changed"):
            cd.validate_manifest(newer, self.cache_root)

    def profile_editor(self, devices=None, profile=None):
        from PyQt6.QtWidgets import QLineEdit, QComboBox, QPushButton
        patches = [patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=False),
                   patch.object(gm, "SETTINGS_FILE", self.root / "settings.json"),
                   patch.object(gm, "PROFILES_FILE", self.root / "profiles.json"),
                   patch.object(gm, "PENDING_FILE", self.root / "pending.json"),
                   patch.object(gm, "CACHE_ROOT", self.cache_root)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        app = gm.App()
        self.addCleanup(app.close)
        app.devices = devices if devices is not None else [replace(A, problem_code=22), replace(B, driver_version="32.0.16.1742")]
        app.edit_profile(profile)
        dialog = app.dialogs[-1]
        entries = dialog.findChildren(QLineEdit)
        entries = [entry for entry in entries if not isinstance(entry.parent(), QComboBox)]
        combos = dialog.findChildren(QComboBox)
        save = next(widget for widget in dialog.findChildren(QPushButton) if widget.text() == "Save profile")
        return app, dialog, entries, combos, save

    def test_add_profile_defaults_to_enabled_version_and_preserves_disabled_role(self):
        app, dialog, entries, combos, save = self.profile_editor()
        self.assertEqual(entries[1].text(), "617.42")
        self.assertEqual([combo.currentText() for combo in combos[1:]], ["Keep disabled", "Use profile driver"])
        self.assertEqual(entries[0].text(), "")

    def test_add_profile_does_not_guess_when_enabled_versions_differ(self):
        _, _, entries, _, _ = self.profile_editor([A, replace(B, driver_version="32.0.16.1742")])
        self.assertEqual(entries[1].text(), "")

    def test_choosing_cache_sets_real_version_and_saves_manifest_and_disabled_gpu(self):
        app, dialog, entries, combos, save = self.profile_editor()
        entries[0].setText("Cached profile")
        combos[0].setCurrentIndex(1)
        self.assertEqual(entries[1].text(), "560.94")
        self.assertEqual(entries[1].isReadOnly(), True)
        with (patch.object(cd, "preflight") as preflight,
              patch.object(app, "start_job", side_effect=lambda label, worker, done: done(worker()))):
            save.click()
        result = load_profiles(self.root / "profiles.json")[0]
        self.assertEqual(result.cache_manifest, str(self.manifest))
        self.assertEqual(result.expected_version, "560.94")
        self.assertEqual(result.required_gpu_ids, [ID_B])
        self.assertEqual(result.disabled_gpu_ids, [ID_A])
        preflight.assert_called_once_with(self.manifest.parent / "package/nv_test.inf", "560.94", [ID_B])
        self.assertIn("cached package", app.status.text())

    def test_bad_cache_or_incompatible_target_preserves_editor_and_does_not_save(self):
        app, dialog, entries, combos, save = self.profile_editor()
        entries[0].setText("Keep my edits")
        combos[0].setCurrentIndex(1)
        with (patch.object(cd, "preflight", side_effect=ValueError("Unsupported GPU")),
              patch("qt_ui.QMessageBox.critical") as error,
              patch.object(app, "start_job", side_effect=lambda label, worker, done: done(worker()))):
            save.click()
        self.assertFalse((self.root / "profiles.json").exists())
        self.assertTrue(dialog.isVisible())
        self.assertEqual(entries[0].text(), "Keep my edits")
        self.assertEqual(save.isEnabled(), True)
        error.assert_called_once()

    def test_edit_cached_profile_selects_package_and_manual_choice_can_detach_it(self):
        _, _, entries, combos, _ = self.profile_editor(profile=self.profile)
        self.assertTrue(combos[0].currentText().startswith("560.94"))
        self.assertEqual(entries[1].isReadOnly(), True)
        combos[0].setCurrentIndex(0)
        self.assertEqual(entries[1].isReadOnly(), False)

    def test_modified_missing_or_extra_file_is_rejected(self):
        package = self.manifest.parent / "package"
        driver = package / "driver.sys"
        driver.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            cd.validate_manifest(self.manifest, self.cache_root)
        driver.unlink()
        with self.assertRaisesRegex(ValueError, "added or removed"):
            cd.validate_manifest(self.manifest, self.cache_root)

    def test_wrong_expected_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            cd.validate_manifest(self.manifest, self.cache_root, "617.42")

    def test_manifest_missing_version_reports_validation_error(self):
        data = json.loads(self.manifest.read_text())
        del data["version"]
        self.manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "manifest version"):
            cd.validate_manifest(self.manifest, self.cache_root)

    def test_manifest_outside_private_cache_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "inside"):
            cd.validate_manifest(self.manifest, self.root / "different-cache")

    def test_manifest_cannot_redirect_inf_outside_package(self):
        data = json.loads(self.manifest.read_text())
        data["inf"] = "../../outside.inf"
        self.manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "missing"):
            cd.validate_manifest(self.manifest, self.cache_root)

    def test_missing_catalog_and_non_nvidia_import_rejected(self):
        (self.source / "nv_test.cat").unlink()
        with self.assertRaisesRegex(ValueError, "signature catalog"):
            cd.cache_package(self.inf, self.cache_root)
        self.inf.write_text(self.inf.read_text(encoding="utf-16").replace('NVIDIA="NVIDIA"', 'NVIDIA="Different Vendor"'), encoding="utf-16")
        with self.assertRaisesRegex(ValueError, "not an NVIDIA"):
            cd.inf_metadata(self.inf)

    def test_new_version_gets_separate_cache_preserving_old_copy(self):
        self.inf.write_text(self.inf.read_text(encoding="utf-16").replace("32.0.15.6094", "32.0.16.1742"), encoding="utf-16")
        newer = cd.cache_package(self.inf, self.cache_root)
        self.assertNotEqual(newer, self.manifest)
        self.assertEqual(cd.validate_manifest(newer, self.cache_root)[0]["version"], "617.42")
        self.assertEqual(cd.validate_manifest(self.manifest, self.cache_root)[0]["version"], "560.94")

    def test_compatible_existing_cache_reused_without_copy(self):
        with patch.object(cd, "preflight") as preflight, patch.object(cd, "cache_package") as copy:
            self.assertEqual(cd.cache_profile(self.profile, self.cache_root, []), self.manifest)
            preflight.assert_called_once()
            copy.assert_not_called()

    def test_driver_version_integer_decoded_as_four_words(self):
        self.assertEqual(cd.unpack_driver_version((32 << 48) | (15 << 16) | 6094), "32.0.15.6094")

    def test_preflight_happens_before_staging_and_exact_device_install(self):
        mocks = self.backend()
        calls = Mock()
        calls.attach_mock(mocks["preflight"], "preflight")
        calls.attach_mock(mocks["run_command"], "stage")
        calls.attach_mock(mocks["DeviceDriver"], "select")
        _, report, passed = gm.switch_cached_profile(self.profile, True)
        self.assertTrue(passed)
        self.assertIn("verification PASSED", report)
        self.assertEqual(calls.mock_calls[0][0], "preflight")
        stage_args = mocks["run_command"].call_args.args[0]
        self.assertEqual(stage_args[1], "/add-driver")
        self.assertNotIn("/install", stage_args)
        self.assertNotIn("/delete-driver", stage_args)
        self.assertEqual([call.args[0] for call in mocks["DeviceDriver"].call_args_list], [ID_B, ID_A])
        mocks["change_device"].assert_called_once_with(ID_A, "enable")

    def test_incompatible_package_cannot_stage_or_install(self):
        mocks = self.backend()
        mocks["preflight"].side_effect = RuntimeError("incompatible")
        with self.assertRaisesRegex(RuntimeError, "incompatible"):
            gm.switch_cached_profile(self.profile, True)
        mocks["run_command"].assert_not_called()
        mocks["DeviceDriver"].assert_not_called()

    def test_stage_signature_failure_cannot_install(self):
        mocks = self.backend(stage_exit=5)
        with self.assertRaisesRegex(RuntimeError, "Windows rejected"):
            gm.switch_cached_profile(self.profile, True)
        mocks["DeviceDriver"].assert_not_called()
        mocks["change_device"].assert_not_called()

    def test_active_outside_profile_gpu_blocks_mixed_version_switch(self):
        profile = replace(self.profile, required_gpu_ids=[ID_B])
        mocks = self.backend(before=[replace(A, driver_version="32.0.16.1742"), replace(B, driver_version="32.0.16.1742")])
        with self.assertRaisesRegex(RuntimeError, "outside this profile"):
            gm.switch_cached_profile(profile, True)
        mocks["DeviceDriver"].assert_not_called()
        mocks["run_command"].assert_not_called()

    def test_restart_boolean_keeps_switch_pending_even_with_matching_versions(self):
        self.backend(restart=True)
        _, report, passed = gm.switch_cached_profile(self.profile, True)
        self.assertFalse(passed)
        self.assertIn("Windows requires a restart", report)
        self.assertEqual(json.loads(gm.PENDING_FILE.read_text())["state"], "restart required")

    def test_partial_failure_is_reported_and_not_automatically_rolled_back(self):
        mocks = self.backend()
        mocks["DeviceDriver"].return_value.__enter__.return_value.install.side_effect = [False, OSError("test failure")]
        _, report, passed = gm.switch_cached_profile(self.profile, True)
        self.assertFalse(passed)
        self.assertIn("Earlier device changes may already have completed", report)
        self.assertEqual(mocks["DeviceDriver"].call_count, 2)
        self.assertTrue(gm.PENDING_FILE.exists())

    def test_disabled_target_without_enable_choice_blocks_before_staging(self):
        mocks = self.backend()
        with self.assertRaisesRegex(RuntimeError, "required GPU is disabled"):
            gm.switch_cached_profile(self.profile, False)
        mocks["run_command"].assert_not_called()

    def test_already_loaded_profile_does_not_reinstall(self):
        mocks = self.backend(before=[A, B])
        _, report, passed = gm.switch_cached_profile(self.profile, True)
        self.assertTrue(passed)
        self.assertIn("No change was necessary", report)
        mocks["run_command"].assert_not_called()
        mocks["DeviceDriver"].assert_not_called()

    def test_cache_all_preserves_profiles_and_records_partial_results(self):
        newer = replace(self.profile, name="Newer", expected_version="617.42", cache_manifest="")
        with patch.object(gm, "CACHE_ROOT", self.cache_root), patch.object(gm, "PROFILES_FILE", self.root / "profiles.json"), patch.object(cd, "discover_packages", return_value=[]), patch.object(cd, "cache_profile", side_effect=[self.manifest, RuntimeError("not available")]):
            profiles, report = gm.cache_profiles([self.profile, newer])
            self.assertEqual(profiles[0].cache_manifest, str(self.manifest))
            self.assertEqual(profiles[1].cache_manifest, "")
            self.assertIn("Not cached: Newer", report)
            self.assertEqual(load_profiles(gm.PROFILES_FILE), profiles)


if __name__ == "__main__":
    unittest.main()
