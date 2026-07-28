"""Automatic part-outline extraction for CamScan's Outline tool.

Given a photo and (ideally) a seed point on the part, segment that object from the
background and return its outer boundary as a simplified polygon in image pixel
coordinates. The client turns that into an editable, closed outline — so a customer
gets a near-instant trace to nudge instead of clicking every point by hand.

Deterministic and fast: a border-ring background colour model (robust to a central
object) + Otsu on the colour-distance map + connected components + approxPolyDP. The
seed disambiguates which object to trace, which matters because the calibration card
is itself "foreground" against the surface — a seed on the part ignores the card.
"""
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np


def auto_outline(
    img_bgr: np.ndarray,
    seed: Optional[Sequence[float]] = None,
    exclude_boxes: Sequence[Tuple[float, float, float, float]] = (),
    simplify: float = 0.006,
    max_points: int = 120,
    downscale_to: int = 1000,
) -> Optional[List[List[float]]]:
    """Return the part's outer boundary as [[x, y], ...] in IMAGE pixel coords, or None.

    seed          : (x, y) on the part (image coords). Strongly recommended — without it
                    the largest non-excluded foreground blob is used, which may be the card.
    exclude_boxes : (x, y, w, h) regions to blank out (e.g. the calibration marker).
    simplify      : approxPolyDP epsilon as a fraction of the contour perimeter.
    max_points    : cap on returned vertices (epsilon grows until met) — keeps it editable.
    """
    if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
        return None
    H, W = img_bgr.shape[:2]
    scale = min(1.0, float(downscale_to) / float(max(H, W)))
    small = (cv2.resize(img_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             if scale < 1.0 else img_bgr.copy())
    sh, sw = small.shape[:2]

    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)

    # Background colour model from a border ring — robust when the part sits centrally.
    b = max(2, int(round(0.03 * min(sh, sw))))
    ring = np.ones((sh, sw), np.uint8)
    ring[b:sh - b, b:sw - b] = 0
    ring_px = lab[ring.astype(bool)]
    if ring_px.size == 0:
        return None
    bg = np.median(ring_px, axis=0)
    dist = np.linalg.norm(lab - bg, axis=2)
    dist_u8 = cv2.normalize(dist, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, fg = cv2.threshold(dist_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k, iterations=2)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k, iterations=1)

    # Blank out excluded regions (the calibration marker), padded a little.
    for box in (exclude_boxes or ()):
        try:
            bx, by, bw, bh = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        x0 = int(bx * scale) - 4; y0 = int(by * scale) - 4
        x1 = int((bx + bw) * scale) + 4; y1 = int((by + bh) * scale) + 4
        cv2.rectangle(fg, (max(0, x0), max(0, y0)), (min(sw, x1), min(sh, y1)), 0, -1)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    if n <= 1:
        return None

    target = _pick_component(labels, stats, n, seed, scale, sw, sh)
    if target is None:
        return None

    # Outer boundary only (fill interior detail — holes are captured with the circle tool).
    mask = (labels == target).astype(np.uint8) * 255
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.003 * sh * sw:
        return None

    poly = _simplify(c, simplify, max_points)
    if poly is None or len(poly) < 3:
        return None
    return [[float(x) / scale, float(y) / scale] for (x, y) in poly]


def _pick_component(labels, stats, n, seed, scale, sw, sh):
    """Choose the component to trace: the seed's object, else the largest non-trivial one.

    When a seed is given we trace ONLY what the user tapped (or a blob just beside it) and
    return None if they tapped empty space — tapping nothing must not grab a random object.
    """
    if seed is not None:
        try:
            sx = int(round(float(seed[0]) * scale)); sy = int(round(float(seed[1]) * scale))
        except (TypeError, ValueError, IndexError):
            return None
        if not (0 <= sx < sw and 0 <= sy < sh):
            return None
        lbl = int(labels[sy, sx])
        if lbl != 0:
            return lbl
        # Seed landed just off the object (a highlight/hole edge): take the nearest blob.
        r = max(3, int(0.02 * min(sw, sh)))
        win = labels[max(0, sy - r):sy + r, max(0, sx - r):sx + r]
        vals, counts = np.unique(win[win != 0], return_counts=True)
        return int(vals[counts.argmax()]) if vals.size else None

    best, best_area = None, 0
    total = float(sh * sw)
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < 0.005 * total or area > 0.9 * total:   # skip speckle and background inversions
            continue
        if area > best_area:
            best, best_area = i, area
    return best


def _simplify(contour, simplify, max_points):
    peri = cv2.arcLength(contour, True)
    if peri <= 0:
        return None
    eps = max(1.0, simplify * peri)
    approx = cv2.approxPolyDP(contour, eps, True).reshape(-1, 2)
    guard = 0
    while len(approx) > max_points and guard < 20:
        eps *= 1.3
        approx = cv2.approxPolyDP(contour, eps, True).reshape(-1, 2)
        guard += 1
    return approx
