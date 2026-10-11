"""HydroSHEDS regional products: download, extract, locate on disk."""
from __future__ import annotations

import shutil
import urllib.request
import zipfile
from pathlib import Path

RIVERS_URL = "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_{region}_shp.zip"
DEM_URL = "https://data.hydrosheds.org/file/hydrosheds-v1-dem/hyd_{region}_dem_30s.zip"

#: HydroSHEDS continental region codes.
REGIONS = {
    "af": "Africa",
    "ar": "North American Arctic",
    "as": "Asia (South, East and Southeast)",
    "au": "Australia and Oceania",
    "eu": "Europe and Middle East",
    "gr": "Greenland",
    "na": "North America and Caribbean",
    "sa": "South America",
    "si": "Siberia",
}


def data_root(root: str | Path | None = None) -> Path:
    """Directory holding downloaded HydroSHEDS products."""
    return Path(root) if root else Path(__file__).resolve().parent.parent / "data"


def rivers_path(region: str, root=None) -> Path:
    r = data_root(root) / "raw" / f"HydroRIVERS_v10_{region}_shp"
    return r / f"HydroRIVERS_v10_{region}.shp"


def dem_path(region: str, root=None) -> Path:
    return data_root(root) / "raw" / f"hyd_{region}_dem_30s.tif"


#: data.hydrosheds.org sits behind a CDN that rejects the default
#: "Python-urllib/3.x" agent with a 403, so every request sends a real one.
USER_AGENT = "Mozilla/5.0 (compatible; river-basin-dendrograms/0.1)"


def _download(url: str, dest: Path) -> Path:
    """Stream a URL to disk with a coarse progress line."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        step = max(total // 20, 1 << 22)
        next_mark = step
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
            done += len(chunk)
            if done >= next_mark:
                pct = f"{100 * done / total:5.1f}%" if total else "     "
                print(f"    {pct}  {done / 1e6:8.1f} MB", flush=True)
                next_mark += step
    tmp.replace(dest)
    return dest


def _unzip(archive: Path, into: Path) -> None:
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(into)


def ensure_rivers(region: str, root=None) -> Path:
    """Return the path to the regional HydroRIVERS shapefile, downloading if absent."""
    shp = rivers_path(region, root)
    if shp.exists():
        return shp
    raw = data_root(root) / "raw"
    archive = raw / f"HydroRIVERS_v10_{region}_shp.zip"
    if not archive.exists():
        _download(RIVERS_URL.format(region=region), archive)
    _unzip(archive, raw)
    if not shp.exists():
        raise FileNotFoundError(f"{shp} missing after extracting {archive}")
    return shp


def ensure_dem(region: str, root=None) -> Path:
    """Return the path to the regional 30 arc-second DEM, downloading if absent."""
    tif = dem_path(region, root)
    if tif.exists():
        return tif
    raw = data_root(root) / "raw"
    archive = raw / f"hyd_{region}_dem_30s.zip"
    if not archive.exists():
        _download(DEM_URL.format(region=region), archive)
    _unzip(archive, raw)
    if not tif.exists():
        raise FileNotFoundError(f"{tif} missing after extracting {archive}")
    return tif


def free_space_mb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1e6
