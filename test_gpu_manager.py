"""Run with python -m unittest -v. No actual device/installer commands run."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import driver_profiles as dp
import gpu_errors as ge
import gpu_manager as gm

ID_A = r"PCI\VEN_10DE&DEV_1B81\TEST1070"
ID_B = r"PCI\VEN_10DE&DEV_2504\TEST3060"
A = gm.GPU("GTX 1070", ID_A, "OK", True, 0, "32.0.15.6094", "oem31.inf")
B = gm.GPU("RTX 3060", ID_B, "OK", True, 0, "32.0.15.6094", "oem31.inf")


class ProfileChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        # Validate the absolute cleanup target before TemporaryDirectory's cleanup.
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.profile = dp.DriverProfile("AI multi-GPU", "560.94", str(self.root / "nvidia.exe"),
                                        [ID_A, ID_B], "a" * 64)

    def tearDown(self):
        self.temp.cleanup()

    def test_windows_and_nvidia_versions_compare_equally(self):
        self.assertEqual(dp.nvidia_version("32.0.15.6094"), "560.94")
        self.assertEqual(dp.nvidia_version("32.0.16.1742"), "617.42")
        self.assertEqual(dp.nvidia_version("21.21.13.7653"), "376.53")
        self.assertEqual(dp.nvidia_version("9.18.13.4052"), "340.52")
        self.assertEqual(dp.nvidia_version("6.14.10.9371"), "93.71")
        passed, _ = dp.verify_profile(replace(self.profile, expected_version="32.0.15.6094"), [A, B])
        self.assertTrue(passed)

    def test_invalid_version_is_not_treated_as_a_match(self):
        for version in ("", "560", "latest", "32.0.15.10000", 560):
            with self.subTest(version=version), self.assertRaises(ValueError):
                dp.nvidia_version(version)

    def test_every_required_gpu_must_be_healthy_and_present(self):
        for bad in (replace(A, present=False), replace(A, problem_code=22),
                    replace(A, problem_code=32), replace(A, problem_code=39),
                    replace(A, driver_version="32.0.16.1742"), replace(A, driver_version=None)):
            with self.subTest(bad=bad):
                self.assertFalse(dp.verify_profile(self.profile, [bad, B])[0])
        self.assertFalse(dp.verify_profile(self.profile, [B])[0])

    def test_newer_profile_does_not_require_the_excluded_gpu(self):
        profile = replace(self.profile, expected_version="617.42", required_gpu_ids=[ID_B])
        passed, report = dp.verify_profile(profile, [replace(A, problem_code=22), replace(B, driver_version="32.0.16.1742")])
        self.assertTrue(passed)
        self.assertIn("Outside profile: GTX 1070", report)

    def test_capture_refuses_broken_or_mixed_working_state(self):
        for devices in ([replace(A, problem_code=32), B], [A, replace(B, driver_version="32.0.16.1742")], []):
            with self.subTest(devices=devices), self.assertRaises(ValueError):
                dp.capture_working_profile(devices)
        captured = dp.capture_working_profile([A, B])
        self.assertEqual(captured.required_gpu_ids, [ID_A, ID_B])
        self.assertEqual(captured.expected_version, "560.94")

    def test_saved_profiles_round_trip_without_losing_target_ids(self):
        path = self.root / "profiles.json"
        dp.save_profiles(path, [self.profile])
        self.assertEqual(dp.load_profiles(path), [self.profile])

    def test_capture_and_persistence_include_disabled_gpu_states(self):
        captured = dp.capture_working_profile([replace(A, problem_code=22), B], name="Gaming")
        self.assertEqual(captured.required_gpu_ids, [ID_B])
        self.assertEqual(captured.disabled_gpu_ids, [ID_A])
        path = self.root / "profile-with-states.json"
        dp.save_profiles(path, [captured])
        self.assertEqual(dp.load_profiles(path), [captured])
        self.assertTrue(dp.verify_profile(captured, [replace(A, problem_code=22), B])[0])
        self.assertFalse(dp.verify_profile(captured, [A, B])[0])

    def test_legacy_profiles_load_without_changing_existing_requirements(self):
        from dataclasses import asdict
        data = asdict(self.profile)
        data.pop("disabled_gpu_ids")
        path = self.root / "old-profile.json"
        path.write_text(json.dumps({"schema": 1, "profiles": [data]}))
        loaded = dp.load_profiles(path)[0]
        self.assertEqual(loaded.disabled_gpu_ids, [])
        self.assertEqual(loaded.required_gpu_ids, self.profile.required_gpu_ids)

    def test_conflicting_gpu_roles_are_rejected(self):
        for disabled in ([ID_A], [ID_A, ID_A], ["bad-id"]):
            with self.subTest(disabled=disabled), self.assertRaises(ValueError):
                replace(self.profile, disabled_gpu_ids=disabled).validate()

    def test_corrupt_profile_file_is_preserved(self):
        path = self.root / "profiles.json"
        path.write_text('{"schema": 999}', encoding="utf-8")
        with self.assertRaises(ValueError):
            dp.load_profiles(path)
        self.assertEqual(path.read_text(), '{"schema": 999}')

    def test_duplicate_and_invalid_targets_cannot_be_saved(self):
        for ids in ([], [ID_A, ID_A.lower()], [ID_A + '"'], [r"PCI\VEN_8086&DEV_1234\TEST"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                replace(self.profile, required_gpu_ids=ids).validate()

    def test_full_error_reference_and_code_32_distinction(self):
        self.assertEqual(set(ge.PROBLEMS), set(range(58)))
        self.assertIn("driver service", ge.problem_details(32).lower())
        self.assertIn("device disabled", ge.problem_details(22).lower())
        self.assertTrue(ge.problem_info(20).legacy)
        self.assertIn("Code 999", ge.problem_details(999))
        self.assertEqual(ge.problem_info(999).url, ge.SOURCE)

    def backend(self, after=None, exit_code=0, export_exit=0, before=None):
        context = []
        self.addCleanup(lambda: [item.stop() for item in reversed(context)])
        for name, value in (("APP_DIR", self.root), ("PENDING_FILE", self.root / "pending.json")):
            item = patch.object(gm, name, value)
            item.start()
            context.append(item)
        mocks = {}
        for name, kwargs in (
            ("is_admin", {"return_value": True}),
            ("check_installer", {"return_value": "a" * 64}),
            ("inventory", {"side_effect": [before or [A, B], after or [A, B]]}),
            ("run_command", {"return_value": subprocess.CompletedProcess([], export_exit, b"backup result", b"")}),
            ("change_device", {"return_value": ([A, B], "enabled")}),
        ):
            item = patch.object(gm, name, **kwargs)
            mocks[name] = item.start()
            context.append(item)
        item = patch.object(gm.subprocess, "Popen")
        mocks["Popen"] = item.start()
        mocks["Popen"].return_value.wait.return_value = exit_code
        context.append(item)
        return mocks

    def test_success_backs_up_before_launch_and_verifies_all_gpus(self):
        mocks = self.backend()
        order = Mock()
        order.attach_mock(mocks["run_command"], "export")
        order.attach_mock(mocks["Popen"], "launch")
        devices, report, passed = gm.install_profile(self.profile, True)
        self.assertTrue(passed)
        self.assertIn("verification PASSED", report)
        self.assertEqual(devices, [A, B])
        self.assertEqual(order.mock_calls[0][0], "export")
        args = mocks["run_command"].call_args.args[0]
        self.assertEqual(args[1:3], ["/export-driver", "oem31.inf"])
        self.assertEqual(mocks["run_command"].call_count, 1)
        mocks["Popen"].assert_called_once_with([self.profile.installer], cwd=str(self.root), shell=False)
        self.assertFalse(gm.PENDING_FILE.exists())
        self.assertEqual(len(list((self.root / "driver_history").glob("*/before.json"))), 1)

    def test_full_installer_applies_and_verifies_saved_disabled_state(self):
        disabled = replace(A, problem_code=22)
        profile = replace(self.profile, required_gpu_ids=[ID_B], disabled_gpu_ids=[ID_A])
        mocks = self.backend(before=[A, B], after=[disabled, B])
        mocks["change_device"].return_value = ([disabled, B], "PnPUtil exit code: 0")
        with patch.object(gm, "recovery_file", return_value=self.root / "enable.cmd"):
            _, report, passed = gm.install_profile(profile, True)
        self.assertTrue(passed)
        mocks["change_device"].assert_called_once_with(ID_A, "disable")
        self.assertIn("disabled as saved", report)

    def test_full_installer_reenabling_excluded_gpu_prevents_verified_result(self):
        profile = replace(self.profile, required_gpu_ids=[ID_B], disabled_gpu_ids=[ID_A])
        self.backend(before=[replace(A, problem_code=22), B], after=[A, B])
        _, report, passed = gm.install_profile(profile, True)
        self.assertFalse(passed)
        self.assertIn("must be disabled", report)

    def test_backup_failure_never_launches_installer_or_changes_gpus(self):
        mocks = self.backend(export_exit=5)
        with self.assertRaisesRegex(RuntimeError, "installation was not started"):
            gm.install_profile(self.profile, True)
        mocks["Popen"].assert_not_called()
        mocks["change_device"].assert_not_called()

    def test_successful_installer_exit_does_not_hide_a_wrong_loaded_version(self):
        self.backend(after=[replace(A, driver_version="32.0.16.1742"), B])
        _, report, passed = gm.install_profile(self.profile, True)
        self.assertFalse(passed)
        self.assertIn("617.42, expected 560.94", report)
        self.assertTrue(gm.PENDING_FILE.exists())

    def test_restart_required_keeps_persistent_verification_record(self):
        self.backend(exit_code=3010)
        _, report, passed = gm.install_profile(self.profile, True)
        self.assertFalse(passed)
        self.assertIn("Restart Windows", report)
        self.assertEqual(json.loads(gm.PENDING_FILE.read_text())["state"], "restart required")

    def test_installer_failure_is_not_reported_as_a_success(self):
        self.backend(exit_code=1)
        _, report, passed = gm.install_profile(self.profile, True)
        self.assertFalse(passed)
        self.assertIn("failure/cancellation", report)
        self.assertTrue(gm.PENDING_FILE.exists())

    def test_enable_only_the_disabled_profile_target(self):
        mocks = self.backend(before=[replace(A, problem_code=22), B])
        gm.install_profile(self.profile, True)
        mocks["change_device"].assert_called_once_with(ID_A, "enable")

    def test_disabled_target_without_enable_consent_blocks_install(self):
        mocks = self.backend(before=[replace(A, problem_code=22), B])
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            gm.install_profile(self.profile, False)
        mocks["Popen"].assert_not_called()
        mocks["change_device"].assert_not_called()

    def test_missing_target_blocks_install(self):
        mocks = self.backend(before=[B])
        with self.assertRaisesRegex(RuntimeError, "not present"):
            gm.install_profile(self.profile, True)
        mocks["Popen"].assert_not_called()

    def test_admin_required_before_any_profile_actions(self):
        mocks = self.backend()
        mocks["is_admin"].return_value = False
        with self.assertRaisesRegex(RuntimeError, "administrator"):
            gm.install_profile(self.profile, True)
        mocks["inventory"].assert_not_called()
        mocks["Popen"].assert_not_called()

    def test_changed_installer_blocked_before_launch(self):
        path = Path(self.profile.installer)
        path.write_bytes(b"not a driver - test fixture")
        with patch.object(gm, "run_command") as command:
            with self.assertRaisesRegex(RuntimeError, "changed"):
                gm.check_installer(self.profile, compare_hash=True)
            command.assert_not_called()

    def test_invalid_or_non_nvidia_signature_is_rejected(self):
        path = Path(self.profile.installer)
        path.write_bytes(b"test fixture")
        for status, subject in (("NotSigned", ""), ("Valid", "CN=Different Vendor, O=Different Vendor")):
            result = subprocess.CompletedProcess([], 0, json.dumps({"status": status, "subject": subject}).encode(), b"")
            with self.subTest(status=status), patch.object(gm, "run_command", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "NVIDIA Corporation signature"):
                    gm.check_installer(self.profile)

    def test_valid_nvidia_signature_and_fingerprint_accepted(self):
        path = Path(self.profile.installer)
        path.write_bytes(b"test fixture")
        result = subprocess.CompletedProcess([], 0, json.dumps({"status": "Valid", "subject": "CN=NVIDIA Corporation, O=NVIDIA Corporation, C=US"}).encode(), b"")
        profile = replace(self.profile, sha256=dp.sha256_file(path))
        with patch.object(gm, "run_command", return_value=result):
            self.assertEqual(gm.check_installer(profile, compare_hash=True), profile.sha256)

    def test_last_working_gpu_and_desktop_disable_guards_preserved(self):
        for devices in ([A], [A, replace(B, problem_code=39)], [replace(A, desktop="Primary desktop"), B]):
            with self.subTest(devices=devices), self.assertRaises(RuntimeError):
                gm.validate_action(devices, ID_A, "disable")

    def test_gui_tabs_show_code_32_and_profile_targets(self):
        profile_file = self.root / "profiles.json"
        dp.save_profiles(profile_file, [replace(self.profile, installer="", sha256="")])
        with patch.object(gm, "PROFILES_FILE", profile_file), patch.object(gm, "PENDING_FILE", self.root / "pending.json"), patch.object(gm, "inventory", return_value=[replace(A, problem_code=32), B]), patch.object(gm, "is_admin", return_value=True):
            app = gm.App()
            try:
                deadline = time.monotonic() + 5
                while app.busy and time.monotonic() < deadline:
                    app._application.processEvents()
                    time.sleep(.02)
                self.assertFalse(app.busy)
                app.tree.selectRow(0)
                app.selection_changed()
                self.assertIn("Driver service disabled", app.details.toPlainText())
                self.assertIn(A.name, app.profile_detail.toPlainText())
                app.show_profile_details()
                from PyQt6.QtWidgets import QPlainTextEdit
                dialog = app.dialogs[-1]
                details = dialog.findChild(QPlainTextEdit)
                self.assertIn(ID_A, details.toPlainText())
                self.assertFalse(app.install_profile_button.isEnabled())
                self.assertTrue(app.verify_profile_button.isEnabled())
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
