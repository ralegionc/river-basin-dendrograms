"""Colour ramps for elevation, built to hold up against a black ground.

Each ramp runs low elevation (outlet, trunk) to high elevation (headwaters).

The important constraint is at the dark end. Elevation and stroke width are
correlated, so the trunk river is both the thickest line and the lowest value.
If the ramp bottoms out near black the trunk dissolves into the background at
exactly the place the picture wants its most weight. So the low end of every
ramp here is a deep but clearly luminous colour, not a near black, and the
progression to the pale end is compressed into what actually reads on screen
and in print.
"""
from __future__ import annotations

from matplotlib.colors import LinearSegmentedColormap

RAMPS: dict[str, list[str]] = {
    # deep water to pale mint: the default
    "abyss": ["#12406e", "#166f9e", "#1ba0ac", "#48c9ad", "#a4e6c6", "#f0faf4"],
    # violet through ember to bone, the most anatomical of the set
    "ember": ["#5c1463", "#8b1a5c", "#c53a5a", "#ec6a33", "#f7ab45", "#ffeab8"],
    # glacial blues into white
    "ice": ["#123f6b", "#1a6fae", "#2f9ede", "#6cc8f2", "#aee2ff", "#ffffff"],
    # moss to acid green, jungle basins
    "verdant": ["#135c44", "#15704a", "#2f9a54", "#76bb52", "#c6e35c", "#f4f7cc"],
    # oxidised metal, warm and quiet
    "copper": ["#5e3117", "#7d4419", "#b06327", "#d98b39", "#f0b65e", "#ffe9c6"],
    # arterial reds, the nervous system look
    "sanguine": ["#61102b", "#8c1435", "#c02f42", "#e2603f", "#f49256", "#ffdcaa"],
    # single hue, for print
    "bone": ["#50505c", "#5e5e69", "#8b8b96", "#b9b9c2", "#dedee4", "#ffffff"],
    # cold trunk, hot headwaters
    "aurora": ["#0f4f60", "#12796f", "#1fa273", "#7bc857", "#e2c74c", "#ffeaa6"],
    # near monochrome cyan, very cold
    "cyan": ["#0d4d5c", "#12798c", "#19a8b4", "#4fd0d0", "#9ceae2", "#eafffb"],
}

#: Pure black is the default ground for every ramp.
DEFAULT_BG = "#000000"


def get_cmap(name: str):
    """Return a colormap by ramp name, falling back to matplotlib's registry."""
    if name in RAMPS:
        return LinearSegmentedColormap.from_list(name, RAMPS[name], N=1024)
    import matplotlib.pyplot as plt

    try:
        return plt.get_cmap(name)
    except (ValueError, KeyError):
        raise SystemExit(
            f"unknown palette {name!r}. Built in: {', '.join(sorted(RAMPS))} "
            "(any matplotlib colormap name also works)"
        ) from None


def available() -> list[str]:
    return sorted(RAMPS)


# ---------------------------------------------------------------------------
# Qualitative sets, for colouring by tributary system rather than by elevation.
# ---------------------------------------------------------------------------

#: Hues are ordered around the wheel; the renderer walks them with a stride so
#: that neighbouring tributaries never land on neighbouring hues.
HUE_SETS: dict[str, list[str]] = {
    # full spectrum, maximum separation between adjacent basins
    "prism": [
        "#ff3b5c", "#ff7a2f", "#ffc23d", "#eaf24a", "#9fe84a", "#4ad96b",
        "#2fd9a8", "#2fd2d9", "#39a8ff", "#4b6bff", "#8b5cff", "#c65cff",
        "#ff5cd0", "#ff5c8a", "#ff9a6b", "#ffd98a",
    ],
    # warm half of the wheel, embers and rust
    "coral": [
        "#ff3b3b", "#ff6a2f", "#ff9a3d", "#ffc24a", "#ffe86b", "#ff8a5c",
        "#e8552f", "#ffb07a", "#d93b5c", "#ff7aa8", "#c94a2f", "#ffd2a8",
    ],
    # cool half, water and ice
    "reef": [
        "#2fd9d9", "#39a8ff", "#4b6bff", "#6b8aff", "#2fd9a8", "#4ad98a",
        "#8b5cff", "#5cc8ff", "#2f9ed9", "#7ae8d0", "#a8c2ff", "#5ce8b0",
    ],
    # muted, closer to a printed atlas
    "faded": [
        "#d98a8a", "#d9b48a", "#c9d98a", "#8ad99a", "#8ad9d0", "#8ab4d9",
        "#9a8ad9", "#c98ad9", "#d98ab4", "#b4a88a", "#8ac9b4", "#a8a8d9",
    ],
}


def hue_set(name: str) -> list[str]:
    try:
        return HUE_SETS[name]
    except KeyError:
        raise SystemExit(
            f"unknown hue set {name!r}. Built in: {', '.join(sorted(HUE_SETS))}"
        ) from None


def hue_sets() -> list[str]:
    return sorted(HUE_SETS)
