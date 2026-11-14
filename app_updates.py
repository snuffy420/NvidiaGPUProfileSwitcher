"""Check and securely download published GitHub release installers."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
import urllib.request

OWNER = "snuffy420"
REPOSITORY = "NvidiaGPUProfileSwitcher"
API = f"https://api.github.com/repos/{OWNER}/{REPOSITORY}/releases/latest"
MAX_API_SIZE = 2 * 1024 * 1024
MAX_INSTALLER_SIZE = 512 * 1024 * 1024


def _github_https(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError("GitHub returned a malformed update URL.") from error
    host = (parsed.hostname or "").lower()
    allowed = (host == "github.com" or host.endswith(".github.com")
               or host == "githubusercontent.com" or host.endswith(".githubusercontent.com")
               or host == "githubassets.com" or host.endswith(".githubassets.com"))
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443) or not allowed:
        raise ValueError(f"GitHub returned an unrecognized update host: {host or 'missing host'}.")
    return url


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        _github_https(new_url)
        return super().redirect_request(request, file_pointer, code, message, headers, new_url)


def opener():
    return urllib.request.build_opener(GitHubRedirect())


def read_release() -> dict:
    request = urllib.request.Request(API, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "NVIDIA-GPU-Driver-Profile-Switcher",
    })
    with opener().open(request, timeout=20) as response:
        data = response.read(MAX_API_SIZE + 1)
    if len(data) > MAX_API_SIZE:
        raise ValueError("GitHub's release information was unexpectedly large.")
    release = json.loads(data.decode("utf-8"))
    if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
        raise ValueError("GitHub did not return a published stable release.")
    return release


def version_key(value: str) -> tuple[int, int, int, int]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})", value.strip())
    if not match:
        raise ValueError("The GitHub release has an invalid version tag.")
    parts = [int(part) for part in match.group(1).split(".")]
    return tuple((parts + [0] * 4)[:4])


@dataclass(frozen=True)
class ReleaseUpdate:
    version: str
    installer_url: str
    installer_size: int
    installer_sha256: str
    notes: str
    release_url: str


def check_for_update(current_version: str) -> ReleaseUpdate | None:
    release = read_release()
    tag = release.get("tag_name")
    if not isinstance(tag, str):
        raise ValueError("GitHub's latest release did not include a version tag.")
    latest = tag.removeprefix("v")
    if version_key(latest) <= version_key(current_version):
        return None
    expected_name = f"NVIDIA-GPU-Driver-Profile-Switcher-v{latest}-Setup-Windows-x64.exe"
    asset = next((item for item in release.get("assets", [])
                  if isinstance(item, dict) and item.get("name") == expected_name), None)
    if asset is None:
        raise ValueError(f"GitHub release {tag} does not include the Windows installer.")
    url = asset.get("browser_download_url")
    parsed = urlsplit(url) if isinstance(url, str) else None
    expected_path = f"/{OWNER}/{REPOSITORY}/releases/download/{tag}/{expected_name}"
    if parsed is None or parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.path != expected_path:
        raise ValueError("GitHub returned an unexpected installer location.")
    size = asset.get("size")
    if not isinstance(size, int) or not 0 < size <= MAX_INSTALLER_SIZE:
        raise ValueError("The GitHub installer has an unexpected file size.")
    digest = asset.get("digest")
    match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest or "") if isinstance(digest, str) else None
    if match is None:
        raise ValueError("GitHub did not provide a SHA-256 digest for the installer.")
    notes = release.get("body") if isinstance(release.get("body"), str) else ""
    notes = html.unescape(notes).strip()[:4000]
    release_url = release.get("html_url")
    if not isinstance(release_url, str) or urlsplit(release_url).hostname != "github.com":
        release_url = f"https://github.com/{OWNER}/{REPOSITORY}/releases/tag/{tag}"
    return ReleaseUpdate(latest, url, size, match.group(1).lower(), notes, release_url)


def download_installer(update: ReleaseUpdate, destination: Path) -> Path:
    """Download the release installer and verify the API-published SHA-256 digest."""
    _github_https(update.installer_url)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / Path(urlsplit(update.installer_url).path).name
    partial = path.with_suffix(path.suffix + ".part")
    for candidate in (path, partial):
        if candidate.is_symlink() or (candidate.exists() and not candidate.is_file()):
            raise ValueError("The update download path is not a regular file.")
    request = urllib.request.Request(update.installer_url, headers={"User-Agent": "NVIDIA-GPU-Driver-Profile-Switcher"})
    digest = hashlib.sha256()
    total = 0
    deadline = time.monotonic() + 900
    try:
        with opener().open(request, timeout=30) as response, partial.open("wb") as output:
            header = response.headers.get("Content-Length")
            if header and int(header) != update.installer_size:
                raise ValueError("The GitHub installer size does not match its release metadata.")
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > update.installer_size or total > MAX_INSTALLER_SIZE or time.monotonic() > deadline:
                    raise ValueError("The GitHub installer exceeded its published size or time limit.")
                digest.update(block)
                output.write(block)
        if total != update.installer_size or digest.hexdigest() != update.installer_sha256:
            raise ValueError("The GitHub installer failed its SHA-256 or size check; it was not installed.")
        partial.replace(path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return path.resolve()
