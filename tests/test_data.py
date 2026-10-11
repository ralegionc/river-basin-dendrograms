"""Checks against the real HydroSHEDS files.

These assert the things the renderer takes on faith about the upstream data,
the assumptions that unit tests on synthetic fixtures cannot reach. They skip
cleanly when the data has not been downloaded, so the suite still runs on a
fresh checkout:

    python render.py magdalena     # fetches South America, then
    python -m pytest tests -q
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import shapely

from hydrodendro import basins, regions
from hydrodendro.elevation import sample_points

ROOT = Path(__file__).resolve().parent.parent / "data"
REGION = "sa"

pytestmark = pytest.mark.skipif(
    not regions.rivers_path(REGION, ROOT).exists(),
    reason="HydroRIVERS South America not downloaded; run a render first",
)


@pytest.fixture(scope="module")
def shp():
    return regions.rivers_path(REGION, ROOT)


@pytest.fixture(scope="module")
def amazon():
    """The cached Amazon extract, if a render has already produced it."""
    import geopandas as gpd

    hits = sorted((ROOT / "cache").glob("sa_*.parquet"))
    hits = [h for h in hits if h.stem != "sa_0"]
    if not hits:
        pytest.skip("no cached South American basin; run a render first")
    biggest = max(hits, key=lambda p: p.stat().st_size)
    return gpd.read_parquet(biggest)


class TestFlowDirection:
    """The taper interpolates from a reach's upstream end to its downstream
    end, so vertex order has to be flow order. This is the assumption the
    whole picture rests on."""

    def test_last_vertex_joins_the_downstream_neighbour(self, amazon):
        geom = amazon.geometry.values
        counts = shapely.get_num_coordinates(geom)
        coords = shapely.get_coordinates(geom)
        ends = np.cumsum(counts) - 1
        first, last = coords[ends - counts + 1], coords[ends]

        pos = pd.Series(np.arange(len(amazon)), index=amazon.HYRIV_ID.values)
        nd = pos.reindex(amazon.NEXT_DOWN.values).to_numpy(dtype=float)
        linked = np.isfinite(nd)
        child = np.nonzero(linked)[0]
        parent = nd[linked].astype(int)

        # A reach's last vertex must coincide with its neighbour's first.
        gap = np.hypot(*(last[child] - first[parent]).T)
        assert np.median(gap) == 0.0
        assert (gap < 1e-9).mean() > 0.99

    def test_reversing_the_test_fails(self, amazon):
        """Guards the test itself: the first vertex must NOT be the join."""
        geom = amazon.geometry.values
        counts = shapely.get_num_coordinates(geom)
        coords = shapely.get_coordinates(geom)
        ends = np.cumsum(counts) - 1
        first = coords[ends - counts + 1]

        pos = pd.Series(np.arange(len(amazon)), index=amazon.HYRIV_ID.values)
        nd = pos.reindex(amazon.NEXT_DOWN.values).to_numpy(dtype=float)
        linked = np.isfinite(nd)
        gap = np.hypot(*(first[np.nonzero(linked)[0]]
                         - first[nd[linked].astype(int)]).T)
        assert np.median(gap) > 0.0

    def test_flow_runs_downhill_on_average(self, amazon):
        """A weaker but independent check, against the DEM rather than topology.
        Only 'on average' because a 30 arc-second DEM cannot resolve the drop
        along a single lowland reach."""
        dem = regions.dem_path(REGION, ROOT)
        if not dem.exists():
            pytest.skip("DEM not downloaded")

        geom = amazon.geometry.values[::37]        # thin it out, this is enough
        counts = shapely.get_num_coordinates(geom)
        coords = shapely.get_coordinates(geom)
        ends = np.cumsum(counts) - 1
        first, last = coords[ends - counts + 1], coords[ends]

        e_first = sample_points(dem, first[:, 0], first[:, 1])
        e_last = sample_points(dem, last[:, 0], last[:, 1])
        ok = np.isfinite(e_first) & np.isfinite(e_last)
        drops = (e_last[ok] < e_first[ok]).sum()
        climbs = (e_last[ok] > e_first[ok]).sum()
        assert drops > 3 * climbs


class TestSchema:
    def test_every_field_the_renderer_reads_exists(self, shp):
        import pyogrio

        from hydrodendro.network import FIELDS

        present = set(pyogrio.read_info(shp)["fields"])
        assert set(FIELDS) <= present

    def test_where_clause_needs_its_own_column(self, shp):
        """OGR applies column selection before the attribute filter, so asking
        for a filter on a column you did not request silently returns nothing.
        This bit once, and it fails as an empty render rather than an error."""
        import pyogrio

        with_it = pyogrio.read_dataframe(
            shp, where="MAIN_RIV = 60443230", read_geometry=False,
            columns=["HYRIV_ID", "MAIN_RIV"], max_features=5,
        )
        without = pyogrio.read_dataframe(
            shp, where="MAIN_RIV = 60443230", read_geometry=False,
            columns=["HYRIV_ID"], max_features=5,
        )
        assert len(with_it) > 0
        assert len(without) == 0        # the trap, documented

    def test_dist_dn_decreases_downstream(self, amazon):
        """This ordering is what lets tributary labelling run in a single pass
        instead of recursing."""
        pos = pd.Series(np.arange(len(amazon)), index=amazon.HYRIV_ID.values)
        nd = pos.reindex(amazon.NEXT_DOWN.values).to_numpy(dtype=float)
        linked = np.isfinite(nd)
        child = amazon.DIST_DN_KM.values[np.nonzero(linked)[0]]
        parent = amazon.DIST_DN_KM.values[nd[linked].astype(int)]
        assert np.all(child > parent)

    def test_upland_area_accumulates_downstream(self, amazon):
        pos = pd.Series(np.arange(len(amazon)), index=amazon.HYRIV_ID.values)
        nd = pos.reindex(amazon.NEXT_DOWN.values).to_numpy(dtype=float)
        linked = np.isfinite(nd)
        child = amazon.UPLAND_SKM.values[np.nonzero(linked)[0]]
        parent = amazon.UPLAND_SKM.values[nd[linked].astype(int)]
        assert np.all(parent >= child)


class TestSeedResolution:
    def test_obidos_resolves_to_the_amazon(self, shp):
        hit = basins.resolve_seed(shp, *basins.lookup("amazon").seed)
        assert hit["upland_skm"] > 4_000_000

    def test_presets_agree_with_published_areas(self, shp):
        """Each seed must land on a basin of roughly the published size. Catches
        a seed that has drifted onto a neighbouring creek."""
        expected = {           # km2, from published basin areas
            "amazon": 5_900_000,
            "parana": 2_600_000,
            "orinoco": 950_000,
            "tocantins": 770_000,
            "magdalena": 260_000,
        }
        for key, area in expected.items():
            preset = basins.lookup(key)
            if preset.region != REGION:
                continue
            hit = basins.resolve_seed(shp, *preset.seed)
            ratio = hit["upland_skm"] / area
            assert 0.55 < ratio < 1.45, (
                f"{key}: seed found {hit['upland_skm']:,.0f} km2, expected ~{area:,}"
            )

    def test_a_seed_in_open_ocean_is_rejected(self, shp):
        with pytest.raises(basins.BasinNotFound):
            basins.resolve_seed(shp, -35.0, -25.0, pad=0.05)


class TestElevationSampling:
    def test_known_elevations(self):
        dem = regions.dem_path(REGION, ROOT)
        if not dem.exists():
            pytest.skip("DEM not downloaded")
        # Obidos on the lower Amazon is near sea level; the Altiplano is high.
        low = sample_points(dem, np.array([-55.51]), np.array([-1.95]))[0]
        high = sample_points(dem, np.array([-68.5]), np.array([-16.5]))[0]
        assert 0 <= low < 100
        assert high > 3000

    def test_ocean_returns_nan_not_a_sentinel(self):
        dem = regions.dem_path(REGION, ROOT)
        if not dem.exists():
            pytest.skip("DEM not downloaded")
        v = sample_points(dem, np.array([-40.0]), np.array([-20.0]))
        assert not np.isfinite(v[0]) or v[0] > -1000


class TestRiverNames:
    """Matching against Natural Earth. Skips unless the 2 MB names file is on
    disk, which a --label-rivers render fetches."""

    @pytest.fixture(scope="class")
    def ne(self):
        shp = ROOT / "raw" / "ne_10m_rivers_lake_centerlines.shp"
        if not shp.exists():
            pytest.skip("Natural Earth names not downloaded")
        return shp

    def test_accents_survive_the_encoding(self, ne):
        """The shapefile is latin-1 with no .cpg to declare it, so without an
        explicit encoding every accented name comes back as mojibake."""
        from hydrodendro.names import load_named_rivers

        df = load_named_rivers(ne, bbox=(-75, -12, -50, 2))
        labels = set(df["label"])
        assert "Tapaj\u00f3s" in labels          # Tapajos with an acute o
        assert not any("\ufffd" in s for s in labels)

    def test_amazon_tributaries_get_the_right_names(self, ne, amazon):
        from hydrodendro.names import match_names
        from hydrodendro.topology import tributary_labels

        lab, info = tributary_labels(amazon, 24)
        got = match_names(amazon, lab, info, ne, verbose=False)
        by_name = {v["name"]: k for k, v in got.items()}

        # The mainstem must be the mainstem, and the largest tributary must be
        # the Madeira, which is the check that the whole match is anchored.
        assert got[0]["name"] == "Amazonas"
        assert got[1]["name"] == "Madeira"
        for expected in ("Rio Negro", "Xingu", "Tapaj\u00f3s", "Ucayali"):
            assert expected in by_name

    def test_each_name_is_used_at_most_once(self, ne, amazon):
        from hydrodendro.names import match_names
        from hydrodendro.topology import tributary_labels

        lab, info = tributary_labels(amazon, 24)
        got = match_names(amazon, lab, info, ne, verbose=False)
        used = [v["name"] for v in got.values()]
        assert len(used) == len(set(used))

    def test_a_weak_match_is_left_unnamed(self, ne, amazon):
        """Raising the vote threshold past any real agreement must drop names
        rather than invent them."""
        from hydrodendro.names import match_names
        from hydrodendro.topology import tributary_labels

        lab, info = tributary_labels(amazon, 24)
        strict = match_names(amazon, lab, info, ne, min_votes=1.01, verbose=False)
        assert strict == {}

    def test_anchor_points_land_inside_the_basin(self, ne, amazon):
        from hydrodendro.names import match_names
        from hydrodendro.topology import tributary_labels

        import shapely

        minx, miny, maxx, maxy = shapely.total_bounds(amazon.geometry.values)
        lab, info = tributary_labels(amazon, 24)
        got = match_names(amazon, lab, info, ne, verbose=False)
        for v in got.values():
            assert minx <= v["lon"] <= maxx
            assert miny <= v["lat"] <= maxy
