"""Windows GPU Manager. Python 3.10+, standard library only.

Opening this app only reads devices. Mutations require elevation, an explicit
selection and confirmation. --list is a read-only JSON diagnostic command.
"""
from __future__ import annotations

import base64
import cached_drivers as cache
import installer_cache
import driver_updates
from ui_theme import Theme, ScrollPane, enable_dpi_awareness
import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser

from driver_profiles import (DriverProfile, capture_working_profile, is_nvidia,
                             load_profiles, nvidia_version, save_profiles,
                             sha256_file, verify_profile, write_json)
from gpu_errors import PROBLEMS, SOURCE as ERROR_SOURCE, problem_details, problem_info

APP_DIR = Path(__file__).resolve().parent
PROFILES_FILE = APP_DIR / "driver_profiles.json"
PENDING_FILE = APP_DIR / "pending-driver-switch.json"
CACHE_ROOT = APP_DIR / "driver_cache"
SETTINGS_FILE = APP_DIR / "ui_settings.json"
APP_NAME = "NVIDIA GPU Driver Profile Switcher"
APP_VERSION = "1.0"


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
    folder = Path(__file__).resolve().parent / "recovery" / stamp
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
        label = ("Gaming RTX 3060" if len(targets) == 1 and "3060" in targets[0].name
                 else targets[0].name if len(targets) == 1 else "AI candidate (both GPUs)")
        base = (name.strip() or label) + " " + version
        title = base
        index = 2
        while any(item.name.casefold() == title.casefold() for item in profiles):
            title = f"{base} ({index})"
            index += 1
    saved = DriverProfile(title, version, str(path.resolve()), device_ids,
                          fingerprint, str(manifest))
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
    pending_restart = exit_code in (3010, 1641)
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
    manifest, inf = cache.validate_manifest(Path(profile.cache_manifest), CACHE_ROOT, profile.expected_version)
    before = inventory()
    by_id = {gpu.instance_id.upper(): gpu for gpu in before}
    target_ids = {device_id.upper() for device_id in profile.required_gpu_ids}
    targets = [by_id.get(device_id.upper()) for device_id in profile.required_gpu_ids]
    if any(gpu is None or gpu.present is not True for gpu in targets):
        raise RuntimeError("A GPU required by the cached profile is not present. No drivers were switched.")
    if not reenable and any(gpu.problem_code == 22 for gpu in targets):
        raise RuntimeError("A required GPU is disabled. Enable it or select Re-enable profile GPUs.")
    for gpu in before:
        if is_nvidia(gpu) and gpu.present is True and gpu.instance_id.upper() not in target_ids and gpu.problem_code != 22:
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
    # Switch the currently enabled adapter first, then the disabled target(s).
    # Reopen an exact-device context for each call after any re-enumeration.
    for gpu in sorted(targets, key=lambda item: item.problem_code == 22):
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
        fields = ("present", "problem_code", "driver_version", "driver_inf")
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


class App(tk.Tk):
    def __init__(self):
        enable_dpi_awareness()
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        scale = max(1, float(self.tk.call("tk", "scaling")) / (96 / 72))
        width = min(int(1320 * scale), self.winfo_screenwidth() - 80)
        height = min(int(920 * scale), self.winfo_screenheight() - 160)
        self.geometry(f"{width}x{height}+{max(0, (self.winfo_screenwidth() - width) // 2)}+20")
        self.minsize(min(int(1000 * scale), width), min(int(700 * scale), height))
        self.theme = Theme(self, SETTINGS_FILE)
        self.devices: list[GPU] = []
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.profiles: list[DriverProfile] = []
        self.profile_load_error = None
        try:
            self.profiles = load_profiles(PROFILES_FILE)
        except Exception as error:
            self.profile_load_error = str(error)
        frame = ttk.Frame(self, padding=(24, 20))
        self.content_frame = frame
        frame.pack(fill="both", expand=True)
        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 14))
        brand = ttk.Frame(header)
        brand.pack(side="left", fill="x", expand=True)
        title_row = ttk.Frame(brand)
        title_row.pack(anchor="w")
        ttk.Label(title_row, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(title_row, text=f"v{APP_VERSION}", style="Badge.TLabel").pack(side="left", padx=(14, 0))
        appearance = ttk.Frame(header)
        appearance.pack(side="right", anchor="n", padx=(18, 0))
        ttk.Label(appearance, text="APPEARANCE", style="Muted.TLabel").pack(anchor="e", pady=(0, 5))
        self.theme_choice = tk.StringVar(value=self.theme.name)
        theme_picker = ttk.Combobox(appearance, values=("Dark", "Light"), state="readonly", width=10,
                                    textvariable=self.theme_choice)
        theme_picker.pack(anchor="e")
        theme_picker.bind("<<ComboboxSelected>>", self.change_theme)
        self.logo_label = ttk.Label(header, style="Logo.TLabel")
        self.logo_label.pack(side="right", padx=(16, 6))
        self.load_logo()
        self.mode = ttk.Label(frame, text=("Administrator: device controls available" if is_admin()
                                          else "Preview mode · elevate when you are ready to switch drivers"), style="Badge.TLabel")
        self.mode.pack(anchor="w", pady=(0, 8))
        notebook = ttk.Notebook(frame)
        self.notebook = notebook
        notebook.pack(fill="both", expand=True)
        gpu_frame = ttk.Frame(notebook, padding=(0, 18))
        driver_frame = ttk.Frame(notebook, padding=(0, 18))
        errors_frame = ttk.Frame(notebook, padding=(0, 18))
        notebook.add(gpu_frame, text="GPUs")
        notebook.add(driver_frame, text="Driver library")
        notebook.add(errors_frame, text="Error reference")
        driver_scroll = ScrollPane(driver_frame)
        driver_scroll.pack(fill="both", expand=True)
        self.driver_scroll = driver_scroll
        self.build_driver_tab(driver_scroll.content)
        self.build_error_tab(errors_frame)
        notebook.select(driver_frame)
        frame = gpu_frame
        ttk.Label(frame, wraplength=1040, text=(
            "Move monitor cables to the RTX 3060 before disabling the GTX 1070. "
            "Disable keeps the device installed; uninstalling allows Windows to rediscover it. "
            "GPU 0 / GPU 1 numbering does not identify the primary desktop."
        )).pack(anchor="w", pady=(0, 10))
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=(0, 10))
        self.refresh_button = ttk.Button(toolbar, text="Refresh GPUs", command=self.refresh)
        self.refresh_button.pack(side="left")
        self.admin_button = ttk.Button(toolbar, text="Restart as administrator", command=self.elevate)
        self.admin_button.pack(side="left", padx=6)
        self.export_button = ttk.Button(toolbar, text="Export diagnosis", command=self.export)
        self.export_button.pack(side="left")
        ttk.Button(toolbar, text="Display settings", command=lambda: os.startfile("ms-settings:display")).pack(side="right")
        ttk.Button(toolbar, text="NVIDIA drivers", command=lambda: webbrowser.open(
            "https://www.nvidia.com/en-us/drivers/")).pack(side="right", padx=6)
        columns = ("name", "present", "state", "driver", "desktop")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", height=7, selectmode="browse")
        for key, label, width in zip(columns,
                                     ("GPU", "Present", "Windows state", "Driver version", "Desktop connection"),
                                     (280, 70, 210, 140, 190)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=60)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        self.details = tk.Text(frame, height=9, wrap="word", state="disabled", font=("Consolas", 10))
        self.details.pack(fill="x", pady=8)
        controls = ttk.Frame(frame)
        controls.pack(fill="x")
        self.disable_button = ttk.Button(controls, text="Disable selected GPU...", style="Danger.TButton", command=lambda: self.prepare("disable"))
        self.disable_button.pack(side="left")
        self.enable_button = ttk.Button(controls, text="Enable selected GPU...", command=lambda: self.prepare("enable"))
        self.enable_button.pack(side="left", padx=8)
        ttk.Label(frame, wraplength=1040, text=(
            "A recovery CMD file is saved before disabling. Desktop connection can be Unknown, especially "
            "with remote sessions or a broken driver. Confirm your monitor cabling yourself. "
            "This tool uses Windows device controls; driver installers can still change device state."
        )).pack(anchor="w", pady=10)
        self.log = tk.Text(frame, height=7, wrap="word", state="disabled", font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Ready")
        footer = ttk.Frame(self, padding=(24, 0, 24, 14))
        footer.pack(fill="x", side="bottom", before=self.content_frame)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=110)
        self.progress.pack(side="left", padx=(0, 12))
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel", wraplength=800).pack(side="left", fill="x", expand=True)
        ttk.Label(footer, text="Independent community utility", style="Muted.TLabel").pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        if self.profile_load_error:
            self.append_log("Profile file could not be loaded; it was preserved. " + self.profile_load_error)
        self.after(100, self.poll)
        self.theme.refresh_widgets(self)
        self.bind("<Map>", self.style_dialog, add="+")
        self.refresh()

    def change_theme(self, _event=None):
        self.theme.apply(self.theme_choice.get())
        self.load_logo()
        try:
            self.theme.save()
        except OSError as error:
            self.append_log(f"Theme applied, but its preference could not be saved: {error}")

    def load_logo(self):
        try:
            path = APP_DIR / "assets" / f"nvidia-logo-{self.theme.name.lower()}.png"
            self.nvidia_logo = tk.PhotoImage(file=str(path))
            self.logo_label.configure(image=self.nvidia_logo)
        except tk.TclError:
            self.nvidia_logo = None
            self.logo_label.configure(image="")

    def style_dialog(self, event):
        if isinstance(event.widget, tk.Toplevel):
            self.theme.refresh_widgets(event.widget)

    def mode_profile(self, mode):
        candidates = [profile for profile in self.profiles
                      if (len(profile.required_gpu_ids) > 1 if mode == "ai" else len(profile.required_gpu_ids) == 1)]
        if mode == "ai":
            candidates.sort(key=lambda profile: ("candidate" in profile.name.casefold(), "ai" not in profile.name.casefold()))
        return candidates[0] if candidates else None

    def select_mode_profile(self, mode):
        profile = self.mode_profile(mode)
        if profile and not self.busy:
            self.profile_choice.set(profile.name)
            self.profile_summary()

    def build_driver_tab(self, frame):
        cards = ttk.Frame(frame)
        cards.pack(fill="x", pady=(0, 16))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        self.mode_cards = {}
        for column, mode, kicker, title, subtitle in (
            (0, "ai", "AI WORKFLOWS", "Shared GPU driver profile", "Use the compatible driver required by your local AI tools."),
            (1, "gaming", "GAMING", "Main GPU driver profile", "Keep the older GPU disabled while using a newer release."),
        ):
            card = ttk.Frame(cards, style="Card.TFrame", padding=14)
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 8) if column == 0 else (8, 0))
            ttk.Label(card, text=kicker, style="Kicker.TLabel").pack(anchor="w")
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w", pady=(6, 4))
            ttk.Label(card, text=subtitle, style="CardMuted.TLabel", wraplength=500).pack(anchor="w")
            version = tk.StringVar()
            ttk.Label(card, textvariable=version, style="Card.TLabel").pack(anchor="w", pady=(10, 8))
            button = ttk.Button(card, text="Select profile", command=lambda key=mode: self.select_mode_profile(key))
            button.pack(anchor="w")
            self.mode_cards[mode] = (version, button)
        ttk.Label(frame, text="Driver profiles", style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(frame)
        row.pack(fill="x")
        self.profile_choice = ttk.Combobox(row, state="readonly", width=50)
        self.profile_choice.pack(side="left", fill="x", expand=True)
        self.profile_choice.bind("<<ComboboxSelected>>", lambda event: self.profile_summary())
        self.profile_admin_button = ttk.Button(row, text="Restart as administrator", command=self.elevate)
        self.profile_admin_button.pack(side="right", padx=(8, 0))
        self.reenable_targets = tk.BooleanVar(value=True)
        self.reenable_check = ttk.Checkbutton(frame, text="Re-enable this profile's GPUs if they are disabled (Code 22)",
                                            variable=self.reenable_targets)
        self.reenable_check.pack(anchor="w", pady=8)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, 4))
        self.cached_switch_button = ttk.Button(row, text="Switch using cached package...", style="Accent.TButton", command=self.launch_cached_profile)
        self.cached_switch_button.pack(side="left", padx=(0, 8))
        self.install_profile_button = ttk.Button(row, text="Use full installer...", command=self.launch_profile)
        self.install_profile_button.pack(side="left")
        self.verify_profile_button = ttk.Button(row, text="Verify loaded profile", command=self.verify_selected_profile)
        self.verify_profile_button.pack(side="left", padx=8)
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill="x", pady=10)
        self.profile_buttons = []
        for label, callback in (
            ("Check NVIDIA updates...", self.check_driver_updates),
            ("Cache NVIDIA installer (.exe)...", self.cache_nvidia_installer),
        ):
            button = ttk.Button(toolbar, text=label, command=callback)
            button.pack(side="left", padx=(0, 6))
            self.profile_buttons.append(button)
        for title, entries in (
            ("Manage profiles", (("Capture working drivers...", self.capture_profile),
                                 ("Add profile...", lambda: self.edit_profile()),
                                 ("Edit selected...", lambda: self.edit_profile(self.current_profile())),
                                 ("Remove profile...", self.remove_profile))),
            ("More cache options", (("Cache saved profiles", self.cache_saved_profiles),
                                    ("Cache installed version...", self.cache_installed_version),
                                    ("Import extracted INF...", self.import_cached_inf))),
        ):
            button = ttk.Menubutton(toolbar, text=title)
            menu = tk.Menu(button, tearoff=False)
            for label, command in entries:
                menu.add_command(label=label, command=command)
            button.configure(menu=menu)
            button.pack(side="left", padx=(0, 6))
            self.profile_buttons.append(button)
        self.profile_detail = tk.Text(frame, height=7, wrap="word", state="disabled", font=("Consolas", 10))
        self.profile_detail.pack(fill="both", expand=True)
        ttk.Label(frame, wraplength=1020, text=(
            "Close games and AI jobs before switching. Displays may briefly blank; restart if Windows requests it. "
            "Cached switching updates the display driver; use the full installer for other NVIDIA components."
        ), style="Muted.TLabel").pack(anchor="w", pady=10)
        self.profile_status = tk.StringVar(value="Ready")
        ttk.Label(frame, textvariable=self.profile_status, wraplength=1020).pack(anchor="w")
        self.reload_profile_choices()
        if PENDING_FILE.exists():
            try:
                pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
                if any(profile.name == pending.get("profile") for profile in self.profiles):
                    self.profile_choice.set(pending["profile"])
                    self.profile_summary()
                self.profile_status.set(f"Previous switch: {pending.get('profile', 'unknown')} / "
                                        f"{pending.get('state', 'verification pending')}. "
                                        "Finish setup and any restart, then Verify loaded profile.")
            except (OSError, ValueError, TypeError, AttributeError):
                self.profile_status.set("Previous switch record could not be read. Refresh and verify your loaded drivers.")
        self.profile_controls()

    def profile_controls(self):
        profile = self.current_profile()
        for button in self.profile_buttons:
            button.configure(state="disabled" if self.busy else "normal")
        for mode, (label, button) in self.mode_cards.items():
            candidate = self.mode_profile(mode)
            label.set(f"{candidate.expected_version} · {candidate.name}" if candidate else "Create a profile to get started")
            button.configure(state="normal" if candidate and not self.busy else "disabled")
        self.profile_choice.configure(state="disabled" if self.busy else "readonly")
        self.reenable_check.configure(state="disabled" if self.busy else "normal")
        self.profile_admin_button.configure(state="normal" if not self.busy and not is_admin() else "disabled")
        self.install_profile_button.configure(state=("normal" if not self.busy and is_admin()
                                                    and profile and profile.installer and profile.sha256 else "disabled"))
        self.cached_switch_button.configure(state=("normal" if not self.busy and is_admin()
                                                   and profile and profile.cache_manifest else "disabled"))
        self.verify_profile_button.configure(state="normal" if not self.busy and profile else "disabled")

    def current_profile(self) -> DriverProfile | None:
        name = self.profile_choice.get()
        return next((profile for profile in self.profiles if profile.name == name), None)

    def reload_profile_choices(self, selected=None):
        self.profile_choice["values"] = [profile.name for profile in self.profiles]
        names = [profile.name for profile in self.profiles]
        current = selected or self.profile_choice.get()
        self.profile_choice.set(current if current in names else names[0] if names else "")
        self.profile_summary()

    def profile_summary(self):
        profile = self.current_profile()
        if profile:
            text = (f"Profile: {profile.name}\nExpected driver: {nvidia_version(profile.expected_version)}\n"
                    f"Cached package: {profile.cache_manifest or 'Not cached - use Cache NVIDIA installer (.exe) or Cache saved profiles'}\n"
                    f"Full installer fallback: {profile.installer or 'Not attached'}\n\nGPUs required to work:\n"
                    + "\n".join(profile.required_gpu_ids))
        else:
            text = ("No driver profiles saved yet.\n\n"
                    "1. Click Capture working drivers to save the version and GPUs that work now.\n"
                    "2. Click Cache saved profiles to copy versions already in Windows.\n"
                    "3. After updating a GPU, click Cache installed version to retain the new version too.\n\n"
                    "Cache NVIDIA installer (.exe) unpacks a fresh download without running NVIDIA setup. "
                    "Import extracted INF can cache a complete NVIDIA Display.Driver package before installation. "
                    "Keeping the full installers gives you an additional recovery option.")
        self.set_text(self.profile_detail, text)
        if hasattr(self, "install_profile_button"):
            self.profile_controls()

    def cache_saved_profiles(self):
        if self.busy or self.profile_load_error:
            return
        def done(result):
            self.profiles, report = result
            self.reload_profile_choices()
            self.append_log(report)
            self.profile_status.set("Cache operation complete. Read the result for any packages that were unavailable.")
            messagebox.showinfo("Cached driver packages", report, parent=self)
        self.start_job("Copying and hashing saved driver versions. Active drivers will remain unchanged...",
                       lambda: cache_profiles(self.profiles), done)

    def cache_installed_version(self):
        if self.busy or self.profile_load_error:
            return
        def done(devices):
            self.display_inventory(devices)
            cards = [gpu for gpu in devices if is_nvidia(gpu) and gpu.present is True and gpu.problem_code == 0]
            if not cards:
                messagebox.showerror("No working NVIDIA GPU", "A present NVIDIA GPU must report Code 0 before its installed version can be captured.", parent=self)
                return
            dialog = tk.Toplevel(self)
            dialog.title("Cache installed driver version")
            dialog.geometry("650x220")
            dialog.transient(self)
            dialog.grab_set()
            frame = ttk.Frame(dialog, padding=16)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text="Choose the GPU whose currently installed version you want to keep:", wraplength=610).pack(anchor="w")
            labels = [f"{gpu.name} - {self.version_label(gpu)}" for gpu in cards]
            choice = ttk.Combobox(frame, values=labels, state="readonly", width=65)
            choice.pack(fill="x", pady=12)
            choice.current(next((index for index, gpu in enumerate(cards) if "3060" in gpu.name), 0))
            ttk.Label(frame, text="A new version gets its own profile and cache. Older cached versions remain available.", wraplength=610).pack(anchor="w", pady=(0, 12))
            def selected():
                gpu = cards[choice.current()]
                version = nvidia_version(gpu.driver_version)
                targets = [item.instance_id for item in cards if nvidia_version(item.driver_version) == version]
                profile = next((item for item in self.profiles if nvidia_version(item.expected_version) == version
                                and {key.upper() for key in item.required_gpu_ids} == {key.upper() for key in targets}), None)
                if profile is None:
                    name = ("AI multi-GPU" if len(targets) > 1 else gpu.name) + " " + version
                    used = {item.name.casefold() for item in self.profiles}
                    base = name
                    index = 2
                    while name.casefold() in used:
                        name = f"{base} {index}"
                        index += 1
                    profile = DriverProfile(name, version, required_gpu_ids=targets)
                dialog.destroy()
                def job():
                    # Recheck current state before labeling an installed version.
                    current = inventory()
                    if not verify_profile(profile, current)[0]:
                        raise RuntimeError("The selected GPU/driver state changed. Refresh and capture it again.")
                    manifest = cache.cache_profile(profile, CACHE_ROOT)
                    updated = replace(profile, cache_manifest=str(manifest))
                    profiles = [updated if item.name == profile.name else item for item in self.profiles]
                    if not any(item.name == profile.name for item in profiles):
                        profiles.append(updated)
                    save_profiles(PROFILES_FILE, profiles)
                    return profiles, updated
                def saved(result):
                    self.profiles, updated = result
                    self.reload_profile_choices(updated.name)
                    self.profile_status.set(f"Cached installed version {version}. No driver was switched.")
                    self.append_log(f"Saved installed-version cache: {updated.name}\n{updated.cache_manifest}")
                self.start_job("Caching the installed driver version...", job, saved)
            ttk.Button(frame, text="Cache this installed version", command=selected).pack(anchor="w")
        self.start_job("Reading installed NVIDIA driver versions...", inventory, done)

    def check_driver_updates(self):
        if self.busy or self.profile_load_error:
            return
        def job():
            if ctypes.sizeof(ctypes.c_void_p) != 8:
                raise RuntimeError("Driver update checks require 64-bit Python on Windows 10/11 x64.")
            devices = inventory()
            os_id = 135 if sys.getwindowsversion().build >= 22000 else 57
            results = driver_updates.check_updates(devices, os_id)
            write_json(APP_DIR / "last-driver-update-check.json", {
                "checked_at": datetime.now().astimezone().isoformat(), "os_id": os_id,
                "results": [asdict(item) for item in results]})
            return devices, results
        def done(result):
            devices, results = result
            self.display_inventory(devices)
            self.show_driver_updates(results)
        self.start_job("Checking NVIDIA's official catalogue separately for every present NVIDIA GPU...", job, done)

    def show_driver_updates(self, results):
        dialog = tk.Toplevel(self)
        dialog.title("NVIDIA driver updates — choose which GPUs")
        dialog.geometry("1000x680")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        report = []
        for gpu in results:
            report.append(f"{gpu.name} | Installed package: {gpu.installed}" + (" | DEVICE DISABLED" if gpu.disabled else ""))
            if gpu.error:
                report.append("Could not confirm updates: " + gpu.error)
            elif gpu.newer():
                offer = gpu.newer()[0]
                report.append(f"Update available: {offer.version} — {offer.title} — {offer.date}")
            elif gpu.offers:
                report.append(f"No newer compatible WHQL package found. Newest returned: {gpu.offers[0].version}.")
            report.append("")
        view = tk.Text(frame, height=10, wrap="word", font=("Segoe UI", 10))
        view.pack(fill="both", expand=True)
        view.insert("1.0", "\n".join(report) or "No present NVIDIA GPUs were detected.")
        view.configure(state="disabled")
        choices = driver_updates.update_choices(results)
        selected = tk.IntVar(value=next((index for index, item in enumerate(choices)
                                       if len(item.device_ids) == 1 and "3060" in item.label), -1))
        for index, item in enumerate(choices):
            ttk.Radiobutton(frame, text=item.label, variable=selected, value=index).pack(anchor="w", pady=5)
        ttk.Label(frame, wraplength=950, text=(
            "Your tested older AI profile is kept. For new games, select the RTX 3060-only update and keep the 1070 disabled. "
            "An update for the 1070 alone is not an AI multi-GPU upgrade. "
            "A both-GPU update is offered only when NVIDIA lists the same newer package for every target, and still needs AI workload testing. "
            "The 1070 and 3060 may have different latest compatible versions, which cannot be used as separate "
            "simultaneously active NVIDIA stacks. A single-GPU switch requires other GPUs on a different version "
            "to be disabled first. A selected disabled GPU will be re-enabled only if you approve that choice "
            "in the switch confirmation. Older cached profiles remain available."
        )).pack(anchor="w", pady=12)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=6)
        def details():
            if 0 <= selected.get() < len(choices):
                webbrowser.open(choices[selected.get()].offer.details_url)
        def submit():
            if not 0 <= selected.get() < len(choices):
                return
            choice = choices[selected.get()]
            prompt = (f"Download NVIDIA {choice.offer.version} and cache a profile for:\n{choice.label}\n\n"
                      f"{choice.offer.download_url}\n\nThe installer EXE will not be executed. Its signature and "
                      "target GPU compatibility will be checked. Any installation requires a separate switch confirmation.")
            if not messagebox.askyesno("Download and cache update", prompt, parent=dialog, default="no"):
                return
            dialog.destroy()
            def download_job():
                path = driver_updates.download_offer(choice.offer, APP_DIR / "downloads")
                return cache_downloaded_installer(path, list(choice.device_ids), self.profiles,
                                                  expected_version=choice.offer.version)
            self.start_job("Downloading from NVIDIA, checking its signature, and caching the selected update...",
                           download_job, lambda result: self.installer_cached(result, offer_switch=True))
        details_button = ttk.Button(row, text="NVIDIA release details", command=details)
        details_button.pack(side="left")
        download_button = ttk.Button(row, text="Download, cache and review switch...", command=submit)
        download_button.pack(side="left", padx=8)
        def choice_changed(*_args):
            state = "normal" if 0 <= selected.get() < len(choices) else "disabled"
            details_button.configure(state=state)
            download_button.configure(state=state)
        selected.trace_add("write", choice_changed)
        choice_changed()
        ttk.Button(row, text="Keep current drivers", command=dialog.destroy).pack(side="right")
        self.append_log("NVIDIA update check:\n" + "\n".join(report))

    def cache_nvidia_installer(self):
        if self.busy or self.profile_load_error:
            return
        filename = filedialog.askopenfilename(parent=self, title="Cache a downloaded NVIDIA driver EXE (setup will not run)",
                                             filetypes=[("NVIDIA offline driver installer", "*.exe")])
        if not filename:
            return
        def loaded(devices):
            self.display_inventory(devices)
            cards = [gpu for gpu in devices if is_nvidia(gpu) and gpu.present is True]
            if not cards:
                messagebox.showerror("No NVIDIA GPU", "No present NVIDIA GPU could be found.", parent=self)
                return
            dialog = tk.Toplevel(self)
            dialog.title("Cache NVIDIA installer (.exe)")
            dialog.geometry("800x440")
            dialog.transient(self)
            dialog.grab_set()
            frame = ttk.Frame(dialog, padding=16)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame, text=filename, wraplength=755).pack(anchor="w", pady=(0, 12))
            ttk.Label(frame, text="Choose the GPUs for this cached profile:").pack(anchor="w")
            choices = []
            default = next((gpu.instance_id for gpu in cards if "3060" in gpu.name), cards[0].instance_id)
            for gpu in cards:
                selected = tk.BooleanVar(value=gpu.instance_id == default)
                ttk.Checkbutton(frame, variable=selected, text=f"{gpu.name} — {gpu.state}\n{gpu.instance_id}").pack(anchor="w", pady=6)
                choices.append((gpu.instance_id, selected))
            ttk.Label(frame, wraplength=755, text=(
                "The version is detected from the EXE contents and each selected GPU is checked for compatibility. "
                "Unselected GPUs are excluded from this profile. Caching does not install drivers or enable devices. "
                "For a new 3060 driver, leave the disabled GTX 1070 unchecked."
            )).pack(anchor="w", pady=12)
            def submit():
                ids = [key for key, selected in choices if selected.get()]
                if not ids:
                    messagebox.showerror("Choose a GPU", "Select at least one GPU for the profile.", parent=dialog)
                    return
                dialog.destroy()
                self.start_job("Checking NVIDIA signature, extracting EXE, and caching the compatible display package...",
                               lambda: cache_downloaded_installer(Path(filename), ids, self.profiles),
                               self.installer_cached)
            ttk.Button(frame, text="Extract and cache — leave active drivers unchanged", command=submit).pack(anchor="w", pady=8)
        self.start_job("Reading GPUs for the downloaded driver profile...", inventory, loaded)

    def installer_cached(self, result, offer_switch=False):
        self.profiles, profile = result
        self.reload_profile_choices(profile.name)
        self.profile_status.set(f"Cached NVIDIA {profile.expected_version}. No drivers were installed or GPUs enabled.")
        self.append_log(f"Cached EXE profile: {profile.name}\n{profile.cache_manifest}")
        if offer_switch and is_admin():
            self.launch_cached_profile()
            return
        messagebox.showinfo("NVIDIA EXE cached", f"Saved {profile.name}.\n\n"
                            "The active drivers are unchanged. To install this profile, restart as administrator if needed, "
                            "then click Switch using cached package. Only this profile's GPUs are targeted.", parent=self)

    def import_cached_inf(self):
        profile = self.current_profile()
        if self.busy or profile is None or self.profile_load_error:
            return
        filename = filedialog.askopenfilename(parent=self, title="Choose an INF in the extracted NVIDIA Display.Driver folder",
                                             filetypes=[("NVIDIA display driver INF", "*.inf")])
        if not filename:
            return
        def job():
            source = Path(filename)
            metadata = cache.inf_metadata(source)
            if metadata.version != nvidia_version(profile.expected_version):
                raise ValueError(f"This package is {metadata.version}, but the selected profile expects {profile.expected_version}. "
                                 "Add or edit a profile for that version first.")
            cache.preflight(source, metadata.version, profile.required_gpu_ids)
            manifest = cache.cache_package(source, CACHE_ROOT)
            updated = replace(profile, cache_manifest=str(manifest))
            profiles = [updated if item.name == profile.name else item for item in self.profiles]
            save_profiles(PROFILES_FILE, profiles)
            return profiles, updated
        def done(result):
            self.profiles, updated = result
            self.reload_profile_choices(updated.name)
            self.append_log(f"Imported cached package: {updated.cache_manifest}")
            self.profile_status.set("Extracted driver package cached. Windows will validate its signature when staging it for a switch.")
        self.start_job("Checking and copying the complete extracted package...", job, done)

    def launch_cached_profile(self):
        profile = self.current_profile()
        if self.busy or profile is None or not is_admin():
            return
        reenable = self.reenable_targets.get()
        prompt = (f"Switch to cached profile: {profile.name}\nExpected driver: {profile.expected_version}\n"
                  f"Cache: {profile.cache_manifest}\n\nExact target devices:\n"
                  + "\n".join(profile.required_gpu_ids)
                  + f"\n\nRe-enable disabled profile GPUs: {'Yes' if reenable else 'No'}\n\n"
                  "Stop AI jobs and games first. This replaces the active display driver on the profile GPUs, "
                  "including a downgrade when selected. Displays may briefly go black. "
                  "Windows may require a restart; the program will not restart it automatically. "
                  "Other NVIDIA GPUs using a different version must be disabled first.")
        if not messagebox.askyesno("Confirm cached driver switch", prompt, parent=self, default="no"):
            return
        def done(result):
            devices, report, passed = result
            self.display_inventory(devices)
            self.append_log(report)
            self.profile_status.set("Cached profile loaded and verified. Test your AI workflow next." if passed else
                                    "Cached switch needs attention. Read the result and restart/verify if requested.")
            messagebox.showinfo("Cached switch result", report, parent=self)
        self.start_job("Validating cached files, preserving previous packages, then switching the selected driver...",
                       lambda: switch_cached_profile(profile, reenable), done)

    def capture_profile(self):
        if self.busy:
            return
        def done(devices):
            self.display_inventory(devices)
            try:
                profile = capture_working_profile(devices)
            except ValueError as error:
                messagebox.showerror("Cannot capture working drivers", str(error), parent=self)
                return
            existing = {item.name.casefold() for item in self.profiles}
            base = profile.name
            index = 2
            while profile.name.casefold() in existing:
                profile = replace(profile, name=f"{base} {index}")
                index += 1
            self.edit_profile(profile, creating=True)
        self.start_job("Reading current working drivers...", inventory, done)

    def edit_profile(self, profile=None, creating=False):
        if self.busy:
            return
        if self.profile_load_error:
            messagebox.showerror("Profile file needs repair", "The existing profile file was preserved. "
                                 "Repair or rename driver_profiles.json before saving new profiles.\n"
                                 + self.profile_load_error, parent=self)
            return
        original = profile if profile and not creating else None
        if profile is None:
            nvidia_gpus = [gpu for gpu in self.devices if is_nvidia(gpu) and gpu.present is True]
            profile = DriverProfile("", "560.94", required_gpu_ids=[gpu.instance_id for gpu in nvidia_gpus])
        dialog = tk.Toplevel(self)
        dialog.title("Driver profile")
        dialog.geometry("850x590")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        name = tk.StringVar(value=profile.name)
        version = tk.StringVar(value=profile.expected_version)
        installer = tk.StringVar(value=profile.installer)
        for label, variable in (("Profile name", name), ("Expected NVIDIA version (560.94 or full Windows version)", version)):
            ttk.Label(frame, text=label).pack(anchor="w")
            ttk.Entry(frame, textvariable=variable).pack(fill="x", pady=(3, 10))
        ttk.Label(frame, text="Downloaded official NVIDIA installer (.exe)").pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(3, 10))
        ttk.Entry(row, textvariable=installer).pack(side="left", fill="x", expand=True)
        def browse():
            path = filedialog.askopenfilename(parent=dialog, title="Choose NVIDIA driver installer",
                                             filetypes=[("NVIDIA installer", "*.exe")])
            if path:
                installer.set(path)
        ttk.Button(row, text="Browse...", command=browse).pack(side="left", padx=(6, 0))
        ttk.Label(frame, wraplength=800, text=(
            "You can save the working version now and attach its installer later. "
            "The expected version is your label; it is verified against the loaded driver after setup. "
            "Selecting an installer checks its NVIDIA signature and records its fingerprint."
        )).pack(anchor="w", pady=(0, 10))
        ttk.Label(frame, text="GPUs that must be present, healthy, and use this version:").pack(anchor="w")
        targets = ttk.Frame(frame)
        targets.pack(fill="both", expand=True, pady=5)
        known = {gpu.instance_id: gpu.name for gpu in self.devices if is_nvidia(gpu)}
        for device_id in profile.required_gpu_ids:
            known.setdefault(device_id, device_id)
        required = {item.upper() for item in profile.required_gpu_ids}
        choices = []
        for device_id, label in known.items():
            selected = tk.BooleanVar(value=device_id.upper() in required)
            ttk.Checkbutton(targets, text=f"{label}\n{device_id}", variable=selected).pack(anchor="w", pady=3)
            choices.append((device_id, selected))
        def save():
            candidate = DriverProfile(name.get().strip(), version.get().strip(), installer.get().strip(),
                                      [device_id for device_id, selected in choices if selected.get()],
                                      cache_manifest=profile.cache_manifest if version.get().strip() == profile.expected_version else "")
            try:
                candidate.validate()
                other = [item for item in self.profiles if item is not original]
                if any(item.name.casefold() == candidate.name.casefold() for item in other):
                    raise ValueError("Choose a unique profile name.")
            except ValueError as error:
                messagebox.showerror("Check profile", str(error), parent=dialog)
                return
            dialog.destroy()
            def save_job():
                checked = replace(candidate, sha256=check_installer(candidate)) if candidate.installer else candidate
                profiles = other + [checked]
                save_profiles(PROFILES_FILE, profiles)
                return profiles, checked
            def done(result):
                self.profiles, checked = result
                self.reload_profile_choices(checked.name)
                self.append_log(f"Saved driver profile: {checked.name} ({nvidia_version(checked.expected_version)})")
                self.profile_status.set("Profile saved. Attach an installer before switching." if not checked.installer else
                                        "Profile saved; NVIDIA installer signature and fingerprint checked.")
            self.start_job("Checking installer and saving driver profile...", save_job, done)
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="Save profile", command=save).pack(side="left")
        ttk.Button(row, text="Cancel", command=dialog.destroy).pack(side="left", padx=8)

    def remove_profile(self):
        profile = self.current_profile()
        if self.busy or profile is None:
            return
        if messagebox.askyesno("Remove profile", f"Remove '{profile.name}' from the saved profiles? "
                               "Its installer and backups will remain on disk.", parent=self, default="no"):
            profiles = [item for item in self.profiles if item is not profile]
            def save_job():
                save_profiles(PROFILES_FILE, profiles)
                return profiles
            def done(result):
                self.profiles = result
                self.reload_profile_choices()
            self.start_job("Saving profile list...", save_job, done)

    def launch_profile(self):
        profile = self.current_profile()
        if self.busy or profile is None:
            return
        if not is_admin():
            messagebox.showinfo("Administrator required", "Restart as administrator first.", parent=self)
            return
        reenable = self.reenable_targets.get()
        target_names = {gpu.instance_id.upper(): gpu.name for gpu in self.devices}
        prompt = (f"Install profile: {profile.name}\nExpected driver: {nvidia_version(profile.expected_version)}\n"
                  f"Installer: {profile.installer}\n\nRequired GPUs:\n"
                  + "\n".join(target_names.get(device_id.upper(), device_id) for device_id in profile.required_gpu_ids)
                  + f"\n\nRe-enable disabled profile GPUs: {'Yes' if reenable else 'No'}\n\n"
                  "Stop AI jobs and games first. This reinstalls the system NVIDIA driver. "
                  "Complete the visible NVIDIA installer; a restart may be required. "
                  "Current display driver packages and inventory will be backed up before changes. "
                  "The program will not restart Windows automatically.")
        if messagebox.askyesno("Confirm driver profile installation", prompt, parent=self, default="no"):
            def done(result):
                devices, report, passed = result
                self.display_inventory(devices)
                self.append_log(report)
                self.profile_status.set("Loaded driver matches this profile. Test your AI workflow next." if passed else
                                        "Switch is not fully verified. Finish setup/restart, then Verify loaded profile.")
                messagebox.showinfo("Driver profile result", report, parent=self)
            self.start_job("Backing up driver, then running NVIDIA setup. Complete the installer window...",
                           lambda: install_profile(profile, reenable), done)

    def verify_selected_profile(self):
        profile = self.current_profile()
        if self.busy or profile is None:
            return
        def verify_job():
            devices = inventory()
            passed, report = verify_profile(profile, devices)
            if passed and PENDING_FILE.exists():
                pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
                if pending.get("profile") == profile.name:
                    PENDING_FILE.unlink()
                    report += "\nPrevious switch record cleared. Complete any installer-requested restart if you have not done so."
            return devices, passed, report
        def done(result):
            devices, passed, report = result
            self.display_inventory(devices)
            self.append_log(report)
            self.profile_status.set("Loaded driver/device verification passed. Test your AI workflow next." if passed else
                                    "Loaded driver/device verification did not pass. See the result for each GPU.")
            messagebox.showinfo("Profile verification", report, parent=self)
        self.start_job("Checking loaded versions and GPU error codes...", verify_job, done)

    def build_error_tab(self, frame):
        ttk.Label(frame, text="Device Manager problem code reference", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Offline meanings and next steps for Codes 0-57. Legacy/internal SDK codes are labeled.",
                  wraplength=1020).pack(anchor="w", pady=8)
        search = tk.StringVar()
        ttk.Entry(frame, textvariable=search).pack(fill="x", pady=(0, 8))
        table_frame = ttk.Frame(frame)
        table_frame.pack(fill="both", expand=True)
        table = ttk.Treeview(table_frame, columns=("code", "meaning", "category"), show="headings", height=10, selectmode="browse")
        for key, title, width in (("code", "Code", 60), ("meaning", "Meaning", 560), ("category", "Category", 230)):
            table.heading(key, text=title)
            table.column(key, width=width)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
        table.configure(yscrollcommand=scrollbar.set)
        table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        details = tk.Text(frame, height=8, wrap="word", state="disabled", font=("Consolas", 10))
        details.pack(fill="x", pady=10)
        def selected(_event=None):
            if table.selection():
                self.set_text(details, problem_details(int(table.selection()[0])))
        table.bind("<<TreeviewSelect>>", selected)
        def filter_codes(*_args):
            term = search.get().strip().casefold()
            table.delete(*table.get_children())
            for code, info in PROBLEMS.items():
                if term in f"{code} {info.symbol} {info.title} {info.meaning} {info.next_step}".casefold():
                    table.insert("", "end", iid=str(code), values=(code, info.title,
                                 "Legacy / internal SDK" if info.legacy else "No problem" if code == 0 else "Device Manager"))
        search.trace_add("write", filter_codes)
        filter_codes()
        table.selection_set("32")
        table.see("32")
        selected()
        def source():
            selection = table.selection()
            code = int(selection[0]) if selection else None
            webbrowser.open(problem_info(code).url if code not in (0, None) else ERROR_SOURCE)
        ttk.Button(frame, text="Microsoft documentation for selected code", command=source).pack(anchor="w")

    def selected(self) -> GPU | None:
        selection = self.tree.selection()
        return self.devices[int(selection[0])] if selection else None

    @staticmethod
    def set_text(widget, value: str):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def append_log(self, value: str):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.now():%H:%M:%S}] {value}\n\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def selection_changed(self, _event=None):
        gpu = self.selected()
        text = "Select a GPU to see its exact device ID."
        if gpu:
            text = (f"{gpu.name}\nInstance ID: {gpu.instance_id}\n"
                    f"Driver: {self.version_label(gpu)} | Windows: {gpu.driver_version or 'Unknown'}\n"
                    f"Driver INF: {gpu.driver_inf or 'Unknown'} | Location: {gpu.location or 'Unknown'}\n")
            text += problem_details(gpu.problem_code)
        self.set_text(self.details, text)
        available = gpu is not None and gpu.present is True and is_admin() and not self.busy
        self.disable_button.configure(state="normal" if available and gpu.problem_code != 22 else "disabled")
        self.enable_button.configure(state="normal" if available and gpu.problem_code == 22 else "disabled")

    def display_inventory(self, devices: list[GPU]):
        previous = self.selected()
        self.tree.delete(*self.tree.get_children())
        self.devices = devices
        for index, gpu in enumerate(devices):
            self.tree.insert("", "end", iid=str(index), values=(gpu.name,
                             {True: "Yes", False: "No", None: "Unknown"}[gpu.present],
                             gpu.state, self.version_label(gpu), gpu.desktop))
        match = next((index for index, gpu in enumerate(devices)
                      if previous and gpu.instance_id == previous.instance_id), None)
        if match is not None:
            self.tree.selection_set(str(match))
        self.selection_changed()

    @staticmethod
    def version_label(gpu: GPU) -> str:
        if is_nvidia(gpu) and gpu.driver_version:
            try:
                return nvidia_version(gpu.driver_version)
            except ValueError:
                pass
        return gpu.driver_version or "Unknown"

    def start_job(self, label: str, function, done):
        self.busy = True
        self.progress.start(12)
        self.status.set(label)
        self.profile_status.set(label)
        for button in (self.refresh_button, self.admin_button, self.export_button):
            button.configure(state="disabled")
        self.profile_controls()
        self.selection_changed()
        def worker():
            try:
                self.events.put((done, function(), None))
            except Exception as error:
                self.events.put((done, None, str(error)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            done, result, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.progress.stop()
            self.status.set("Ready" if not error else "Action failed; see details")
            self.profile_status.set("Ready" if not error else "Action failed: " + error)
            self.refresh_button.configure(state="normal")
            self.admin_button.configure(state="disabled" if is_admin() else "normal")
            self.export_button.configure(state="normal")
            self.selection_changed()
            self.profile_controls()
            if error:
                self.append_log(error)
                messagebox.showerror("GPU Manager", error, parent=self)
            else:
                done(result)
        self.after(100, self.poll)

    def refresh(self):
        def done(devices):
            self.display_inventory(devices)
            self.append_log(f"Found {len(devices)} display adapter(s). "
                            "Select a device for its problem-code explanation and next steps.")
        self.start_job("Reading Windows GPU inventory...", inventory, done)

    def prepare(self, action: str):
        gpu = self.selected()
        if self.busy or gpu is None or not is_admin():
            return
        def prepare_job():
            devices = inventory()
            target = validate_action(devices, gpu.instance_id, action)
            path = recovery_file(target, devices) if action == "disable" else None
            return target, devices, path
        def done(result):
            target, devices, path = result
            self.display_inventory(devices)
            prompt = f"{action.capitalize()} {target.name}?\n\nExact device:\n{target.instance_id}"
            if path:
                self.append_log(f"Recovery saved: {path}")
                prompt += (f"\n\nRecovery file:\n{path}\n\n"
                           "Confirm all monitor cables are connected to a different, working GPU. "
                           "Displays attached to this GPU may go black. "
                           "To restore it, right-click the recovery CMD file and Run as administrator.")
            if messagebox.askyesno("Confirm GPU change", prompt, parent=self, default="no"):
                self.start_job(f"{action.capitalize()} operation and verification...",
                               lambda: change_device(target.instance_id, action), self.changed)
        self.start_job("Checking selected GPU and preparing recovery...", prepare_job, done)

    def changed(self, result):
        devices, detail = result
        self.display_inventory(devices)
        self.append_log(detail)
        messagebox.showinfo("Windows device result", detail, parent=self)

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json",
                                          initialfile="gpu-diagnosis.json", filetypes=[("JSON", "*.json")])
        if path:
            try:
                Path(path).write_text(json.dumps([asdict(gpu) for gpu in self.devices], indent=2), encoding="utf-8")
                self.append_log(f"Diagnosis exported to {path}")
            except OSError as error:
                messagebox.showerror("Export failed", str(error), parent=self)

    def elevate(self):
        if self.busy or is_admin():
            return
        shell = ctypes.windll.shell32.ShellExecuteW
        shell.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                          wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
        shell.restype = ctypes.c_ssize_t
        result = shell(None, "runas", sys.executable,
                       subprocess.list2cmdline([str(Path(__file__).resolve())]),
                       str(Path(__file__).resolve().parent), 1)
        if result <= 32:
            messagebox.showerror("Elevation canceled or failed", "No GPU settings were changed.", parent=self)
        else:
            self.destroy()

    def close(self):
        if self.busy:
            messagebox.showinfo("Operation in progress", "Wait for the current operation to finish.", parent=self)
        else:
            self.destroy()


if __name__ == "__main__":
    if os.name != "nt" or sys.getwindowsversion().build < 19041:
        raise SystemExit("Requires Windows 10 version 2004+ or Windows 11.")
    if "--list" in sys.argv:
        print(json.dumps([asdict(gpu) for gpu in inventory()], indent=2))
    else:
        App().mainloop()
