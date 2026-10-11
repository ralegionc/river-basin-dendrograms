#!/usr/bin/env python3
"""Render a set of basins in one run, each with a palette that suits it.

    python gallery.py                 # the default six
    python gallery.py --width 6000    # print resolution
    python gallery.py amazon congo    # just these, with their chosen palettes

The first render of any basin pays a one off scan of its regional shapefile,
and downloads that region if it is missing. Later runs read the cache.
"""
from __future__ import annotations

import argparse
import sys
import time

import render as cli

# Each plate names a basin and how to colour it. Tributary mode suits the big
# branching systems where the story is which river owns which country; the
# elevation ramps suit the ones whose story is relief.
PLATES = [
    ("amazon", {"color": "basin", "hues": "prism", "tributaries": "24"}),
    ("ganges", {"color": "basin", "hues": "coral", "tributaries": "20"}),
    ("congo", {"color": "basin", "hues": "reef", "tributaries": "20"}),
    ("mississippi", {"color": "basin", "hues": "prism", "tributaries": "20"}),
    ("parana", {"color": "basin", "hues": "reef", "tributaries": "16"}),
    ("danube", {"palette": "ice"}),
    ("nile", {"palette": "sanguine"}),
    ("mekong", {"palette": "cyan"}),
    ("yukon", {"palette": "bone"}),
    ("mackenzie", {"color": "basin", "hues": "faded", "tributaries": "16"}),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("basins", nargs="*", help="subset of the gallery to render")
    ap.add_argument("--width", type=int, default=4000)
    ap.add_argument("--no-title", action="store_true")
    args = ap.parse_args(argv)

    chosen = PLATES
    if args.basins:
        wanted = set(args.basins)
        chosen = [(b, o) for b, o in PLATES if b in wanted]
        for name in sorted(wanted - {b for b, _ in PLATES}):
            chosen.append((name, {"palette": "abyss"}))

    t0 = time.time()
    failures = []
    for i, (basin, opts) in enumerate(chosen, 1):
        label = opts.get("hues") or opts.get("palette", "abyss")
        print(f"\n=== [{i}/{len(chosen)}] {basin} / {label} " + "=" * 30)
        argv_i = [basin, "--width", str(args.width)]
        for key, value in opts.items():
            argv_i += [f"--{key}", value]
        if not args.no_title:
            argv_i.append("--title")
        try:
            cli.main(argv_i)
        except SystemExit as exc:  # a bad preset should not kill the batch
            if exc.code not in (0, None):
                print(f"  FAILED: {exc}")
                failures.append(basin)
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            failures.append(basin)

    print(f"\ngallery finished in {time.time() - t0:.0f}s")
    if failures:
        print("failed: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
