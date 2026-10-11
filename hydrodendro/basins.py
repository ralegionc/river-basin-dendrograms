"""Naming and resolving river basins inside a HydroRIVERS region.

A basin in HydroRIVERS is identified by ``MAIN_RIV``: every reach carries the
``HYRIV_ID`` of the most downstream reach of its network, so selecting all
reaches sharing a ``MAIN_RIV`` yields one complete, topologically closed basin.

Rather than hard-coding those ids (they are version specific), each preset
stores a seed point on the trunk river. At run time we read the reaches in a
small box around the seed and take the one draining the largest area; its
``MAIN_RIV`` is the basin. That survives small errors in the seed coordinate
and any renumbering in a future HydroRIVERS release.
"""
from __future__ import annotations

from dataclasses import dataclass

import pyogrio


@dataclass(frozen=True)
class BasinPreset:
    key: str
    label: str
    region: str
    seed: tuple[float, float]  # lon, lat on the trunk river
    note: str = ""


#: Seed points sit on the trunk well inside the basin, away from deltas and
#: distributaries where the network splits into many small separate outlets.
PRESETS: dict[str, BasinPreset] = {
    p.key: p
    for p in [
        # South America
        BasinPreset("amazon", "Amazon", "sa", (-55.51, -1.95), "seed at Obidos"),
        BasinPreset("parana", "Parana", "sa", (-58.83, -27.47), "seed at Corrientes"),
        BasinPreset("orinoco", "Orinoco", "sa", (-63.55, 8.13), "seed at Ciudad Bolivar"),
        BasinPreset("tocantins", "Tocantins", "sa", (-49.13, -5.35), "seed at Maraba"),
        BasinPreset("sao-francisco", "Sao Francisco", "sa", (-40.50, -9.39), "seed at Petrolina"),
        BasinPreset("magdalena", "Magdalena", "sa", (-74.75, 9.24), "seed at Magangue"),
        # Asia
        BasinPreset("ganges", "Ganges", "as", (85.13, 25.61),
                    "seed at Patna; HydroRIVERS joins the Ganges, Brahmaputra "
                    "and Meghna into one delta network"),
        BasinPreset("brahmaputra", "Brahmaputra", "as", (91.74, 26.18), "seed at Guwahati"),
        BasinPreset("mekong", "Mekong", "as", (105.80, 15.12), "seed at Pakse"),
        BasinPreset("yangtze", "Yangtze", "as", (114.30, 30.60), "seed at Wuhan"),
        BasinPreset("yellow-river", "Yellow River", "as", (111.20, 34.80), "seed at Sanmenxia"),
        BasinPreset("indus", "Indus", "as", (68.87, 27.70), "seed at Sukkur"),
        BasinPreset("irrawaddy", "Irrawaddy", "as", (95.22, 21.98), "seed at Mandalay"),
        # Africa
        BasinPreset("congo", "Congo", "af", (18.26, 0.05), "seed at Mbandaka"),
        BasinPreset("nile", "Nile", "af", (32.89, 24.09), "seed at Aswan"),
        BasinPreset("niger", "Niger", "af", (6.78, 6.14), "seed at Onitsha"),
        BasinPreset("zambezi", "Zambezi", "af", (33.58, -16.15), "seed at Tete"),
        # North America
        BasinPreset("mississippi", "Mississippi", "na", (-90.88, 32.31), "seed at Vicksburg"),
        BasinPreset("mackenzie", "Mackenzie", "ar", (-121.30, 61.86), "seed at Fort Simpson"),
        BasinPreset("columbia", "Columbia", "na", (-119.27, 45.94), "seed at Umatilla"),
        BasinPreset("yukon", "Yukon", "ar", (-149.10, 64.80), "seed near Rampart"),
        # Europe
        BasinPreset("danube", "Danube", "eu", (20.45, 44.82), "seed at Belgrade"),
        BasinPreset("volga", "Volga", "eu", (44.50, 48.70), "seed at Volgograd"),
        BasinPreset("rhine", "Rhine", "eu", (6.96, 50.94), "seed at Cologne"),
        # Siberia
        BasinPreset("ob", "Ob", "si", (73.37, 61.25), "seed at Surgut"),
        BasinPreset("yenisei", "Yenisei", "si", (92.87, 56.01), "seed at Krasnoyarsk"),
        BasinPreset("lena", "Lena", "si", (129.73, 62.03), "seed at Yakutsk"),
        # Australia
        BasinPreset("murray-darling", "Murray-Darling", "au", (142.16, -34.19), "seed at Mildura"),
    ]
}


class BasinNotFound(RuntimeError):
    pass


def resolve_seed(shp, lon: float, lat: float, pad: float = 0.25) -> dict:
    """Find the basin containing the trunk river nearest to ``(lon, lat)``.

    Reads only the reaches inside a small bounding box, then keeps the one with
    the largest upstream area. Widens the box until something is found.
    """
    for attempt in range(5):
        p = pad * (2**attempt)
        box = (lon - p, lat - p, lon + p, lat + p)
        df = pyogrio.read_dataframe(
            shp, bbox=box, read_geometry=False,
            columns=["HYRIV_ID", "MAIN_RIV", "UPLAND_SKM"],
        )
        if len(df):
            row = df.loc[df["UPLAND_SKM"].idxmax()]
            return {
                "main_riv": int(row["MAIN_RIV"]),
                "upland_skm": float(row["UPLAND_SKM"]),
                "search_pad": p,
            }
    raise BasinNotFound(
        f"no river reaches within {pad * 16:.1f} degrees of ({lon}, {lat}); "
        "check the coordinate order (lon, lat) and the region"
    )


def largest_basins(shp, limit: int = 20):
    """Rank every basin in a region by drainage area. Reads the whole attribute
    table, so it takes roughly a minute on the larger regions."""
    df = pyogrio.read_dataframe(
        shp, read_geometry=False, columns=["MAIN_RIV", "UPLAND_SKM", "ORD_STRA"]
    )
    out = df.groupby("MAIN_RIV").agg(
        upland_skm=("UPLAND_SKM", "max"),
        reaches=("UPLAND_SKM", "size"),
        max_strahler=("ORD_STRA", "max"),
    )
    return out.sort_values("upland_skm", ascending=False).head(limit).reset_index()


def lookup(key: str) -> BasinPreset:
    try:
        return PRESETS[key]
    except KeyError:
        raise BasinNotFound(
            f"unknown basin {key!r}. Known: {', '.join(sorted(PRESETS))}"
        ) from None
