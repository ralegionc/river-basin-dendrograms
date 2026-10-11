"""A key explaining what the colours and the line weights mean.

Three variables ride on every plate at once: hue, brightness and stroke width.
Without a key a reader has to guess, and the most common wrong guess is that
thickness and colour say the same thing. They never do. Width is always
drainage area; colour is whatever was asked for.

The key is deliberately an encoding key rather than a list of categories. In
tributary mode there may be 24 hues and in region mode 15,511, so enumerating
them would be a second map. Naming the channels is what a reader needs.

Everything is laid out top down from a single cursor in figure coordinates, so
the block keeps its proportions on any canvas aspect, including a letterboxed
print size.
"""
from __future__ import annotations

import numpy as np

FG = "#ffffff"


def _text(fig, x, y, s, size, alpha=0.8, weight="normal", ha="left"):
    fig.text(x, y, s, color=FG, alpha=alpha, fontsize=size, ha=ha,
             va="center", family="DejaVu Sans", zorder=7, weight=weight)


def _fmt(v, unit):
    """Compact, axis-style numbers. A key has no room for 5,912,761."""
    if unit == "m":
        return f"{v:,.0f} m"
    if unit == "km":
        return f"{v:,.0f} km"
    if unit == "km2":
        if abs(v) >= 1e6:
            return f"{v / 1e6:.1f}M km²"
        if abs(v) >= 1e3:
            return f"{v / 1e3:,.0f}k km²"
        return f"{v:,.0f} km²"
    if unit == "order":
        return f"order {v:,.0f}"
    if unit == "log10 m3/s":
        return f"{10 ** v:,.0f} m³/s"
    return f"{v:,.0f}"


def draw(fig, spec, scale=1.0, origin=(0.035, 0.30), width=0.155):
    """Draw the key with its top left corner at ``origin`` in figure coords."""
    x0, top = origin
    aspect = fig.get_figwidth() / fig.get_figheight()

    # Bars are sized as a fraction of the canvas while text is sized in points,
    # so the two only stay in proportion if they are tuned against each other.
    # These are set so that at the 4000px reference a bar is about 30px tall
    # and the body text about 18px, which is the ratio that reads as a caption
    # under a swatch rather than a label lost beside a slab.
    bar = 0.0075 * aspect
    gap = 0.004 * aspect
    line = 0.0105 * aspect
    s_head = 11.0 * scale
    s_body = 13.0 * scale

    y = top

    def header(label):
        nonlocal y
        _text(fig, x0, y, label, s_head, alpha=0.5, weight="bold")
        y -= line * 1.15

    def caption(label, alpha=0.45, right=None):
        nonlocal y
        _text(fig, x0, y, label, s_body, alpha=alpha)
        if right is not None:
            _text(fig, x0 + width, y, right, s_body, alpha=alpha, ha="right")
        y -= line

    def strip(image, interp="nearest"):
        nonlocal y
        ax = fig.add_axes([x0, y - bar, width, bar])
        ax.set_axis_off()
        ax.imshow(image, aspect="auto", interpolation=interp, zorder=7)
        y -= bar + gap * 2.2

    # ---- colour -----------------------------------------------------------
    header("COLOUR")
    if spec["mode"] == "categorical":
        strip(np.asarray(spec["swatches"])[None, :, :])
        caption(spec["category_label"])
        y -= gap * 0.4
        strip(np.asarray(spec["shade_ramp"])[None, :, :], interp="bilinear")
        caption(f"within one hue: {spec['value_label']}")
        caption(_fmt(spec["value_min"], spec["unit"]), alpha=0.35,
                right=_fmt(spec["value_max"], spec["unit"]))
    else:
        grad = np.linspace(0, 1, 256)[None, :]
        ax = fig.add_axes([x0, y - bar, width, bar])
        ax.set_axis_off()
        ax.imshow(grad, aspect="auto", cmap=spec["cmap"],
                  interpolation="bilinear", zorder=7)
        y -= bar + gap * 2.2
        caption(spec["value_label"])
        caption(_fmt(spec["value_min"], spec["unit"]), alpha=0.35,
                right=_fmt(spec["value_max"], spec["unit"]))

    # ---- width, the channel readers most often misread ---------------------
    y -= gap * 1.6
    header("LINE WIDTH")
    ax = fig.add_axes([x0, y - bar * 1.3, width, bar * 1.3])
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(-1.25, 1.25)
    t = np.linspace(0, 1, 200)
    # The same exponent the renderer uses, so the taper shown is the taper drawn.
    half = (spec["lw_min"] + (spec["lw_max"] - spec["lw_min"])
            * t ** spec.get("width_exp", 0.4)) / max(spec["lw_max"], 1e-6)
    ax.fill_between(t, -half, half, color=FG, alpha=0.7, linewidth=0, zorder=7)
    y -= bar * 1.3 + gap * 2.2
    caption("drainage area, in every mode")
    caption(_fmt(spec["area_min"], "km2"), alpha=0.35,
            right=_fmt(spec["area_max"], "km2"))
