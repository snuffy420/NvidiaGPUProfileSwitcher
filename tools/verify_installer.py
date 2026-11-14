"""Verify an isolated build of Setup, without changing an existing installation."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import winreg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gpu_manager import APP_VERSION
TEST_ID = "{B7587D93-28ED-4EA4-A9CB-879AD8CF2118}"
NAME = f"NVIDIA-GPU-Driver-Profile-Switcher-v{APP_VERSION}-Portable-Windows-x64.exe"


def main():
    compiler = Path(sys.argv[1])
    output = ROOT / "build/installer-verification"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), "/Q", f"/O{output}", "/FSetup-Verification", "/DAppIdValue={" + TEST_ID,
                    str(ROOT / "tools/installer.iss")], check=True, cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix="gpu-installer-verification-") as temporary:
        folder = Path(temporary).resolve()
        assert folder.parent == Path(tempfile.gettempdir()).resolve()
        installed = folder / "app"
        subprocess.run([str(output / "Setup-Verification.exe"), "/VERYSILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART",
                        "/NOICONS", "/TASKS=", f"/DIR={installed}", f"/LOG={folder / 'setup.log'}"], check=True, timeout=90)
        uninstaller = installed / "unins000.exe"
        try:
            assert hashlib.sha256((installed / NAME).read_bytes()).digest() == hashlib.sha256((ROOT / "dist/portable" / NAME).read_bytes()).digest()
            assert (installed / "assets/app-icon.ico").read_bytes() == (ROOT / "assets/app-icon.ico").read_bytes()
            environment = os.environ.copy()
            environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
            for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
                environment.pop(key, None)
            report = folder / "installed-runtime.json"
            subprocess.run([str(installed / NAME), "--check-runtime", str(report)], env=environment, check=True, timeout=45)
            data = json.loads(report.read_text())
            assert data["icon"] == "loaded" and data["frozen"]
            assert Path(data["data_directory"]) == installed
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{TEST_ID}_is1", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                assert winreg.QueryValueEx(key, "DisplayIcon")[0] == str(installed / "assets/app-icon.ico")
            # Synthetic user data must survive uninstallation.
            (installed / "driver_profiles.json").write_text('{"schema":1,"profiles":[]}', encoding="utf-8")
            (installed / "driver_cache").mkdir()
            (installed / "driver_cache/verification.txt").write_text("synthetic cache marker", encoding="utf-8")
        finally:
            subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], check=True, timeout=60)
        assert not (installed / NAME).exists()
        assert (installed / "driver_profiles.json").exists()
        assert (installed / "driver_cache/verification.txt").read_text() == "synthetic cache marker"
        print("Isolated Setup install/uninstall passed; exact verified EXE and original icon installed, Qt runtime loaded, user data retained. Existing installation unchanged.")


if __name__ == "__main__":
    main()
