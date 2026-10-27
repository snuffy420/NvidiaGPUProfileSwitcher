"""Build a clean source release and GitHub repository upload ZIP."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from gpu_manager import APP_VERSION
from build_exe import checksum


def main():
    name = f"NVIDIA-GPU-Driver-Profile-Switcher-v{APP_VERSION}"
    output = ROOT / f"dist/v{APP_VERSION}-github"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{name}-Source.7z"
    files = [ROOT / value for value in (
        "gpu_manager.py", "cached_drivers.py", "driver_profiles.py", "driver_updates.py", "gpu_errors.py", "installer_cache.py", "ui_theme.py", "qt_ui.py",
        "requirements.txt", "NVIDIA GPU Driver Profile Switcher.cmd", "NVIDIA GPU Driver Profile Switcher.vbs", "README.md", "LICENSE", "NOTICE.md", ".gitignore",
        "assets/nvidia-logo-dark.png", "assets/nvidia-logo-light.png", "assets/app-icon.ico", "assets/app-icon.png", "assets/app-icon.svg", "assets/contact-button.svg",
        "docs/contact.html", "docs/CONTACT.md", "docs/PACKAGE_README.md", "CHANGELOG.md", "V2.0_RELEASE_NOTES.md", ".nojekyll",
        "tools/build_exe.py", "tools/build_installer.py", "tools/build_release.py", "tools/create_installer_art.py", "tools/installer.iss",
        "tools/installer_art/wizard.bmp", "tools/installer_art/wizard-small.bmp", "tools/7zip/7zr.exe", "tools/7zip/License.txt", "tools/7zip/SOURCE.txt",
    )]
    files.extend(ROOT / "assets/screenshots" / f"{name}.png" for name in ("driver-library-dark", "driver-library-light", "gpu-inventory", "profile-editor", "cache-manager", "error-reference", "dark", "light"))
    repository_zip = ROOT / "dist" / f"{name}-GitHub-Repository.zip"
    with tempfile.TemporaryDirectory(prefix="gpu-v2-source-") as temporary:
        folder = Path(temporary) / name
        folder.mkdir()
        for source in files:
            if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise ValueError("Source release inputs must be regular project files.")
            target = folder / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        fresh = Path(temporary) / archive.name
        subprocess.run([str(ROOT / "tools/7zip/7zr.exe"), "a", "-t7z", str(fresh), name], cwd=temporary, check=True, capture_output=True)
        shutil.copy2(fresh, archive)
        with zipfile.ZipFile(repository_zip, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for source in sorted(folder.rglob("*")):
                if source.is_file():
                    bundle.write(source, source.relative_to(folder).as_posix())
    checksum(archive)
    checksum(repository_zip)
    print(f"Built {archive} and {repository_zip}; {len(files)} explicit public files, no machine data.")


if __name__ == "__main__":
    main()
