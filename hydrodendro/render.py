"""Drawing tapered river networks and the optional bloom pass."""
from __future__ import annotations

import time

import matplotlib
import numpy as np

matplotlib.use("Agg")

import matplotlib.patheffects as pe  # noqa: E402
from matplotlib.colors import to_rgb  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402


from . import legend as legend_mod  # noqa: E402
from .palettes import get_cmap, hue_set  # noqa: E402

# Stroke widths are authored against a 4000 px wide canvas and scaled from
# there, so raising --width keeps the same look instead of thinning the lines.
REFERENCE_WIDTH_PX = 4000
DPI = 100


def _stretch(value, low_pct, high_pct, mode="rank", knots=1025):
    """Map colour values onto 0 to 1, returning that plus the data range used.

    ``linear`` is a plain percentile stretch: honest about absolute elevation,
    but hopeless on a basin like the Amazon, where almost every reach sits
    below 200 m and a thin Andean rim holds the top of the range. Under a
    linear ramp the entire lowland collapses into one flat colour.

    ``rank`` replaces each value with its own percentile, so colour tracks
    where a reach falls in the basin's own distribution rather than in metres.
    That spends the whole ramp on the range that actually contains the data and
    brings out the downstream gradient across the lowlands. It is the default
    because it is what makes the picture readable; use ``linear`` when the
    colours need to mean absolute height.
    """
    value = np.asarray(value, dtype=np.float64)
    lo = float(np.percentile(value, low_pct))
    hi = float(np.percentile(value, high_pct))
    if hi <= lo:
        hi = lo + 1.0

    if mode == "linear":
        return np.clip((value - lo) / (hi - lo), 0.0, 1.0), lo, hi
    if mode != "rank":
        raise SystemExit(f"unknown stretch {mode!r}; use rank or linear")

    # Sample the distribution at evenly spaced quantiles and interpolate. This
    # is a histogram equalisation that costs one percentile call rather than a
    # full sort of every segment.
    qs = np.linspace(low_pct, high_pct, knots)
    xp = np.percentile(value, qs)
    xp = np.maximum.accumulate(xp)
    xp, keep = np.unique(xp, return_index=True)
    fp = (qs[keep] - low_pct) / max(high_pct - low_pct, 1e-9)
    if xp.size < 2:
        return np.zeros_like(value), lo, hi
    return np.clip(np.interp(value, xp, fp), 0.0, 1.0), lo, hi


def _tributary_colors(group, t, hues, shade=0.55, sat_lo=0.22, sat_hi=1.00,
                      val_lo=0.42, val_hi=1.00, val_gamma=0.70):
    """One hue per tributary, with the shading term riding inside that hue.

    Hues are assigned with a stride co-prime to the palette length, so the two
    largest neighbouring tributaries never come out adjacent on the wheel.

    ``shade`` decides how much relief to mix in, and it needs a limit. The
    shading term is absolute elevation, but a tributary can sit entirely in the
    lowlands: the Negro never climbs, so at full strength every reach it owns
    lands at the bottom of the value ramp and the whole system turns muddy
    brown regardless of its hue. Damping the range keeps each river's identity
    legible and still shows which ones rise into the Andes. ``shade=0`` gives
    flat poster colour, ``1`` gives the full ramp.
    """
    from matplotlib.colors import hsv_to_rgb, to_rgb

    palette = np.array([to_rgb(h) for h in hues], dtype=np.float32)
    k = len(palette)
    stride = next((v for v in (7, 5, 3, 1) if np.gcd(v, k) == 1), 1)
    base = palette[(group.astype(np.int64) * stride) % k]

    shade = float(np.clip(shade, 0.0, 1.0))
    val_floor = 1.0 - shade * (1.0 - val_lo)
    sat_floor = 1.0 - shade * (1.0 - sat_lo)

    hsv = np.asarray(matplotlib.colors.rgb_to_hsv(base), dtype=np.float32)
    tt = np.clip(t, 0.0, 1.0).astype(np.float32)
    shaped = np.power(tt, val_gamma, dtype=np.float32)
    hsv[:, 1] = sat_hi + (sat_floor - sat_hi) * shaped
    hsv[:, 2] = val_floor + (val_hi - val_floor) * shaped

    rgb = hsv_to_rgb(hsv)
    out = np.ones((len(rgb), 4), dtype=np.float32)
    out[:, :3] = rgb
    return out


def _draw_labels(ax, fig, labels, scale, colour="#ffffff"):
    """Place river names, dropping any that would collide.

    Labels are added largest river first and measured in display space, so a
    small tributary never covers a big one. There is no leader line and no
    nudging: on a network this dense, a label that has to be moved far enough
    to fit is a label pointing at the wrong river, so it is dropped instead.
    Returns the names actually drawn.
    """
    renderer = fig.canvas.get_renderer()
    placed, drawn = [], []

    for item in sorted(labels, key=lambda d: -d.get("weight", 0.0)):
        size = item.get("size", 11.0) * scale
        txt = ax.text(item["x"], item["y"], item["name"], color=colour,
                      fontsize=size, ha="center", va="center", zorder=6,
                      alpha=0.93, family="DejaVu Sans",
                      path_effects=[pe.withStroke(linewidth=2.4 * scale,
                                                  foreground="#000000",
                                                  alpha=0.75)])
        box = txt.get_window_extent(renderer=renderer).expanded(1.18, 1.5)
        if any(box.overlaps(other) for other in placed):
            txt.remove()
            continue
        placed.append(box)
        drawn.append(item["name"])
    return drawn


def _vignette(rgb, strength):
    """Darken the corners a little. Gives the canvas a centre and stops a big
    basin from looking like it was cropped out of a larger sheet."""
    if strength <= 0:
        return rgb
    h, w = rgb.shape[:2]
    yy = np.linspace(-1.0, 1.0, h, dtype=np.float32)[:, None]
    xx = np.linspace(-1.0, 1.0, w, dtype=np.float32)[None, :]
    r = np.sqrt(xx * xx + yy * yy) / np.sqrt(2.0)
    mask = 1.0 - strength * np.clip(r, 0.0, 1.0) ** 2.2
    return np.clip(rgb * mask[..., None], 0.0, 1.0)


def _bloom(rgb, amount, sigma, threshold):
    """Screen a blurred copy of the bright pixels back over the image.

    Antialiased hairlines on black land in the darkest few percent of the
    range, so a real blur pass is what separates this from flat line art: the
    trunk river gains a halo and dense headwater country starts to glow as a
    mass rather than as separate strokes.
    """
    from scipy.ndimage import gaussian_filter

    lum = rgb.max(axis=2)
    mask = np.clip((lum - threshold) / max(1.0 - threshold, 1e-6), 0.0, 1.0)
    bright = rgb * mask[..., None]

    # Three octaves rather than one. A single gaussian gives every stroke the
    # same halo; stacking a tight, a medium and a wide pass puts a crisp edge
    # on the trunk and a broad atmospheric haze over dense headwater country,
    # which is what actually reads as glow.
    glow = np.zeros_like(bright)
    for scale, weight in ((1.0, 0.50), (2.6, 0.33), (6.5, 0.17)):
        glow += weight * gaussian_filter(
            bright, sigma=(sigma * scale, sigma * scale, 0), mode="nearest"
        )
    return np.clip(1.0 - (1.0 - rgb) * (1.0 - np.clip(glow * amount, 0, 1)), 0, 1)


def render(segments, out_path, palette="abyss", bg="#000000", width_px=4000,
           margin=0.03, bloom=0.55, bloom_sigma=None, bloom_threshold=0.045,
           clip_pct=(2.0, 98.0), stretch="rank", invert_ramp=False, title=None,
           subtitle=None, hues="prism", shade=0.55, vignette=0.18,
           canvas=None, dpi_meta=None, labels=None, legend=None,
           verbose=True):
    """Render segments to a PNG and return the output path."""
    xmin, ymin, xmax, ymax = segments.bounds
    data_x = max(xmax - xmin, 1e-6)
    data_y = max(ymax - ymin, 1e-6)
    span_x = data_x * (1 + 2 * margin)
    span_y = data_y * (1 + 2 * margin)
    nominal_x = span_x

    if canvas is None:
        height_px = int(round(width_px * span_y / span_x))
    else:
        # Printing to a fixed sheet: the basin almost never shares the paper's
        # aspect, so grow the view on whichever axis has room and let the
        # background fill the rest. Cropping instead would cut off headwaters.
        width_px, height_px = canvas
        sheet = height_px / width_px
        if span_y / span_x > sheet:
            span_x = span_y / sheet
        else:
            span_y = span_x * sheet

    cx = 0.5 * (xmin + xmax)
    cy = 0.5 * (ymin + ymax)
    xlim = (cx - span_x / 2, cx + span_x / 2)
    ylim = (cy - span_y / 2, cy + span_y / 2)

    # Stroke width has to follow how large the basin is drawn, not how large
    # the sheet is, or letterboxing would fatten every line on a mismatched
    # paper size.
    scale = (width_px / span_x) / (REFERENCE_WIDTH_PX / nominal_x)

    # No supersampling here on purpose. It is the obvious lever for an image
    # made almost entirely of sub-pixel hairlines, but Agg already resolves
    # each path with analytic coverage antialiasing rather than point sampling,
    # so rendering at 2x and averaging down lands within 0.2 percent of the
    # direct render even on the Amazon. Measured, not assumed.

    t, lo, hi = _stretch(segments.value, clip_pct[0], clip_pct[1], stretch)
    if invert_ramp:
        t = 1.0 - t

    if segments.group is not None:
        colors = _tributary_colors(segments.group, t, hue_set(hues), shade=shade)
    else:
        colors = get_cmap(palette)(t)

    # Widths are given in reference pixels; matplotlib wants points.
    lw = segments.width * scale * (72.0 / DPI)

    fig = plt.figure(figsize=(width_px / DPI, height_px / DPI), dpi=DPI)
    fig.patch.set_facecolor(bg)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(bg)
    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect("equal")
    ax.axis("off")

    t0 = time.time()
    lc = LineCollection(
        segments.xy, linewidths=lw, colors=colors,
        capstyle="round", joinstyle="round", antialiaseds=True,
    )
    ax.add_collection(lc)

    # Wide letter spacing suits the poster look, and matplotlib has no
    # letter-spacing property, so the caller pads the string itself.
    if title:
        ax.text(0.5, 0.038, " ".join(title.upper()), transform=ax.transAxes,
                ha="center", va="bottom", color="#ffffff", alpha=0.80,
                fontsize=17 * scale, family="DejaVu Sans", zorder=5)
    if subtitle:
        ax.text(0.5, 0.022, subtitle, transform=ax.transAxes, ha="center",
                va="bottom", color="#ffffff", alpha=0.40,
                fontsize=8.5 * scale, family="DejaVu Sans", zorder=5)

    if legend:
        # Built here rather than by the caller so it always reports the values
        # actually used, including the percentile window the ramp was stretched
        # over rather than the raw data range.
        spec = dict(legend)
        spec.setdefault("value_min", lo)
        spec.setdefault("value_max", hi)
        if spec["mode"] == "categorical":
            base = hue_set(hues)
            picks = [to_rgb(base[(i * 7) % len(base)]) for i in range(8)]
            spec["swatches"] = picks
            spec["shade_ramp"] = _tributary_colors(
                np.zeros(64, dtype=np.int64), np.linspace(0, 1, 64),
                base, shade=shade)[:, :3]
        else:
            cmap = get_cmap(palette)
            spec["cmap"] = cmap.reversed() if invert_ramp else cmap
        legend_mod.draw(fig, spec, scale)

    if labels:
        fig.canvas.draw()          # labels need a renderer to measure against
        drawn = _draw_labels(ax, fig, labels, scale)
        if verbose:
            print(f"  placed {len(drawn)} of {len(labels)} river labels")

    fig.canvas.draw()
    if verbose:
        print(f"  rasterised {len(lw):,} segments in {time.time() - t0:.0f}s")

    rgb = np.asarray(fig.canvas.buffer_rgba(), dtype=np.float32)[..., :3] / 255.0
    plt.close(fig)

    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None

    if bloom > 0:
        t0 = time.time()
        sigma = bloom_sigma if bloom_sigma is not None else max(2.0, 4.0 * scale)
        rgb = _bloom(rgb, bloom, sigma, bloom_threshold)
        if verbose:
            print(f"  bloom sigma={sigma:.1f} amount={bloom:.2f} in {time.time() - t0:.0f}s")

    rgb = _vignette(rgb, vignette)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray((rgb * 255).round().astype(np.uint8))
    save_kw = {"dpi": (dpi_meta, dpi_meta)} if dpi_meta else {}
    img.save(out_path, **save_kw)
    if verbose:
        note = f" at {dpi_meta}dpi" if dpi_meta else ""
        print(f"  wrote {out_path}  ({rgb.shape[1]} x {rgb.shape[0]}px{note})")
    return out_path, (lo, hi)
