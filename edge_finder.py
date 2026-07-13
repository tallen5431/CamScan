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
    "roi_margin_frac": 0.15,     # used by fit_outer_square_on_full_image
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


def _perspective_distortion(contour) -> float:
    """Max deviation (degrees) of the contour's 4-corner angles from 90°.

    A fronto-parallel square projects to a square at any in-plane rotation
    (deviation ≈ 0). Camera tilt projects it to a trapezoid, so the corner
    angles move away from 90° — this grows with foreshortening, which is exactly
    when a single mm/px scale becomes unreliable. Returns 0.0 when the contour
    does not reduce to a clean quadrilateral (unknown → do not penalise).

    NOTE: computed from the raw contour, because the refined corners come from
    minAreaRect and are always a perfect rectangle (they hide perspective).
    """
    peri = cv2.arcLength(contour, True)
    if peri <= 0:
        return 0.0
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    if len(approx) != 4:
        return 0.0
    p = [approx[i][0].astype(np.float64) for i in range(4)]
    max_dev = 0.0
    for i in range(4):
        a = p[(i - 1) % 4] - p[i]
        b = p[(i + 1) % 4] - p[i]
        na = float(np.linalg.norm(a)); nb = float(np.linalg.norm(b))
        if na < 1e-6 or nb < 1e-6:
            continue
        cosang = float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))
        ang = float(np.degrees(np.arccos(cosang)))
        max_dev = max(max_dev, abs(ang - 90.0))
    return max_dev


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

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
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
            # so only apply this margin for dark outer-square mode.
            if polarity == "dark":
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

    # Report perspective foreshortening of the winning contour (0.0 if none / not
    # a clean quad) so callers can flag low-confidence calibration.
    if metrics is not None:
        metrics["distortion_deg"] = (
            _perspective_distortion(best_contour) if best_contour is not None else 0.0
        )

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


# ─────────────────────────────────────────
# Helper: refine outer square on full frame
# ─────────────────────────────────────────

def fit_outer_square_on_full_image(
    full_img_bgr: np.ndarray,
    outer_box: Tuple[int, int, int, int],
    warp: bool = False,
    warp_size: Optional[int] = None,
    margin_frac: Optional[float] = None,
    debug: bool = False,
    use_enhanced_preprocessing: bool = True,  # kept for compat, ignored
):
    """
    Refine the outer calibration square edges on the full frame.

    Args:
        full_img_bgr: Full original frame (BGR).
        outer_box:    (x, y, w, h) bounding box of the outer square from detect_squares.
        warp:         If True, also return a perspective-warped view of the square.
        warp_size:    Warped size (defaults to EDGE_FINDER_CONFIG["warp_size"]).
        margin_frac:  Extra margin around outer_box for the ROI (fraction of max(w, h)).

    Returns:
        overlay_full_bgr: Full image with refined quad drawn.
        warped_roi:       Warped square (or None).
        corners_full:     List of 4 (x, y) in full-image coordinates (or None).
    """
    if full_img_bgr is None or full_img_bgr.size == 0:
        return full_img_bgr, None, None

    h_img, w_img = full_img_bgr.shape[:2]
    x, y, w, h = map(int, outer_box)

    if warp_size is None:
        warp_size = EDGE_FINDER_CONFIG["warp_size"]

    if margin_frac is None:
        margin_frac = EDGE_FINDER_CONFIG.get("roi_margin_frac", 0.15)

    margin = int(max(w, h) * margin_frac)
    x0 = max(0, x - margin)
    y0 = max(0, y - margin)
    x1 = min(w_img, x + w + margin)
    y1 = min(h_img, y + h + margin)

    if x1 <= x0 or y1 <= y0:
        if debug:
            print("[fit_outer_square_on_full_image] Degenerate ROI, using original.")
        return full_img_bgr.copy(), None, None

    roi = full_img_bgr[y0:y1, x0:x1]

    # Use simplified edge finder on ROI (outer square is dark)
    _, _, warped, corners_roi = find_main_edges(
        roi,
        max_edges=10,
        warp=warp,
        warp_size=warp_size,
        min_area=None,
        debug=debug,
        polarity="dark",
    )

    overlay_full = full_img_bgr.copy()
    corners_full = None

    if corners_roi is not None and len(corners_roi) == 4:
        corners_full = [(cx + x0, cy + y0) for (cx, cy) in corners_roi]
        pts_full = np.array(corners_full, dtype=np.int32).reshape(-1, 1, 2)

        cv2.polylines(
            overlay_full,
            [pts_full],
            isClosed=True,
            color=(0, 0, 255),  # red outline on full image
            thickness=4,
            lineType=cv2.LINE_AA,
        )
        for j, (cx, cy) in enumerate(corners_full):
            cv2.circle(overlay_full, (cx, cy), 7, (0, 0, 0), -1)
            cv2.circle(overlay_full, (cx, cy), 4, (0, 255, 0), -1)
            cv2.putText(
                overlay_full,
                str(j),
                (cx + 6, cy - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

    return overlay_full, warped, corners_full
