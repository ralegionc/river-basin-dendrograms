"""Putting names on the tributaries.

HydroRIVERS carries no river names at all: a reach knows its id, its neighbour
and its drainage area, and nothing else. So the plates come out with 24 hues
and no way to tell which one is the Madeira.

Natural Earth publishes named river centrelines, about 1,400 of them worldwide
in a 2 MB download. They are a generalised cartographic layer, drawn at a much
coarser scale than HydroRIVERS, so they will not line up exactly. They do not
need to: matching only has to decide which named line a tributary runs along,
and a vote over many sample points is robust to the two datasets disagreeing
about precisely where the channel sits.
"""
from __future__ import annotations

import zipfile
from collections import Counter
from pathlib import Path

import numpy as np
import shapely

from .topology import _children, downstream_index

NE_URL = ("https://naciscdn.org/naturalearth/10m/physical/"
          "ne_10m_rivers_lake_centerlines.zip")


def ensure_names(root: Path) -> Path:
    """Download the Natural Earth river centrelines if they are not present."""
    raw = Path(root) / "raw"
    shp = raw / "ne_10m_rivers_lake_centerlines.shp"
    if shp.exists():
        return shp

    from .regions import _download

    archive = raw / "ne_10m_rivers_lake_centerlines.zip"
    if not archive.exists():
        _download(NE_URL, archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(raw)
    if not shp.exists():
        raise FileNotFoundError(f"{shp} missing after extracting {archive}")
    return shp


def load_named_rivers(shp, bbox=None):
    """Named centrelines, optionally clipped to a bounding box.

    The shapefile is latin-1, and has no .cpg to say so, so accented names come
    back as mojibake unless the encoding is forced.
    """
    import pyogrio

    df = pyogrio.read_dataframe(shp, bbox=bbox, encoding="latin-1")
    name = df["name"].astype("string")
    alt = df["name_en"].astype("string") if "name_en" in df else name
    best = name.fillna(alt)
    keep = best.notna() & (best.str.len() > 0)
    out = df.loc[keep].copy()
    out["label"] = best.loc[keep].str.strip()
    return out.reset_index(drop=True)


def group_trunk(gdf, labels, group, nd_idx=None, max_steps=100_000):
    """Row indices along the main channel of one tributary group.

    Starts at the group's own outlet, the reach where it meets the parent
    river, and walks upstream always taking the largest feeder that is still
    inside the group. Following the trunk rather than taking every reach in the
    group matters: a vote over the whole group would drag in sub tributaries
    that carry their own names.
    """
    upland = gdf["UPLAND_SKM"].to_numpy(np.float64)
    if nd_idx is None:
        nd_idx = downstream_index(gdf["HYRIV_ID"].to_numpy(np.int64),
                                  gdf["NEXT_DOWN"].to_numpy(np.int64))
    member = labels == group
    if not member.any():
        return np.empty(0, dtype=np.int64)

    kids, start = _children(nd_idx)
    node = int(np.flatnonzero(member)[np.argmax(upland[member])])

    path = []
    for _ in range(max_steps):
        path.append(node)
        lo, hi = start[node], start[node + 1]
        cand = kids[lo:hi]
        cand = cand[member[cand]]
        if cand.size == 0:
            break
        node = int(cand[np.argmax(upland[cand])])
    return np.asarray(path, dtype=np.int64)


def _sample_points(geoms, every=3, cap=400):
    """A thinned set of vertices from a run of reaches."""
    coords = shapely.get_coordinates(geoms)
    if len(coords) > cap * every:
        idx = np.linspace(0, len(coords) - 1, cap).astype(np.int64)
    else:
        idx = np.arange(0, len(coords), every)
    return coords[idx]


def match_names(gdf, labels, group_info, ne_shp, max_dist_deg=0.25,
                min_votes=0.25, verbose=True):
    """Decide which named river each tributary group runs along.

    For every group, sample points down its trunk, find the nearest named line
    to each, and let them vote. A group is left unnamed unless a clear winner
    takes at least ``min_votes`` of the sampled points, which is what stops a
    tributary with no Natural Earth counterpart from stealing its neighbour's
    name.
    """
    geom = gdf.geometry.values
    minx, miny, maxx, maxy = shapely.total_bounds(geom)
    pad = 1.0
    named = load_named_rivers(ne_shp, bbox=(minx - pad, miny - pad,
                                            maxx + pad, maxy + pad))
    if not len(named):
        return {}

    tree = shapely.STRtree(named.geometry.values)
    nd_idx = downstream_index(gdf["HYRIV_ID"].to_numpy(np.int64),
                              gdf["NEXT_DOWN"].to_numpy(np.int64))

    results = {}
    for info in group_info:
        g = info["label"]
        trunk = group_trunk(gdf, labels, g, nd_idx)
        if trunk.size == 0:
            continue
        pts_xy = _sample_points(geom[trunk])
        if len(pts_xy) < 4:
            continue
        pts = shapely.points(pts_xy)

        nearest = tree.nearest(pts)
        dist = shapely.distance(pts, named.geometry.values[nearest])
        close = dist <= max_dist_deg
        if close.sum() < 3:
            continue

        votes = Counter(named["label"].to_numpy()[nearest[close]])
        name, count = votes.most_common(1)[0]
        share = count / len(pts)
        if share < min_votes:
            continue

        # Anchor the label halfway along the part of the trunk that actually
        # matched, not at the group's mouth where it is crowded by the parent.
        matched = pts_xy[close][
            named["label"].to_numpy()[nearest[close]] == name
        ]
        anchor = matched[len(matched) // 2] if len(matched) else pts_xy[len(pts_xy) // 2]

        results[int(g)] = {
            "name": str(name),
            "lon": float(anchor[0]),
            "lat": float(anchor[1]),
            "share": float(share),
            "upland_skm": float(info["upland_skm"]),
        }

    if verbose:
        print(f"  named {len(results)} of {len(group_info)} groups "
              f"from Natural Earth")
    return results
