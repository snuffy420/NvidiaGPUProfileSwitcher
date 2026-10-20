"""Private NVIDIA package cache and exact-device Windows driver selection.

Caching copies packages; it never changes the active driver or edits the
Windows Driver Store. Switching uses SetupAPI compatibility matching and
DiInstallDevice with an explicit driver node, so older versions can be chosen.
"""
from __future__ import annotations

from contextlib import ExitStack
import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil

from driver_profiles import nvidia_version, sha256_file, write_json

DISPLAY_GUID = "{4d36e968-e325-11ce-bfc1-08002be10318}"


@dataclass(frozen=True)
class Package:
    inf: str
    version: str
    provider: str
    published_inf: str = ""


def inf_metadata(path: Path) -> Package:
    data = path.read_bytes()
    text = data.decode("utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig", errors="replace")
    sections = {}
    section = ""
    for line in text.splitlines():
        line = line.split(";", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().casefold()
        elif "=" in line:
            key, value = line.split("=", 1)
            sections.setdefault(section, {})[key.strip().casefold()] = value.strip().strip('"')
    version = sections.get("version", {})
    if version.get("class", "").casefold() != "display" or version.get("classguid", "").casefold() != DISPLAY_GUID:
        raise ValueError("Choose a display-adapter INF package.")
    provider = version.get("provider", "")
    if provider.startswith("%") and provider.endswith("%"):
        key = provider.strip("%").casefold()
        provider = sections.get("strings", {}).get(key, provider)
    if "nvidia" not in provider.casefold():
        raise ValueError("The selected display INF is not an NVIDIA package.")
    driver_version = version.get("driverver", "").split(",")[-1].strip()
    return Package(str(path.resolve()), nvidia_version(driver_version), provider)


def discover_packages(inf_dir: Path | None = None) -> list[Package]:
    directory = inf_dir or Path(os.environ.get("SystemRoot", r"C:\Windows")) / "INF"
    packages = []
    for path in sorted(directory.glob("oem*.inf")):
        try:
            package = inf_metadata(path)
            packages.append(Package(package.inf, package.version, package.provider, path.name))
        except (OSError, ValueError):
            continue
    return packages


def _setup_api():
    if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("Cached switching requires 64-bit Python on 64-bit Windows.")
    return ctypes.WinDLL("setupapi", use_last_error=True)


def driver_store_inf(published: Path) -> Path:
    api = _setup_api()
    function = api.SetupGetInfDriverStoreLocationW
    function.argtypes = [w.LPCWSTR, ctypes.c_void_p, w.LPCWSTR, w.LPWSTR, w.DWORD, ctypes.POINTER(w.DWORD)]
    function.restype = w.BOOL
    buffer = ctypes.create_unicode_buffer(260)
    required = w.DWORD()
    if not function(str(published), None, None, buffer, len(buffer), ctypes.byref(required)):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value).resolve()


class GUID(ctypes.Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD), ("Data4", w.BYTE * 8)]


class DeviceInfo(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("ClassGuid", GUID), ("DevInst", w.DWORD), ("Reserved", ctypes.c_size_t)]


class InstallParams(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("Flags", w.DWORD), ("FlagsEx", w.DWORD),
                ("hwndParent", w.HWND), ("InstallMsgHandler", ctypes.c_void_p),
                ("InstallMsgHandlerContext", ctypes.c_void_p), ("FileQueue", ctypes.c_void_p),
                ("ClassInstallReserved", ctypes.c_size_t), ("Reserved", w.DWORD), ("DriverPath", w.WCHAR * 260)]


class DriverInfo(ctypes.Structure):
    _fields_ = [("cbSize", w.DWORD), ("DriverType", w.DWORD), ("Reserved", ctypes.c_size_t),
                ("Description", w.WCHAR * 256), ("MfgName", w.WCHAR * 256),
                ("ProviderName", w.WCHAR * 256), ("DriverDate", w.FILETIME), ("DriverVersion", ctypes.c_ulonglong)]


def unpack_driver_version(value: int) -> str:
    return ".".join(str((value >> shift) & 0xffff) for shift in (48, 32, 16, 0))


class DeviceDriver:
    """Read-only until install() is explicitly called. Keeps driver node alive."""
    def __init__(self, instance_id: str, inf: Path, version: str):
        if not re.fullmatch(r"[A-Za-z0-9_\\&.{}#+:\-]+", instance_id) or "VEN_10DE&" not in instance_id.upper():
            raise ValueError("An exact NVIDIA instance ID is required.")
        self.inf = inf.resolve()
        if len(str(self.inf)) >= 260:
            raise ValueError("Package path is too long for SetupAPI. Move the app/package to a shorter path.")
        self.instance_id = instance_id
        self.version = nvidia_version(version)
        self.api = _setup_api()
        definitions = {
            "SetupDiCreateDeviceInfoList": ([ctypes.POINTER(GUID), w.HWND], ctypes.c_void_p),
            "SetupDiOpenDeviceInfoW": ([ctypes.c_void_p, w.LPCWSTR, w.HWND, w.DWORD, ctypes.POINTER(DeviceInfo)], w.BOOL),
            "SetupDiGetDeviceInstallParamsW": ([ctypes.c_void_p, ctypes.POINTER(DeviceInfo), ctypes.POINTER(InstallParams)], w.BOOL),
            "SetupDiSetDeviceInstallParamsW": ([ctypes.c_void_p, ctypes.POINTER(DeviceInfo), ctypes.POINTER(InstallParams)], w.BOOL),
            "SetupDiBuildDriverInfoList": ([ctypes.c_void_p, ctypes.POINTER(DeviceInfo), w.DWORD], w.BOOL),
            "SetupDiEnumDriverInfoW": ([ctypes.c_void_p, ctypes.POINTER(DeviceInfo), w.DWORD, w.DWORD, ctypes.POINTER(DriverInfo)], w.BOOL),
            "SetupDiDestroyDeviceInfoList": ([ctypes.c_void_p], w.BOOL),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.SetupDiCreateDeviceInfoList(None, None)
        if self.handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.device = DeviceInfo()
            self.device.cbSize = ctypes.sizeof(self.device)
            self._check(self.api.SetupDiOpenDeviceInfoW(self.handle, instance_id, None, 0, ctypes.byref(self.device)))
            params = InstallParams()
            params.cbSize = ctypes.sizeof(params)
            self._check(self.api.SetupDiGetDeviceInstallParamsW(self.handle, ctypes.byref(self.device), ctypes.byref(params)))
            params.Flags |= 0x00010000  # DI_ENUMSINGLEINF
            params.Flags &= ~0x00080000  # clear DI_COMPAT_FROM_CLASS
            params.FlagsEx |= 0x00000800  # DI_FLAGSEX_ALLOWEXCLUDEDDRVS
            params.FlagsEx &= ~(0x04000000 | 0x02000000)  # installed-only / similar-driver filters
            params.DriverPath = str(self.inf)
            self._check(self.api.SetupDiSetDeviceInstallParamsW(self.handle, ctypes.byref(self.device), ctypes.byref(params)))
            self._check(self.api.SetupDiBuildDriverInfoList(self.handle, ctypes.byref(self.device), 2))
            index = 0
            self.driver = None
            while True:
                driver = DriverInfo()
                driver.cbSize = ctypes.sizeof(driver)
                if not self.api.SetupDiEnumDriverInfoW(self.handle, ctypes.byref(self.device), 2, index, ctypes.byref(driver)):
                    if ctypes.get_last_error() != 259:  # ERROR_NO_MORE_ITEMS
                        raise ctypes.WinError(ctypes.get_last_error())
                    break
                index += 1
                if "nvidia" in driver.ProviderName.casefold() and nvidia_version(unpack_driver_version(driver.DriverVersion)) == self.version:
                    self.driver = driver
                    break
            if self.driver is None:
                raise RuntimeError(f"Windows found no compatible NVIDIA {self.version} driver in {self.inf.name} for {instance_id}.")
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(success):
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

    def install(self) -> bool:
        """Return NeedReboot; never automatically reboot or choose another driver."""
        if not self.handle or self.driver is None:
            raise RuntimeError("The selected driver context is closed.")
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError("Administrator rights are required to switch cached drivers.")
        api = ctypes.WinDLL("newdev", use_last_error=True)
        function = api.DiInstallDevice
        function.argtypes = [w.HWND, ctypes.c_void_p, ctypes.POINTER(DeviceInfo), ctypes.POINTER(DriverInfo), w.DWORD, ctypes.POINTER(w.BOOL)]
        function.restype = w.BOOL
        reboot = w.BOOL()
        self._check(function(None, self.handle, ctypes.byref(self.device), ctypes.byref(self.driver), 0, ctypes.byref(reboot)))
        return bool(reboot.value)

    def close(self):
        if self.handle:
            self.api.SetupDiDestroyDeviceInfoList(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def preflight(inf: Path, version: str, device_ids: list[str]):
    with ExitStack() as stack:
        for device_id in device_ids:
            stack.enter_context(DeviceDriver(device_id, inf, version))


def _contained(path: Path, parent: Path) -> bool:
    return path.resolve().is_relative_to(parent.resolve())


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def validate_manifest(path: Path, cache_root: Path, expected_version: str | None = None) -> tuple[dict, Path]:
    path = path.resolve()
    if not _contained(path, cache_root) or path.is_symlink():
        raise ValueError("The cache manifest must be inside this application's driver_cache folder.")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != 1 or not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        raise ValueError("Invalid or incomplete cached-driver manifest.")
    if not isinstance(manifest.get("version"), str):
        raise ValueError("Invalid cached-driver manifest version.")
    nvidia_version(manifest["version"])
    if expected_version and nvidia_version(manifest["version"]) != nvidia_version(expected_version):
        raise ValueError("The cached package version does not match this profile.")
    package = path.parent / "package"
    actual = {}
    for item in package.rglob("*"):
        if _is_link(item) or not _contained(item, package):
            raise ValueError("A cached package contains a link outside its folder.")
        if item.is_file():
            actual[item.relative_to(package).as_posix()] = item
    if set(actual) != set(manifest["files"]):
        raise ValueError("Cached package files were added or removed. Cache the package again.")
    for relative, digest in manifest["files"].items():
        if not isinstance(digest, str) or sha256_file(actual[relative]) != digest:
            raise ValueError(f"Cached package file changed: {relative}. Cache the package again.")
    inf_name = manifest.get("inf")
    if not isinstance(inf_name, str) or inf_name not in actual:
        raise ValueError("Cached INF is missing from the manifest.")
    inf = actual[inf_name]
    metadata = inf_metadata(inf)
    if metadata.version != manifest["version"]:
        raise ValueError("Cached INF metadata does not match its manifest.")
    return manifest, inf


def cache_package(source_inf: Path, cache_root: Path, published_inf="") -> Path:
    source_inf = source_inf.resolve()
    metadata = inf_metadata(source_inf)
    source = source_inf.parent
    if _contained(source, cache_root) or _contained(cache_root, source):
        raise ValueError("Import the original extracted Display.Driver package, outside driver_cache.")
    files = []
    for item in source.rglob("*"):
        if _is_link(item) or not _contained(item, source):
            raise ValueError("Import source contains linked files/folders; choose the actual extracted package.")
        if item.is_file():
            files.append(item)
    if not any(item.suffix.casefold() == ".cat" for item in files):
        raise ValueError("The package has no signature catalog (.cat). Choose the complete Display.Driver folder.")
    required = sum(item.stat().st_size for item in files)
    cache_root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(cache_root).free < required + 64 * 1024 * 1024:
        raise RuntimeError("Not enough free space to cache this driver package.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = cache_root / f"{metadata.version}-{source_inf.stem}-{stamp}"
    package = folder / "package"
    folder.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source, package)
    hashes = {item.relative_to(package).as_posix(): sha256_file(item)
              for item in package.rglob("*") if item.is_file()}
    manifest = folder / "cache-manifest.json"
    write_json(manifest, {"schema": 1, "version": metadata.version, "provider": metadata.provider,
                         "inf": source_inf.name, "published_inf": published_inf,
                         "source_inf": str(source_inf), "cached_at": datetime.now().isoformat(), "files": hashes})
    return manifest


def cache_profile(profile, cache_root: Path, packages: list[Package] | None = None) -> Path:
    expected = nvidia_version(profile.expected_version)
    profile.validate()
    # Reuse a valid private copy, including copies made for a different profile.
    for path in cache_root.glob("*/cache-manifest.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != expected:
                continue
            _, inf = validate_manifest(path, cache_root, expected)
            preflight(inf, expected, profile.required_gpu_ids)
            return path.resolve()
        except (OSError, ValueError, RuntimeError):
            continue
    failures = []
    for package in packages if packages is not None else discover_packages():
        if package.version != expected:
            continue
        try:
            source_inf = driver_store_inf(Path(package.inf))
            preflight(source_inf, expected, profile.required_gpu_ids)
            return cache_package(source_inf, cache_root, package.published_inf)
        except (OSError, ValueError, RuntimeError) as error:
            failures.append(str(error))
    detail = "\n".join(failures)
    raise RuntimeError(f"No compatible {expected} package could be cached from Windows. "
                       "Use Import extracted INF, or install that version once and cache it afterward.\n" + detail)
