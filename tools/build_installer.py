"""Compile the themed Inno Setup installer from the portable EXE."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_exe import checksum


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compiler", type=Path)
    arguments = parser.parse_args()
    compiler = arguments.compiler
    if compiler is None:
        compiler = Path(shutil.which("ISCC.exe") or Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6/ISCC.exe")
    if not compiler.is_file():
        raise SystemExit("Supply --compiler with the path to Inno Setup 6.7+ ISCC.exe.")
    subprocess.run([str(compiler), "/Qp", str(ROOT / "tools/installer.iss")], cwd=ROOT, check=True)
    executable = ROOT / "dist/v2.0-github/NVIDIA-GPU-Driver-Profile-Switcher-v2.0-Setup-Windows-x64.exe"
    checksum(executable)
    print(f"Built {executable} ({executable.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
