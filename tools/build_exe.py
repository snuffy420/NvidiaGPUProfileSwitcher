"""Build the self-contained Windows x64 portable release."""
from pathlib import Path
from importlib.metadata import distribution
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gpu_manager import APP_VERSION


def checksum(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def runtime_licenses():
    destination = ROOT / "build/runtime-licenses"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", destination / "Python-LICENSE.txt")
    for package in ("PyQt6", "PyQt6-Qt6", "PyQt6_sip"):
        metadata = distribution(package)
        for entry in metadata.files or []:
            if entry.name.upper() in ("LICENSE", "LICENSE.TXT", "COPYING"):
                shutil.copy2(metadata.locate_file(entry), destination / f"{package}-LICENSE.txt")
    for filename in ("License.txt", "SOURCE.txt"):
        shutil.copy2(ROOT / "tools/7zip" / filename, destination / f"7zip-{filename}")
    return destination


def main():
    if sys.platform != "win32" or platform.machine().upper() not in ("AMD64", "X86_64") or sys.maxsize <= 2**32:
        raise SystemExit("Build with x64 Python on Windows x64.")
    name = f"NVIDIA-GPU-Driver-Profile-Switcher-v{APP_VERSION}-Portable-Windows-x64"
    destination = ROOT / "dist/portable"
    destination.mkdir(parents=True, exist_ok=True)
    licenses = runtime_licenses()
    version_file = ROOT / "build/version-info.txt"
    version_file.write_text("""VSVersionInfo(ffi=FixedFileInfo(filevers=(2,0,0,0), prodvers=(2,0,0,0), mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable('040904B0', [StringStruct('CompanyName', 'zerØ software / snuffy420'), StringStruct('FileDescription', 'NVIDIA GPU Driver Profile Switcher'), StringStruct('FileVersion', '2.0.0.0'), StringStruct('InternalName', 'GPUDriverProfileSwitcher'), StringStruct('ProductName', 'NVIDIA GPU Driver Profile Switcher'), StringStruct('ProductVersion', '2.0'), StringStruct('OriginalFilename', 'NVIDIA-GPU-Driver-Profile-Switcher-v2.0-Portable-Windows-x64.exe')])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])""", encoding="utf-8")
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed", "--noupx",
               "--name", name, "--distpath", str(destination), "--workpath", str(ROOT / "build/pyinstaller"),
               "--specpath", str(ROOT / "build"), "--icon", str(ROOT / "assets/app-icon.ico"),
               "--version-file", str(version_file)]
    for source, target in (("assets/nvidia-logo-dark.png", "assets"), ("assets/nvidia-logo-light.png", "assets"),
                           ("assets/app-icon.ico", "assets"), ("tools/7zip/License.txt", "tools/7zip"),
                           ("tools/7zip/SOURCE.txt", "tools/7zip"), ("LICENSE", "licenses/project"), ("NOTICE.md", "licenses/project")):
        command.extend(["--add-data", f"{ROOT / source}:{target}"])
    command.extend(["--add-data", f"{licenses}:licenses", "--add-binary", f"{ROOT / 'tools/7zip/7zr.exe'}:tools/7zip", str(ROOT / "gpu_manager.py")])
    # Do not let unrelated tools' DLL directories contaminate binary discovery.
    from PyQt6.QtCore import QLibraryInfo
    qt_bin = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.BinariesPath))
    environment = os.environ.copy()
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    environment["PATH"] = os.pathsep.join(map(str, (qt_bin, Path(sys.base_prefix), windows / "System32", windows)))
    for key in ("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        environment.pop(key, None)
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    executable = destination / f"{name}.exe"
    from PyInstaller.archive.readers import CArchiveReader
    bundled = CArchiveReader(str(executable))
    forbidden = [name for name in bundled.toc if Path(name).name.lower() in ("icuuc.dll", "ucrtbase.dll")
                 or Path(name).name.lower().startswith(("api-ms-win-", "ext-ms-win-"))]
    if forbidden:
        raise RuntimeError("Unrelated Windows runtime shims entered the build: " + ", ".join(forbidden))
    uploads = ROOT / f"dist/v{APP_VERSION}-github"
    uploads.mkdir(parents=True, exist_ok=True)
    archive = uploads / f"{name}.7z"
    with tempfile.TemporaryDirectory(prefix="gpu-v2-portable-") as temporary:
        folder = Path(temporary) / name
        (folder / "assets").mkdir(parents=True)
        shutil.copy2(executable, folder / executable.name)
        for filename in ("LICENSE", "NOTICE.md"):
            shutil.copy2(ROOT / filename, folder / filename)
        shutil.copy2(ROOT / "docs/PACKAGE_README.md", folder / "README.md")
        shutil.copy2(ROOT / "assets/app-icon.ico", folder / "assets/app-icon.ico")
        shutil.copytree(licenses, folder / "licenses")
        fresh = Path(temporary) / archive.name
        subprocess.run([str(ROOT / "tools/7zip/7zr.exe"), "a", "-t7z", str(fresh), name], cwd=temporary, check=True, capture_output=True)
        shutil.copy2(fresh, archive)
    for path in (executable, archive):
        checksum(path)
        print(f"Built {path} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
