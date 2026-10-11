"""Horton's laws: the numbers behind the shape of a drainage network.

Horton observed in 1945 that a river network is close to self similar. Count
the streams of each Strahler order and the counts fall off as a geometric
series; measure their mean lengths and mean drainage areas and those grow as
geometric series too. The ratios between successive orders, bifurcation Rb,
length Rl and area Ra, stay roughly constant across the whole network, and for
natural basins Rb almost always lands between 3 and 5.

That constancy is the reason a basin looks like a nervous system at every zoom
level, so it is worth measuring rather than only drawing.

One subtlety drives the whole module. A Strahler *stream* is a maximal chain of
reaches sharing an order, not a single reach. HydroRIVERS stores short segments,
so a single order 4 stream may be fifty consecutive order 4 rows. Counting rows
instead of streams inflates every count by the same rough factor and quietly
destroys the ratios, which is the easy way to get this wrong.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .topology import downstream_index


def stream_table(gdf) -> pd.DataFrame:
    """Per Strahler order: stream count, mean length, mean drainage area.

    A stream is counted once, at its downstream end, which is the reach whose
    neighbour carries a different order (or is the basin outlet).
    """
    order = gdf["ORD_STRA"].to_numpy(np.int64)
    length = gdf["LENGTH_KM"].to_numpy(np.float64)
    upland = gdf["UPLAND_SKM"].to_numpy(np.float64)
    nd = downstream_index(gdf["HYRIV_ID"].to_numpy(np.int64),
                          gdf["NEXT_DOWN"].to_numpy(np.int64))

    # -1 for an outlet, which never equals a real order, so outlets correctly
    # close a stream.
    down_order = np.where(nd >= 0, order[np.maximum(nd, 0)], -1)
    ends_a_stream = down_order != order

    rows = []
    for w in range(1, int(order.max()) + 1):
        in_order = order == w
        n = int((in_order & ends_a_stream).sum())
        if n == 0:
            continue
        rows.append({
            "order": w,
            "streams": n,
            "reaches": int(in_order.sum()),
            "total_length_km": float(length[in_order].sum()),
            "mean_length_km": float(length[in_order].sum() / n),
            "mean_area_skm": float(upland[in_order & ends_a_stream].mean()),
        })
    return pd.DataFrame(rows)


def _ratio_fit(x, y):
    """Fit a geometric series and return its common ratio plus fit quality.

    Horton's laws are linear in log space, so the ratio is the slope of a least
    squares line through the logs, which uses every order rather than only the
    two at the ends.
    """
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    keep = y > 0
    if keep.sum() < 2:
        return float("nan"), float("nan")
    slope, intercept = np.polyfit(x[keep], np.log(y[keep]), 1)
    pred = slope * x[keep] + intercept
    resid = np.log(y[keep]) - pred
    ss_res = float((resid**2).sum())
    ss_tot = float(((np.log(y[keep]) - np.log(y[keep]).mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return float(np.exp(abs(slope))), r2


def horton_ratios(table: pd.DataFrame) -> dict:
    """The three Horton ratios, each fitted across every order at once."""
    w = table["order"].to_numpy()
    rb, rb_r2 = _ratio_fit(w, table["streams"].to_numpy())
    rl, rl_r2 = _ratio_fit(w, table["mean_length_km"].to_numpy())
    ra, ra_r2 = _ratio_fit(w, table["mean_area_skm"].to_numpy())
    return {
        "bifurcation": rb, "bifurcation_r2": rb_r2,
        "length": rl, "length_r2": rl_r2,
        "area": ra, "area_r2": ra_r2,
        "orders": int(len(table)),
    }


def stepwise(table: pd.DataFrame) -> pd.DataFrame:
    """Ratios between each adjacent pair of orders, to show the scatter the
    single fitted ratio hides."""
    t = table.sort_values("order").reset_index(drop=True)
    out = pd.DataFrame({"from_order": t["order"][:-1].to_numpy(),
                        "to_order": t["order"][1:].to_numpy()})
    out["Rb"] = t["streams"][:-1].to_numpy() / np.maximum(t["streams"][1:].to_numpy(), 1)
    out["Rl"] = t["mean_length_km"][1:].to_numpy() / t["mean_length_km"][:-1].to_numpy()
    out["Ra"] = t["mean_area_skm"][1:].to_numpy() / t["mean_area_skm"][:-1].to_numpy()
    return out
