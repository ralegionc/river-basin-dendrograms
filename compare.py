#!/usr/bin/env python3
"""Draw several basins side by side at one shared true scale.

    python compare.py
    python compare.py amazon congo mississippi rhine --width 3000

Every basin here is drawn at the same metres per pixel and with the same width
scale, so the Amazon really is that much larger than the Rhine. That is the
whole point: rendered one at a time, each basin fills its own frame and they
all look about equally impressive, which is exactly the comparison a reader
makes by accident.

Each basin keeps its own Lambert azimuthal equal-area projection centred on
itself. Every one of those is equal-area, so sharing a scale across them is
fair, whereas a single projection spanning the globe would not be.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402

from hydrodendro import basins, network, regions  # noqa: E402
from hydrodendro.palettes import get_cmap  # noqa: E402
from hydrodendro.render import _bloom, _stretch, _vignette  # noqa: E402

HERE = Path(__file__).resolve().parent

#: Fraction of each cell height kept clear at the bottom for the caption.
MAP_INSET = 0.17

BG = "#000000"
FG = "#e8edf2"
DIM = "#8a95a1"

#: A spread of sizes and shapes rather than just the biggest ones, because the
#: point of the sheet is the contrast.
DEFAULT_SET = [
    "amazon", "congo", "mississippi", "parana", "ob", "nile",
    "yangtze", "ganges", "mackenzie", "danube", "magdalena", "rhine",
]


def load_one(key, root, verbose=True):
    preset = basins.lookup(key)
    shp = regions.ensure_rivers(preset.region, root)
    hit = basins.resolve_seed(shp, *preset.seed)
    basin = network.load_basin(shp, preset.region, hit["main_riv"], root,
                               verbose=verbose)
    return preset, basin


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="compare.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="basins: " + ", ".join(sorted(basins.PRESETS)))
    p.add_argument("basins", nargs="*", default=None,
                   help=f"basins to compare (default: {len(DEFAULT_SET)} presets)")
    p.add_argument("--width", type=int, default=3600, help="output width in pixels")
    p.add_argument("--cols", type=int, default=0, help="grid columns (0 picks one)")
    p.add_argument("--palette", default="cyan", help="colour ramp")
    p.add_argument("--lw-gain", type=float, default=2.2,
                   help="thicken every stroke by this factor. The shared width "
                        "scale is what makes the sheet honest, but it also "
                        "makes the smallest basins sub-pixel, so the whole "
                        "sheet is scaled up together rather than per basin")
    p.add_argument("--bloom", type=float, default=0.5, help="glow strength")
    p.add_argument("--color", default="distance",
                   choices=["elevation", "strahler", "discharge", "distance"])
    p.add_argument("--min-strahler", type=int, default=2,
                   help="drop the smallest streams; they are sub-pixel in a "
                        "grid cell anyway, and it is applied to every basin "
                        "equally so the comparison stays fair")
    p.add_argument("--out", default=None)
    p.add_argument("--data-root", default=str(HERE / "data"))
    args = p.parse_args(argv)

    keys = args.basins or DEFAULT_SET
    root = Path(args.data_root)
    t_start = time.time()

    # Pass one: load everything, so the shared scale can be set from the real
    # maximum rather than guessed.
    loaded = []
    for i, key in enumerate(keys, 1):
        print(f"[{i}/{len(keys)}] {key}")
        preset, basin = load_one(key, root)
        loaded.append((preset, basin))

    area_ref = max(b.upland_skm for _, b in loaded)
    print(f"\nshared width scale from the largest basin: {area_ref:,.0f} km2")

    dem_needed = args.color == "elevation"
    built = []
    for preset, basin in loaded:
        dem = regions.ensure_dem(preset.region, root) if dem_needed else None
        segs = network.build_segments(
            basin, dem_path=dem, color_mode=args.color,
            min_strahler=args.min_strahler, area_ref=area_ref, verbose=False,
        )
        built.append((preset, basin, segs))
    built.sort(key=lambda r: -r[1].upland_skm)

    # One shared view span: the widest basin sets it, everything else is drawn
    # into the same window and simply occupies less of it.
    spans = []
    for _, _, s in built:
        x0, y0, x1, y1 = s.bounds
        spans.append(max(x1 - x0, y1 - y0))
    span = max(spans) * 1.06

    n = len(built)
    cols = args.cols or int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))

    dpi = 100
    cell = args.width / cols
    fig_w = args.width
    fig_h = int(cell * rows + cell * 0.30)          # strip at the top for a title
    fig = plt.figure(figsize=(fig_w / dpi, fig_h / dpi), dpi=dpi)
    fig.patch.set_facecolor(BG)

    top = 1.0 - (cell * 0.30) / fig_h
    print(f"rendering {n} basins into a {cols} x {rows} grid")
    for i, (preset, basin, segs) in enumerate(built):
        r, c = divmod(i, cols)
        cx0, cy0 = c / cols, top - (r + 1) / rows * top
        cw, ch = 1 / cols, top / rows
        # Reserve the bottom of the cell for the caption. The Nile runs from
        # Lake Victoria to the Mediterranean and is the tallest basin here, so
        # a caption drawn inside the map area lands on the river itself.
        ax = fig.add_axes([cx0, cy0 + MAP_INSET * ch, cw, (1 - MAP_INSET) * ch])
        ax.set_facecolor(BG)
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_color("#161a1f")

        x0, y0, x1, y1 = segs.bounds
        cxx, cyy = (x0 + x1) / 2, (y0 + y1) / 2
        ax.set_xlim(cxx - span / 2, cxx + span / 2)
        ax.set_ylim(cyy - span / 2, cyy + span / 2)
        ax.set_aspect("equal")

        t, _, _ = _stretch(segs.value, 2.0, 98.0, "rank")
        colors = get_cmap(args.palette)(t)
        # Widths are in reference pixels against a 4000px canvas; here the
        # canvas is one cell wide. Every cell shares both the cell size and the
        # view span, so one factor keeps all of them consistent.
        lw = (segs.width * (cell * (1 - MAP_INSET) / 4000.0)
              * (72.0 / dpi) * args.lw_gain)
        ax.add_collection(LineCollection(
            segs.xy, linewidths=lw, colors=colors, capstyle="round",
            joinstyle="round", antialiaseds=True))

        fig.text(cx0 + cw / 2, cy0 + 0.085 * ch, preset.label.upper(),
                 ha="center", va="bottom", color=FG, alpha=0.9,
                 fontsize=cell / 74)
        fig.text(cx0 + cw / 2, cy0 + 0.035 * ch, f"{basin.upland_skm:,.0f} km$^2$",
                 ha="center", va="bottom", color=DIM, fontsize=cell / 105)

    header_px = cell * 0.30
    fig.text(0.5, 1 - (header_px * 0.34) / fig_h,
             "RIVER BASINS AT TRUE RELATIVE SCALE",
             color=FG, fontsize=cell / 46, ha="center", va="top")
    fig.text(0.5, 1 - (header_px * 0.62) / fig_h,
             f"every basin drawn at the same metres per pixel and the same width "
             f"scale   /   Strahler {args.min_strahler} and above"
             f"   /   HydroSHEDS HydroRIVERS v1.0",
             color=DIM, fontsize=cell / 98, ha="center", va="top")

    out = Path(args.out) if args.out else HERE / "out" / "comparison.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    # Same finishing passes as a single basin render, so the sheet sits beside
    # the other plates instead of looking like a different project.
    fig.canvas.draw()
    rgb = np.asarray(fig.canvas.buffer_rgba(), dtype=np.float32)[..., :3] / 255.0
    plt.close(fig)
    if args.bloom > 0:
        rgb = _bloom(rgb, args.bloom, max(2.0, 2.4 * fig_w / 4000.0), 0.045)
    rgb = _vignette(rgb, 0.12)

    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    Image.fromarray((rgb * 255).round().astype(np.uint8)).save(out)
    print(f"  wrote {out}  ({fig_w} x {fig_h}px)")
    print(f"done in {time.time() - t_start:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
