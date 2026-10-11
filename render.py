#!/usr/bin/env python3
"""Render a single river basin from HydroSHEDS as a tapered dendrogram.

Examples
--------
    python render.py amazon
    python render.py ganges --palette ember --width 6000
    python render.py --at -63.55,8.13 --region sa --name Orinoco
    python render.py --list-basins sa
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from hydrodendro import basins, names, network, palettes, paper, regions
from hydrodendro.render import render

HERE = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="render.py",
        description="Render a HydroSHEDS river basin as a tapered dendrogram.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="basins: " + ", ".join(sorted(basins.PRESETS)),
    )
    p.add_argument("basin", nargs="?", help="preset basin name, for example amazon")

    loc = p.add_argument_group("basin selection")
    loc.add_argument("--at", metavar="LON,LAT",
                     help="seed point on a trunk river, instead of a preset")
    loc.add_argument("--region", choices=sorted(regions.REGIONS),
                     help="HydroSHEDS region code, required with --at")
    loc.add_argument("--main-riv", type=int,
                     help="select a basin directly by its MAIN_RIV id")
    loc.add_argument("--name", help="label for the caption and output filename")
    loc.add_argument("--list-basins", metavar="REGION",
                     help="rank the largest basins in a region and exit")
    loc.add_argument("--all-basins", metavar="REGION",
                     help="render every basin in a whole continental region, "
                          "each one its own colour, instead of a single basin")

    art = p.add_argument_group("look")
    art.add_argument("--palette", default="abyss",
                     help=f"colour ramp ({', '.join(palettes.available())}) "
                          "or any matplotlib colormap name")
    art.add_argument("--color", default="elevation",
                     choices=["basin", "elevation", "strahler", "discharge", "distance"],
                     help="what drives colour; 'basin' gives each tributary "
                          "system its own hue (default: elevation)")
    art.add_argument("--hues", default="prism",
                     help=f"hue set for --color basin "
                          f"({', '.join(palettes.hue_sets())})")
    art.add_argument("--tributaries", type=int, default=24,
                     help="how many tributary systems get their own hue")
    art.add_argument("--shade", type=float, default=0.55,
                     help="how much relief to mix into each tributary hue; "
                          "0 is flat poster colour, 1 is the full ramp")
    art.add_argument("--shade-by", default="elevation",
                     choices=["elevation", "strahler", "discharge", "distance"],
                     help="what varies lightness inside each tributary hue")
    art.add_argument("--invert-ramp", action="store_true",
                     help="flip the ramp so headwaters take the dark end")
    art.add_argument("--bg", default="#000000", help="background colour")
    art.add_argument("--width", type=int, default=4000, help="output width in pixels")
    art.add_argument("--paper", metavar="SIZE",
                     help=f"render to an exact print size instead of --width "
                          f"({', '.join(paper.available())})")
    art.add_argument("--dpi", type=int, default=300,
                     help="dots per inch for --paper (default: 300)")
    art.add_argument("--lw-min", type=float, default=0.25,
                     help="stroke width of a first order headwater, in pixels at 4000px")
    art.add_argument("--lw-max", type=float, default=7.0,
                     help="stroke width at the outlet, in pixels at 4000px")
    art.add_argument("--width-exp", type=float, default=0.40,
                     help="width goes as (drainage area) ** this; raise it to "
                          "thin the tributaries and let the trunk dominate")
    art.add_argument("--stretch", default="rank", choices=["rank", "linear"],
                     help="rank equalises the colour histogram (default); "
                          "linear keeps colour proportional to absolute value")
    art.add_argument("--bloom", type=float, default=0.55,
                     help="glow strength, 0 disables the pass")
    art.add_argument("--bloom-sigma", type=float, default=None,
                     help="glow radius in pixels (default scales with --width)")
    art.add_argument("--clip", default="2,98", metavar="LO,HI",
                     help="percentile stretch for the colour ramp")
    art.add_argument("--margin", type=float, default=0.03, help="border as a fraction")
    art.add_argument("--vignette", type=float, default=0.18,
                     help="corner darkening, 0 disables it")
    art.add_argument("--title", action="store_true", help="draw a caption")
    art.add_argument("--legend", action="store_true",
                     help="draw a key explaining what colour and line width mean")
    art.add_argument("--label-rivers", action="store_true",
                     help="name the tributaries, matched against Natural Earth "
                          "centrelines (needs --color basin)")
    art.add_argument("--label-size", type=float, default=11.0,
                     help="river label size in points at 4000px")

    dat = p.add_argument_group("data and geometry")
    dat.add_argument("--projection", default="laea", choices=["laea", "aea", "none"],
                     help="equal area projection centred on the basin (default: laea)")
    dat.add_argument("--min-strahler", type=int, default=0,
                     help="drop reaches below this Strahler order")
    dat.add_argument("--simplify", type=float, default=0.0,
                     help="Douglas-Peucker tolerance in degrees, for faster drafts")
    dat.add_argument("--data-root", default=str(HERE / "data"),
                     help="where HydroSHEDS downloads and caches live")
    dat.add_argument("--out", default=None, help="output PNG path")
    dat.add_argument("--no-cache", action="store_true",
                     help="ignore any cached basin extract")
    return p


def resolve_target(args):
    """Work out (region, main_riv, label) from whichever selector was given."""
    if args.basin:
        preset = basins.lookup(args.basin)
        return preset.region, None, preset.seed, args.name or preset.label

    if args.at:
        if not args.region:
            raise SystemExit("--at also needs --region (for example --region sa)")
        try:
            lon, lat = (float(v) for v in args.at.split(","))
        except ValueError:
            raise SystemExit("--at expects LON,LAT, for example --at -55.51,-1.95") from None
        return args.region, None, (lon, lat), args.name or f"{lat:.2f}, {lon:.2f}"

    if args.main_riv is not None:
        if not args.region:
            raise SystemExit("--main-riv also needs --region")
        return args.region, args.main_riv, None, args.name or f"MAIN_RIV {args.main_riv}"

    raise SystemExit("give a preset basin, --at LON,LAT, or --main-riv (see --help)")


def parse_clip(text):
    try:
        lo, hi = (float(v) for v in text.split(","))
    except ValueError:
        raise SystemExit("--clip expects LO,HI percentiles, for example 2,98") from None
    if not 0.0 <= lo < hi <= 100.0:
        raise SystemExit(f"--clip needs 0 <= LO < HI <= 100, got {text!r}")
    return lo, hi


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.data_root)

    # Fail on bad arguments now rather than after a two minute render.
    clip = parse_clip(args.clip)
    # Validate both, whichever mode is active, so a typo in the unused one is
    # still caught now rather than ignored through a two minute render.
    palettes.hue_set(args.hues)
    palettes.get_cmap(args.palette)
    if args.paper:
        # Aspect is not known until the geometry is built, but the name and dpi
        # can be rejected now rather than after a three minute render.
        paper.canvas_px(args.paper, args.dpi, 1.0)

    if args.list_basins:
        shp = regions.ensure_rivers(args.list_basins, root)
        print(f"largest basins in {regions.REGIONS[args.list_basins]}:")
        table = basins.largest_basins(shp)
        print(f"{'MAIN_RIV':>12} {'km2':>14} {'reaches':>10} {'strahler':>9}")
        for _, r in table.iterrows():
            print(f"{int(r.MAIN_RIV):>12} {r.upland_skm:>14,.0f} "
                  f"{int(r.reaches):>10,} {int(r.max_strahler):>9}")
        return 0

    region_wide = bool(args.all_basins)
    if region_wide:
        if args.all_basins not in regions.REGIONS:
            raise SystemExit(
                f"unknown region {args.all_basins!r}; "
                f"use one of {', '.join(sorted(regions.REGIONS))}"
            )
        region, main_riv, seed = args.all_basins, None, None
        label = args.name or regions.REGIONS[region]
    else:
        region, main_riv, seed, label = resolve_target(args)
    t_start = time.time()

    print(f"[1/4] data for {regions.REGIONS[region]}")
    shp = regions.ensure_rivers(region, root)
    needs_dem = args.color == "elevation" or (
        args.color == "basin" and args.shade_by == "elevation")
    dem = regions.ensure_dem(region, root) if needs_dem else None

    if seed is not None and main_riv is None:
        hit = basins.resolve_seed(shp, *seed)
        main_riv = hit["main_riv"]
        print(f"      seed ({seed[0]}, {seed[1]}) resolved to MAIN_RIV={main_riv}, "
              f"{hit['upland_skm']:,.0f} km2 upstream")

    if region_wide:
        print(f"[2/4] loading every basin in {label}")
        basin = network.load_region(shp, region, root, use_cache=not args.no_cache)
    else:
        print(f"[2/4] loading basin {label}")
        basin = network.load_basin(shp, region, main_riv, root,
                                   use_cache=not args.no_cache)

    print("[3/4] building tapered geometry")
    segs = network.build_segments(
        basin, dem_path=dem, color_mode=args.color,
        lw_min=args.lw_min, lw_max=args.lw_max, width_exp=args.width_exp,
        projection=args.projection, min_strahler=args.min_strahler,
        simplify=args.simplify, n_tributaries=args.tributaries,
        shade_by=args.shade_by,
        group_by="mainriv" if region_wide else "tributary",
    )

    slug = (args.name or label).lower().replace(" ", "-").replace(",", "")
    tag = f"basin-{args.hues}" if args.color == "basin" else f"{args.palette}_{args.color}"
    out = Path(args.out) if args.out else HERE / "out" / f"{slug}_{tag}.png"
    river_labels = None
    if args.label_rivers:
        if segs.group is None:
            raise SystemExit("--label-rivers needs --color basin")
        ne = names.ensure_names(root)
        # Reuse the labels build_segments already computed rather than
        # recomputing them here: repeating the grouping means repeating the
        # --min-strahler filtering too, and the two could drift apart.
        matched = names.match_names(segs.label_gdf, segs.reach_labels,
                                    segs.groups, ne)

        if matched:
            from pyproj import CRS, Transformer

            tf = Transformer.from_crs(CRS.from_epsg(4326), segs.crs, always_xy=True)
            river_labels = []
            biggest = max(m["upland_skm"] for m in matched.values())
            for m in matched.values():
                x, y = tf.transform(m["lon"], m["lat"])
                # Size tracks drainage area so the trunk reads first, but only
                # mildly: a linear map would make the smallest names illegible.
                rel = (m["upland_skm"] / biggest) ** 0.16
                river_labels.append({
                    "x": float(x), "y": float(y), "name": m["name"],
                    "weight": m["upland_skm"],
                    "size": args.label_size * (0.58 + 0.42 * rel),
                })

    legend_spec = None
    if args.legend:
        # Deliberately not named `unit` or `label`: both are live names in this
        # function, and shadowing `label` here fed the basin name's dict into
        # the caption instead of the name.
        value_units = {"elevation": "m", "distance": "km",
                       "discharge": "log10 m3/s", "strahler": "order"}
        value_names = {"elevation": "elevation above sea level",
                       "distance": "distance to the outlet",
                       "discharge": "mean discharge",
                       "strahler": "Strahler order"}
        shown = args.shade_by if args.color == "basin" else args.color
        areas = basin.gdf["UPLAND_SKM"]
        legend_spec = {
            "lw_min": args.lw_min, "lw_max": args.lw_max,
            "width_exp": args.width_exp,
            "area_min": float(max(areas.min(), 1.0)), "area_max": float(areas.max()),
            "unit": value_units[shown], "value_label": value_names[shown],
        }
        if args.color == "basin":
            legend_spec["mode"] = "categorical"
            legend_spec["category_label"] = (
                "one hue per basin" if region_wide
                else "one hue per tributary system")
        else:
            legend_spec["mode"] = "continuous"

    canvas = dpi_meta = None
    if args.paper:
        xmin, ymin, xmax, ymax = segs.bounds
        aspect = max(ymax - ymin, 1e-6) / max(xmax - xmin, 1e-6)
        cw, ch, note = paper.canvas_px(args.paper, args.dpi, aspect)
        canvas, dpi_meta = (cw, ch), args.dpi
        print(f"      print size {note}")

    print("[4/4] rendering")
    title = subtitle = None
    if args.title:
        title = label
        elo, ehi = segs.elev_range
        if region_wide:
            # basin.upland_skm is the single largest basin, which would read as
            # the whole continent's area if it were printed unqualified.
            n_basins = int(basin.gdf["MAIN_RIV"].nunique())
            bits = [f"{n_basins:,} basins", f"{segs.n_reaches:,} reaches",
                    f"largest {basin.upland_skm:,.0f} km2"]
        else:
            bits = [f"{basin.upland_skm:,.0f} km2", f"{segs.n_reaches:,} reaches"]
            if segs.groups:
                bits.append(f"{len(segs.groups) - 1} tributary systems")
        if needs_dem:
            bits.append(f"{elo:,.0f} to {ehi:,.0f} m")
        subtitle = "   /   ".join(bits) + "   /   HydroSHEDS HydroRIVERS v1.0"

    out, (lo, hi) = render(
        segs, out, palette=args.palette, bg=args.bg, width_px=args.width,
        margin=args.margin, bloom=args.bloom, bloom_sigma=args.bloom_sigma,
        clip_pct=clip, stretch=args.stretch, invert_ramp=args.invert_ramp,
        title=title, subtitle=subtitle, hues=args.hues, shade=args.shade,
        vignette=args.vignette, canvas=canvas, dpi_meta=dpi_meta,
        labels=river_labels, legend=legend_spec,
    )
    unit = {"elevation": "m", "distance": "km", "discharge": "log10 m3/s",
            "strahler": "order"}[args.shade_by if args.color == "basin" else args.color]
    print(f"      colour ramp spans {lo:,.1f} to {hi:,.1f} {unit}")
    print(f"done in {time.time() - t_start:.0f}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except basins.BasinNotFound as exc:
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        sys.exit(130)
