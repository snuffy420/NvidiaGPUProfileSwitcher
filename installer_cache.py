"""Extract NVIDIA EXE payloads without ever executing their setup programs."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import subprocess
import tempfile
import sys

import cached_drivers as cache
from driver_profiles import sha256_file, write_json

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(__file__).resolve().parent
EXTRACTOR = RESOURCE_DIR / "tools" / "7zip" / "7zr.exe"
EXTRACTOR_SHA256 = "256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282"


@dataclass(frozen=True)
class ArchiveFile:
    name: str
    size: int


def extractor() -> Path:
    if not EXTRACTOR.is_file() or sha256_file(EXTRACTOR) != EXTRACTOR_SHA256:
        raise RuntimeError("The bundled 7-Zip extractor is missing or changed. Restore tools/7zip/7zr.exe before importing an EXE.")
    return EXTRACTOR


def run_extractor(tool: Path, args: list[str], timeout=600) -> str:
    result = subprocess.run([str(tool), *args], capture_output=True, shell=False, timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = (result.stdout + result.stderr).decode("utf-8-sig", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"7-Zip could not unpack this installer (exit {result.returncode}). "
                           "Use a complete NVIDIA offline driver EXE, or Import extracted INF.\n" + output[-4000:])
    return output


def archive_files(listing: str) -> list[ArchiveFile]:
    """Validate the technical listing before allowing any extraction."""
    files = []
    seen = set()
    for block in re.split(r"\r?\n\s*\r?\n", listing.strip()):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if "Path" not in fields:
            continue
        name = fields["Path"]
        path = PureWindowsPath(name)
        if (not name or path.is_absolute() or path.drive or path.root or ":" in name
                or any(part in {".", ".."} or part.endswith((" ", ".")) for part in path.parts)
                or path.is_reserved() or any(ord(char) < 32 for char in name)):
            raise ValueError("The installer contains an unsafe archive path.")
        key = str(path).casefold()
        if key in seen:
            raise ValueError("The installer contains duplicate archive paths.")
        seen.add(key)
        if (fields.get("Encrypted", "-") != "-" or "Symbolic Link" in fields or "Hard Link" in fields
                or "L" in fields.get("Attributes", "").split(" ")[0]):
            raise ValueError("Linked or encrypted installer contents are not supported.")
        if fields.get("Folder") == "+" or "D" in fields.get("Attributes", "").split(" ")[0]:
            continue
        try:
            size = int(fields["Size"])
        except (KeyError, ValueError) as error:
            raise ValueError("The installer archive listing is incomplete.") from error
        if size < 0:
            raise ValueError("The installer contains an invalid file size.")
        files.append(ArchiveFile(name, size))
    selected = [item for item in files if any(part.casefold() == "display.driver"
                                             for part in PureWindowsPath(item.name).parts[:-1])]
    if not selected or not any(item.name.lower().endswith(".inf") for item in selected):
        raise ValueError("No Display.Driver package was found. Choose the full NVIDIA GPU driver EXE, not the NVIDIA App/web installer.")
    if not any(item.name.lower().endswith(".cat") for item in selected):
        raise ValueError("The installer display package has no signature catalog (.cat).")
    return selected


def cache_exe(installer: Path, cache_root: Path, device_ids: list[str], fingerprint: str,
              work_root: Path | None = None) -> tuple[Path, str]:
    """Caller authenticates the NVIDIA signature first; no device changes here."""
    installer = installer.resolve()
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or sha256_file(installer) != fingerprint:
        raise ValueError("The NVIDIA installer changed after its signature was checked.")
    tool = extractor()
    listing = run_extractor(tool, ["l", "-t7z", "-slt", "-ba", "-sccUTF-8", "--", str(installer)])
    selected = archive_files(listing)
    required = sum(item.size for item in selected)
    work_root = (work_root or APP_DIR / ".driver_imports").resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)
    # Extraction and the permanent copy can live on different volumes.
    reserve = 64 * 1024 * 1024
    if shutil.disk_usage(work_root).free < required + reserve:
        raise RuntimeError("Not enough free space to extract this display driver.")
    if shutil.disk_usage(cache_root).free < required + reserve:
        raise RuntimeError("Not enough free space to cache this display driver.")
    if os.path.splitdrive(str(work_root))[0].casefold() == os.path.splitdrive(str(cache_root))[0].casefold():
        if shutil.disk_usage(work_root).free < required * 2 + reserve:
            raise RuntimeError("Not enough free space to extract and cache this display driver.")
    with tempfile.TemporaryDirectory(prefix="nvidia-", dir=work_root) as temporary:
        work = Path(temporary).resolve()
        if work == work_root or not work.is_relative_to(work_root):
            raise RuntimeError("The temporary extraction directory is outside the import workspace.")
        payload = work / "payload"
        entries = work / "selected-files.txt"
        entries.write_text("\n".join(item.name for item in selected) + "\n", encoding="utf-8")
        run_extractor(tool, ["x", "-t7z", "-y", "-spd", "-sccUTF-8", "-scsUTF-8",
                             f"-o{payload}", str(installer), f"@{entries}"])
        if sha256_file(installer) != fingerprint:
            raise ValueError("The NVIDIA installer changed during extraction. Import it again.")
        for item in selected:
            path = payload.joinpath(*PureWindowsPath(item.name).parts)
            if not path.resolve().is_relative_to(payload.resolve()) or not path.is_file() or path.stat().st_size != item.size:
                raise ValueError("The extracted driver package is incomplete or outside its folder.")
        failures = []
        for inf in sorted(payload.rglob("*.inf")):
            try:
                metadata = cache.inf_metadata(inf)
                cache.preflight(inf, metadata.version, device_ids)
            except (OSError, ValueError, RuntimeError) as error:
                failures.append(f"{inf.name}: {error}")
                continue
            manifest = cache.cache_package(inf, cache_root)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["installer_source"] = str(installer)
            data["installer_sha256"] = fingerprint
            write_json(manifest, data)
            return manifest, metadata.version
        raise RuntimeError("No display INF in this EXE supports every selected GPU. "
                           "Select only GPUs supported by this driver package.\n"
                           + "\n".join(failures[-8:]))
