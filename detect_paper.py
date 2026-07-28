"""
Paper-sheet detector for CamScan.

Finds the largest bright, roughly-rectangular quadrilateral in a photo — a sheet
of paper the customer laid beside/under their part — and returns its four image
corners. It is a FALLBACK scale reference used when no printed calibration square
is found: four corners give a full perspective homography (tilt correction), not
just a scale, which is what makes a sheet of paper a better reference than a ruler.

The detector never sets a scale on its own. It only proposes corners; the client
asks the user to confirm the real paper size (A4 / US Letter / custom) before the
corners become a scale — a coin and a sheet look nothing alike, but two paper
standards do, and only the user knows which they used.

Public:
  - detect_paper_sheet(img) -> dict | None
        {"corners": [[x,y] x4 ordered TL,TR,BR,BL], "aspect": float, "guess": "a4"|"letter"}
"""
from typing import Optional, Dict, Any, List

import cv2
import numpy as np

# A4 long/short = 297/210 = 1.414 ; US Letter = 279.4/215.9 = 1.294. Accept a wide band
# around them so perspective foreshortening of a real sheet still qualifies, while a
# square-ish or very long object (box, book spine, the part itself) is rejected.
_ASPECT_MIN = 1.15
_ASPECT_MAX = 1.90
_A4_ASPECT = 297.0 / 210.0
_LETTER_ASPECT = 279.4 / 215.9


def _order_corners(pts: np.ndarray) -> List[List[float]]:
    """Order 4 points as TL, TR, BR, BL by angle around the centroid.
    (The client re-orders defensively too, but a sane order keeps the JSON readable.)

    Angle-around-centroid is robust to rotation. The classic sum/diff trick ties near 45° — two
    corners share the min/max of x+y (and of x-y) — so argmin/argmax return the same index
    twice, assigning one corner two roles and dropping another (a degenerate quad → wrong
    homography/scale). This is the same fix edge_finder._order_quad already carries.
    """
    pts = np.asarray(pts, dtype=float).reshape(4, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    ordered = pts[np.argsort(ang)]                       # a full permutation — no ties/drops
    # Clockwise winding in image coords (y grows downward): a positive shoelace sum is clockwise.
    area2 = 0.0
    for i in range(4):
        x1, y1 = ordered[i]
        x2, y2 = ordered[(i + 1) % 4]
        area2 += x1 * y2 - x2 * y1
    if area2 < 0:
        ordered = ordered[::-1]
    start = int(np.argmin(ordered[:, 0] + ordered[:, 1]))   # rotate to start at top-left (min x+y)
    ordered = np.roll(ordered, -start, axis=0)
    return [[float(x), float(y)] for (x, y) in ordered]


def detect_paper_sheet(img: np.ndarray,
                       min_area_frac: float = 0.06,
                       max_area_frac: float = 0.94) -> Optional[Dict[str, Any]]:
    """Return the largest paper-like bright quadrilateral, or None.

    min_area_frac / max_area_frac bound the sheet's share of the frame so tiny bright
    specks and full-frame washes are ignored. Conservative by design: a false negative
    just means the user taps the 4 corners with the manual Paper tool, whereas a false
    positive would offer a wrong scale — so we'd rather miss than misfire.
    """
    if img is None or not hasattr(img, "shape") or img.size == 0:
        return None

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape[:2]
    frame_area = float(h * w)
    if frame_area <= 0:
        return None

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    # Otsu split: paper (bright) vs a darker backdrop. THRESH_BINARY (not inverted) keeps
    # the bright side as foreground. Close small gaps so a textured sheet stays one blob;
    # a dark part resting ON the sheet is an interior hole, invisible to RETR_EXTERNAL.
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, k, iterations=2)

    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_quad = None
    best_area = 0.0
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area_frac * frame_area or area > max_area_frac * frame_area:
            continue

        peri = cv2.arcLength(c, True)
        if peri <= 0:
            continue
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue

        quad = approx.reshape(4, 2).astype(np.float32)

        # Rectangularity: the contour must nearly fill its own minAreaRect, so a bright
        # blob that merely happens to have 4 hull corners (an L-shape, a splash) is out.
        (cx, cy), (rw, rh), ang = cv2.minAreaRect(quad)
        if rw <= 0 or rh <= 0:
            continue
        if area / (rw * rh) < 0.85:
            continue

        aspect = max(rw, rh) / min(rw, rh)
        if aspect < _ASPECT_MIN or aspect > _ASPECT_MAX:
            continue

        # Reject a sheet whose corners are clipped by the frame — its true corners aren't
        # visible, so any homography would be wrong. One corner near the edge is fine;
        # two or more means it's likely running off-frame.
        margin = 3.0
        clipped = sum(1 for (px, py) in quad
                      if px <= margin or py <= margin or px >= w - margin or py >= h - margin)
        if clipped >= 2:
            continue

        if area > best_area:
            best_area = area
            best_quad = quad

    if best_quad is None:
        return None

    (_, _), (rw, rh), _ = cv2.minAreaRect(best_quad)
    aspect = max(rw, rh) / max(1.0, min(rw, rh))
    guess = "a4" if abs(aspect - _A4_ASPECT) <= abs(aspect - _LETTER_ASPECT) else "letter"

    return {
        "corners": _order_corners(best_quad),
        "aspect": float(aspect),
        "guess": guess,
    }
