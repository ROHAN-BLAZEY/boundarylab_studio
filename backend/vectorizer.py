"""Edge map -> SVG. Contours are extracted with OpenCV and simplified with Douglas-Peucker."""
import cv2

from .styles import PALETTE, NEON_COLORS


def edges_to_svg(edges, style="classic", stroke=1.0, scale=3, neon_color="magenta"):
    h, w = edges.shape
    c = PALETTE.get(style, PALETTE["classic"])
    color = NEON_COLORS.get(neon_color, NEON_COLORS["magenta"]) if style == "neon" else c["fg"]
    fg, bg = "rgb(%d,%d,%d)" % color, "rgb(%d,%d,%d)" % c["bg"]
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    parts = []
    for cnt in contours:
        if cv2.arcLength(cnt, False) < 8:
            continue
        pts = cv2.approxPolyDP(cnt, 0.9, False).reshape(-1, 2)
        if len(pts) >= 2:
            parts.append("M" + "L".join("%d %d" % (x, y) for x, y in pts))
    d = "".join(parts)
    glow = ""
    if style == "neon":
        glow = ('<defs><filter id="g" x="-10%" y="-10%" width="120%" height="120%">'
                '<feGaussianBlur stdDeviation="2.2"/></filter></defs>'
                f'<path d="{d}" fill="none" stroke="{fg}" stroke-width="{stroke + 1}" filter="url(#g)"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w * scale}" height="{h * scale}">'
            f'<rect width="{w}" height="{h}" fill="{bg}"/>{glow}'
            f'<path d="{d}" fill="none" stroke="{fg}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round"/></svg>')
