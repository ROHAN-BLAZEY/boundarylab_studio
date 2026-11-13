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
    rotation: int = 0         # clockwise rotation in degrees
    contrast_method: str = "percentile"
    smoothing_method: str = "bilateral"
    edge_method: str = "canny"
    morphology: str = "none"
    neon_color: str = "magenta"
    inspect_stage: int = -1

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
        p.rotation = int(np.clip(p.rotation, 0, 100))
        p.contrast_method = p.contrast_method if p.contrast_method in ("none", "percentile", "histogram") else "percentile"
        p.smoothing_method = p.smoothing_method if p.smoothing_method in ("none", "bilateral", "gaussian", "median") else "bilateral"
        p.edge_method = p.edge_method if p.edge_method in ("canny", "sobel", "laplacian", "threshold") else "canny"
        p.morphology = p.morphology if p.morphology in ("none", "open", "close") else "none"
        p.neon_color = p.neon_color if p.neon_color in styles.NEON_COLORS else "magenta"
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
    if p.rotation:
        h, w = bgr.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), -p.rotation, 1.0)
        bgr = cv2.warpAffine(bgr, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    gray_raw = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = gray_raw.copy()
    if p.auto_contrast and p.contrast_method == "percentile":
        gray = auto_contrast(gray)
    elif p.auto_contrast and p.contrast_method == "histogram":
        gray = cv2.equalizeHist(gray)
    if p.smoothing_method == "bilateral":
        smooth = cv2.bilateralFilter(gray, 7, max(5, p.smoothing), 5)
    elif p.smoothing_method == "gaussian":
        smooth = cv2.GaussianBlur(gray, (5, 5), max(0.5, p.smoothing / 20))
    elif p.smoothing_method == "median":
        smooth = cv2.medianBlur(gray, 5)
    else:
        smooth = gray
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)
    sobel = np.clip(cv2.magnitude(gx, gy) / 4 * 1.6, 0, 255).astype(np.uint8)
    if p.edge_method == "sobel":
        edges = cv2.threshold(sobel, p.threshold1, 255, cv2.THRESH_BINARY)[1]
    elif p.edge_method == "laplacian":
        lap = cv2.Laplacian(smooth, cv2.CV_16S, ksize=3)
        edges = cv2.convertScaleAbs(lap)
        edges = cv2.threshold(edges, p.threshold1, 255, cv2.THRESH_BINARY)[1]
    elif p.edge_method == "threshold":
        edges = cv2.adaptiveThreshold(smooth, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                      cv2.THRESH_BINARY_INV, 11, 2)
    else:
        edges = cv2.Canny(smooth, p.threshold1 * 4, p.threshold2 * 4, L2gradient=True)
    threshold = edges.copy()
    edges = drop_small(edges, p.cleanup)
    if p.morphology != "none":
        op = cv2.MORPH_OPEN if p.morphology == "open" else cv2.MORPH_CLOSE
        edges = cv2.morphologyEx(edges, op, np.ones((3, 3), np.uint8))
    morph = edges.copy()
    if p.thickness > 1:
        k = 2 * (p.thickness - 1) + 1
        edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    contour_view = np.zeros_like(edges)
    if p.inspect_stage == 7:
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(contour_view, contours, -1, 255, 1)
    out = styles.render(smooth if p.style == "drawing" else gray, edges, p)
    return Result(out, edges, {"grayscale": gray_raw, "contrast": gray, "smoothed": smooth,
        "sobel": sobel, "threshold": threshold, "edges": edges, "morphology": morph,
        "contours": contour_view})


def to_data_url(img, quality=78):
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()
