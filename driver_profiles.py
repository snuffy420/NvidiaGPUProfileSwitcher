"""Saved NVIDIA installer profiles; no driver changes occur in this module."""
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re


def nvidia_version(version: str | None) -> str:
    if version is not None and not isinstance(version, str):
        raise ValueError("Driver versions must be text.")
    value = (version or "").strip()
    if re.fullmatch(r"\d{2,4}\.\d{2}", value):
        major, minor = value.split(".")
        return f"{int(major)}.{minor}"
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", value):
        parts = [int(part) for part in value.split(".")]
        if (all(part <= 65535 for part in parts) and parts[0] >= 6
                and 10 <= parts[2] <= 99 and parts[3] <= 9999):
            digits = str(parts[2] - 10) + f"{parts[3]:04d}"
            return str(int(digits[:-2])) + "." + digits[-2:]
    raise ValueError("Enter an NVIDIA version such as 560.94, or a full Windows version such as 32.0.15.6094.")


def is_nvidia(gpu) -> bool:
    return "VEN_10DE&" in gpu.instance_id.upper()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class DriverProfile:
    name: str
    expected_version: str
    installer: str = ""
    required_gpu_ids: list[str] = field(default_factory=list)
    sha256: str = ""
    cache_manifest: str = ""
    disabled_gpu_ids: list[str] = field(default_factory=list)

    def validate(self, require_installer=False):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("A profile needs a name.")
        nvidia_version(self.expected_version)
        if not isinstance(self.required_gpu_ids, list) or not self.required_gpu_ids:
            raise ValueError("Select at least one NVIDIA GPU that must work in this profile.")
        for device_id in self.required_gpu_ids:
            if (not isinstance(device_id, str) or "VEN_10DE&" not in device_id.upper()
                    or not re.fullmatch(r"[A-Za-z0-9_\\&.{}#+:\-]+", device_id)):
                raise ValueError("A profile contains an invalid NVIDIA GPU instance ID.")
        if len({item.upper() for item in self.required_gpu_ids}) != len(self.required_gpu_ids):
            raise ValueError("A profile contains duplicate GPU IDs.")
        if not isinstance(self.disabled_gpu_ids, list):
            raise ValueError("Invalid disabled GPU list.")
        for device_id in self.disabled_gpu_ids:
            if (not isinstance(device_id, str) or "VEN_10DE&" not in device_id.upper()
                    or not re.fullmatch(r"[A-Za-z0-9_\\&.{}#+:\-]+", device_id)):
                raise ValueError("A profile contains an invalid disabled NVIDIA GPU ID.")
        disabled = {item.upper() for item in self.disabled_gpu_ids}
        if len(disabled) != len(self.disabled_gpu_ids) or disabled & {item.upper() for item in self.required_gpu_ids}:
            raise ValueError("A GPU cannot be both a driver target and disabled, or appear twice.")
        if not isinstance(self.installer, str) or not isinstance(self.sha256, str):
            raise ValueError("Invalid installer information.")
        if not isinstance(self.cache_manifest, str) or (self.cache_manifest and not Path(self.cache_manifest).is_absolute()):
            raise ValueError("A cached-package manifest needs an absolute path.")
        if self.sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("Invalid installer fingerprint.")
        if require_installer:
            path = Path(self.installer)
            if not self.installer or not path.is_absolute() or path.suffix.lower() != ".exe" or not path.is_file():
                raise ValueError("Choose a downloaded NVIDIA .exe driver installer in Add / edit profile.")
            if not self.sha256:
                raise ValueError("Save this profile again to record its installer fingerprint.")


def load_profiles(path: Path) -> list[DriverProfile]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("profiles"), list):
        raise ValueError("Unsupported driver profile file. The original file has been preserved.")
    profiles = [DriverProfile(**item) for item in data["profiles"]]
    for profile in profiles:
        profile.validate()
    if len({profile.name.casefold() for profile in profiles}) != len(profiles):
        raise ValueError("Driver profile names must be unique.")
    return profiles


def write_json(path: Path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)


def save_profiles(path: Path, profiles: list[DriverProfile]):
    for profile in profiles:
        profile.validate()
    if len({profile.name.casefold() for profile in profiles}) != len(profiles):
        raise ValueError("Choose a unique profile name.")
    write_json(path, {"schema": 1, "profiles": [asdict(profile) for profile in profiles]})


def capture_working_profile(devices, name="GPU profile") -> DriverProfile:
    cards = [gpu for gpu in devices if is_nvidia(gpu) and gpu.present is True]
    targets = [gpu for gpu in cards if gpu.problem_code == 0]
    if not targets or any(gpu.problem_code not in (0, 22) for gpu in cards):
        raise ValueError("At least one NVIDIA GPU must be healthy; other present GPUs must be healthy or intentionally disabled (Code 22).")
    versions = {nvidia_version(gpu.driver_version) for gpu in targets}
    if len(versions) != 1:
        raise ValueError("The NVIDIA GPUs have different driver versions. Create a profile manually after checking the installation.")
    return DriverProfile(name, versions.pop(), required_gpu_ids=[gpu.instance_id for gpu in targets],
                         disabled_gpu_ids=[gpu.instance_id for gpu in cards if gpu.problem_code == 22])


def verify_profile(profile: DriverProfile, devices) -> tuple[bool, str]:
    profile.validate()
    expected = nvidia_version(profile.expected_version)
    by_id = {gpu.instance_id.upper(): gpu for gpu in devices}
    lines = [f"Profile: {profile.name} | Expected NVIDIA driver: {expected}"]
    success = True
    for device_id in profile.required_gpu_ids:
        gpu = by_id.get(device_id.upper())
        reason = []
        if gpu is None or gpu.present is not True:
            reason.append("not detected as present")
        if gpu is not None:
            if gpu.problem_code != 0:
                reason.append(f"Code {gpu.problem_code if gpu.problem_code is not None else 'unknown'}")
            try:
                actual = nvidia_version(gpu.driver_version)
            except ValueError:
                actual = "unknown"
            if actual != expected:
                reason.append(f"loaded driver {actual}, expected {expected}")
        success = success and not reason
        lines.append(f"{'CHECK' if reason else 'OK'}: {gpu.name if gpu else device_id}: "
                     + ("; ".join(reason) if reason else f"driver {expected}, Code 0"))
    for device_id in profile.disabled_gpu_ids:
        gpu = by_id.get(device_id.upper())
        disabled = gpu is not None and gpu.present is True and gpu.problem_code == 22
        success = success and disabled
        lines.append(f"{'OK' if disabled else 'CHECK'}: {gpu.name if gpu else device_id}: "
                     + ("disabled as saved (Code 22)" if disabled else "must be disabled (Code 22) for this profile"))
    configured = {item.upper() for item in profile.required_gpu_ids + profile.disabled_gpu_ids}
    outside = [gpu for gpu in devices if gpu.present is True and is_nvidia(gpu)
               and gpu.instance_id.upper() not in configured]
    for gpu in outside:
        lines.append(f"Outside profile: {gpu.name}: {gpu.state}, driver {gpu.driver_version or 'unknown'}")
    lines.append("Driver/device verification PASSED." if success else
                 "Driver/device verification NOT PASSED. Finish setup/restart if needed, then verify again.")
    lines.append("Code 0 and a version match do not verify CUDA or your AI workflow; run a small workload afterward.")
    return success, "\n".join(lines)
