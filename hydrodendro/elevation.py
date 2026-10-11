"""Sampling the HydroSHEDS 30 arc-second DEM at river vertices.

rasterio's ``sample`` walks points in Python, which is far too slow for the
few million vertices in a large basin. Instead we read one window covering the
basin into memory and index it with numpy, which turns the whole job into two
array lookups.
"""
from __future__ import annotations

import numpy as np
import rasterio
from rasterio.windows import Window


def sample_points(dem_path, lon: np.ndarray, lat: np.ndarray, pad: int = 4) -> np.ndarray:
    """Elevation in metres at each ``(lon, lat)``, as float32 with NaN for nodata.

    Reads only the window enclosing the points, so basin-sized queries stay
    cheap even though the regional DEM is a few hundred megabytes.
    """
    with rasterio.open(dem_path) as src:
        inv = ~src.transform
        col_f, row_f = inv * (lon, lat)
        col = np.floor(np.asarray(col_f)).astype(np.int64)
        row = np.floor(np.asarray(row_f)).astype(np.int64)

        c0 = max(int(col.min()) - pad, 0)
        r0 = max(int(row.min()) - pad, 0)
        c1 = min(int(col.max()) + pad + 1, src.width)
        r1 = min(int(row.max()) + pad + 1, src.height)
        if c1 <= c0 or r1 <= r0:
            return np.full(lon.shape, np.nan, dtype=np.float32)

        block = src.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
        nodata = src.nodata

    rr = np.clip(row - r0, 0, block.shape[0] - 1)
    cc = np.clip(col - c0, 0, block.shape[1] - 1)
    out = block[rr, cc].astype(np.float32)
    if nodata is not None:
        out[out == np.float32(nodata)] = np.nan
    # HydroSHEDS marks ocean with large negative sentinels in some tiles
    out[out < -1000] = np.nan
    return out


def fill_gaps(values: np.ndarray, groups: np.ndarray) -> np.ndarray:
    """Replace NaNs with the mean of their reach, then with the global mean.

    Coastal and floodplain vertices occasionally land on a nodata DEM cell.
    Leaving them NaN would punch holes in the colouring, so they inherit the
    elevation of the reach they belong to.
    """
    out = np.asarray(values, dtype=np.float32).copy()
    bad = ~np.isfinite(out)
    if not bad.any():
        return out

    n = int(groups.max()) + 1 if len(groups) else 0
    good = ~bad
    total = np.bincount(groups[good], weights=out[good], minlength=n)
    count = np.bincount(groups[good], minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        reach_mean = np.where(count > 0, total / np.maximum(count, 1), np.nan)

    out[bad] = reach_mean[groups[bad]]
    still = ~np.isfinite(out)
    if still.any():
        fallback = np.nanmean(out) if np.isfinite(out).any() else 0.0
        out[still] = fallback
    return out
