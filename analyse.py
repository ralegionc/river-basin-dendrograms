#!/usr/bin/env python3
"""Measure a basin against Horton's laws and plot the result.

    python analyse.py amazon
    python analyse.py ganges --out out/ganges-horton.png

Horton's laws say a drainage network is close to self similar: stream counts
fall off geometrically with Strahler order while mean lengths and mean areas
grow geometrically. This draws all three and fits the ratios.
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

from hydrodendro import basins, network, regions  # noqa: E402
from hydrodendro.horton import horton_ratios, stepwise, stream_table  # noqa: E402

HERE = Path(__file__).resolve().parent

BG = "#0b0d10"
FG = "#e8edf2"
DIM = "#7b8794"
ACCENTS = ["#2fd9d9", "#ff7a2f", "#9fe84a"]


def _panel(ax, order, values, colour, title, ylabel, ratio, r2, ratio_label,
           rising=False):
    ax.set_facecolor(BG)
    keep = np.asarray(values) > 0
    x, y = np.asarray(order)[keep], np.asarray(values)[keep]

    slope, intercept = np.polyfit(x, np.log(y), 1)
    xs = np.linspace(x.min(), x.max(), 100)
    ax.plot(xs, np.exp(slope * xs + intercept), color=colour, lw=1.2,
            alpha=0.55, zorder=1)
    ax.scatter(x, y, s=46, color=colour, zorder=3, edgecolor=BG, linewidth=1.2)

    ax.set_yscale("log")
    ax.set_title(title, color=FG, fontsize=11, pad=12, loc="left")
    ax.set_xlabel("Strahler order", color=DIM, fontsize=9)
    ax.set_ylabel(ylabel, color=DIM, fontsize=9)
    ax.tick_params(colors=DIM, labelsize=8.5)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#2a3138")
    ax.grid(True, which="major", color="#1a1f25", lw=0.8)
    ax.set_axisbelow(True)
    # A rising series fills the top right of its own panel, so the annotation
    # has to move to the opposite corner or it lands on the data.
    xy, ha = ((0.03, 0.94), "left") if rising else ((0.97, 0.94), "right")
    ax.annotate(f"{ratio_label} = {ratio:.2f}\nR$^2$ = {r2:.3f}",
                xy=xy, xycoords="axes fraction", ha=ha, va="top",
                color=colour, fontsize=10)


def make_figure(table, ratios, steps, label, out_path, width_px=2400):
    dpi = 150
    fig = plt.figure(figsize=(width_px / dpi, width_px / dpi * 0.52), dpi=dpi)
    fig.patch.set_facecolor(BG)
    gs = fig.add_gridspec(2, 3, height_ratios=[3.1, 1.0], hspace=0.55, wspace=0.28,
                          left=0.07, right=0.965, top=0.80, bottom=0.10)

    order = table["order"].to_numpy()
    _panel(fig.add_subplot(gs[0, 0]), order, table["streams"], ACCENTS[0],
           "Law of stream numbers", "streams of that order",
           ratios["bifurcation"], ratios["bifurcation_r2"], "R$_b$")
    _panel(fig.add_subplot(gs[0, 1]), order, table["mean_length_km"], ACCENTS[1],
           "Law of stream lengths", "mean length (km)",
           ratios["length"], ratios["length_r2"], "R$_l$", rising=True)
    _panel(fig.add_subplot(gs[0, 2]), order, table["mean_area_skm"], ACCENTS[2],
           "Law of stream areas", "mean drainage area (km$^2$)",
           ratios["area"], ratios["area_r2"], "R$_a$", rising=True)

    # The fitted ratio is one number for the whole network; this strip shows how
    # steady it actually is, and where the top orders run out of streams.
    ax = fig.add_subplot(gs[1, :])
    ax.set_facecolor(BG)
    xs = steps["from_order"].to_numpy()
    for key, colour, name in zip(("Rb", "Rl", "Ra"), ACCENTS, ("R$_b$", "R$_l$", "R$_a$")):
        ax.plot(xs, steps[key], color=colour, lw=1.3, marker="o", ms=4,
                label=name, alpha=0.9)
    ax.axhspan(3, 5, color="#ffffff", alpha=0.045, zorder=0)
    ax.set_title("ratio between each adjacent pair of orders"
                 "      (shaded band: R$_b$ 3 to 5, where most natural basins fall)",
                 color=FG, fontsize=10, pad=10, loc="left")
    ax.set_xlabel("from order", color=DIM, fontsize=9)
    ax.tick_params(colors=DIM, labelsize=8.5)
    ax.set_xticks(xs)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#2a3138")
    ax.grid(True, color="#1a1f25", lw=0.8)
    ax.set_axisbelow(True)
    # Headroom for the legend, which would otherwise sit on the Ra line.
    lo, hi = ax.get_ylim()
    ax.set_ylim(lo, hi * 1.35)
    leg = ax.legend(loc="upper right", frameon=False, ncol=3, fontsize=9)
    for text in leg.get_texts():
        text.set_color(DIM)

    fig.text(0.07, 0.925, f"{label.upper()}  /  HORTON'S LAWS", color=FG,
             fontsize=15, ha="left")
    total = int(table["streams"].sum())
    fig.text(0.07, 0.878,
             f"{total:,} Strahler streams across {ratios['orders']} orders"
             f"   /   {int(table['reaches'].sum()):,} reaches"
             f"   /   HydroSHEDS HydroRIVERS v1.0",
             color=DIM, fontsize=9, ha="left")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor=BG, dpi=dpi)
    plt.close(fig)
    return out_path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="analyse.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="basins: " + ", ".join(sorted(basins.PRESETS)))
    p.add_argument("basin", help="preset basin name, for example amazon")
    p.add_argument("--out", default=None, help="output PNG path")
    p.add_argument("--width", type=int, default=2400, help="output width in pixels")
    p.add_argument("--data-root", default=str(HERE / "data"))
    p.add_argument("--csv", default=None, help="also write the table as CSV")
    args = p.parse_args(argv)

    root = Path(args.data_root)
    preset = basins.lookup(args.basin)
    t0 = time.time()

    shp = regions.ensure_rivers(preset.region, root)
    hit = basins.resolve_seed(shp, *preset.seed)
    basin = network.load_basin(shp, preset.region, hit["main_riv"], root)

    table = stream_table(basin.gdf)
    ratios = horton_ratios(table)
    steps = stepwise(table)

    print(table.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    print(f"\n  Rb = {ratios['bifurcation']:.2f}  (R2 {ratios['bifurcation_r2']:.3f})"
          f"   Rl = {ratios['length']:.2f}  (R2 {ratios['length_r2']:.3f})"
          f"   Ra = {ratios['area']:.2f}  (R2 {ratios['area_r2']:.3f})")

    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(args.csv, index=False)
        print(f"  wrote {args.csv}")

    out = Path(args.out) if args.out else HERE / "out" / f"{args.basin}-horton.png"
    make_figure(table, ratios, steps, preset.label, out, args.width)
    print(f"  wrote {out}")
    print(f"done in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
