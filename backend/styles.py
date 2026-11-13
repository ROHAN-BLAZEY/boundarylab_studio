"""Procedural output styles. Every style takes the processed gray image and the
binary edge map and returns a BGR image. PALETTE (RGB) is shared with the vectorizer."""
import cv2
import numpy as np

STYLE_NAMES = {
    "classic": "Classic outline",
    "blueprint": "Blueprint",
    "sketch": "Coloring book",
    "neon": "Neon glow",
    "drawing": "Drawing guide",
}

# RGB colours: used by the raster renderer and by the SVG exporter.
PALETTE = {
    "classic": {"bg": (10, 12, 11), "fg": (255, 255, 255)},
    "blueprint": {"bg": (23, 74, 140), "fg": (219, 234, 254)},
    "sketch": {"bg": (251, 248, 239), "fg": (30, 63, 48)},
    "neon": {"bg": (12, 8, 24), "fg": (255, 60, 200)},
    "drawing": {"bg": (247, 244, 235), "fg": (38, 42, 40)},
}


def _bgr(rgb):
    return (rgb[2], rgb[1], rgb[0])


def _solid(shape, rgb):
    img = np.empty((shape[0], shape[1], 3), np.uint8)
    img[:] = _bgr(rgb)
    return img


def _lines(img, mask, rgb):
    img[mask > 0] = _bgr(rgb)
    return img


def classic(gray, edges, p):
    return _lines(_solid(edges.shape, PALETTE["classic"]["bg"]), edges, PALETTE["classic"]["fg"])


def blueprint(gray, edges, p):
    c = PALETTE["blueprint"]
    img = _solid(edges.shape, c["bg"])
    h, w = edges.shape
    grid = img.copy()
    for x in range(0, w, 16):
        cv2.line(grid, (x, 0), (x, h), _bgr(c["fg"]), 1)
    for y in range(0, h, 16):
        cv2.line(grid, (0, y), (w, y), _bgr(c["fg"]), 1)
    img = cv2.addWeighted(grid, 0.16, img, 0.84, 0)
    return _lines(img, edges, c["fg"])


def sketch(gray, edges, p):
    c = PALETTE["sketch"]
    return _lines(_solid(edges.shape, c["bg"]), edges, c["fg"])


def neon(gray, edges, p):
    c = PALETTE["neon"]
    base = _solid(edges.shape, c["bg"]).astype(np.int16)
    glow = np.zeros(base.shape, np.uint8)
    _lines(glow, edges, c["fg"])
    wide = cv2.GaussianBlur(glow, (0, 0), 5).astype(np.int16)
    tight = cv2.GaussianBlur(glow, (0, 0), 1.5).astype(np.int16)
    core = np.zeros(base.shape, np.uint8)
    _lines(core, edges, (255, 220, 245))
    out = base + wide * 2 + tight + glow.astype(np.int16) + (core * 0.55).astype(np.int16)
    return np.clip(out, 0, 255).astype(np.uint8)


def drawing(gray, edges, p):
    """Pencil-style drawing guide: soft graphite shading plus firm contour lines."""
    c = PALETTE["drawing"]
    gray = cv2.GaussianBlur(gray, (0, 0), 1.6)
    blur = cv2.GaussianBlur(255 - gray, (0, 0), max(gray.shape) / 40)
    dodge = cv2.divide(gray, 255 - blur, scale=256)           # classic pencil-sketch dodge blend
    s = float(np.clip(p.shading, 0, 100)) / 100.0
    shade = (255 - (255 - dodge.astype(np.float32)) * s * 1.2).clip(0, 255)
    paper = np.array(_bgr(c["bg"]), np.float32) / 255.0
    img = shade[..., None] / 255.0 * paper * 255.0
    line = cv2.GaussianBlur(cv2.dilate(edges, np.ones((2, 2), np.uint8)), (0, 0), 0.6)
    line = line.astype(np.float32)[..., None] / 255.0 * 0.85
    ink = np.array(_bgr(c["fg"]), np.float32)
    return (img * (1 - line) + ink * line).clip(0, 255).astype(np.uint8)


def grid_overlay(img, n):
    """Proportion grid (n x n) with column letters and row numbers, for drawing."""
    if n < 2:
        return img
    h, w = img.shape[:2]
    over = img.copy()
    col = (106, 196, 233)  # gold, BGR
    for i in range(1, n):
        cv2.line(over, (w * i // n, 0), (w * i // n, h), col, 1, cv2.LINE_AA)
        cv2.line(over, (0, h * i // n), (w, h * i // n), col, 1, cv2.LINE_AA)
    out = cv2.addWeighted(over, 0.8, img, 0.2, 0)
    for i in range(n):
        cv2.putText(out, chr(65 + i), (w * i // n + 4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
        cv2.putText(out, str(i + 1), (3, h * i // n + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
    return out


RENDERERS = {"classic": classic, "blueprint": blueprint, "sketch": sketch, "neon": neon, "drawing": drawing}


def render(gray, edges, p):
    img = RENDERERS.get(p.style, classic)(gray, edges, p)
    return grid_overlay(img, int(p.grid))
