"""Developer smoke check: isolated portable startup; never changes drivers."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gpu_manager import APP_VERSION
NAME = f"NVIDIA-GPU-Driver-Profile-Switcher-v{APP_VERSION}-Portable-Windows-x64"
ARCHIVE = ROOT / f"dist/v{APP_VERSION}-github" / f"{NAME}.7z"


def main():
    with tempfile.TemporaryDirectory(prefix="gpu-portable-smoke-") as temporary:
        staging = Path(temporary).resolve()
        assert staging.parent == Path(tempfile.gettempdir()).resolve()
        subprocess.run([str(ROOT / "tools/7zip/7zr.exe"), "x", str(ARCHIVE), f"-o{staging}"],
                       check=True, capture_output=True)
        assert len(list(staging.iterdir())) == 1, "Expected a single containing folder."
        folder = staging / NAME
        assert {path.name for path in folder.iterdir()} == {f"{NAME}.exe", "README.md", "LICENSE", "NOTICE.md", "assets", "licenses"}
        assert (folder / "README.md").read_bytes() == (ROOT / "docs/PACKAGE_README.md").read_bytes()
        assert (folder / "assets/app-icon.ico").read_bytes() == (ROOT / "assets/app-icon.ico").read_bytes()
        executable = folder / f"{NAME}.exe"
        environment = os.environ.copy()
        environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        for variable in ("PYTHONHOME", "PYTHONPATH"):
            environment.pop(variable, None)
        report = folder / "runtime.json"
        runtime = subprocess.Popen([str(executable), "--check-runtime", str(report)], cwd=folder, env=environment)
        try:
            assert runtime.wait(timeout=60) == 0, "Portable runtime exited with an error."
        finally:
            if runtime.poll() is None:
                for child in psutil.Process(runtime.pid).children(recursive=True):
                    child.kill()
                runtime.kill()
                runtime.wait(timeout=15)
        result = json.loads(report.read_text(encoding="utf-8"))
        assert result["frozen"] is True
        assert Path(result["data_directory"]) == folder
        assert Path(result["resource_directory"]) != folder
        assert result["extractor"] == "verified and started"
        assert result["icon"] == "loaded"
        assert all(width > 0 and height > 0 for width, height in result["logos"])

        # Open the normal GUI, then close only a window owned by this copied EXE.
        process = subprocess.Popen([str(executable)], cwd=folder, env=environment)
        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        windows = []

        @callback_type
        def find_window(handle, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            try:
                owned = Path(psutil.Process(pid.value).exe()).resolve() == executable
            except (psutil.Error, OSError):
                return True
            title = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(handle, title, len(title))
            if owned and user32.IsWindowVisible(handle) and title.value == f"NVIDIA GPU Driver Profile Switcher v{APP_VERSION}":
                windows.append(handle)
            return True

        try:
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline and not windows:
                if process.poll() is not None:
                    raise RuntimeError("Portable EXE exited before showing its GUI.")
                user32.EnumWindows(find_window, 0)
                time.sleep(0.2)
            assert windows, "Portable GUI did not open."
            # The normal GUI may still be reading inventory. Do not click driver
            # controls or wait on its busy-close dialog; stop only this test copy.
            time.sleep(1)
        finally:
            if process.poll() is None:
                children = psutil.Process(process.pid).children(recursive=True)
                for child in reversed(children):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                psutil.wait_procs(children, timeout=10)
                process.kill()
                process.wait(timeout=15)
        print(json.dumps(result, indent=2))
        print("Portable runtime and normal GUI startup passed with Python removed from PATH.")


if __name__ == "__main__":
    main()
