"""Paper sizes, for output meant to be printed rather than looked at on screen.

Rendering to "4000 pixels wide" is fine for a screen but useless at a print
shop, which wants a physical size at a real dpi. These helpers turn a paper
name and a dpi into an exact pixel canvas, and the renderer letterboxes the
basin inside it so the file matches the sheet it will be printed on.
"""
from __future__ import annotations

#: Short and long edge in millimetres.
SIZES_MM: dict[str, tuple[float, float]] = {
    "a5": (148.0, 210.0),
    "a4": (210.0, 297.0),
    "a3": (297.0, 420.0),
    "a2": (420.0, 594.0),
    "a1": (594.0, 841.0),
    "a0": (841.0, 1189.0),
    "letter": (215.9, 279.4),
    "legal": (215.9, 355.6),
    "tabloid": (279.4, 431.8),
    "square30": (300.0, 300.0),
    "square50": (500.0, 500.0),
    "square70": (700.0, 700.0),
}

MM_PER_INCH = 25.4


def available() -> list[str]:
    return list(SIZES_MM)


def canvas_px(name: str, dpi: int, data_aspect: float) -> tuple[int, int, str]:
    """Pixel canvas for a paper size, oriented to suit the basin.

    ``data_aspect`` is height over width of the basin. The sheet is turned to
    whichever orientation wastes less paper, so a long thin basin like the
    Ganges lands on a landscape sheet without being asked.
    """
    try:
        short_mm, long_mm = SIZES_MM[name]
    except KeyError:
        raise SystemExit(
            f"unknown paper size {name!r}. Known: {', '.join(SIZES_MM)}"
        ) from None
    if dpi <= 0:
        raise SystemExit("--dpi must be positive")

    portrait = (short_mm, long_mm)
    landscape = (long_mm, short_mm)

    # Pick the orientation whose aspect is closer to the basin's, which is the
    # one where the fitted image leaves less empty paper.
    def waste(size):
        w, h = size
        sheet = h / w
        return max(sheet / data_aspect, data_aspect / sheet)

    w_mm, h_mm = min((portrait, landscape), key=waste)
    orient = "portrait" if h_mm > w_mm else "landscape"
    w_px = int(round(w_mm / MM_PER_INCH * dpi))
    h_px = int(round(h_mm / MM_PER_INCH * dpi))
    return w_px, h_px, f"{name} {orient} {w_mm:.0f}x{h_mm:.0f}mm at {dpi}dpi"
