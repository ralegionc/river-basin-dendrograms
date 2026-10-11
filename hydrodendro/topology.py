"""Splitting a basin into its mainstem and its major tributary systems.

Colouring by elevation shows relief but hides structure: on the Amazon every
lowland reach lands in the same narrow band and the picture reads as one blue
haze. Colouring by tributary instead gives each major river its own hue, so the
Madeira, the Negro and the Tapajos separate at a glance and the basin reads as
a set of competing systems rather than a texture.

The whole thing runs off ``NEXT_DOWN``, which makes the reach table a forest of
in-trees rooted at the outlet.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def downstream_index(hyriv_id: np.ndarray, next_down: np.ndarray) -> np.ndarray:
    """Row index of each reach's downstream neighbour, or -1 at an outlet."""
    pos = pd.Series(np.arange(len(hyriv_id), dtype=np.int64), index=hyriv_id)
    nd = pos.reindex(next_down).to_numpy(dtype=np.float64)
    nd[~np.isfinite(nd)] = -1.0
    return nd.astype(np.int64)


def _children(nd_idx: np.ndarray):
    """CSR style child lists: children of row j are ``kids[start[j]:start[j+1]]``."""
    n = len(nd_idx)
    has_parent = nd_idx >= 0
    kids = np.argsort(nd_idx[has_parent], kind="stable")
    parents = nd_idx[has_parent][kids]
    kids = np.nonzero(has_parent)[0][kids]
    start = np.searchsorted(parents, np.arange(n + 1))
    return kids, start


def mainstem_mask(upland: np.ndarray, nd_idx: np.ndarray) -> np.ndarray:
    """The trunk: walk up from the outlet, always taking the largest feeder.

    Following drainage area rather than channel length is what a hydrologist
    means by the main stem, and it is also what keeps the widest line on the
    canvas continuous from headwater to sea.
    """
    kids, start = _children(nd_idx)
    mask = np.zeros(len(upland), dtype=bool)

    node = int(np.argmax(upland))
    while True:
        mask[node] = True
        lo, hi = start[node], start[node + 1]
        if lo == hi:
            break
        candidates = kids[lo:hi]
        node = int(candidates[np.argmax(upland[candidates])])
    return mask


def tributary_labels(gdf, n_groups: int = 24):
    """Label every reach with the tributary system it belongs to.

    Returns ``(labels, info)`` where label 0 is the mainstem and 1..n are the
    ``n_groups`` largest tributaries, ranked by drainage area. Anything joining
    the trunk below that cut, the thousands of small creeks that enter it
    directly, falls back to the mainstem group.
    """
    hyriv = gdf["HYRIV_ID"].to_numpy(np.int64)
    upland = gdf["UPLAND_SKM"].to_numpy(np.float64)
    dist_dn = gdf["DIST_DN_KM"].to_numpy(np.float64)
    nd_idx = downstream_index(hyriv, gdf["NEXT_DOWN"].to_numpy(np.int64))

    trunk = mainstem_mask(upland, nd_idx)

    # A tributary root is any reach that pours straight into the trunk without
    # being part of it. Keep the biggest few; the rest stay with the trunk.
    joins_trunk = np.zeros(len(gdf), dtype=bool)
    feed = nd_idx >= 0
    joins_trunk[feed] = trunk[nd_idx[feed]]
    roots = np.nonzero(joins_trunk & ~trunk)[0]
    roots = roots[np.argsort(-upland[roots])][:n_groups]

    labels = np.full(len(gdf), -1, dtype=np.int32)
    labels[trunk] = 0
    labels[roots] = np.arange(1, len(roots) + 1, dtype=np.int32)

    # Propagate upstream. DIST_DN_KM strictly decreases downstream, so visiting
    # reaches in ascending order guarantees a reach's downstream neighbour is
    # already resolved: one linear pass, no recursion.
    order = np.argsort(dist_dn, kind="stable")
    lab = labels
    for i in order:
        if lab[i] == -1:
            j = nd_idx[i]
            lab[i] = lab[j] if j >= 0 else 0
    lab[lab < 0] = 0

    # Count after propagation, so group 0 reports the trunk plus the small
    # creeks that drain straight into it rather than the walked trunk alone.
    counts = np.bincount(lab, minlength=len(roots) + 1)
    info = [{"label": 0, "upland_skm": float(upland[trunk].max()),
             "reaches": int(counts[0]), "trunk_reaches": int(trunk.sum()),
             "is_mainstem": True}]
    for k, r in enumerate(roots, start=1):
        info.append({"label": k, "upland_skm": float(upland[r]),
                     "reaches": int(counts[k]), "trunk_reaches": 0,
                     "is_mainstem": False})
    return lab, info


def basin_labels(gdf, n_groups: int = 40):
    """Label every reach by which basin it belongs to, ranked by drainage area.

    The region-wide counterpart to :func:`tributary_labels`. Within one basin
    the interesting split is by tributary; across a continent it is by basin,
    and ``MAIN_RIV`` already carries that, so this is a ranking rather than a
    tree walk.

    South America holds 15,511 separate basins, almost all of them small
    coastal creeks. The largest ``n_groups`` get the front of the palette in
    order of size; the rest keep cycling through it, which is what gives the
    coastline its fringe of small independent systems instead of one flat mass.
    """
    main = gdf["MAIN_RIV"].to_numpy()
    upland = gdf["UPLAND_SKM"].to_numpy(np.float64)

    by_basin = pd.Series(upland).groupby(main).max().sort_values(ascending=False)
    rank_of = pd.Series(np.arange(len(by_basin), dtype=np.int32), index=by_basin.index)
    labels = rank_of.reindex(main).to_numpy(np.int32)

    counts = pd.Series(np.ones(len(main), dtype=np.int64)).groupby(main).sum()
    counts = counts.reindex(by_basin.index).to_numpy()
    info = [
        {"label": int(i), "upland_skm": float(a), "reaches": int(c),
         "main_riv": int(m), "is_mainstem": False}
        for i, (m, a, c) in enumerate(zip(by_basin.index, by_basin.to_numpy(), counts))
    ]
    return labels, info[:n_groups]
