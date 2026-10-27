"""Read NVIDIA's public driver catalogue; download only after user selection."""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode, unquote, urlsplit
import urllib.request
import xml.etree.ElementTree as ET

from driver_profiles import is_nvidia, nvidia_version

LOOKUP = "https://www.nvidia.com/Download/API/lookupValueSearch.aspx"
SEARCH = "https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php"


def nvidia_url(url: str) -> str:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not (host == "nvidia.com" or host.endswith(".nvidia.com") or host == "gfwsl.geforce.com")):
        raise ValueError("NVIDIA returned a link outside its official HTTPS servers.")
    return url


class NvidiaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        nvidia_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_nvidia(url: str):
    request = urllib.request.Request(nvidia_url(url), headers={"User-Agent": "GPUManager/1.1"})
    return urllib.request.build_opener(NvidiaRedirect()).open(request, timeout=30)


def read_catalogue(url: str) -> bytes:
    with open_nvidia(url) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("NVIDIA's catalogue response was unexpectedly large.")
    return data


def version_key(value: str) -> tuple[int, int]:
    return tuple(int(part) for part in nvidia_version(value).split("."))


def product_name(value: str) -> str:
    value = unquote(value).strip().casefold().replace("®", "").replace("™", "")
    return " ".join(re.sub(r"^nvidia\s+", "", value).split())


def catalogue_entries(kind: int, parent: str | None = None) -> tuple[tuple[str, str], ...]:
    query = {"TypeID": kind}
    if parent is not None:
        query["ParentID"] = parent
    root = ET.fromstring(read_catalogue(LOOKUP + "?" + urlencode(query)))
    values = []
    for item in root.findall(".//LookupValue"):
        name, identifier = item.findtext("Name"), item.findtext("Value")
        if name and identifier and identifier.isdigit():
            values.append((name, identifier))
    return tuple(values)


def series_rank(name: str, gpu_name: str) -> tuple[int, int]:
    """Order catalogue searches, without treating a partial name as a match."""
    model = product_name(gpu_name)
    numbers = re.findall(r"\d+", model)
    prefixes = [number.rstrip("0") or number for number in re.findall(r"\d+", name)]
    numeric = max((len(prefix) for prefix in prefixes for number in numbers if number.startswith(prefix)), default=0)
    words = set(re.findall(r"[a-z]+", model)) - {"nvidia", "geforce", "series", "gpu"}
    shared = len(words & set(re.findall(r"[a-z]+", name.casefold())))
    return numeric, shared


def resolve_products(gpu_name: str, taxonomy: dict) -> tuple[tuple[str, str, str], ...]:
    """Find exact product names in NVIDIA's live category/series/product tree."""
    def entries(kind, parent=None):
        key = kind, parent
        if key not in taxonomy:
            taxonomy[key] = catalogue_entries(kind, parent)
        return taxonomy[key]
    expected = product_name(gpu_name)
    categories = [item for item in entries(1) if not any(word in item[0].casefold() for word in ("cuda toolkit", "3d vision"))]
    def category_rank(item):
        label = product_name(item[0])
        words = set(re.findall(r"[a-z]+", expected))
        score = len(words & set(re.findall(r"[a-z]+", label)))
        if "geforce" in expected:
            score += 10 if "geforce" in label else 0
        elif any(word in expected for word in ("quadro", "rtx", "nvs", "ion", "titan")):
            score += 5 if words & set(re.findall(r"[a-z]+", label)) else 0
        elif "data center" in label or "tesla" in label:
            score += 4
        return score
    categories.sort(key=category_rank, reverse=True)
    for _category, identifier in categories:
        series = list(entries(2, identifier))
        scores = sorted({series_rank(name, gpu_name) for name, _key in series}, reverse=True)
        for score in scores:
            group = [item for item in series if series_rank(item[0], gpu_name) == score]
            missing = [(3, key) for _name, key in group if (3, key) not in taxonomy]
            if missing:
                # Only independent read-only catalogue requests run concurrently.
                with ThreadPoolExecutor(max_workers=6) as workers:
                    values = list(workers.map(lambda key: catalogue_entries(*key), missing))
                taxonomy.update(zip(missing, values))
            matches = [(product, series_id, product_id) for _label, series_id in group
                       for product, product_id in taxonomy[(3, series_id)]
                       if product_name(product) == expected]
            if matches:
                return tuple(matches)
    raise ValueError("An exact product entry could not be confirmed in NVIDIA's catalogue. "
                     "Use its official offline EXE; Windows will check the selected GPUs before installation.")


@dataclass(frozen=True)
class DriverOffer:
    version: str
    title: str
    date: str
    details_url: str
    download_url: str
    supported: tuple[str, ...]


@dataclass(frozen=True)
class GPUUpdate:
    instance_id: str
    name: str
    installed: str
    disabled: bool
    offers: tuple[DriverOffer, ...] = ()
    error: str = ""

    def newer(self) -> tuple[DriverOffer, ...]:
        try:
            installed = version_key(self.installed)
        except ValueError:
            return self.offers
        return tuple(item for item in self.offers if version_key(item.version) > installed)


@dataclass(frozen=True)
class UpdateChoice:
    label: str
    offer: DriverOffer
    device_ids: tuple[str, ...]


def parse_offers(data: dict, expected_product: str, os_id=135) -> tuple[DriverOffer, ...]:
    if not isinstance(data, dict) or not str(data.get("Success", "")).isdigit() or not isinstance(data.get("IDS"), list):
        raise ValueError("NVIDIA changed its driver search response. Use the official driver page or import an EXE.")
    offers = {}
    expected_os = "Windows 11" if os_id == 135 else "Windows 10 64-bit"
    for row in data["IDS"]:
        info = row.get("downloadInfo", {})
        if str(info.get("IsWHQL")) != "1" or str(info.get("IsBeta")) != "0" or str(info.get("Is64Bit")) != "1":
            continue
        systems = [unquote(item.get("OSName", "")) for item in info.get("OSList", [])]
        if expected_os not in systems:
            continue
        supported = tuple(unquote(item.get("productName", "")) for series in info.get("series", [])
                          for item in series.get("products", []))
        if product_name(expected_product) not in {product_name(item) for item in supported}:
            continue
        version = nvidia_version(info.get("Version"))
        details = nvidia_url(unquote(info.get("DetailsURL", "")))
        download = nvidia_url(unquote(info.get("DownloadURL", "")))
        if not urlsplit(download).path.lower().endswith(".exe"):
            continue
        offers[(version, download)] = DriverOffer(version, unquote(info.get("Name", "NVIDIA driver")),
                                                unquote(info.get("ReleaseDateTime", "")), details, download, supported)
    return tuple(sorted(offers.values(), key=lambda item: version_key(item.version), reverse=True))


def check_updates(devices, os_id=135) -> list[GPUUpdate]:
    if os_id not in (57, 135):
        raise ValueError("Driver checks currently support Windows 10/11 x64.")
    results = []
    taxonomy = {}
    for gpu in devices:
        if not is_nvidia(gpu) or gpu.present is not True:
            continue
        try:
            installed = nvidia_version(gpu.driver_version)
        except ValueError:
            installed = "unknown"
        offers, error = (), ""
        try:
            matches = resolve_products(gpu.name, taxonomy)
            candidates = []
            for product, series, product_id in matches:
                packages = {}
                for dch in (1, 0):
                    query = urlencode(dict(func="DriverManualLookup", psid=series, pfid=product_id, osID=os_id,
                                           languageCode=1033, isWHQL=1, dch=dch, numberOfResults=30, sort1=1))
                    for offer in parse_offers(json.loads(read_catalogue(SEARCH + "?" + query)), product, os_id):
                        packages[(offer.version, offer.download_url)] = offer
                candidates.append(packages)
            common = set(candidates[0])
            for packages in candidates[1:]:
                common &= set(packages)
            if len(candidates) > 1 and not common:
                raise ValueError("NVIDIA lists multiple exact-name product variants with different packages. "
                                 "Choose the correct desktop/notebook or OEM driver EXE manually; no variant was guessed.")
            offers = tuple(sorted((candidates[0][key] for key in common), key=lambda item: version_key(item.version), reverse=True))
            if not offers:
                error = "NVIDIA returned no confirmed compatible WHQL Windows packages; availability is unknown."
        except Exception as problem:
            error = str(problem)
        results.append(GPUUpdate(gpu.instance_id, gpu.name, installed, gpu.problem_code == 22, offers, error))
    return results


def update_choices(results: list[GPUUpdate]) -> list[UpdateChoice]:
    choices = []
    for gpu in results:
        if gpu.newer():
            offer = gpu.newer()[0]
            choices.append(UpdateChoice(f"{gpu.name} only — {offer.version} ({offer.title})", offer, (gpu.instance_id,)))
    if len(results) > 1 and all(item.offers for item in results):
        # A common update must be newer for every target and advertise every
        # exact product. Never combine two different NVIDIA driver branches.
        common = {(item.version, item.download_url): item for item in results[0].newer()}
        for gpu in results[1:]:
            keys = {(item.version, item.download_url) for item in gpu.newer()}
            common = {key: value for key, value in common.items() if key in keys}
        if common:
            offer = max(common.values(), key=lambda item: version_key(item.version))
            choices.append(UpdateChoice(f"Both/all {len(results)} GPUs — common driver {offer.version} (AI testing required)", offer,
                                        tuple(item.instance_id for item in results)))
    return choices


def download_offer(offer: DriverOffer, destination: Path) -> Path:
    """Download only. NVIDIA signature and INF compatibility are checked later."""
    name = Path(urlsplit(nvidia_url(offer.download_url)).path).name
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.exe", name, re.IGNORECASE):
        raise ValueError("Invalid NVIDIA download filename.")
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / name
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError("The existing download path is not a regular file.")
        # The importer still checks NVIDIA's signature, fingerprint, expected
        # version and device compatibility before using this saved download.
        return path.resolve()
    partial = path.with_suffix(".exe.part")
    if partial.exists():
        raise ValueError(f"An incomplete download exists at {partial}. Remove or move it before retrying.")
    try:
        with open_nvidia(offer.download_url) as response, partial.open("xb") as output:
            header = response.headers.get("Content-Length")
            expected = int(header) if header else None
            maximum = 4 * 1024**3
            if expected is not None and not 0 < expected <= maximum:
                raise ValueError("The NVIDIA download has an unexpected size.")
            total = 0
            deadline = time.monotonic() + 1800
            for block in iter(lambda: response.read(1024 * 1024), b""):
                total += len(block)
                if total > maximum or time.monotonic() > deadline:
                    raise ValueError("The NVIDIA download exceeded the size or time limit.")
                output.write(block)
            if not total or (expected is not None and total != expected):
                raise ValueError("The NVIDIA download is incomplete. Try again.")
        partial.replace(path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return path.resolve()
