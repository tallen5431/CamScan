"""
Lean calibration square detector for CamScan.

Public:
  - detect_dark_squares(img, ...)
  - detect_dark_squares_robust(img, ...)
  - draw_squares(img, detections, ...)

Detections are tuples:
  (score, x, y, w, h, mean_val)

When enforce_calibration_pattern=True, tries to find:
  - 4 inner pads of similar size in a compact 2x2-like cluster
  - 1 outer square (padded bounding box around those 4)

Order in pattern mode:
  [outer, pad1, pad2, pad3, pad4]
"""

from typing import List, Tuple, Optional
from itertools import combinations

import cv2
import numpy as np

Detection = Tuple[float, int, int, int, int, float]

# Defaults - IMPROVED for better detection reliability
MIN_AREA_DEFAULT        = 50    # LOWERED from 150 to catch smaller inner squares
MAX_AREA_RATIO_DEFAULT  = 0.6
MAX_ASPECT_DEFAULT      = 3.0

MIN_FILL_RATIO_DEFAULT  = 0.6  # Already good - tolerates slight distortions
MIN_HULL_RATIO_DEFAULT  = 0.8  # Already good
MIN_COMPACTNESS_DEFAULT = 0.5  # Already good

PATTERN_MAX_CANDIDATES  = 18   # top-N for 4-pad search


# ─────────────────────────────
# Basic helpers
# ─────────────────────────────

def _prepare_gray(img: np.ndarray, enhance_contrast: bool = True) -> np.ndarray:
    """
    BGR→GRAY + optional CLAHE + light blur.

    IMPROVED: Added CLAHE for better contrast in varying lighting conditions.
    """
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # Apply CLAHE for better local contrast (helps with dark backgrounds)
    if enhance_contrast:
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

    return cv2.GaussianBlur(gray, (5, 5), 0)


def _dark_mask(gray: np.ndarray) -> np.ndarray:
    """Otsu dark-foreground mask + light morphology. Detects dark pads/squares (e.g. on a
    glary surface where the calibration pads are not the brightest regions)."""
    _, dark = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, k, iterations=1)
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN,  k, iterations=1)
    return dark


def _bright_mask(gray: np.ndarray) -> np.ndarray:
    """Bright-foreground mask (the inverse of the dark mask) — the default for our printed
    calibration square, whose four pads are the bright regions."""
    return cv2.bitwise_not(_dark_mask(gray))


def _iou_xywh(a, b) -> float:
    """IoU for XYWH boxes."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b

    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)

    if x2 <= x1 or y2 <= y1:
        return 0.0

    inter = (x2 - x1) * (y2 - y1)
    ua = aw * ah
    ub = bw * bh
    union = ua + ub - inter
    return inter / union if union > 0 else 0.0


# ─────────────────────────────
# 4-pad pattern
# ─────────────────────────────

def _find_4pad_pattern(
    candidates: List[Detection],
    size_ratio_max: float = 2.5,   # IMPROVED from 2.0 - more tolerant of size variations
    pad_frac: float = 0.25,         # IMPROVED from 0.2 - more padding for outer square
    debug: bool = False,
) -> Optional[List[Detection]]:
    """
    Find 4 pads (IMPROVED for real-world tolerance):
      - similar area (allows up to 2.5x size variation)
      - compact, roughly square cluster
      - reasonably uniform distances

    Return [outer, pad1..pad4] or None.

    IMPROVEMENTS:
    - size_ratio_max: 2.0 → 2.5 (tolerates more size variation)
    - pad_frac: 0.2 → 0.25 (adds more padding to outer square)
    """
    n = len(candidates)
    if n < 4:
        if debug:
            print("[4pad] Not enough candidates.")
        return None

    # Pack to arrays
    scores = np.array([c[0] for c in candidates], dtype=float)
    xs     = np.array([c[1] for c in candidates], dtype=float)
    ys     = np.array([c[2] for c in candidates], dtype=float)
    ws     = np.array([c[3] for c in candidates], dtype=float)
    hs     = np.array([c[4] for c in candidates], dtype=float)
    areas  = ws * hs

    # Top-N by score
    keep = np.argsort(-scores)[:PATTERN_MAX_CANDIDATES]
    xs_k, ys_k, ws_k, hs_k = xs[keep], ys[keep], ws[keep], hs[keep]
    areas_k, scores_k = areas[keep], scores[keep]

    if debug:
        print(f"[4pad] Using top {len(keep)} candidates")

    # Centers + pairwise distances once
    cx_k = xs_k + ws_k / 2.0
    cy_k = ys_k + hs_k / 2.0
    pts  = np.stack([cx_k, cy_k], axis=1)

    diff     = pts[:, None, :] - pts[None, :, :]
    dist_all = np.linalg.norm(diff, axis=-1)

    best_score = -1e9
    best_idx_local = None

    for combo in combinations(range(len(keep)), 4):
        idxs = np.fromiter(combo, dtype=int)

        a4 = areas_k[idxs]
        amin = float(a4.min())
        amax = float(a4.max())
        if amin <= 0:
            continue

        ar = amax / amin
        if ar > size_ratio_max:
            continue
        size_pen = (ar - 1.0) ** 2

        cx4 = cx_k[idxs]
        cy4 = cy_k[idxs]
        span_x = cx4.max() - cx4.min()
        span_y = cy4.max() - cy4.min()
        if span_x <= 0 or span_y <= 0:
            continue

        mean_side = float(np.sqrt(a4.mean()))
        if span_x < mean_side * 0.6 or span_y < mean_side * 0.6:
            continue

        aspect_cluster = max(span_x / span_y, span_y / span_x)
        layout_pen = (aspect_cluster - 1.0) ** 2

        dsub  = dist_all[np.ix_(idxs, idxs)]
        dists = dsub[dsub > 1e-6]
        mean_d = dists.mean()
        if mean_d < mean_side * 0.5:
            continue
        rel_std = dists.std() / max(mean_d, 1e-6)

        sm = scores_k[idxs].mean()
        score = (
            sm
            - 20.0 * size_pen
            - 10.0 * layout_pen
            - 5.0  * rel_std
        )

        if score > best_score:
            best_score = score
            best_idx_local = idxs

    if best_idx_local is None:
        if debug:
            print("[4pad] No good 4-pad pattern.")
        return None

    if debug:
        print(f"[4pad] Best score: {best_score:.3f}, local idxs: {best_idx_local}")

    inner_idxs = keep[best_idx_local]

    # Outer box from 4 pads
    x0s = xs[inner_idxs]
    y0s = ys[inner_idxs]
    x1s = xs[inner_idxs] + ws[inner_idxs]
    y1s = ys[inner_idxs] + hs[inner_idxs]

    x_min = float(x0s.min())
    y_min = float(y0s.min())
    x_max = float(x1s.max())
    y_max = float(y1s.max())

    w_outer = x_max - x_min
    h_outer = y_max - y_min

    x_min_p = x_min - pad_frac * w_outer
    y_min_p = y_min - pad_frac * h_outer
    x_max_p = x_max + pad_frac * w_outer
    y_max_p = y_max + pad_frac * h_outer

    x_min_p = max(0.0, x_min_p)
    y_min_p = max(0.0, y_min_p)
    w_outer_p = max(1.0, x_max_p - x_min_p)
    h_outer_p = max(1.0, y_max_p - y_min_p)

    inner_dets   = [candidates[i] for i in inner_idxs]
    inner_scores = [d[0] for d in inner_dets]
    inner_means  = [d[5] for d in inner_dets]

    outer_det: Detection = (
        float(np.mean(inner_scores)),
        int(round(x_min_p)),
        int(round(y_min_p)),
        int(round(w_outer_p)),
        int(round(h_outer_p)),
        float(np.mean(inner_means)) if inner_means else 0.0,
    )

    # Stable ordering: top→bottom, left→right
    centers = [
        (d[1] + 0.5 * d[3], d[2] + 0.5 * d[4]) for d in inner_dets
    ]
    order = sorted(range(len(inner_dets)), key=lambda i: (centers[i][1], centers[i][0]))
    inner_sorted = [inner_dets[i] for i in order]

    return [outer_det] + inner_sorted


# ─────────────────────────────
# Main detector
# ─────────────────────────────

def detect_dark_squares(
    img: np.ndarray,
    *,
    min_area: float = MIN_AREA_DEFAULT,
    max_area_ratio: float = MAX_AREA_RATIO_DEFAULT,
    max_aspect: float = MAX_ASPECT_DEFAULT,
    brightness_thresh: Optional[int] = None,
    approx_eps: float = 0.02,
    max_results: int = 25,
    # Shape quality:
    min_fill_ratio: float = MIN_FILL_RATIO_DEFAULT,
    min_hull_ratio: float = MIN_HULL_RATIO_DEFAULT,
    min_compactness: float = MIN_COMPACTNESS_DEFAULT,
    border_margin_frac: float = 0.01,
    polarity: str = "bright",              # your use-case: "bright"
    enforce_calibration_pattern: bool = False,
    calibration_outer_only: bool = False,
    debug: bool = False,
) -> List[Detection]:
    """
    Detect bright square-ish pads and optionally find the
    4-pad + outer-square calibration pattern.
    """
    if img is None or img.size == 0:
        return []

    gray = _prepare_gray(img)
    h_img, w_img = gray.shape[:2]
    frame_area = float(h_img * w_img)

    # Build the detection mask(s) by polarity: 'bright' (default, our printed square's pads),
    # 'dark' (dark pads/squares), or 'both' (candidates from each). Previously polarity was
    # ignored and only the bright mask was ever used, so the 'dark'/'both' fallback strategies
    # in calibration_core were silent no-ops.
    _pol = (polarity or "bright").lower()
    _specs = []   # (mask, is_bright) — the flag lets the brightness gate below stay polarity-aware
    if _pol in ("bright", "both"):
        _specs.append((_bright_mask(gray), True))
    if _pol in ("dark", "both"):
        _specs.append((_dark_mask(gray), False))
    if not _specs:
        _specs.append((_bright_mask(gray), True))

    # IMPROVED: Adaptive brightness thresholds using percentiles
    # Works better for dark backgrounds and varying lighting
    auto_mean = float(np.mean(gray))
    if brightness_thresh is None:
        # Use percentile-based threshold for better adaptivity
        bright_percentile = float(np.percentile(gray, 70))  # 70th percentile
        # Accept bright regions above 90% of the bright percentile
        bright_thresh = int(max(bright_percentile * 0.9, auto_mean * 1.02))
        use_brightness = True

        if debug:
            print(f"[Adaptive threshold] bright_thresh={bright_thresh}, "
                  f"mean={auto_mean:.1f}, p70={bright_percentile:.1f}")
    elif brightness_thresh == 0:
        bright_thresh = 0
        use_brightness = False
    else:
        bright_thresh = int(brightness_thresh)
        use_brightness = True

    margin_x = border_margin_frac * w_img
    margin_y = border_margin_frac * h_img
    eps = 1e-6

    candidates: List[Detection] = []

    # Collect contours from each polarity mask, tagged with the polarity they came from.
    contours = []   # (contour, is_bright)
    for _m, _isb in _specs:
        _cs, _ = cv2.findContours(_m, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend((c, _isb) for c in _cs)
    if debug:
        print(f"[detect_dark_squares] polarity={_pol}, contours: {len(contours)}")

    for c, _is_bright in contours:
        area = cv2.contourArea(c)
        if area <= 0:
            continue

        if area < min_area:
            continue
        if area / frame_area > max_area_ratio:
            continue

        x, y, w, h = cv2.boundingRect(c)
        if w <= 0 or h <= 0:
            continue

        if border_margin_frac > 0.0:
            if (
                x <= margin_x
                or y <= margin_y
                or x + w >= w_img - margin_x
                or y + h >= h_img - margin_y
            ):
                continue

        short_side = float(min(w, h))
        long_side  = float(max(w, h))
        aspect     = long_side / short_side
        if aspect > max_aspect:
            continue

        rect_area  = float(w * h)
        fill_ratio = area / (rect_area + eps)
        if fill_ratio < min_fill_ratio:
            continue

        peri   = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, approx_eps * peri, True)
        if len(approx) < 4:
            continue

        hull       = cv2.convexHull(c)
        hull_area  = cv2.contourArea(hull)
        hull_ratio = area / (hull_area + eps) if hull_area > 0 else 0.0
        if hull_ratio < min_hull_ratio:
            continue

        compactness = 0.0
        if peri > 0:
            compactness = 4.0 * np.pi * area / (peri * peri + eps)
        if compactness < min_compactness:
            continue

        roi = gray[y : y + h, x : x + w]
        if roi.size == 0:
            continue
        mean_val = float(np.mean(roi))

        # Polarity-aware brightness gate: a BRIGHT pad must be bright enough; a DARK pad (the
        # glary-surface fallback) is the inverse — it must be darker than the image average.
        # Applying the bright gate to dark candidates rejected every one, which left the
        # dark/both fallbacks non-functional even after the dark mask was built.
        if use_brightness:
            if _is_bright and mean_val < bright_thresh:
                continue
            if (not _is_bright) and mean_val >= auto_mean:
                continue

        # simple score: geometric + brightness + relative size
        size_ratio = area / frame_area
        size_score = min(1.0, size_ratio / 0.1)
        geom_score = (fill_ratio + hull_ratio + compactness) / 3.0
        intens_score = max(0.0, mean_val / 255.0)

        score = 0.4 * geom_score + 0.4 * intens_score + 0.2 * size_score
        candidates.append((score, int(x), int(y), int(w), int(h), mean_val))

    # Sort by score
    candidates.sort(key=lambda t: t[0], reverse=True)

    # Pattern mode: try outer + 4 pads first
    if enforce_calibration_pattern and candidates:
        pattern = _find_4pad_pattern(candidates, debug=debug)
        if pattern is not None:
            if calibration_outer_only:
                return [pattern[0]][:max_results]
            return pattern[:max_results]
        if debug:
            print("[detect_dark_squares] 4-pad pattern not found; fallback.")

    # Fallback: IoU dedupe + truncate
    final: List[Detection] = []
    for cand in candidates:
        _, x, y, w, h, _ = cand
        box = (x, y, w, h)
        if any(_iou_xywh(box, (e[1], e[2], e[3], e[4])) > 0.6 for e in final):
            continue
        final.append(cand)
        if len(final) >= max_results:
            break

    if debug:
        print(f"[detect_dark_squares] kept (final): {len(final)}")

    return final


def detect_dark_squares_robust(img, edge_mm: float = 40.0, **kwargs):
    """
    Compatibility wrapper for existing code.

    edge_mm is ignored (detection is purely pixel-based; the real-mm edge is applied later in
    calibration_core); the default is kept at 40 only to match the pipeline's EDGE_MM_DEFAULT
    so the signature doesn't mislead. Unknown keyword arguments
    (e.g. tuning params like ``clahe_clip`` that some callers pass) are silently
    dropped instead of raising TypeError — otherwise a fallback detection strategy
    could crash the whole upload for images with few/no calibration squares.
    """
    import inspect
    valid = set(inspect.signature(detect_dark_squares).parameters)
    filtered = {k: v for k, v in kwargs.items() if k in valid}
    return detect_dark_squares(img, **filtered)


def draw_squares(
    img: np.ndarray,
    detections: List[Detection],
    color=(0, 255, 0),
    thickness: int = 4,
    label: bool = True,
):
    """Simple visualization helper for detections."""
    if img is None or not hasattr(img, "shape"):
        return img

    out = img.copy()
    for rank, (_, x, y, w, h, _) in enumerate(detections, start=1):
        cv2.rectangle(out, (x, y), (x + w, y + h), color, thickness)
        if label:
            cv2.putText(
                out,
                f"S{rank}",
                (x + 10, y + 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
    return out
