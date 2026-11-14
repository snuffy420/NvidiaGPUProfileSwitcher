"""Capture the actual Qt UI with isolated demonstration data; no GPU operations."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PyQt6.QtWidgets import QDialog
import gpu_manager as gm
import cached_drivers as cache
from driver_profiles import DriverProfile, save_profiles


def main():
    destination = ROOT / "assets/screenshots"
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gpu-v2-screenshots-") as temporary:
        root = Path(temporary).resolve()
        assert root.parent == Path(tempfile.gettempdir()).resolve()
        older_id = r"PCI\VEN_10DE&DEV_1B06\DEMO_OLDER"
        newer_id = r"PCI\VEN_10DE&DEV_2704\DEMO_NEWER"
        older = gm.GPU("NVIDIA GeForce GTX 1080 Ti", older_id, "OK", True, 22, "32.0.15.6094", "oem24.inf", "PCI bus 2, device 0, function 0")
        newer = gm.GPU("NVIDIA GeForce RTX 4080", newer_id, "OK", True, 0, "32.0.16.1742", "oem36.inf", "PCI bus 1, device 0, function 0", "Primary desktop")
        display_cache = Path(r"C:\GPU Driver Profiles\driver_cache")
        manifests = [display_cache / "560.94-shared/cache-manifest.json", display_cache / "617.42-gaming/cache-manifest.json"]
        profiles = [DriverProfile("Multi-GPU · shared driver", "560.94", required_gpu_ids=[older_id, newer_id], cache_manifest=str(manifests[0])),
                    DriverProfile("Gaming · newer GPU", "617.42", required_gpu_ids=[newer_id], disabled_gpu_ids=[older_id], cache_manifest=str(manifests[1]))]
        packages = [cache.CachedPackage(manifests[0], "560.94", manifests[0].parent / "package/nv_dispi.inf"),
                    cache.CachedPackage(manifests[1], "617.42", manifests[1].parent / "package/nv_dispi.inf")]
        save_profiles(root / "profiles.json", profiles)
        def references(manifest, *_):
            return ([item.name for item in profiles if item.cache_manifest == str(manifest)], [])
        with (patch.object(gm.App, "refresh"), patch.object(gm, "is_admin", return_value=True),
              patch.object(gm, "APP_DIR", root), patch.object(gm, "CACHE_ROOT", display_cache), patch.object(gm, "CACHE_ROOTS", []),
              patch.object(gm, "PROFILES_FILE", root / "profiles.json"), patch.object(gm, "SETTINGS_FILE", root / "settings.json"),
              patch.object(gm, "PENDING_FILE", root / "pending.json"), patch.object(gm, "discover_all_cached_packages", return_value=packages),
              patch.object(gm, "cache_references", side_effect=references)):
            window = gm.App()
            try:
                window.resize(1320, 860)
                window.display_inventory([older, newer])
                window.select_profile(profiles[1].name)
                window.show()
                def capture(widget, filename):
                    for _ in range(4):
                        window._application.processEvents()
                    if not widget.grab().save(str(destination / filename)):
                        raise RuntimeError(f"Could not save {filename}")
                for mode in ("Dark", "Light"):
                    window.theme_choice.setCurrentText(mode)
                    capture(window, f"driver-library-{mode.lower()}.png")
                window.theme_choice.setCurrentText("Dark")
                window.notebook.setCurrentIndex(0)
                window.tree.selectRow(1)
                capture(window, "gpu-inventory.png")
                window.notebook.setCurrentIndex(2)
                capture(window, "error-reference.png")
                window.notebook.setCurrentIndex(1)
                for callback, filename in ((lambda: window.edit_profile(profiles[1]), "profile-editor.png"), (window.manage_caches, "cache-manager.png")):
                    callback()
                    window._application.processEvents()
                    child = next(dialog for dialog in window.findChildren(QDialog) if dialog.isVisible())
                    capture(child, filename)
                    child.reject()
                    window._application.processEvents()
                for mode in ("dark", "light"):
                    (destination / f"{mode}.png").write_bytes((destination / f"driver-library-{mode}.png").read_bytes())
            finally:
                window.close()
    print(f"Captured six v{gm.APP_VERSION} Qt views using demonstration data; no driver, network, or device actions ran.")


if __name__ == "__main__":
    main()
