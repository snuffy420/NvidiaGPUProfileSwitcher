"""EXE extraction, per-GPU catalogue selection and preservation regressions."""
from dataclasses import replace
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cached_drivers as cd
import driver_updates as du
import gpu_manager as gm
import installer_cache as ic
from driver_profiles import DriverProfile, load_profiles, sha256_file
from test_gpu_manager import A, B, ID_A, ID_B
import test_cached_drivers as cache_tests


def listing(name, size=1, attributes="A", extra=""):
    return f"Path = {name}\nSize = {size}\nAttributes = {attributes}\nEncrypted = -\n{extra}\n"


def offer_json(version="620.00", products=("GeForce RTX 3060",), **changes):
    info = dict(IsWHQL="1", IsBeta="0", Is64Bit="1", Version=version, Name="Game%20Ready%20Driver",
                ReleaseDateTime="test date", DetailsURL="https://www.nvidia.com/en-us/drivers/details/1/",
                DownloadURL=f"https://us.download.nvidia.com/Windows/{version}/{version}-driver.exe",
                OSList=[{"OSName": "Windows%2011"}, {"OSName": "Windows%2010%2064-bit"}],
                series=[{"products": [{"productName": item} for item in products]}])
    info.update(changes)
    return {"Success": "1", "IDS": [{"downloadInfo": info}]}


class ExeImportChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gpu-exe-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def synthetic_installer(self):
        source = self.root / "source"
        driver = source / "Display.Driver"
        driver.mkdir(parents=True)
        (driver / "nv_test.inf").write_text(
            '[Version]\nClass=Display\nClassGUID={4d36e968-e325-11ce-bfc1-08002be10318}\n'
            'Provider=%NVIDIA%\nDriverVer=10/20/2026,32.0.16.2000\nCatalogFile=nv_test.cat\n'
            '[Strings]\nNVIDIA="NVIDIA"\n', encoding="utf-16")
        (driver / "nv_test.cat").write_bytes(b"SYNTHETIC catalog, never staged")
        (driver / "driver.sys").write_bytes(b"SYNTHETIC driver, never installed")
        (source / "setup.exe").write_bytes(b"MUST NOT BE EXTRACTED OR RUN")
        archive = self.root / "archive.7z"
        result = subprocess.run([str(ic.extractor()), "a", "-t7z", str(archive), "Display.Driver", "setup.exe"],
                                cwd=source, capture_output=True)
        self.assertEqual(result.returncode, 0)
        path = self.root / "wrong-version-in-filename.exe"
        path.write_bytes(b"MZ" + bytes(510) + archive.read_bytes())
        return path

    def test_archive_only_selects_display_package(self):
        entries = ic.archive_files(listing("setup.exe") + listing("Display.Driver/nv.inf") + listing("Display.Driver/nv.cat"))
        self.assertEqual([entry.name for entry in entries], ["Display.Driver/nv.inf", "Display.Driver/nv.cat"])

    def test_archive_paths_links_and_encryption_rejected_before_extracting(self):
        good = listing("Display.Driver/nv.inf") + listing("Display.Driver/nv.cat")
        for bad in ("../bad", "C:/bad", "/bad", "Display.Driver/nv.inf:stream", "Display.Driver/../bad", "CON"):
            with self.subTest(path=bad), self.assertRaises(ValueError):
                ic.archive_files(good + listing(bad))
        for extra in ("Symbolic Link = target", "Hard Link = target", "Encrypted = +"):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                ic.archive_files(good + listing("other", extra=extra))

    def test_web_installer_and_missing_catalog_rejected(self):
        for data in (listing("setup.exe"), listing("Display.Driver/nv.inf")):
            with self.assertRaises(ValueError):
                ic.archive_files(data)

    def test_real_extractor_caches_sfx_payload_without_executing_setup(self):
        installer = self.synthetic_installer()
        with patch.object(cd, "preflight") as preflight:
            manifest, version = ic.cache_exe(installer, self.root / "cache", [ID_B], sha256_file(installer), self.root / "imports")
        self.assertEqual(version, "620.00")  # INF, never filename
        data, inf = cd.validate_manifest(manifest, self.root / "cache", version)
        self.assertEqual(data["installer_sha256"], sha256_file(installer))
        self.assertTrue((inf.parent / "driver.sys").is_file())
        self.assertFalse(list((self.root / "cache").rglob("setup.exe")))
        self.assertFalse(list((self.root / "imports").iterdir()))
        preflight.assert_called_once()
        self.assertEqual(preflight.call_args.args[2], [ID_B])

    def test_changed_exe_is_rejected_before_extractor(self):
        path = self.root / "driver.exe"
        path.write_bytes(b"changed")
        with patch.object(ic, "run_extractor") as run, self.assertRaisesRegex(ValueError, "changed"):
            ic.cache_exe(path, self.root / "cache", [ID_B], "a" * 64)
        run.assert_not_called()

    def test_new_exe_profile_preserves_ai_profile_and_does_not_change_devices(self):
        installer = self.synthetic_installer()
        ai = DriverProfile("AI tested 560.94", "560.94", required_gpu_ids=[ID_A, ID_B])
        devices = [replace(A, problem_code=22), B]
        with (patch.object(gm, "APP_DIR", self.root), patch.object(gm, "CACHE_ROOT", self.root / "cache"),
              patch.object(gm, "PROFILES_FILE", self.root / "profiles.json"),
              patch.object(gm, "inventory", return_value=devices),
              patch.object(gm, "check_nvidia_exe", return_value=sha256_file(installer)),
              patch.object(cd, "preflight"), patch.object(gm, "change_device") as change,
              patch.object(cd.DeviceDriver, "install") as install):
            profiles, saved = gm.cache_downloaded_installer(installer, [ID_B], [ai])
        self.assertEqual(profiles[0], ai)
        self.assertEqual(saved.required_gpu_ids, [ID_B])
        self.assertEqual(saved.disabled_gpu_ids, [ID_A])
        self.assertEqual(saved.expected_version, "620.00")
        self.assertEqual(load_profiles(self.root / "profiles.json"), profiles)
        change.assert_not_called()
        install.assert_not_called()

    def test_signature_failure_cannot_extract_or_save(self):
        with (patch.object(gm, "inventory", return_value=[A, B]),
              patch.object(gm, "check_nvidia_exe", side_effect=RuntimeError("signature invalid")),
              patch.object(ic, "cache_exe") as extract,
              patch.object(gm, "save_profiles") as save,
              self.assertRaisesRegex(RuntimeError, "signature")):
            gm.cache_downloaded_installer(self.root / "driver.exe", [ID_B], [])
        extract.assert_not_called()
        save.assert_not_called()


class UpdateChecks(unittest.TestCase):
    def test_catalogue_success_value_can_be_result_count(self):
        data = offer_json()
        data["Success"] = "30"
        self.assertEqual(du.parse_offers(data, "GeForce RTX 3060")[0].version, "620.00")

    def test_windows_10_uses_official_id_57(self):
        with (patch.object(du, "resolve_products", return_value=(("GeForce RTX 3060", "120", "942"),)),
              patch.object(du, "read_catalogue", return_value=json.dumps(offer_json()).encode()) as read):
            result = du.check_updates([B], os_id=57)
        self.assertTrue(result[0].offers)
        self.assertIn("osID=57", read.call_args_list[1].args[0])

    def test_exact_support_rejects_3060_ti_for_3060(self):
        self.assertFalse(du.parse_offers(offer_json(products=("GeForce RTX 3060 Ti",)), "GeForce RTX 3060"))

    def test_beta_wrong_os_or_non_whql_not_offered(self):
        for changes in ({"IsBeta": "1"}, {"IsWHQL": "0"}, {"Is64Bit": "0"}, {"OSList": [{"OSName": "Linux"}]}):
            with self.subTest(changes=changes):
                self.assertFalse(du.parse_offers(offer_json(**changes), "GeForce RTX 3060"))

    def test_official_http_link_upgrades_and_other_domains_are_rejected(self):
        offer = du.parse_offers(offer_json(DownloadURL="http://www.nvidia.com/driver.exe"), "GeForce RTX 3060")
        self.assertEqual(offer[0].download_url, "https://www.nvidia.com/driver.exe")
        for url in ("https://nvidia.com.evil.test/test.exe", "https://other.test/test.exe"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                du.parse_offers(offer_json(DownloadURL=url), "GeForce RTX 3060")

    def test_different_branches_are_never_offered_as_both_gpu_update(self):
        legacy = du.parse_offers(offer_json("582.78", ("GeForce GTX 1070",)), "GeForce GTX 1070")
        gaming = du.parse_offers(offer_json(), "GeForce RTX 3060")
        results = [du.GPUUpdate(ID_A, A.name, "560.94", True, legacy), du.GPUUpdate(ID_B, B.name, "617.42", False, gaming)]
        choices = du.update_choices(results)
        self.assertEqual(len(choices), 2)
        self.assertTrue(all(len(item.device_ids) == 1 for item in choices))

    def test_common_newer_package_can_offer_both_without_replacing_old_profiles(self):
        common = du.parse_offers(offer_json(products=("GeForce RTX 3060", "GeForce GTX 1070")), "GeForce RTX 3060")
        results = [du.GPUUpdate(ID_A, A.name, "560.94", True, common), du.GPUUpdate(ID_B, B.name, "617.42", False, common)]
        self.assertEqual(len(du.update_choices(results)[-1].device_ids), 2)

    def test_disabled_gpu_is_checked_and_failure_is_reported_per_card(self):
        data = json.dumps(offer_json("582.78", ("GeForce GTX 1070",))).encode()
        with (patch.object(du, "resolve_products", side_effect=[(("GeForce GTX 1070", "101", "816"),), OSError("offline")]),
              patch.object(du, "read_catalogue", return_value=data)):
            results = du.check_updates([replace(A, problem_code=22), B])
        self.assertTrue(results[0].disabled)
        self.assertEqual(results[0].newer()[0].version, "582.78")
        self.assertIn("offline", results[1].error)

    def test_any_nvidia_device_id_can_use_catalogue_matching(self):
        gpu = replace(B, name="NVIDIA RTX A6000", instance_id=ID_B.replace("2504", "2230"))
        data = offer_json(products=("NVIDIA RTX A6000",))
        with (patch.object(du, "resolve_products", return_value=(("NVIDIA RTX A6000", "122", "935"),)) as resolve,
              patch.object(du, "read_catalogue", return_value=json.dumps(data).encode())):
            results = du.check_updates([gpu])
        resolve.assert_called_once_with(gpu.name, {})
        self.assertTrue(results[0].offers)

    def test_no_update_for_same_or_newer_installed_version(self):
        offers = du.parse_offers(offer_json(), "GeForce RTX 3060")
        for version in ("620.00", "621.00"):
            result = du.GPUUpdate(ID_B, B.name, version, False, offers)
            self.assertFalse(result.newer())
            self.assertFalse(du.update_choices([result]))

    def test_truncated_download_cannot_leave_a_finished_exe(self):
        with tempfile.TemporaryDirectory(prefix="gpu-download-test-") as tmp:
            response = io.BytesIO(b"partial")
            response.headers = {"Content-Length": "100"}
            offer = du.parse_offers(offer_json(), "GeForce RTX 3060")[0]
            with patch.object(du, "open_nvidia", return_value=response), self.assertRaisesRegex(ValueError, "incomplete"):
                du.download_offer(offer, Path(tmp))
            self.assertFalse(list(Path(tmp).iterdir()))


class CatalogueChecks(unittest.TestCase):
    def catalogue(self):
        return {
            (1, None): (("GeForce", "1"), ("NVIDIA RTX / Quadro", "3"), ("Data Center / Tesla", "7")),
            (2, "1"): (("GeForce RTX 70 Series", "170"), ("GeForce RTX 70 Series (Notebooks)", "171")),
            (3, "170"): (("GeForce RTX 7090", "2000"),),
            (3, "171"): (("GeForce RTX 7090 Laptop GPU", "2001"),),
            (2, "3"): (("RTX Professional", "180"),),
            (3, "180"): (("NVIDIA RTX A6000", "2100"),),
            (2, "7"): (("Data Center H-Series", "190"),),
            (3, "190"): (("NVIDIA H100 PCIe", "2200"),),
        }

    def test_dynamic_matching_spans_new_consumer_notebook_workstation_and_datacenter_products(self):
        data = self.catalogue()
        with patch.object(du, "catalogue_entries", side_effect=lambda kind, parent=None: data.get((kind, parent), ())):
            taxonomy = {}
            for name, expected in (("NVIDIA GeForce RTX 7090", "2000"),
                                   ("NVIDIA GeForce RTX 7090 Laptop GPU", "2001"),
                                   ("NVIDIA RTX A6000", "2100"), ("NVIDIA H100 PCIe", "2200")):
                with self.subTest(product=name):
                    matches = du.resolve_products(name, taxonomy)
                    self.assertEqual(len(matches), 1)
                    self.assertEqual(matches[0][2], expected)

    def test_similar_names_never_count_as_exact_products(self):
        data = self.catalogue()
        with patch.object(du, "catalogue_entries", side_effect=lambda kind, parent=None: data.get((kind, parent), ())):
            with self.assertRaisesRegex(ValueError, "exact product"):
                du.resolve_products("NVIDIA GeForce RTX 7090 Ti", {})

    def test_conflicting_desktop_notebook_packages_are_not_guessed(self):
        product = "GeForce RTX 2060"
        gpu = replace(B, name="NVIDIA " + product)
        def response(url):
            data = offer_json(products=(product,))
            if "pfid=2&" in url:
                data["IDS"][0]["downloadInfo"]["DownloadURL"] = "https://us.download.nvidia.com/notebook-driver.exe"
            return json.dumps(data).encode()
        with (patch.object(du, "resolve_products", return_value=((product, "11", "1"), (product, "12", "2"))),
              patch.object(du, "read_catalogue", side_effect=response)):
            result = du.check_updates([gpu])[0]
        self.assertFalse(result.offers)
        self.assertIn("multiple exact-name", result.error)

    def test_model_independent_default_prefers_healthy_primary_adapter(self):
        first = replace(A, name="NVIDIA RTX 6000 Ada Generation", problem_code=22)
        second = replace(B, name="NVIDIA GeForce RTX 5080", desktop="Primary desktop")
        self.assertEqual(gm.preferred_gpu([first, second]), second)


class OutsideGPUChecks(unittest.TestCase):
    setUp = cache_tests.CacheChecks.setUp
    tearDown = cache_tests.CacheChecks.tearDown
    backend = cache_tests.CacheChecks.backend

    def test_saved_disabled_gpu_is_disabled_before_target_driver_install(self):
        disabled = replace(A, problem_code=22)
        profile = replace(self.profile, required_gpu_ids=[ID_B], disabled_gpu_ids=[ID_A])
        mocks = self.backend(before=[A, replace(B, driver_version="32.0.16.1742")], after=[disabled, B])
        mocks["change_device"].return_value = ([disabled, B], "PnPUtil exit code: 0")
        from unittest.mock import Mock
        order = Mock()
        order.attach_mock(mocks["run_command"], "stage")
        order.attach_mock(mocks["change_device"], "state")
        order.attach_mock(mocks["DeviceDriver"], "install")
        with patch.object(gm, "recovery_file", return_value=self.root / "enable.cmd") as recovery:
            _, report, passed = gm.switch_cached_profile(profile, True)
        self.assertTrue(passed)
        self.assertEqual([call[0] for call in order.mock_calls][:3], ["stage", "state", "install"])
        mocks["change_device"].assert_called_once_with(ID_A, "disable")
        recovery.assert_called_once()
        self.assertIn("disabled as saved", report)
        self.assertEqual([call.args[0] for call in mocks["DeviceDriver"].call_args_list], [ID_B])

    def test_saved_disable_failure_stops_before_any_target_driver_install(self):
        profile = replace(self.profile, required_gpu_ids=[ID_B], disabled_gpu_ids=[ID_A])
        mocks = self.backend(before=[A, replace(B, driver_version="32.0.16.1742")], after=[A, B])
        mocks["change_device"].return_value = ([A, B], "state not reached")
        with patch.object(gm, "recovery_file", return_value=self.root / "enable.cmd"):
            _, report, passed = gm.switch_cached_profile(profile, True)
        self.assertFalse(passed)
        mocks["DeviceDriver"].assert_not_called()
        self.assertIn("not reached", report)

    def test_saved_disable_of_desktop_gpu_is_blocked_before_staging(self):
        profile = replace(self.profile, required_gpu_ids=[ID_B], disabled_gpu_ids=[ID_A])
        mocks = self.backend(before=[replace(A, desktop="Primary desktop"), B])
        with self.assertRaisesRegex(RuntimeError, "Move all monitors"):
            gm.switch_cached_profile(profile, True)
        mocks["run_command"].assert_not_called()
        mocks["change_device"].assert_not_called()

    def test_rejected_package_cannot_apply_saved_disabled_states(self):
        profile = replace(self.profile, required_gpu_ids=[ID_B], disabled_gpu_ids=[ID_A])
        mocks = self.backend(before=[A, replace(B, driver_version="32.0.16.1742")], after=[A, B], stage_exit=5)
        with self.assertRaisesRegex(RuntimeError, "Windows rejected"):
            gm.switch_cached_profile(profile, True)
        mocks["change_device"].assert_not_called()

    def test_3060_switch_preserves_disabled_1070_bound_old_package(self):
        profile = replace(self.profile, required_gpu_ids=[ID_B])
        disabled = replace(A, problem_code=22)
        mocks = self.backend(before=[disabled, replace(B, driver_version="32.0.16.1742")], after=[disabled, B])
        _, report, passed = gm.switch_cached_profile(profile, True)
        self.assertTrue(passed)
        self.assertIn("Preserved outside-profile GPU", report)
        self.assertEqual([call.args[0] for call in mocks["DeviceDriver"].call_args_list], [ID_B])
        mocks["change_device"].assert_not_called()

    def test_unexpected_1070_reenable_prevents_reporting_success(self):
        profile = replace(self.profile, required_gpu_ids=[ID_B])
        self.backend(before=[replace(A, problem_code=22), replace(B, driver_version="32.0.16.1742")], after=[A, B])
        _, report, passed = gm.switch_cached_profile(profile, True)
        self.assertFalse(passed)
        self.assertIn("Outside-profile GPU state changed", report)


class UpdateGuiChecks(unittest.TestCase):
    def test_1070_only_offer_is_not_preselected_as_an_ai_upgrade(self):
        with tempfile.TemporaryDirectory(prefix="gpu-update-ui-") as tmp:
            root = Path(tmp)
            legacy = du.parse_offers(offer_json("582.78", ("GeForce GTX 1070",)), "GeForce GTX 1070")
            results = [du.GPUUpdate(ID_A, A.name, "560.94", True, legacy), du.GPUUpdate(ID_B, B.name, "620.00", False)]
            with (patch.object(gm, "PROFILES_FILE", root / "profiles.json"),
                  patch.object(gm, "PENDING_FILE", root / "pending.json"),
                  patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=False)):
                app = gm.App()
                try:
                    from PyQt6.QtWidgets import QButtonGroup, QPushButton
                    app.show_driver_updates(results)
                    dialog = app.dialogs[-1]
                    group = dialog.findChild(QButtonGroup)
                    self.assertEqual(group.checkedId(), -1)
                    download = next(widget for widget in dialog.findChildren(QPushButton)
                                    if widget.text().startswith("Download, cache"))
                    self.assertFalse(download.isEnabled())
                    group.button(0).click()
                    self.assertTrue(download.isEnabled())
                finally:
                    app.close()



if __name__ == "__main__":
    unittest.main()
