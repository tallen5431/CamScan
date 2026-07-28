import os
import cv2
import numpy as np
from typing import Tuple, List, Optional

DEBUG = bool(os.getenv("CAMSCAN_DEBUG", "0") == "1")

# Edge detection configuration (much simpler now)
EDGE_FINDER_CONFIG = {
    "min_area": 800.0,     # min contour area to consider
    "warp_size": 512,
    "max_aspect": 2.0,     # how elongated a "square" can be
    "border_margin_frac": 0.02,  # reject blobs hugging image border
}


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────

def _order_quad(pts: np.ndarray) -> np.ndarray:
    """Return the 4 corners as a proper clockwise cycle starting at the top-left.

    Ordering by angle around the centroid is robust to rotation. The previous
    sum/diff heuristic ties near 45° (the corners become axis-aligned diamond
    tips with equal x+y sums), which made it assign one corner twice and drop
    another — the degenerate quad then measured a ~15% short edge length.
    """
    pts = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
    if pts.shape[0] != 4:
        raise ValueError(f"_order_quad expected 4 points, got {pts.shape[0]}")

    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    ordered = pts[np.argsort(ang)]  # consistent cycle, no ties/drops

    # Enforce clockwise winding in image coordinates (y grows downward), so the
    # order is TL, TR, BR, BL after the top-left rotation below. The shoelace sum
    # is positive for a clockwise polygon in a y-down frame.
    area2 = 0.0
    for i in range(4):
        x1, y1 = ordered[i]
        x2, y2 = ordered[(i + 1) % 4]
        area2 += x1 * y2 - x2 * y1
    if area2 < 0:
        ordered = ordered[::-1]

    # Rotate the cycle so it starts at the top-left-most corner (min x+y) for a
    # stable, deterministic labelling.
    start = int(np.argmin(ordered[:, 0] + ordered[:, 1]))
    ordered = np.roll(ordered, -start, axis=0)
    return ordered.astype(np.float32)


def _angle_score(quad: np.ndarray) -> float:
    """Score how close the corners are to 90° (1.0 = perfect)."""
    pts = np.asarray(quad, dtype=np.float32)
    total = 0.0
    for i in range(4):
        a = pts[i] - pts[(i - 1) % 4]
        b = pts[(i + 1) % 4] - pts[i]
        denom = (np.linalg.norm(a) * np.linalg.norm(b) + 1e-6)
        cosang = np.dot(a, b) / denom
        cosang = np.clip(cosang, -1.0, 1.0)
        ang = np.degrees(np.arccos(cosang))
        total += (1.0 - abs(ang - 90.0) / 90.0)
    return total / 4.0


def _score_quad(quad: np.ndarray, img_shape) -> float:
    """Combined score: area, fill ratio, squareness, centrality."""
    quad = np.asarray(quad, dtype=np.float32).reshape(-1, 2)
    contour = quad.reshape(-1, 1, 2)
    area = cv2.contourArea(contour)
    if area <= 0:
        return 0.0

    x, y, w, h = cv2.boundingRect(contour)
    box_area = float(w * h) if w > 0 and h > 0 else 1.0
    fill_ratio = float(area) / box_area

    # aspect penalty
    aspect = max(w, h) / float(max(1, min(w, h)))
    aspect_penalty = max(0.0, aspect - 1.0)

    angle_score = _angle_score(quad)

    # centrality
    center_x, center_y = img_shape[1] / 2, img_shape[0] / 2
    quad_center = np.mean(quad, axis=0)
    dist_to_center = np.linalg.norm(quad_center - [center_x, center_y])
    max_dist = np.sqrt(center_x**2 + center_y**2)
    centrality_bonus = 1.0 - (dist_to_center / max_dist) * 0.25

    score = float(area * fill_ratio * (0.75 * angle_score + 0.25) / (1.0 + aspect_penalty))
    score *= centrality_bonus
    return score


def _square_mask(gray: np.ndarray, polarity: str = "dark") -> np.ndarray:
    """
    Build a clean mask for a square region.

    polarity:
        - "dark": find dark square on light background (outer black square)
        - "bright": find bright square on dark background (inner white squares)
    """
    # Light blur + contrast
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    eq = clahe.apply(blur)

    if polarity == "bright":
        # white squares → white blobs
        _, mask = cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        # black square → white blob
        _, mask = cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Clean up
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)

    return mask


def _line_intersect(l1, l2):
    """Intersect two lines given as (vx, vy, x0, y0) (unit direction + a point on it).

    Returns (x, y) or None when the lines are (near) parallel. Used to recover a
    quad corner as the crossing of its two adjacent edge lines — far more accurate
    than a single boundary vertex when the real corner is rounded or blurred.
    """
    vx1, vy1, x1, y1 = l1
    vx2, vy2, x2, y2 = l2
    det = vx1 * (-vy2) - (-vx2) * vy1
    if abs(det) < 1e-9:
        return None
    dx, dy = x2 - x1, y2 - y1
    t = (dx * (-vy2) - (-vx2) * dy) / det
    return (x1 + t * vx1, y1 + t * vy1)


def _refine_quad_by_edges(contour, coarse):
    """Refine a 4-corner quad by fitting each edge to a line and intersecting them.

    ``coarse`` is an ordered (TL,TR,BR,BL) quad that only approximately follows the
    marker (e.g. from approxPolyDP or minAreaRect). Every contour point is bucketed
    to its nearest coarse edge (corner regions trimmed off), each edge is robustly
    line-fit over its hundreds of boundary points, and adjacent edge lines are
    intersected to give the four corners at sub-pixel precision.

    Why this matters: thresholding + morphology round the square's corners inward,
    and a single approxPolyDP vertex sits on that rounded boundary — an error that
    grows on the foreshortened (far) edge of a tilted shot. Straight edges are not
    rounded, so intersecting the fitted edge lines recovers the TRUE corner the two
    edges would meet at. Returns an ordered quad (np.float32 4x2) or None.
    """
    q = np.asarray(coarse, dtype=np.float32).reshape(4, 2)
    pts = np.asarray(contour, dtype=np.float32).reshape(-1, 2)
    if pts.shape[0] < 16:
        return None

    # Assign every contour point to its nearest coarse edge (vectorised).
    a = q                                   # (4,2) edge starts
    ab = np.roll(q, -1, axis=0) - q         # (4,2) edge directions
    L2 = (ab ** 2).sum(1) + 1e-9            # (4,)
    diff = pts[None, :, :] - a[:, None, :]  # (4,N,2)
    t = (diff * ab[:, None, :]).sum(2) / L2[:, None]        # (4,N) projection param
    tc = np.clip(t, 0.0, 1.0)
    proj = a[:, None, :] + tc[:, :, None] * ab[:, None, :]  # (4,N,2)
    d = np.linalg.norm(pts[None, :, :] - proj, axis=2)      # (4,N)
    assign = np.argmin(d, axis=0)                           # (N,)

    lines = []
    for i in range(4):
        # Trim the corner-rounding regions: keep only the straight middle of each edge.
        sel = (assign == i) & (t[i] >= 0.15) & (t[i] <= 0.85)
        bpts = pts[sel]
        if bpts.shape[0] < 5:
            return None
        vx, vy, x0, y0 = cv2.fitLine(bpts, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((float(vx), float(vy), float(x0), float(y0)))

    refined = []
    for i in range(4):
        pt = _line_intersect(lines[(i - 1) % 4], lines[i])   # corner i = edge(i-1) ∩ edge(i)
        if pt is None:
            return None
        refined.append(pt)
    refined = np.asarray(refined, dtype=np.float32)

    # Guard against a degenerate fit flinging a corner away: each refined corner must
    # stay near its coarse counterpart (within ~30% of the marker's mean side).
    side = float(np.mean([np.linalg.norm(q[(i + 1) % 4] - q[i]) for i in range(4)]))
    if side <= 0 or np.max(np.linalg.norm(refined - q, axis=1)) > 0.3 * side:
        return None
    try:
        return _order_quad(refined)
    except ValueError:
        return None


def _quad_distortion(ordered) -> float:
    """Max corner-angle deviation from 90° for an ordered quad (perspective proxy)."""
    ordered = np.asarray(ordered, dtype=np.float32)
    max_dev = 0.0
    for i in range(4):
        a = ordered[(i - 1) % 4] - ordered[i]
        b = ordered[(i + 1) % 4] - ordered[i]
        na = float(np.linalg.norm(a)); nb = float(np.linalg.norm(b))
        if na < 1e-6 or nb < 1e-6:
            continue
        cosang = float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))
        max_dev = max(max_dev, abs(float(np.degrees(np.arccos(cosang))) - 90.0))
    return max_dev


def _contour_quad_and_distortion(contour):
    """Return (ordered_quad, distortion_deg) from a contour's true 4-corner shape.

    ``ordered_quad`` is the marker's TRUE projected quad (a trapezoid under camera
    tilt) ordered TL,TR,BR,BL — a list of (x, y) floats in the contour's frame — or
    None when no clean quad can be recovered. ``distortion_deg`` is the max corner
    angle deviation from 90°.

    A coarse quad is taken from approxPolyDP when it reduces cleanly to 4 points, and
    otherwise from minAreaRect (so a homography is still produced on noisy contours
    where approxPolyDP splinters). That coarse quad is then sharpened by edge-line
    fitting + intersection (see ``_refine_quad_by_edges``) for sub-pixel corners that
    resist the corner-rounding of a tilted/blurred shot. This is what a rectifying
    homography is built from and what drives the perspective-distortion confidence.
    """
    peri = cv2.arcLength(contour, True)
    if peri <= 0:
        return None, 0.0

    coarse = None
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    if len(approx) == 4:
        try:
            coarse = _order_quad(approx.reshape(-1, 2).astype(np.float32))
        except ValueError:
            coarse = None
    if coarse is None:
        # approxPolyDP didn't give a clean 4-gon (noisy/rounded boundary) — start from
        # the rotated bounding box so edge-fitting can still recover the true corners.
        try:
            coarse = _order_quad(cv2.boxPoints(cv2.minAreaRect(contour)))
        except ValueError:
            return None, 0.0

    refined = _refine_quad_by_edges(contour, coarse)
    ordered = refined if refined is not None else coarse
    quad = [(float(x), float(y)) for (x, y) in ordered]
    return quad, _quad_distortion(ordered)


# ─────────────────────────────────────────
# Main API: find_main_edges
# ─────────────────────────────────────────

def find_main_edges(
    crop: np.ndarray,
    max_edges: int = 10,
    warp: bool = False,
    warp_size: int = None,
    min_area: float = None,
    debug: bool = False,
    use_enhanced_preprocessing: bool = True,  # kept for compatibility (ignored)
    polarity: str = "dark",                   # "dark" outer square, "bright" inner
    metrics: dict = None,                     # optional out-param, see below
    allow_border: bool = False,               # skip the border-hugging rejection (full-frame pass)
):
    """
    Find the best quadrilateral in a cropped region.

    Designed for:
      - outer black square (polarity="dark")
      - inner white squares (polarity="bright") on smaller crops.

    Returns:
        overlay_crop_bgr, num_contours, warped_square, corners_in_crop

    `corners_in_crop` are (x, y) in crop coordinates.

    If a mutable ``metrics`` dict is passed, it is populated with
    ``distortion_deg`` (perspective foreshortening of the winning contour, in
    degrees). Backward compatible: existing callers pass nothing and are
    unaffected.
    """
    cfg = EDGE_FINDER_CONFIG
    if warp_size is None:
        warp_size = cfg["warp_size"]
    if min_area is None:
        min_area = cfg["min_area"]

    if crop is None or crop.size == 0:
        return crop, 0, None, None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask = _square_mask(gray, polarity=polarity)

    if debug and DEBUG:
        try:
            cv2.imshow(f"edge_mask_{polarity}", mask)
            cv2.waitKey(1)
        except Exception:
            pass

    h_img, w_img = gray.shape[:2]
    frame_area = float(h_img * w_img)
    margin_x = cfg["border_margin_frac"] * w_img
    margin_y = cfg["border_margin_frac"] * h_img

    # CHAIN_APPROX_NONE keeps every boundary pixel — the edge-line fit in
    # _contour_quad_and_distortion needs the dense edge points, not just the segment
    # endpoints CHAIN_APPROX_SIMPLE would collapse a straight edge to.
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[: max_edges]

    overlay = crop.copy()
    best_quad = None
    best_score = 0.0
    best_contour = None

    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue

        x, y, w, h = cv2.boundingRect(c)
        if w <= 0 or h <= 0:
            continue

        # Skip blobs hugging image borders
        if (
            x <= margin_x
            or y <= margin_y
            or x + w >= w_img - margin_x
            or y + h >= h_img - margin_y
        ):
            # For inner squares we *allow* close to border, but this crop is tight,
            # so only apply this margin for dark outer-square mode. `allow_border`
            # opts out entirely — used by the full-frame plain-square pass, where a
            # square that (correctly) fills the frame must NOT be rejected for touching
            # the edge.
            if polarity == "dark" and not allow_border:
                continue

        # Aspect filter
        aspect = max(w, h) / float(min(w, h))
        if aspect > cfg["max_aspect"]:
            continue

        # Use minAreaRect → always 4 points
        rect = cv2.minAreaRect(c)
        box = cv2.boxPoints(rect)

        try:
            ordered = _order_quad(box)
        except ValueError:
            continue

        score = _score_quad(ordered, gray.shape)
        if score > best_score:
            best_score = score
            best_quad = ordered
            best_contour = c

    # Report the winning contour's true (trapezoidal) quad and its perspective
    # foreshortening, so callers can build a rectifying homography and flag
    # low-confidence calibration. Both are None/0.0 when there's no clean quad.
    if metrics is not None:
        if best_contour is not None:
            quad, dist = _contour_quad_and_distortion(best_contour)
        else:
            quad, dist = None, 0.0
        metrics["quad"] = quad
        metrics["distortion_deg"] = dist

    if debug and DEBUG:
        print(f"[edge_finder] contours={len(contours)}, best_score={best_score:.1f}")

    corners = None
    if best_quad is not None:
        pts = best_quad.reshape(-1, 2)
        corners = [(int(float(x)), int(float(y))) for (x, y) in pts]

        cv2.polylines(
            overlay,
            [pts.astype(np.int32)],
            isClosed=True,
            color=(0, 200, 255),
            thickness=3,
            lineType=cv2.LINE_AA,
        )
        for j, (x, y) in enumerate(pts):
            cv2.circle(overlay, (int(x), int(y)), 6, (0, 0, 0), -1)
            cv2.circle(overlay, (int(x), int(y)), 4, (0, 200, 255), -1)
            cv2.putText(
                overlay,
                str(j),
                (int(x) + 6, int(y) - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

    warped = None
    if warp and best_quad is not None:
        pts_src = np.float32(best_quad)
        pts_dst = np.float32(
            [
                [0, 0],
                [warp_size - 1, 0],
                [warp_size - 1, warp_size - 1],
                [0, warp_size - 1],
            ]
        )
        try:
            M = cv2.getPerspectiveTransform(pts_src, pts_dst)
            warped = cv2.warpPerspective(crop, M, (warp_size, warp_size))
        except Exception:
            warped = None

    return overlay, len(contours), warped, corners
