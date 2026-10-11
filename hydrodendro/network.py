"""Turning a HydroRIVERS basin into tapered, coloured line segments."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyogrio
import shapely
from pyproj import CRS, Transformer

from . import elevation as elev_mod
from . import topology

# Attributes we need. MAIN_RIV must stay in this list: OGR applies column
# selection before the attribute filter, so dropping it makes the WHERE clause
# silently match nothing.
FIELDS = [
    "HYRIV_ID", "NEXT_DOWN", "MAIN_RIV", "LENGTH_KM", "DIST_DN_KM",
    "CATCH_SKM", "UPLAND_SKM", "ORD_STRA", "DIS_AV_CMS",
]


@dataclass
class Basin:
    gdf: object
    main_riv: int
    region: str

    @property
    def upland_skm(self) -> float:
        return float(self.gdf["UPLAND_SKM"].max())


def cache_path(region: str, main_riv: int, root: Path) -> Path:
    return Path(root) / "cache" / f"{region}_{main_riv}.parquet"


def load_basin(shp, region, main_riv, root, use_cache=True, verbose=True) -> Basin:
    """Read every reach sharing ``main_riv``, caching the result as GeoParquet.

    The attribute filter runs inside OGR, but a shapefile carries no attribute
    index, so the first read scans the whole region and takes about a minute.
    The cache makes every later render of the same basin near instant.
    """
    cache = cache_path(region, main_riv, root)
    if use_cache and cache.exists():
        import geopandas as gpd

        if verbose:
            print(f"  reading cached basin {cache.name}")
        return Basin(gpd.read_parquet(cache), main_riv, region)

    if verbose:
        print(f"  scanning {Path(shp).name} for MAIN_RIV={main_riv} (one off, ~1 min)")
    t0 = time.time()
    gdf = pyogrio.read_dataframe(shp, where=f"MAIN_RIV = {main_riv}", columns=FIELDS)
    if verbose:
        print(f"  {len(gdf):,} reaches in {time.time() - t0:.0f}s")
    if not len(gdf):
        raise SystemExit(f"no reaches with MAIN_RIV={main_riv} in {shp}")

    if use_cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_parquet(cache)
        if verbose:
            print(f"  cached to {cache}")
    return Basin(gdf, main_riv, region)


def load_region(shp, region, root, use_cache=True, verbose=True) -> Basin:
    """Read every reach in a whole continental region, not just one basin.

    Same shape as :func:`load_basin`, but with no attribute filter, so this is
    the full regional file: 1.6 million reaches for South America. Cached as
    GeoParquet under a reserved id, since MAIN_RIV 0 never occurs in the data.
    """
    cache = cache_path(region, 0, root)
    if use_cache and cache.exists():
        import geopandas as gpd

        if verbose:
            print(f"  reading cached region {cache.name}")
        return Basin(gpd.read_parquet(cache), 0, region)

    if verbose:
        print(f"  reading all of {Path(shp).name} (one off, a few minutes)")
    t0 = time.time()
    gdf = pyogrio.read_dataframe(shp, columns=FIELDS)
    if verbose:
        print(f"  {len(gdf):,} reaches in {time.time() - t0:.0f}s")

    if use_cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_parquet(cache)
        if verbose:
            print(f"  cached to {cache}")
    return Basin(gdf, 0, region)


def basin_crs(lon, lat, kind="laea") -> CRS:
    """An equal-area projection centred on the basin.

    Plotting lon/lat straight onto a canvas stretches every basin away from the
    equator. Lambert azimuthal equal-area centred on the basin keeps tributary
    geometry honest at any latitude.
    """
    lon0 = float((lon.min() + lon.max()) / 2)
    lat0 = float((lat.min() + lat.max()) / 2)
    if kind == "laea":
        proj = f"+proj=laea +lat_0={lat0} +lon_0={lon0} +datum=WGS84 +units=m +no_defs"
    elif kind == "aea":
        span = lat.max() - lat.min()
        lat1 = float(lat.min() + span / 6)
        lat2 = float(lat.max() - span / 6)
        proj = (f"+proj=aea +lat_1={lat1} +lat_2={lat2} +lat_0={lat0} "
                f"+lon_0={lon0} +datum=WGS84 +units=m +no_defs")
    elif kind == "none":
        return CRS.from_epsg(4326)
    else:
        raise SystemExit(f"unknown projection {kind!r}; use laea, aea or none")
    return CRS.from_proj4(proj)


def _reach_widths(upland, catch, lw_min, lw_max, exponent, area_floor=0.1,
                  area_ref=None):
    """Stroke width at the upstream and downstream end of every reach.

    UPLAND_SKM is the area draining through the reach's downstream end, so the
    upstream end carries that minus the reach's own catchment. Interpolating
    between the two thickens each reach along its length and keeps widths
    continuous through junctions, which is what produces the branching taper.

    Width follows hydraulic geometry rather than a plain log scale. Channel
    width grows roughly with the square root of discharge, and discharge grows
    roughly with drainage area, so width goes as a power of area. The true
    exponent near 0.5 gives a range too extreme to draw, so ``exponent`` is a
    tunable stand in: around 0.4 the trunk dominates and first order streams
    fall to faint hairlines, which is what makes the branching legible. A
    normalised log scale, by contrast, hands mid order tributaries most of the
    width budget and flattens the whole network to one thickness.
    """
    dn_area = np.maximum(upland, area_floor)
    up_area = np.maximum(upland - catch, area_floor)
    # area_ref lets several basins share one width scale, which is what makes
    # a comparison sheet honest: without it every basin renormalises to its
    # own outlet and the Rhine draws as thick as the Amazon.
    a_max = float(area_ref) if area_ref else float(dn_area.max())
    a_max = max(a_max, area_floor * 10.0)

    def to_width(area):
        return lw_min + (lw_max - lw_min) * np.power(area / a_max, exponent)

    return to_width(up_area), to_width(dn_area)


def _color_values(gdf, mode, elev_vertex, vert_reach):
    """Per-vertex scalar that drives colour."""
    if mode == "elevation":
        return elev_vertex
    if mode == "strahler":
        return gdf["ORD_STRA"].to_numpy(np.float32)[vert_reach]
    if mode == "discharge":
        q = np.maximum(gdf["DIS_AV_CMS"].to_numpy(np.float64), 1e-3)
        return np.log10(q).astype(np.float32)[vert_reach]
    if mode == "distance":
        return gdf["DIST_DN_KM"].to_numpy(np.float32)[vert_reach]
    raise SystemExit(
        f"unknown colour mode {mode!r}; use elevation, strahler, discharge or distance"
    )


@dataclass
class Segments:
    xy: np.ndarray        # (S, 2, 2) projected segment endpoints
    width: np.ndarray     # (S,) stroke width in reference pixels
    value: np.ndarray     # (S,) scalar driving colour
    bounds: tuple         # projected xmin, ymin, xmax, ymax
    elev_range: tuple     # metres, for the caption
    n_reaches: int
    group: np.ndarray = None   # (S,) tributary id, or None when unused
    groups: list = None        # per tributary summary, for the caption
    crs: object = None         # projection used, so callers can place labels
    reach_labels: np.ndarray = None  # (R,) group per reach, post filtering
    label_gdf: object = None         # the filtered reaches those refer to


def build_segments(basin, dem_path=None, color_mode="elevation", lw_min=0.25,
                   lw_max=7.0, width_exp=0.40, projection="laea", min_strahler=0,
                   simplify=0.0, n_tributaries=24, shade_by="elevation",
                   group_by="tributary", area_ref=None, verbose=True) -> Segments:
    """Explode a basin into per-segment coordinates, widths and colour values.

    Everything is vectorised over the whole basin: shapely returns one flat
    coordinate array, and reach-level values are broadcast onto vertices with
    np.repeat. A few million segments stay comfortably in memory.
    """
    gdf = basin.gdf

    # In tributary mode hue is categorical and the continuous channel becomes a
    # shading term, so the DEM is still needed unless shading is switched off.
    tributary = color_mode == "basin"
    value_mode = shade_by if tributary else color_mode

    if min_strahler > 0:
        gdf = gdf.loc[gdf["ORD_STRA"].to_numpy() >= min_strahler].reset_index(drop=True)
        if verbose:
            print(f"  Strahler >= {min_strahler}: {len(gdf):,} reaches kept")
        if not len(gdf):
            raise SystemExit("--min-strahler removed every reach")

    geom = gdf.geometry.values
    if simplify > 0:
        geom = shapely.simplify(geom, simplify, preserve_topology=False)

    counts = shapely.get_num_coordinates(geom).astype(np.int64)
    coords = shapely.get_coordinates(geom)
    n_vert = coords.shape[0]
    vert_reach = np.repeat(np.arange(len(counts), dtype=np.int64), counts)
    if verbose:
        print(f"  {len(counts):,} reaches, {n_vert:,} vertices")

    lon, lat = coords[:, 0], coords[:, 1]

    # Elevation is sampled in geographic coordinates, before reprojection.
    if dem_path is not None:
        raw = elev_mod.sample_points(dem_path, lon, lat)
        elev_vertex = elev_mod.fill_gaps(raw, vert_reach)
        elev_range = ((float(np.nanmin(raw)), float(np.nanmax(raw)))
                      if np.isfinite(raw).any() else (0.0, 0.0))
    else:
        if value_mode == "elevation":
            raise SystemExit(
                "elevation shading needs a DEM; use --color distance, or "
                "--shade-by distance with --color basin"
            )
        elev_vertex = np.zeros(n_vert, np.float32)
        elev_range = (0.0, 0.0)

    crs = basin_crs(lon, lat, projection)
    if crs.to_epsg() == 4326:
        x, y = lon, lat
    else:
        tf = Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
        x, y = tf.transform(lon, lat)
        x = np.asarray(x)
        y = np.asarray(y)

    w_up, w_dn = _reach_widths(
        gdf["UPLAND_SKM"].to_numpy(np.float64),
        gdf["CATCH_SKM"].to_numpy(np.float64),
        lw_min, lw_max, width_exp, area_ref=area_ref,
    )

    # Position of each vertex along its own reach, 0 upstream to 1 downstream.
    # HydroRIVERS digitises every reach in the direction of flow: the last
    # vertex of a reach coincides exactly with the first vertex of its
    # NEXT_DOWN neighbour, which is what makes this interpolation meaningful.
    starts = np.cumsum(counts) - counts
    pos = np.arange(n_vert, dtype=np.int64) - starts[vert_reach]
    denom = np.maximum(counts - 1, 1)[vert_reach]
    t = (pos / denom).astype(np.float32)
    w_vert = w_up[vert_reach] + t * (w_dn[vert_reach] - w_up[vert_reach])

    c_vert = _color_values(gdf, value_mode, elev_vertex, vert_reach)

    if tributary:
        if group_by == "mainriv":
            labels, group_info = topology.basin_labels(gdf, n_tributaries)
            if verbose:
                print(f"  {len(np.unique(labels)):,} separate basins, "
                      f"{len(group_info)} named in the caption")
        else:
            labels, group_info = topology.tributary_labels(gdf, n_tributaries)
            if verbose:
                print(f"  {len(group_info) - 1} tributary systems plus the mainstem")
        g_vert = labels[vert_reach]
    else:
        labels = None
        g_vert, group_info = None, None

    # Every vertex starts a segment except the last one of each reach.
    valid = np.ones(n_vert, dtype=bool)
    valid[np.cumsum(counts) - 1] = False
    i = np.nonzero(valid)[0]
    j = i + 1

    xy = np.empty((i.size, 2, 2), dtype=np.float32)
    xy[:, 0, 0] = x[i]
    xy[:, 0, 1] = y[i]
    xy[:, 1, 0] = x[j]
    xy[:, 1, 1] = y[j]

    width = (0.5 * (w_vert[i] + w_vert[j])).astype(np.float32)
    value = (0.5 * (c_vert[i] + c_vert[j])).astype(np.float32)
    # A group is categorical, so take the upstream end rather than averaging.
    group = g_vert[i] if g_vert is not None else None

    # Draw thin headwaters first so the trunk lands on top of them.
    order = np.argsort(width, kind="stable")
    xy, width, value = xy[order], width[order], value[order]
    if group is not None:
        group = group[order]

    bounds = (float(x.min()), float(y.min()), float(x.max()), float(y.max()))
    if verbose:
        print(f"  {i.size:,} segments, width {width.min():.3f} to {width.max():.2f}")
    return Segments(xy, width, value, bounds, elev_range, len(counts),
                    group=group, groups=group_info, crs=crs,
                    reach_labels=labels, label_gdf=gdf if tributary else None)
