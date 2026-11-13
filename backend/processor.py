"""OpenCV pipeline: Grayscale -> (auto contrast) -> Bilateral -> Sobel -> Canny -> clean-up."""
import base64
from dataclasses import dataclass, fields

import cv2
import numpy as np

from . import styles

MAX_WIDTH = 640


@dataclass
class Params:
    threshold1: int = 28      # Canny low (hysteresis) on a 0-255 gradient scale
    threshold2: int = 75      # Canny high
    smoothing: int = 30       # bilateral colour sigma
    cleanup: int = 14         # drop edge fragments smaller than N pixels
    thickness: int = 1
    auto_contrast: bool = True
    style: str = "classic"
    shading: int = 60         # drawing guide: pencil shading strength 0-100
    grid: int = 0             # proportion grid, 0 = off, else N x N

    @classmethod
    def from_dict(cls, d):
        p = cls()
        for f in fields(cls):
            if d and f.name in d:
                try:
                    v = d[f.name]
                    setattr(p, f.name, bool(v) if isinstance(getattr(p, f.name), bool)
                            else type(getattr(p, f.name))(v))
                except (TypeError, ValueError):
                    pass
        p.threshold2 = max(p.threshold2, p.threshold1)
        p.thickness = int(np.clip(p.thickness, 1, 4))
        if p.style not in styles.RENDERERS:
            p.style = "classic"
        return p


@dataclass
class Result:
    out: np.ndarray      # styled BGR image
    edges: np.ndarray    # uint8 0/255
    stages: dict         # name -> uint8 gray image


def prepare(bgr):
    h, w = bgr.shape[:2]
    if w > MAX_WIDTH:
        bgr = cv2.resize(bgr, (MAX_WIDTH, max(2, round(h * MAX_WIDTH / w))), interpolation=cv2.INTER_AREA)
    return bgr


def auto_contrast(gray):
    lo, hi = np.percentile(gray, (2, 98))
    scale = 255.0 / max(hi - lo, 40.0)
    return np.clip((gray.astype(np.float32) - lo) * scale, 0, 255).astype(np.uint8)


def drop_small(edges, min_px):
    if min_px <= 0:
        return edges
    _, labels, stats, _ = cv2.connectedComponentsWithStats(edges, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= min_px
    keep[0] = False
    return (keep[labels] * 255).astype(np.uint8)


def run(bgr, p: Params) -> Result:
    bgr = prepare(bgr)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if p.auto_contrast:
        gray = auto_contrast(gray)
    smooth = cv2.bilateralFilter(gray, 7, max(5, p.smoothing), 5)
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)
    sobel = np.clip(cv2.magnitude(gx, gy) / 4 * 1.6, 0, 255).astype(np.uint8)
    # Raw Sobel magnitude is ~4x the 0-255 scale the sliders use.
    edges = cv2.Canny(smooth, p.threshold1 * 4, p.threshold2 * 4, L2gradient=True)
    edges = drop_small(edges, p.cleanup)
    if p.thickness > 1:
        k = 2 * (p.thickness - 1) + 1
        edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    out = styles.render(smooth if p.style == "drawing" else gray, edges, p)
    return Result(out, edges, {"gray": gray, "bilateral": smooth, "sobel": sobel, "canny": edges})


def to_data_url(img, quality=78):
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()
