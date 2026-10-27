"""Windows GPU Manager. Python 3.10+ and PyQt6.

Opening this app only reads devices. Mutations require elevation, an explicit
selection and confirmation. --list is a read-only JSON diagnostic command.
"""
from __future__ import annotations

import base64
import cached_drivers as cache
import installer_cache
import driver_updates
from ui_theme import enable_dpi_awareness, load_settings, save_settings
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import tempfile

from driver_profiles import (DriverProfile, capture_working_profile, is_nvidia,
                             load_profiles, nvidia_version, save_profiles,
                             sha256_file, verify_profile, write_json)
from gpu_errors import PROBLEMS, SOURCE as ERROR_SOURCE, problem_details, problem_info

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_DIR = Path(__file__).resolve().parent
PROFILES_FILE = APP_DIR / "driver_profiles.json"
PENDING_FILE = APP_DIR / "pending-driver-switch.json"
CACHE_ROOT = APP_DIR / "driver_cache"
CACHE_ROOTS = []
SETTINGS_FILE = APP_DIR / "ui_settings.json"
APP_NAME = "NVIDIA GPU Driver Profile Switcher"
APP_VERSION = "2.0"


INVENTORY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$devices = @(Get-PnpDevice -Class Display | ForEach-Object {
    $device = $_
    $properties = @{}
    Get-PnpDeviceProperty -InstanceId $device.InstanceId | ForEach-Object {
        $properties[$_.KeyName] = $_.Data
    }
    [pscustomobject]@{
        name = $device.FriendlyName
        instance_id = $device.InstanceId
        status = [string]$device.Status
        present = $properties['DEVPKEY_Device_IsPresent']
        problem_code = $properties['DEVPKEY_Device_ProblemCode']
        driver_version = $properties['DEVPKEY_Device_DriverVersion']
        driver_inf = $properties['DEVPKEY_Device_DriverInfPath']
        location = $properties['DEVPKEY_Device_LocationInfo']
    }
})
ConvertTo-Json -InputObject $devices -Depth 4 -Compress
"""


def system_exe(relative: str) -> str:
    # Sysnative reaches the native tools from a 32-bit Python on 64-bit Windows.
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    folder = "Sysnative" if (root / "Sysnative").is_dir() else "System32"
    return str(root / folder / relative)


def encoded_ps(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def run_command(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, timeout=timeout, shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def output_text(result: subprocess.CompletedProcess, utf8: bool = False) -> str:
    encoding = "utf-8-sig" if utf8 else "oem"
    return (result.stdout + result.stderr).decode(encoding, errors="replace").strip()


def is_admin() -> bool:
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def preferred_gpu(cards):
    """Prefer a healthy desktop adapter without assuming any model or index."""
    candidates = [gpu for gpu in cards if gpu.problem_code == 0] or cards
    return next((gpu for gpu in candidates if gpu.desktop == "Primary desktop"), candidates[0])


@dataclass(frozen=True)
class GPU:
    name: str
    instance_id: str
    status: str
    present: bool | None
    problem_code: int | None
    driver_version: str | None = None
    driver_inf: str | None = None
    location: str | None = None
    desktop: str = "Unknown"

    @property
    def state(self) -> str:
        if self.present is False:
            return "Not present / hidden"
        if self.problem_code == 22:
            return "Disabled (22)"
        if self.problem_code == 0:
            return "Started / no PnP error"
        if self.problem_code is None:
            return f"{self.status} / code unknown"
        return f"Code {self.problem_code}: {problem_info(self.problem_code).title}"


def desktop_devices() -> dict[str, str]:
    """Only match complete PnP instance IDs; never guess from a GPU name."""
    class DisplayDevice(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128),
        ]

    enum = ctypes.windll.user32.EnumDisplayDevicesW
    enum.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                     ctypes.POINTER(DisplayDevice), wintypes.DWORD]
    enum.restype = wintypes.BOOL
    found = {}
    index = 0
    while True:
        device = DisplayDevice()
        device.cb = ctypes.sizeof(device)
        if not enum(None, index, ctypes.byref(device), 0):
            break
        index += 1
        state = ("Primary desktop" if device.StateFlags & 4 else
                 "Desktop attached" if device.StateFlags & 1 else "No desktop attached")
        key = device.DeviceID.upper()
        if key and (key not in found or state == "Primary desktop"):
            found[key] = state
    return found


def inventory() -> list[GPU]:
    if os.name != "nt":
        raise RuntimeError("This utility requires Windows 10 version 2004+ or Windows 11.")
    result = run_command([
        system_exe(r"WindowsPowerShell\v1.0\powershell.exe"),
        "-NoLogo", "-NoProfile", "-NonInteractive",
        "-EncodedCommand", encoded_ps(INVENTORY_SCRIPT),
    ])
    if result.returncode:
        raise RuntimeError(f"Windows could not read the GPU inventory.\n{output_text(result, utf8=True)}")
    # PowerShell may put CLIXML progress/warnings on stderr even on success.
    # Parse only stdout as the JSON payload.
    devices = json.loads(result.stdout.decode("utf-8-sig").strip())
    if not isinstance(devices, list):
        raise RuntimeError("Windows returned an unexpected GPU inventory.")
    desktops = desktop_devices()
    return [GPU(**item, desktop=desktops.get(item["instance_id"].upper(), "Unknown"))
            for item in devices]


def validate_action(devices: list[GPU], instance_id: str, action: str) -> GPU:
    if action not in {"enable", "disable"}:
        raise ValueError("Unsupported device action.")
    if not re.fullmatch(r"[A-Za-z0-9_\\&.{}#+:\-]+", instance_id):
        raise ValueError("Unexpected device ID; no action taken.")
    matches = [gpu for gpu in devices if gpu.instance_id.upper() == instance_id.upper()]
    if len(matches) != 1:
        raise RuntimeError("The selected device changed or disappeared. Refresh and select it again.")
    target = matches[0]
    if target.present is not True:
        raise RuntimeError("Windows cannot confirm that this device is present. No action taken.")
    if action == "disable":
        if target.problem_code == 22:
            raise RuntimeError("This GPU is already disabled.")
        if target.desktop in {"Primary desktop", "Desktop attached"}:
            raise RuntimeError("Move all monitors off this GPU first, then refresh the inventory.")
        alternatives = [gpu for gpu in devices
                        if gpu.instance_id.upper() != instance_id.upper()
                        and gpu.present is True and gpu.problem_code == 0]
        if not alternatives:
            raise RuntimeError("No other present GPU has a healthy driver. Repair or enable the other GPU first.")
    return target


def recovery_file(gpu: GPU, devices: list[GPU]) -> Path:
    """Save exact-device recovery before changing anything; no auto-run tasks."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = APP_DIR / "recovery" / stamp
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "inventory-before.json").write_text(
        json.dumps([asdict(item) for item in devices], indent=2), encoding="utf-8")
    # An encoded PowerShell command avoids cmd.exe interpreting '&' in PCI IDs.
    device_id = gpu.instance_id.replace("'", "''")
    script = (
        "if (-not ([Security.Principal.WindowsPrincipal]"
        "[Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole("
        "[Security.Principal.WindowsBuiltInRole]::Administrator)) {"
        " Write-Host 'Right-click this CMD file and select Run as administrator.'; exit 1 }; "
        f"& ($env:SystemRoot + '\\System32\\pnputil.exe') /enable-device '{device_id}'; "
        "exit $LASTEXITCODE"
    )
    path = folder / "ENABLE-THIS-GPU.cmd"
    path.write_text(
        '@echo off\n'
        '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
        f'-NoLogo -NoProfile -EncodedCommand {encoded_ps(script)}\n'
        'echo.\necho Restart Windows if requested above.\npause\n',
        encoding="ascii", newline="\r\n")
    return path


def change_device(instance_id: str, action: str) -> tuple[list[GPU], str]:
    if not is_admin():
        raise RuntimeError("Restart as administrator before changing a GPU.")
    # Inventory and safety checks run again immediately before the mutation.
    validate_action(inventory(), instance_id, action)
    result = run_command([system_exe("pnputil.exe"), f"/{action}-device", instance_id])
    detail = f"PnPUtil exit code: {result.returncode}\n{output_text(result)}"
    if result.returncode not in (0, 3010):
        raise RuntimeError(detail + "\nRefresh to check the current state.")
    try:
        devices = inventory()
    except Exception as error:
        raise RuntimeError(detail + f"\nAction completed, but verification failed: {error}") from error
    target = next((gpu for gpu in devices if gpu.instance_id.upper() == instance_id.upper()), None)
    verified = target is not None and target.present is True and (
        target.problem_code == 22 if action == "disable" else target.problem_code == 0)
    detail += (f"\nVerified: {target.state}" if verified else
               "\nRequested state is NOT verified. Restart if requested, then refresh.")
    if result.returncode == 3010:
        detail += "\nWindows reports that a restart is required."
    return devices, detail


def check_installer(profile: DriverProfile, compare_hash: bool = False) -> str:
    """Authenticate a saved local installer before launching it elevated."""
    profile.validate(require_installer=compare_hash)
    return check_nvidia_exe(Path(profile.installer), profile.sha256 if compare_hash else "")


def check_nvidia_exe(path: Path, expected_hash: str = "") -> str:
    """Authenticate an EXE without needing a manually entered driver version."""
    if not path.is_absolute() or path.suffix.lower() != ".exe" or not path.is_file():
        raise ValueError("Choose a downloaded NVIDIA .exe driver installer.")
    fingerprint = sha256_file(path)
    if expected_hash and fingerprint != expected_hash:
        raise RuntimeError("The installer changed since this profile was saved. Review it and save the profile again.")
    literal = str(path).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
        "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        f"$signature=Get-AuthenticodeSignature -LiteralPath '{literal}'; "
        "[pscustomobject]@{status=[string]$signature.Status; "
        "subject=$signature.SignerCertificate.Subject} | ConvertTo-Json -Compress"
    )
    result = run_command([system_exe(r"WindowsPowerShell\v1.0\powershell.exe"),
                          "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_ps(script)])
    if result.returncode:
        raise RuntimeError("Could not check the installer signature.\n" + output_text(result, utf8=True))
    signature = json.loads(result.stdout.decode("utf-8-sig"))
    if signature.get("status") != "Valid" or not re.search(
            r"(?:^|,\s*)O=NVIDIA Corporation(?:,|$)", signature.get("subject") or "", re.IGNORECASE):
        raise RuntimeError("This file does not have a valid NVIDIA Corporation signature. Choose the official NVIDIA driver installer.")
    return fingerprint


def cache_downloaded_installer(path: Path, device_ids: list[str], profiles: list[DriverProfile],
                               name: str = "", expected_version: str = "") -> tuple[list[DriverProfile], DriverProfile]:
    """Create/reuse a profile from the EXE's INF metadata. Never runs setup."""
    before = inventory()
    by_id = {gpu.instance_id.upper(): gpu for gpu in before}
    if not device_ids or len({key.upper() for key in device_ids}) != len(device_ids):
        raise ValueError("Select at least one GPU, without duplicates.")
    targets = [by_id.get(key.upper()) for key in device_ids]
    if any(gpu is None or gpu.present is not True or not is_nvidia(gpu) for gpu in targets):
        raise ValueError("A selected NVIDIA GPU is no longer present. Refresh and select it again.")
    fingerprint = check_nvidia_exe(path)
    manifest, version = installer_cache.cache_exe(path, CACHE_ROOT, device_ids, fingerprint,
                                                 work_root=APP_DIR / ".driver_imports")
    if expected_version and nvidia_version(expected_version) != version:
        raise RuntimeError(f"The downloaded EXE contains {version}, but NVIDIA offered {expected_version}. "
                           "No profile was saved and no drivers were changed.")
    existing = next((item for item in profiles if nvidia_version(item.expected_version) == version
                     and {key.upper() for key in item.required_gpu_ids} == {key.upper() for key in device_ids}), None)
    if existing:
        title = existing.name
    else:
        label = f"Gaming {targets[0].name}" if len(targets) == 1 else "AI candidate (multi-GPU)"
        base = (name.strip() or label) + " " + version
        title = base
        index = 2
        while any(item.name.casefold() == title.casefold() for item in profiles):
            title = f"{base} ({index})"
            index += 1
    disabled = sorted({gpu.instance_id for gpu in before if is_nvidia(gpu) and gpu.present is True
                       and gpu.instance_id.upper() not in {key.upper() for key in device_ids}
                       and gpu.problem_code == 22}
                      | set(existing.disabled_gpu_ids if existing else []))
    saved = DriverProfile(title, version, str(path.resolve()), device_ids,
                          fingerprint, str(manifest), disabled_gpu_ids=disabled)
    updated = [saved if item is existing else item for item in profiles]
    if existing is None:
        updated.append(saved)
    save_profiles(PROFILES_FILE, updated)
    return updated, saved


def install_profile(profile: DriverProfile, reenable: bool) -> tuple[list[GPU], str, bool]:
    """Launch the visible official installer and report the actual loaded state.

    No silent switches, automatic restarts, forced INF downgrades, or service
    edits. Reinstalling a profile affects the system's NVIDIA driver stack.
    """
    if not is_admin():
        raise RuntimeError("Restart as administrator before installing a driver profile.")
    check_installer(profile, compare_hash=True)
    before = inventory()
    by_id = {gpu.instance_id.upper(): gpu for gpu in before}
    targets = [by_id.get(device_id.upper()) for device_id in profile.required_gpu_ids]
    if any(gpu is None or gpu.present is not True for gpu in targets):
        raise RuntimeError("A GPU required by this profile is not present. Refresh and review the profile first.")
    if not reenable and any(gpu.problem_code == 22 for gpu in targets):
        raise RuntimeError("A profile GPU is disabled. Enable it first or select Re-enable profile GPUs.")
    disabled_targets = [by_id.get(device_id.upper()) for device_id in profile.disabled_gpu_ids]
    if any(gpu is None or gpu.present is not True for gpu in disabled_targets):
        raise RuntimeError("A GPU saved as disabled in this profile is not present. Review its saved states.")
    for gpu in disabled_targets:
        if gpu.problem_code != 22:
            validate_action(before, gpu.instance_id, "disable")
    excluded = {key.upper() for key in profile.disabled_gpu_ids}
    if any(gpu.problem_code != 22 for gpu in disabled_targets) and not any(
            gpu.present is True and gpu.problem_code == 0 and gpu.instance_id.upper() not in excluded for gpu in before):
        raise RuntimeError("This profile would disable every healthy GPU before a working replacement is available.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    history = APP_DIR / "driver_history" / stamp
    history.mkdir(parents=True, exist_ok=False)
    write_json(history / "before.json", [asdict(gpu) for gpu in before])
    write_json(history / "profile.json", asdict(profile))
    # Keep the currently installed display packages as an additional fallback.
    # These exported INFs are not a complete NVIDIA application installer.
    infs = {gpu.driver_inf for gpu in before if is_nvidia(gpu) and gpu.present is True
            and gpu.driver_inf and re.fullmatch(r"oem\d+\.inf", gpu.driver_inf, re.IGNORECASE)}
    for inf in sorted(infs):
        destination = history / "driver-packages" / inf
        destination.mkdir(parents=True, exist_ok=False)
        result = run_command([system_exe("pnputil.exe"), "/export-driver", inf, str(destination)], timeout=180)
        if result.returncode != 0:
            raise RuntimeError("Could not back up the current display driver; installation was not started.\n"
                               + output_text(result))
    write_json(PENDING_FILE, {"profile": profile.name, "started": datetime.now().isoformat(),
                             "history": str(history), "state": "installation pending verification"})
    enabled = []
    for gpu in disabled_targets:
        if gpu.problem_code == 22:
            continue
        helper = recovery_file(gpu, before)
        changed, detail = change_device(gpu.instance_id, "disable")
        actual = next((item for item in changed if item.instance_id.upper() == gpu.instance_id.upper()), None)
        if actual is None or actual.present is not True or actual.problem_code != 22:
            raise RuntimeError("Saved disabled state was not reached. The NVIDIA installer was not launched.")
        enabled.append(f"Disabled per profile {gpu.name}. Recovery: {helper}\n{detail}")
    if reenable:
        for gpu in targets:
            if gpu.problem_code == 22:
                _devices, detail = change_device(gpu.instance_id, "enable")
                enabled.append(f"Re-enable {gpu.name}:\n{detail}")
    path = Path(profile.installer)
    # Run the downloaded EXE interactively. Some self-extractors return before
    # setup finishes; only inventory verification establishes the loaded state.
    process = subprocess.Popen([str(path)], cwd=str(path.parent), shell=False)
    exit_code = process.wait()
    try:
        after = inventory()
    except Exception as error:
        raise RuntimeError(f"Installer exited with {exit_code}, but GPU verification failed. "
                           f"History: {history}. Refresh/verify after setup and any restart.\n{error}") from error
    passed, verification = verify_profile(profile, after)
    passed = passed and exit_code in (0, 3010, 1641)
    # A restart-required result stays pending even if the current state matches.
    pending_restart = exit_code in (3010, 1641) or any("PnPUtil exit code: 3010" in line for line in enabled)
    write_json(history / "after.json", [asdict(gpu) for gpu in after])
    write_json(history / "result.json", {"installer_exit_code": exit_code,
               "driver_device_match": passed, "restart_requested": pending_restart})
    if passed and not pending_restart:
        PENDING_FILE.unlink(missing_ok=True)
    else:
        write_json(PENDING_FILE, {"profile": profile.name, "history": str(history),
                                 "state": "restart required" if pending_restart else "verification pending",
                                 "installer_exit_code": exit_code})
    report = (f"Installer exit code: {exit_code}\nHistory and driver backup: {history}\n"
              + ("\n".join(enabled) + "\n" if enabled else "") + verification)
    if exit_code not in (0, 3010, 1641):
        report += "\nInstaller reported failure/cancellation. The driver switch is not confirmed."
    if pending_restart:
        report += "\nRestart Windows, reopen GPU Manager, and click Verify loaded profile."
    elif not passed:
        report += "\nThe installer may still be running. Finish setup and any requested restart, then verify again."
    return after, report, passed and not pending_restart


def cache_profiles(profiles: list[DriverProfile]) -> tuple[list[DriverProfile], str]:
    packages = cache.discover_packages()
    updated = list(profiles)
    lines = []
    for index, profile in enumerate(profiles):
        try:
            manifest = cache.cache_profile(profile, CACHE_ROOT, packages)
            updated[index] = replace(profile, cache_manifest=str(manifest))
            save_profiles(PROFILES_FILE, updated)
            lines.append(f"Cached {profile.name}: {manifest}")
        except (OSError, ValueError, RuntimeError) as error:
            lines.append(f"Not cached: {profile.name}: {error}")
    return updated, "\n\n".join(lines) or "No profiles exist yet. Add a profile or cache an installed version first."


def switch_cached_profile(profile: DriverProfile, reenable: bool) -> tuple[list[GPU], str, bool]:
    if not is_admin():
        raise RuntimeError("Restart as administrator before switching cached drivers.")
    profile.validate()
    if not profile.cache_manifest:
        raise ValueError("Cache this profile first, or import its extracted INF package.")
    manifest, inf = cache.validate_manifest(Path(profile.cache_manifest), cache_root_for_manifest(Path(profile.cache_manifest)), profile.expected_version)
    before = inventory()
    by_id = {gpu.instance_id.upper(): gpu for gpu in before}
    target_ids = {device_id.upper() for device_id in profile.required_gpu_ids}
    targets = [by_id.get(device_id.upper()) for device_id in profile.required_gpu_ids]
    disabled_ids = {device_id.upper() for device_id in profile.disabled_gpu_ids}
    to_disable = [by_id.get(device_id.upper()) for device_id in profile.disabled_gpu_ids]
    if any(gpu is None or gpu.present is not True for gpu in targets):
        raise RuntimeError("A GPU required by the cached profile is not present. No drivers were switched.")
    if any(gpu is None or gpu.present is not True for gpu in to_disable):
        raise RuntimeError("A GPU saved as disabled in this profile is not present. Review the profile first.")
    for gpu in to_disable:
        if gpu.problem_code != 22:
            validate_action(before, gpu.instance_id, "disable")
    active_after_disable = [gpu for gpu in before if gpu.present is True and gpu.problem_code == 0
                            and gpu.instance_id.upper() not in disabled_ids]
    if to_disable and any(gpu.problem_code != 22 for gpu in to_disable) and not active_after_disable:
        raise RuntimeError("This profile would disable every healthy GPU before a working replacement is available.")
    if not reenable and any(gpu.problem_code == 22 for gpu in targets):
        raise RuntimeError("A required GPU is disabled. Enable it or select Re-enable profile GPUs.")
    for gpu in before:
        if (is_nvidia(gpu) and gpu.present is True and gpu.instance_id.upper() not in (target_ids | disabled_ids)
                and gpu.problem_code != 22):
            try:
                same_version = nvidia_version(gpu.driver_version) == nvidia_version(profile.expected_version)
            except ValueError:
                same_version = False
            if not same_version:
                raise RuntimeError(f"{gpu.name} is outside this profile and is not disabled. "
                                   "NVIDIA GPUs share driver components. Disable that GPU first, "
                                   "or include it in a compatible profile. No drivers were switched.")
    if verify_profile(profile, before)[0]:
        return before, "The loaded drivers already match this cached profile. No change was necessary.", True
    # Reject incompatible targets before staging or changing any device.
    cache.preflight(inf, profile.expected_version, profile.required_gpu_ids)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    history = APP_DIR / "driver_history" / stamp
    history.mkdir(parents=True, exist_ok=False)
    write_json(history / "before.json", [asdict(gpu) for gpu in before])
    write_json(history / "profile.json", asdict(profile))
    # Cache each currently bound version as a rollback source before changing
    # devices. This includes disabled GPUs with an installed NVIDIA driver.
    rollback = []
    packages = cache.discover_packages()
    for gpu in before:
        if not is_nvidia(gpu) or gpu.present is not True or not gpu.driver_version:
            continue
        baseline = DriverProfile(f"Previous {gpu.name}", nvidia_version(gpu.driver_version),
                                 required_gpu_ids=[gpu.instance_id])
        previous_cache = cache.cache_profile(baseline, CACHE_ROOT, packages)
        rollback.append({"instance_id": gpu.instance_id, "version": baseline.expected_version,
                         "cache_manifest": str(previous_cache), "was_disabled": gpu.problem_code == 22})
    write_json(history / "previous-cached-drivers.json", rollback)
    # Stage only: PnPUtil verifies the signed package but does not choose/activate
    # it. /install would not reliably downgrade a higher-ranked newer driver.
    staged = run_command([system_exe("pnputil.exe"), "/add-driver", str(inf)], timeout=180)
    if staged.returncode not in (0, 3010):
        raise RuntimeError("Windows rejected the cached package. No device install was requested.\n" + output_text(staged))
    pending = {"profile": profile.name, "method": "cached", "history": str(history), "state": "switch in progress"}
    write_json(PENDING_FILE, pending)
    notes = [f"Cached driver {manifest['version']} selected.", f"History / previous package references: {history}"]
    restart = staged.returncode == 3010
    failed = False
    for gpu in to_disable:
        if gpu.problem_code == 22:
            continue
        try:
            helper = recovery_file(gpu, before)
            changed, detail = change_device(gpu.instance_id, "disable")
            actual = next((item for item in changed if item.instance_id.upper() == gpu.instance_id.upper()), None)
            if actual is None or actual.present is not True or actual.problem_code != 22:
                raise RuntimeError("The saved disabled state was not reached; driver installation was stopped.")
            restart = restart or "PnPUtil exit code: 3010" in detail
            notes.append(f"Disabled as saved in profile: {gpu.name}. Recovery: {helper}\n{detail}")
        except Exception as error:
            notes.append(f"Could not apply disabled state for {gpu.name}: {error}. No further devices will be changed.")
            failed = True
            break
    # Switch the currently enabled adapter first, then the disabled target(s).
    # Reopen an exact-device context for each call after any re-enumeration.
    for gpu in sorted(targets, key=lambda item: item.problem_code == 22):
        if failed:
            break
        try:
            with cache.DeviceDriver(gpu.instance_id, inf, profile.expected_version) as selection:
                restart = selection.install() or restart
            notes.append(f"Installed selected package on {gpu.name}.")
            if reenable and gpu.problem_code == 22:
                _, detail = change_device(gpu.instance_id, "enable")
                notes.append(f"Re-enable {gpu.name}: {detail}")
        except Exception as error:
            notes.append(f"Switch stopped at {gpu.name}: {error}. Earlier device changes may already have completed.")
            failed = True
            break
    try:
        after = inventory()
        matched, verification = verify_profile(profile, after)
    except Exception as error:
        pending["state"] = "verification failed"
        write_json(PENDING_FILE, pending)
        raise RuntimeError(f"Cached switch was attempted, but verification failed: {error}. "
                           f"History: {history}. Restart if requested, then verify from the GUI.") from error
    after_by_id = {gpu.instance_id.upper(): gpu for gpu in after}
    for previous in before:
        if not is_nvidia(previous) or previous.present is not True or previous.instance_id.upper() in target_ids:
            continue
        current = after_by_id.get(previous.instance_id.upper())
        fields = (("present", "driver_version", "driver_inf") if previous.instance_id.upper() in disabled_ids
                  else ("present", "problem_code", "driver_version", "driver_inf"))
        if current is None or any(getattr(current, key) != getattr(previous, key) for key in fields):
            failed = True
            notes.append(f"Outside-profile GPU state changed: {previous.name}. "
                         "Its previous driver/disabled state was not preserved; inspect the saved before/after inventories.")
        else:
            notes.append(f"Preserved outside-profile GPU: {previous.name}, {current.state}, "
                         f"installed package {current.driver_version} ({current.driver_inf}).")
    passed = matched and not restart and not failed
    write_json(history / "after.json", [asdict(gpu) for gpu in after])
    write_json(history / "result.json", {"method": "cached", "matched": matched,
                                        "restart_required": restart, "failed": failed, "notes": notes})
    if passed:
        PENDING_FILE.unlink(missing_ok=True)
    else:
        pending["state"] = "restart required" if restart else "verification pending"
        write_json(PENDING_FILE, pending)
    notes.append(verification)
    if restart:
        notes.append("Windows requires a restart. Restart manually, then reopen GPU Manager and Verify loaded profile.")
    if failed or not matched:
        notes.append("The cached switch is not confirmed. Inspect the GPU codes; the full installer remains available as a fallback.")
    notes.append("This changes the display driver package, not NVIDIA App, PhysX, or all installer-managed components.")
    return after, "\n\n".join(notes), passed


def configure_cache(settings_path: Path):
    """Restore the current cache and explicitly trusted previous cache roots."""
    global CACHE_ROOT, CACHE_ROOTS
    settings = load_settings(settings_path)
    current = settings.get("cache_root")
    previous = settings.get("previous_cache_roots", [])
    if isinstance(current, str) and current and Path(current).is_absolute():
        CACHE_ROOT = Path(current).resolve()
    CACHE_ROOTS = []
    if isinstance(previous, list):
        for value in previous:
            if isinstance(value, str) and value and Path(value).is_absolute():
                root = Path(value).resolve()
                if root != CACHE_ROOT and root not in CACHE_ROOTS:
                    CACHE_ROOTS.append(root)


def set_cache_root(path: Path, settings_path: Path):
    """Select a writable directory without moving or deleting any packages."""
    global CACHE_ROOT, CACHE_ROOTS
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Choose an existing directory for the driver cache.")
    new_root = path.resolve()
    with tempfile.TemporaryFile(dir=new_root):
        pass
    old_root = CACHE_ROOT.resolve()
    previous = [root for root in [old_root, *CACHE_ROOTS] if root != new_root]
    previous = list(dict.fromkeys(previous))
    save_settings(settings_path, cache_root=str(new_root), previous_cache_roots=[str(root) for root in previous])
    CACHE_ROOT, CACHE_ROOTS = new_root, previous


def cache_root_for_manifest(manifest: Path) -> Path:
    """Only allow manifests under the current or explicitly retained cache roots."""
    for root in [CACHE_ROOT, *CACHE_ROOTS]:
        if manifest.resolve().is_relative_to(root.resolve()):
            return root
    raise ValueError("The cache manifest must be inside a configured driver cache folder.")


def discover_all_cached_packages():
    packages = []
    seen = set()
    for root in [CACHE_ROOT, *CACHE_ROOTS]:
        for package in cache.discover_cached_packages(root):
            if package.manifest not in seen:
                seen.add(package.manifest)
                packages.append(package)
    return sorted(packages, key=lambda item: (tuple(map(int, item.version.split("."))), str(item.manifest)), reverse=True)


def cache_references(manifest: Path, profiles: list[DriverProfile]):
    """Find saved-profile and historical references to one package."""
    target = manifest.resolve()
    profile_names = [profile.name for profile in profiles
                     if profile.cache_manifest and Path(profile.cache_manifest).resolve() == target]
    history = []
    history_root = APP_DIR / "driver_history"
    if history_root.exists():
        for folder in history_root.iterdir():
            if not folder.is_dir():
                continue
            for filename in ("previous-cached-drivers.json", "profile.json"):
                path = folder / filename
                if not path.exists():
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    records = data if isinstance(data, list) else [data] if isinstance(data, dict) else None
                    if records is None or any(not isinstance(item, dict) for item in records):
                        raise ValueError("Unexpected history format")
                    for item in records:
                        value = item.get("cache_manifest", "")
                        if not isinstance(value, str):
                            raise ValueError("Unexpected cache reference")
                        if value and Path(value).resolve() == target:
                            history.append(path)
                            break
                except (OSError, ValueError, TypeError) as error:
                    raise ValueError(f"Cannot check recovery history {path}: {error}") from error
    return profile_names, history


def delete_cached_package(manifest: Path, allow_history=False):
    """Delete only an unlinked, recognized package after checking live references."""
    manifest = Path(manifest)
    if not manifest.is_absolute() or manifest.name != "cache-manifest.json":
        raise ValueError("Choose a cached package manifest.")
    folder = manifest.parent
    if cache._is_link(manifest) or cache._is_link(folder):
        raise ValueError("Linked cache packages cannot be deleted.")
    root = cache_root_for_manifest(manifest)
    if folder.resolve().parent != root.resolve():
        raise ValueError("The package must be directly inside a configured cache folder.")
    if manifest.resolve() not in {item.manifest for item in cache.discover_cached_packages(root)}:
        raise ValueError("This package is no longer a recognized cached driver.")
    cache.validate_manifest(manifest, root)
    profiles = load_profiles(PROFILES_FILE)
    names, history = cache_references(manifest, profiles)
    if names:
        raise ValueError("This package is used by saved profiles: " + ", ".join(names) + ". Edit those profiles first.")
    if history and not allow_history:
        raise ValueError("This package is referenced by recovery history; explicit confirmation is required.")
    # Never follow links or remove unexpected files inside the selected directory.
    if {item.name for item in folder.iterdir()} != {"cache-manifest.json", "package"}:
        raise ValueError("The cache folder contains unexpected files; no files were deleted.")
    package = folder / "package"
    if not package.is_dir() or cache._is_link(package):
        raise ValueError("The cached package folder is missing or linked.")
    if any(cache._is_link(item) or not item.resolve().is_relative_to(package.resolve()) for item in package.rglob("*")):
        raise ValueError("The cached package contains linked files; no files were deleted.")
    shutil.rmtree(folder)


def check_runtime(report_path: Path):
    """Check packaged Qt GUI/resources without reading or changing GPU state."""
    from PyQt6.QtCore import QT_VERSION_STR
    from PyQt6.QtGui import QIcon, QPixmap
    from PyQt6.QtWidgets import QApplication
    application = QApplication.instance() or QApplication(sys.argv[:1])
    logos = [QPixmap(str(RESOURCE_DIR / "assets" / f"nvidia-logo-{name}.png"))
             for name in ("dark", "light")]
    if any(logo.isNull() for logo in logos):
        raise RuntimeError("An application logo could not be loaded.")
    icon = QIcon(str(RESOURCE_DIR / "assets" / "app-icon.ico"))
    if icon.isNull():
        raise RuntimeError("The application icon could not be loaded.")
    tool = installer_cache.extractor()
    result = subprocess.run([str(tool)], capture_output=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError("Bundled extractor did not start.")
    write_json(report_path, {"version": APP_VERSION, "frozen": bool(getattr(sys, "frozen", False)),
                            "data_directory": str(APP_DIR), "resource_directory": str(RESOURCE_DIR),
                            "qt_version": QT_VERSION_STR,
                            "logos": [[logo.width(), logo.height()] for logo in logos],
                            "icon": "loaded", "icon_sizes": [[size.width(), size.height()] for size in icon.availableSizes()],
                            "extractor": "verified and started"})


# Let the UI use this same backend module when invoked as a script.
if __name__ == "__main__":
    sys.modules["gpu_manager"] = sys.modules[__name__]
from qt_ui import App


if __name__ == "__main__":
    if os.name != "nt" or sys.getwindowsversion().build < 19041:
        raise SystemExit("Requires Windows 10 version 2004+ or Windows 11.")
    if len(sys.argv) == 1:
        # A batch launcher may have created a console. Hide it for the GUI only;
        # diagnostic commands still keep their output visible.
        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.ShowWindow(ctypes.c_void_p(console), 0)
    if "--check-runtime" in sys.argv:
        check_runtime(Path(sys.argv[sys.argv.index("--check-runtime") + 1]))
    elif "--list" in sys.argv:
        print(json.dumps([asdict(gpu) for gpu in inventory()], indent=2))
    else:
        from PyQt6.QtWidgets import QApplication
        enable_dpi_awareness()
        application = QApplication.instance() or QApplication(sys.argv[:1])
        window = App()
        window.show()
        sys.exit(application.exec())
