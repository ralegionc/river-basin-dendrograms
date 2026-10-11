"""Tests for the parts that do not need HydroSHEDS on disk.

Run with:  python -m pytest tests -q
"""
from __future__ import annotations

import pathlib
import tempfile

import numpy as np
import pytest
import shapely

from hydrodendro import palettes
from hydrodendro.elevation import fill_gaps
from hydrodendro.network import Basin, _reach_widths, basin_crs, build_segments
from hydrodendro.render import _stretch


def make_basin(n_reaches=6):
    """A toy chain of reaches, each three vertices, area growing downstream."""
    import geopandas as gpd

    geoms, upland, catch = [], [], []
    for i in range(n_reaches):
        x0 = i * 0.1
        geoms.append(shapely.LineString([(x0, 0.0), (x0 + 0.05, 0.01), (x0 + 0.1, 0.0)]))
        upland.append(10.0 * (i + 1) ** 3)
        catch.append(5.0)
    gdf = gpd.GeoDataFrame(
        {
            "HYRIV_ID": np.arange(1, n_reaches + 1),
            "NEXT_DOWN": list(range(2, n_reaches + 1)) + [0],
            "MAIN_RIV": np.full(n_reaches, n_reaches),
            "LENGTH_KM": np.full(n_reaches, 10.0),
            "DIST_DN_KM": np.arange(n_reaches, dtype=float)[::-1] * 10,
            "CATCH_SKM": catch,
            "UPLAND_SKM": upland,
            "ORD_STRA": np.arange(1, n_reaches + 1),
            "DIS_AV_CMS": np.array(upland) / 100.0,
        },
        geometry=geoms,
        crs="EPSG:4326",
    )
    return Basin(gdf, n_reaches, "xx")


class TestWidths:
    def test_monotonic_in_area(self):
        upland = np.array([1.0, 100.0, 1e4, 1e6])
        catch = np.full(4, 0.5)
        _, dn = _reach_widths(upland, catch, 0.25, 7.0, 0.40)
        assert np.all(np.diff(dn) > 0)

    def test_outlet_hits_lw_max_and_floor_holds(self):
        upland = np.array([0.01, 5_912_761.0])
        catch = np.array([0.01, 9.23])
        up, dn = _reach_widths(upland, catch, 0.25, 7.0, 0.40)
        assert dn[-1] == pytest.approx(7.0)
        assert up.min() >= 0.25 - 1e-9

    def test_upstream_end_is_thinner_than_downstream(self):
        """This is what produces the taper, so it must hold for every reach."""
        upland = np.array([50.0, 500.0, 5000.0])
        catch = np.array([25.0, 250.0, 2500.0])
        up, dn = _reach_widths(upland, catch, 0.25, 7.0, 0.40)
        assert np.all(up < dn)

    def test_exponent_controls_trunk_dominance(self):
        """A higher exponent must thin mid order rivers relative to the trunk."""
        upland = np.array([1e4, 1e6])
        catch = np.full(2, 1.0)
        _, low = _reach_widths(upland, catch, 0.25, 7.0, 0.20)
        _, high = _reach_widths(upland, catch, 0.25, 7.0, 0.60)
        assert high[0] / high[1] < low[0] / low[1]


class TestStretch:
    def test_linear_is_a_percentile_window(self):
        v = np.linspace(0.0, 100.0, 1001)
        t, lo, hi = _stretch(v, 0, 100, "linear")
        assert t.min() == pytest.approx(0.0)
        assert t.max() == pytest.approx(1.0)
        assert (lo, hi) == pytest.approx((0.0, 100.0))

    def test_rank_recovers_a_buried_lowland_mode(self):
        """The Amazon case: most reaches in a narrow lowland band, plus an
        Andean tail fat enough to own the top of the range. Under a linear
        ramp the lowland collapses into a single colour; rank has to spread it
        back out, because that lowland is most of the picture."""
        rng = np.random.default_rng(0)
        lowland = rng.normal(120.0, 40.0, 85_000)
        andes = rng.normal(3800.0, 500.0, 15_000)
        v = np.concatenate([lowland, andes])

        lin, _, _ = _stretch(v, 2, 98, "linear")
        rnk, _, _ = _stretch(v, 2, 98, "rank")

        # spread of the lowland reaches alone, which is what the eye reads
        lin_lowland = lin[: lowland.size]
        rnk_lowland = rnk[: lowland.size]
        assert lin_lowland.std() < 0.02      # one flat colour
        assert rnk_lowland.std() > 0.20      # a real gradient
        assert rnk_lowland.std() > 10 * lin_lowland.std()

    def test_rank_is_monotonic(self):
        rng = np.random.default_rng(1)
        v = rng.gamma(2.0, 50.0, 20_000)
        t, _, _ = _stretch(v, 0, 100, "rank")
        order = np.argsort(v)
        assert np.all(np.diff(t[order]) >= -1e-9)

    def test_constant_input_does_not_divide_by_zero(self):
        t, lo, hi = _stretch(np.full(100, 7.0), 2, 98, "rank")
        assert np.all(np.isfinite(t))
        assert hi > lo

    def test_unknown_mode_rejected(self):
        with pytest.raises(SystemExit):
            _stretch(np.arange(10.0), 2, 98, "sigmoid")


class TestFillGaps:
    def test_nan_inherits_reach_mean(self):
        v = np.array([10.0, np.nan, 30.0, np.nan], dtype=np.float32)
        out = fill_gaps(v, np.array([0, 0, 1, 1]))
        assert out.tolist() == [10.0, 10.0, 30.0, 30.0]

    def test_reach_with_no_data_falls_back_globally(self):
        v = np.array([10.0, 20.0, np.nan, np.nan], dtype=np.float32)
        out = fill_gaps(v, np.array([0, 0, 1, 1]))
        assert np.all(np.isfinite(out))
        assert out[2] == pytest.approx(15.0)

    def test_all_finite_is_untouched(self):
        v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert fill_gaps(v, np.array([0, 0, 1])).tolist() == [1.0, 2.0, 3.0]


class TestProjection:
    def test_laea_is_centred_on_the_basin(self):
        lon = np.array([-70.0, -50.0])
        lat = np.array([-10.0, 5.0])
        crs = basin_crs(lon, lat, "laea")
        text = crs.to_proj4()
        assert "laea" in text and "lon_0=-60" in text

    def test_none_passes_through_as_wgs84(self):
        crs = basin_crs(np.array([0.0, 1.0]), np.array([0.0, 1.0]), "none")
        assert crs.to_epsg() == 4326

    def test_unknown_projection_rejected(self):
        with pytest.raises(SystemExit):
            basin_crs(np.array([0.0, 1.0]), np.array([0.0, 1.0]), "mercator")


class TestBuildSegments:
    def test_segment_count_excludes_reach_boundaries(self):
        """Six reaches of three vertices give two segments each, never five."""
        segs = build_segments(make_basin(6), color_mode="distance", verbose=False)
        assert segs.xy.shape == (12, 2, 2)
        assert segs.n_reaches == 6

    def test_segments_never_bridge_two_reaches(self):
        basin = make_basin(4)
        segs = build_segments(basin, color_mode="distance", projection="none",
                              verbose=False)
        # every segment must be short; a bridge would span the 0.1 degree gap
        lengths = np.hypot(*(segs.xy[:, 1] - segs.xy[:, 0]).T)
        assert lengths.max() < 0.08

    def test_widths_sorted_so_the_trunk_draws_last(self):
        segs = build_segments(make_basin(6), color_mode="distance", verbose=False)
        assert np.all(np.diff(segs.width) >= -1e-6)

    def test_width_range_respects_the_bounds(self):
        segs = build_segments(make_basin(6), color_mode="distance",
                              lw_min=0.25, lw_max=7.0, verbose=False)
        assert segs.width.min() >= 0.25 - 1e-6
        assert segs.width.max() <= 7.0 + 1e-6

    def test_elevation_mode_without_a_dem_is_refused(self):
        with pytest.raises(SystemExit):
            build_segments(make_basin(3), dem_path=None, color_mode="elevation",
                           verbose=False)

    def test_min_strahler_filters_reaches(self):
        segs = build_segments(make_basin(6), color_mode="distance",
                              min_strahler=4, verbose=False)
        assert segs.n_reaches == 3

    def test_min_strahler_removing_everything_is_an_error(self):
        with pytest.raises(SystemExit):
            build_segments(make_basin(6), color_mode="distance",
                           min_strahler=99, verbose=False)

    def test_unknown_colour_mode_rejected(self):
        with pytest.raises(SystemExit):
            build_segments(make_basin(3), color_mode="rainfall", verbose=False)


class TestPalettes:
    def test_every_ramp_runs_dark_to_light(self):
        for name in palettes.available():
            cmap = palettes.get_cmap(name)
            lo = sum(cmap(0.0)[:3])
            hi = sum(cmap(1.0)[:3])
            assert hi > lo, name

    def test_no_ramp_bottoms_out_near_black(self):
        """The trunk river takes the dark end, so it has to stay visible."""
        for name in palettes.available():
            r, g, b = palettes.get_cmap(name)(0.0)[:3]
            assert max(r, g, b) > 0.30, name

    def test_matplotlib_names_still_work(self):
        assert palettes.get_cmap("magma") is not None

    def test_unknown_palette_rejected(self):
        with pytest.raises(SystemExit):
            palettes.get_cmap("not-a-real-ramp")


# ---------------------------------------------------------------------------
# Tributary colouring and the finishing passes
# ---------------------------------------------------------------------------

from hydrodendro.render import _bloom, _tributary_colors, _vignette  # noqa: E402
from hydrodendro.topology import (  # noqa: E402
    downstream_index, mainstem_mask, tributary_labels,
)


def forked_basin():
    """A trunk of four reaches with two tributaries hanging off it.

    ids 1..4 are the trunk (1 is the outlet), 5..6 a big tributary joining at
    reach 2, and 7 a small one joining at reach 3.
    """
    import geopandas as gpd

    rows = [
        # id, next_down, upland, dist_dn
        (1, 0, 1000.0, 0.0),
        (2, 1, 900.0, 10.0),
        (3, 2, 500.0, 20.0),
        (4, 3, 300.0, 30.0),
        (5, 2, 380.0, 20.5),
        (6, 5, 200.0, 30.5),
        (7, 3, 90.0, 25.0),
    ]
    geoms = [shapely.LineString([(i, 0.0), (i + 0.5, 0.1), (i + 1.0, 0.0)])
             for i in range(len(rows))]
    return gpd.GeoDataFrame(
        {
            "HYRIV_ID": [r[0] for r in rows],
            "NEXT_DOWN": [r[1] for r in rows],
            "MAIN_RIV": [1] * len(rows),
            "LENGTH_KM": [10.0] * len(rows),
            "DIST_DN_KM": [r[3] for r in rows],
            "CATCH_SKM": [5.0] * len(rows),
            "UPLAND_SKM": [r[2] for r in rows],
            "ORD_STRA": [3, 3, 2, 1, 2, 1, 1],
            "DIS_AV_CMS": [r[2] / 100 for r in rows],
        },
        geometry=geoms,
        crs="EPSG:4326",
    )


class TestTopology:
    def test_downstream_index_marks_the_outlet(self):
        g = forked_basin()
        nd = downstream_index(g.HYRIV_ID.to_numpy(), g.NEXT_DOWN.to_numpy())
        assert nd[0] == -1           # reach 1 is the outlet
        assert nd[1] == 0            # reach 2 drains into reach 1
        assert nd[4] == 1            # reach 5 drains into reach 2

    def test_mainstem_follows_drainage_area_not_length(self):
        """At the fork, reach 3 (500) beats reach 5 (380), so the trunk takes
        the larger feeder even though both are the same length."""
        g = forked_basin()
        nd = downstream_index(g.HYRIV_ID.to_numpy(), g.NEXT_DOWN.to_numpy())
        trunk = mainstem_mask(g.UPLAND_SKM.to_numpy(), nd)
        assert trunk.tolist() == [True, True, True, True, False, False, False]

    def test_labels_partition_the_basin(self):
        g = forked_basin()
        lab, info = tributary_labels(g, n_groups=2)
        assert (lab >= 0).all()
        assert len(lab) == len(g)
        assert sum(d["reaches"] for d in info) == len(g)

    def test_a_tributary_takes_its_whole_subtree(self):
        """Reach 6 is upstream of reach 5, so it has to inherit 5's label."""
        g = forked_basin()
        lab, _ = tributary_labels(g, n_groups=2)
        assert lab[5] == lab[4] != 0

    def test_small_feeders_fall_back_to_the_mainstem(self):
        """With only one tributary slot, reach 7 has nowhere to go but group 0."""
        g = forked_basin()
        lab, info = tributary_labels(g, n_groups=1)
        assert lab[6] == 0
        assert len(info) == 2                    # mainstem plus one tributary

    def test_group_counts_are_post_propagation(self):
        """Group 0 must report the trunk plus whatever fell back into it."""
        g = forked_basin()
        lab, info = tributary_labels(g, n_groups=1)
        assert info[0]["reaches"] == int((lab == 0).sum())
        assert info[0]["reaches"] > info[0]["trunk_reaches"]

    def test_tributaries_ranked_by_area(self):
        g = forked_basin()
        _, info = tributary_labels(g, n_groups=2)
        areas = [d["upland_skm"] for d in info[1:]]
        assert areas == sorted(areas, reverse=True)


class TestTributaryColors:
    def test_each_group_gets_a_distinct_hue(self):
        from matplotlib.colors import rgb_to_hsv

        groups = np.arange(8)
        c = _tributary_colors(groups, np.full(8, 0.5), palettes.hue_set("prism"))
        hues = rgb_to_hsv(c[:, :3])[:, 0]
        assert len(np.unique(hues.round(3))) == 8

    def test_shade_zero_is_flat_within_a_group(self):
        g = np.zeros(5, dtype=np.int64)
        c = _tributary_colors(g, np.linspace(0, 1, 5), palettes.hue_set("prism"),
                              shade=0.0)
        assert np.allclose(c[0, :3], c[-1, :3], atol=1e-6)

    def test_shade_one_spans_the_full_value_range(self):
        g = np.zeros(5, dtype=np.int64)
        c = _tributary_colors(g, np.linspace(0, 1, 5), palettes.hue_set("prism"),
                              shade=1.0)
        assert c[-1, :3].max() - c[0, :3].max() > 0.4

    def test_hue_survives_shading(self):
        """Relief must not wash a tributary off its own hue."""
        from matplotlib.colors import rgb_to_hsv

        g = np.full(5, 3, dtype=np.int64)
        c = _tributary_colors(g, np.linspace(0, 1, 5), palettes.hue_set("prism"),
                              shade=0.55)
        hues = rgb_to_hsv(c[:, :3])[:, 0]
        assert np.ptp(hues) < 0.02

    def test_output_is_opaque_rgba(self):
        c = _tributary_colors(np.arange(4), np.full(4, 0.5),
                              palettes.hue_set("reef"))
        assert c.shape == (4, 4)
        assert np.all(c[:, 3] == 1.0)

    def test_unknown_hue_set_rejected(self):
        with pytest.raises(SystemExit):
            palettes.hue_set("neon-disco")


class TestFinishingPasses:
    def test_vignette_darkens_corners_not_the_centre(self):
        img = np.ones((64, 64, 3), dtype=np.float32)
        out = _vignette(img, 0.4)
        assert out[32, 32].mean() == pytest.approx(1.0, abs=1e-3)
        assert out[0, 0].mean() < 0.75

    def test_vignette_off_is_a_no_op(self):
        img = np.full((8, 8, 3), 0.5, dtype=np.float32)
        assert np.array_equal(_vignette(img, 0.0), img)

    def test_bloom_only_brightens(self):
        """Screen blending can never darken a pixel."""
        rng = np.random.default_rng(3)
        img = rng.random((48, 48, 3)).astype(np.float32) * 0.6
        out = _bloom(img, 0.6, 2.0, 0.05)
        assert np.all(out >= img - 1e-5)
        assert np.all(out <= 1.0 + 1e-5)

    def test_bloom_spreads_light_beyond_a_single_pixel(self):
        img = np.zeros((64, 64, 3), dtype=np.float32)
        img[32, 32] = 1.0
        out = _bloom(img, 1.0, 3.0, 0.05)
        assert out[32, 38].max() > 0.0      # light reached six pixels away


class TestTributaryIntegration:
    def test_build_segments_emits_groups(self):
        segs = build_segments(make_basin(6), color_mode="basin",
                              shade_by="distance", n_tributaries=3, verbose=False)
        assert segs.group is not None
        assert len(segs.group) == len(segs.width)
        assert segs.groups[0]["is_mainstem"]

    def test_non_basin_modes_carry_no_groups(self):
        segs = build_segments(make_basin(4), color_mode="distance", verbose=False)
        assert segs.group is None
        assert segs.groups is None

    def test_basin_mode_still_needs_a_dem_for_elevation_shading(self):
        with pytest.raises(SystemExit):
            build_segments(make_basin(4), dem_path=None, color_mode="basin",
                           shade_by="elevation", verbose=False)


# ---------------------------------------------------------------------------
# Region-wide grouping and print sizing
# ---------------------------------------------------------------------------

from hydrodendro import paper  # noqa: E402
from hydrodendro.topology import basin_labels  # noqa: E402


def two_basin_region():
    """Two independent basins, so MAIN_RIV grouping has something to separate."""
    import geopandas as gpd

    rows = [                       # id, next_down, main_riv, upland
        (1, 0, 1, 900.0), (2, 1, 1, 400.0), (3, 2, 1, 100.0),
        (4, 0, 4, 50.0), (5, 4, 4, 20.0),
    ]
    geoms = [shapely.LineString([(i, 0.0), (i + 1.0, 0.1)]) for i in range(len(rows))]
    return gpd.GeoDataFrame(
        {
            "HYRIV_ID": [r[0] for r in rows],
            "NEXT_DOWN": [r[1] for r in rows],
            "MAIN_RIV": [r[2] for r in rows],
            "LENGTH_KM": [10.0] * len(rows),
            "DIST_DN_KM": [0.0, 10.0, 20.0, 0.0, 10.0],
            "CATCH_SKM": [5.0] * len(rows),
            "UPLAND_SKM": [r[3] for r in rows],
            "ORD_STRA": [2, 2, 1, 1, 1],
            "DIS_AV_CMS": [r[3] / 100 for r in rows],
        },
        geometry=geoms,
        crs="EPSG:4326",
    )


class TestBasinLabels:
    def test_separate_basins_get_separate_labels(self):
        lab, _ = basin_labels(two_basin_region(), n_groups=10)
        assert lab[0] == lab[1] == lab[2]
        assert lab[3] == lab[4]
        assert lab[0] != lab[3]

    def test_ranked_by_area_largest_first(self):
        lab, info = basin_labels(two_basin_region(), n_groups=10)
        assert lab[0] == 0                       # the 900 km2 basin leads
        assert info[0]["upland_skm"] == 900.0
        assert info[0]["main_riv"] == 1

    def test_info_is_capped_but_labels_are_not(self):
        """Only the largest few are named; every reach still gets a label."""
        lab, info = basin_labels(two_basin_region(), n_groups=1)
        assert len(info) == 1
        assert len(np.unique(lab)) == 2

    def test_reach_counts_are_right(self):
        _, info = basin_labels(two_basin_region(), n_groups=10)
        assert info[0]["reaches"] == 3
        assert info[1]["reaches"] == 2


class TestPaper:
    def test_a4_at_300dpi_is_the_right_pixel_count(self):
        w, h, _ = paper.canvas_px("a4", 300, data_aspect=297 / 210)
        assert (w, h) == (2480, 3508)

    def test_orientation_follows_the_basin(self):
        tall = paper.canvas_px("a3", 150, data_aspect=2.0)
        wide = paper.canvas_px("a3", 150, data_aspect=0.5)
        assert tall[1] > tall[0]        # portrait
        assert wide[0] > wide[1]        # landscape

    def test_dpi_scales_linearly(self):
        low = paper.canvas_px("a4", 150, 1.4)
        high = paper.canvas_px("a4", 300, 1.4)
        assert high[0] == pytest.approx(low[0] * 2, abs=1)

    def test_unknown_size_rejected(self):
        with pytest.raises(SystemExit):
            paper.canvas_px("a9", 300, 1.0)

    def test_bad_dpi_rejected(self):
        with pytest.raises(SystemExit):
            paper.canvas_px("a4", 0, 1.0)


class TestCanvasFitting:
    """Letterboxing must never distort the basin or clip it."""

    def _extent(self, canvas):
        from hydrodendro.render import render

        segs = build_segments(make_basin(5), color_mode="distance",
                              projection="none", verbose=False)
        captured = {}
        import hydrodendro.render as rmod

        real = rmod.plt.figure

        def spy(*a, **k):
            fig = real(*a, **k)
            captured["fig"] = fig
            return fig

        rmod.plt.figure = spy
        try:
            out = pathlib.Path(tempfile.mkdtemp()) / "x.png"
            render(segs, out, width_px=400, canvas=canvas, bloom=0,
                   vignette=0, verbose=False)
        finally:
            rmod.plt.figure = real
        ax = captured["fig"].axes[0]
        return ax.get_xlim(), ax.get_ylim(), segs.bounds

    def test_fitting_preserves_aspect_and_contains_the_data(self):
        (x0, x1), (y0, y1), (bx0, by0, bx1, by1) = self._extent((600, 300))
        assert (x1 - x0) / (y1 - y0) == pytest.approx(2.0, rel=1e-3)
        assert x0 <= bx0 and x1 >= bx1
        assert y0 <= by0 and y1 >= by1

    def test_portrait_canvas_also_contains_the_data(self):
        (x0, x1), (y0, y1), (bx0, by0, bx1, by1) = self._extent((300, 900))
        assert (y1 - y0) / (x1 - x0) == pytest.approx(3.0, rel=1e-3)
        assert x0 <= bx0 and x1 >= bx1
        assert y0 <= by0 and y1 >= by1


# ---------------------------------------------------------------------------
# Horton's laws and river naming
# ---------------------------------------------------------------------------

from hydrodendro.horton import _ratio_fit, horton_ratios, stepwise, stream_table  # noqa: E402
from hydrodendro.names import group_trunk  # noqa: E402


def binary_tree_basin():
    """A perfect binary network: 4 first order streams, 2 second, 1 third.

    Every ratio is exactly 2, so any deviation is the code's fault rather than
    the data's.
    """
    import geopandas as gpd

    rows = [                 # id, next_down, order, dist_dn, upland
        (1, 5, 1, 20.0, 10.0), (2, 5, 1, 20.0, 10.0),
        (3, 6, 1, 20.0, 10.0), (4, 6, 1, 20.0, 10.0),
        (5, 7, 2, 10.0, 20.0), (6, 7, 2, 10.0, 20.0),
        (7, 0, 3, 0.0, 40.0),
    ]
    geoms = [shapely.LineString([(i, 0.0), (i + 1.0, 0.0)]) for i in range(len(rows))]
    return gpd.GeoDataFrame(
        {
            "HYRIV_ID": [r[0] for r in rows],
            "NEXT_DOWN": [r[1] for r in rows],
            "MAIN_RIV": [7] * len(rows),
            "LENGTH_KM": [10.0] * len(rows),
            "DIST_DN_KM": [r[3] for r in rows],
            "CATCH_SKM": [5.0] * len(rows),
            "UPLAND_SKM": [r[4] for r in rows],
            "ORD_STRA": [r[2] for r in rows],
            "DIS_AV_CMS": [1.0] * len(rows),
        },
        geometry=geoms,
        crs="EPSG:4326",
    )


def chain_basin():
    """Five reaches of the same order in a row: one stream, not five."""
    import geopandas as gpd

    n = 5
    geoms = [shapely.LineString([(i, 0.0), (i + 1.0, 0.0)]) for i in range(n)]
    return gpd.GeoDataFrame(
        {
            "HYRIV_ID": np.arange(1, n + 1),
            "NEXT_DOWN": list(range(2, n + 1)) + [0],
            "MAIN_RIV": np.full(n, n),
            "LENGTH_KM": np.full(n, 4.0),
            "DIST_DN_KM": np.arange(n, dtype=float)[::-1] * 4,
            "CATCH_SKM": np.full(n, 1.0),
            "UPLAND_SKM": np.arange(1, n + 1) * 3.0,
            "ORD_STRA": np.ones(n, dtype=int),
            "DIS_AV_CMS": np.ones(n),
        },
        geometry=geoms,
        crs="EPSG:4326",
    )


class TestStreamCounting:
    def test_consecutive_reaches_are_one_stream(self):
        """The trap this module exists to avoid: counting rows, not streams."""
        t = stream_table(chain_basin())
        assert t.loc[0, "streams"] == 1
        assert t.loc[0, "reaches"] == 5

    def test_mean_length_uses_streams_as_the_divisor(self):
        t = stream_table(chain_basin())
        assert t.loc[0, "mean_length_km"] == pytest.approx(20.0)

    def test_binary_tree_counts(self):
        t = stream_table(binary_tree_basin())
        assert t["streams"].tolist() == [4, 2, 1]

    def test_orders_with_no_streams_are_skipped(self):
        t = stream_table(binary_tree_basin())
        assert t["order"].tolist() == [1, 2, 3]


class TestHortonRatios:
    def test_perfect_tree_gives_ratio_two(self):
        r = horton_ratios(stream_table(binary_tree_basin()))
        assert r["bifurcation"] == pytest.approx(2.0, rel=1e-6)
        assert r["bifurcation_r2"] == pytest.approx(1.0, abs=1e-9)

    def test_ratio_fit_recovers_a_known_series(self):
        x = np.arange(1, 8)
        ratio, r2 = _ratio_fit(x, 1000 * 3.5 ** (-x.astype(float)))
        assert ratio == pytest.approx(3.5, rel=1e-6)
        assert r2 == pytest.approx(1.0, abs=1e-9)

    def test_fit_is_direction_agnostic(self):
        """Counts fall and areas rise; both should report a positive ratio."""
        x = np.arange(1, 6)
        rising, _ = _ratio_fit(x, 2.0 ** x.astype(float))
        falling, _ = _ratio_fit(x, 2.0 ** (-x.astype(float)))
        assert rising == pytest.approx(2.0, rel=1e-6)
        assert falling == pytest.approx(2.0, rel=1e-6)

    def test_too_few_points_is_nan_not_a_crash(self):
        ratio, r2 = _ratio_fit([1], [5.0])
        assert np.isnan(ratio) and np.isnan(r2)

    def test_stepwise_has_one_row_per_gap(self):
        t = stream_table(binary_tree_basin())
        s = stepwise(t)
        assert len(s) == len(t) - 1
        assert s["Rb"].tolist() == [2.0, 2.0]


class TestGroupTrunk:
    def test_trunk_follows_the_largest_feeder(self):
        g = forked_basin()
        lab, _ = tributary_labels(g, n_groups=2)
        trunk = group_trunk(g, lab, 0)
        # reaches 1..4 are the mainstem chain, by row index 0..3
        assert trunk.tolist()[:4] == [0, 1, 2, 3]

    def test_trunk_stays_inside_its_own_group(self):
        g = forked_basin()
        lab, _ = tributary_labels(g, n_groups=2)
        for grp in np.unique(lab):
            trunk = group_trunk(g, lab, grp)
            assert np.all(lab[trunk] == grp)

    def test_unknown_group_gives_an_empty_path(self):
        g = forked_basin()
        lab, _ = tributary_labels(g, n_groups=2)
        assert group_trunk(g, lab, 999).size == 0


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------

from hydrodendro import legend as legend_mod  # noqa: E402


def continuous_spec():
    return {
        "mode": "continuous", "cmap": palettes.get_cmap("abyss"),
        "value_min": 15.0, "value_max": 3276.5, "unit": "m",
        "value_label": "elevation above sea level",
        "lw_min": 0.25, "lw_max": 7.0, "width_exp": 0.40,
        "area_min": 2.0, "area_max": 260739.0,
    }


def categorical_spec():
    base = palettes.hue_set("prism")
    from matplotlib.colors import to_rgb
    return {
        "mode": "categorical",
        "swatches": [to_rgb(c) for c in base[:8]],
        "shade_ramp": np.linspace(0.2, 1.0, 64)[:, None] * np.array([[1.0, 0.3, 0.4]]),
        "category_label": "one hue per tributary system",
        "value_min": 15.0, "value_max": 3276.5, "unit": "m",
        "value_label": "elevation above sea level",
        "lw_min": 0.25, "lw_max": 7.0, "width_exp": 0.40,
        "area_min": 2.0, "area_max": 260739.0,
    }


class TestLegendFormatting:
    def test_large_areas_are_abbreviated(self):
        assert legend_mod._fmt(5_912_761, "km2") == "5.9M km\u00b2"
        assert legend_mod._fmt(260_739, "km2") == "261k km\u00b2"
        assert legend_mod._fmt(42, "km2") == "42 km\u00b2"

    def test_discharge_is_shown_unlogged(self):
        """The ramp works in log space; a reader should still see m3/s."""
        assert legend_mod._fmt(3.0, "log10 m3/s") == "1,000 m\u00b3/s"

    def test_plain_units(self):
        # 3276.5 renders as 3,276: Python's format rounds half to even. That is
        # fine for a rounded legend bound, and pinning it here stops anyone
        # "fixing" it into a surprise later.
        assert legend_mod._fmt(3276.5, "m") == "3,276 m"
        assert legend_mod._fmt(3277.5, "m") == "3,278 m"
        assert legend_mod._fmt(7, "order") == "order 7"


class TestLegendDrawing:
    def _figure(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt.figure(figsize=(8, 6), dpi=100)

    def test_continuous_draws_one_bar(self):
        import matplotlib.pyplot as plt
        fig = self._figure()
        before = len(fig.axes)
        legend_mod.draw(fig, continuous_spec())
        assert len(fig.axes) - before == 2      # colour ramp + width taper
        plt.close(fig)

    def test_categorical_draws_three_bars(self):
        """Hue swatches, the brightness ramp inside a hue, and the taper."""
        import matplotlib.pyplot as plt
        fig = self._figure()
        before = len(fig.axes)
        legend_mod.draw(fig, categorical_spec())
        assert len(fig.axes) - before == 3
        plt.close(fig)

    def test_categorical_names_both_channels(self):
        import matplotlib.pyplot as plt
        fig = self._figure()
        legend_mod.draw(fig, categorical_spec())
        said = " ".join(t.get_text() for t in fig.texts)
        assert "one hue per tributary system" in said
        assert "within one hue" in said
        assert "drainage area" in said
        plt.close(fig)

    def test_continuous_does_not_mention_hues(self):
        import matplotlib.pyplot as plt
        fig = self._figure()
        legend_mod.draw(fig, continuous_spec())
        said = " ".join(t.get_text() for t in fig.texts)
        assert "hue" not in said
        assert "elevation above sea level" in said
        plt.close(fig)

    def test_value_range_is_printed(self):
        import matplotlib.pyplot as plt
        fig = self._figure()
        legend_mod.draw(fig, continuous_spec())
        said = " ".join(t.get_text() for t in fig.texts)
        assert "15 m" in said and "3,276 m" in said
        plt.close(fig)

    def test_stays_inside_the_canvas(self):
        import matplotlib.pyplot as plt
        fig = self._figure()
        legend_mod.draw(fig, categorical_spec())
        for ax in fig.axes:
            x0, y0, x1, y1 = ax.get_position().extents
            assert 0.0 <= x0 and x1 <= 1.0
            assert 0.0 <= y0 and y1 <= 1.0
        plt.close(fig)
