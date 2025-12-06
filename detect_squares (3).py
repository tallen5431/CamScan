"""
Improved calibration-square detector using dual Otsu masks.

Public functions:
  - detect_dark_squares(img, ...)
  - detect_dark_squares_robust(img, ...)
  - draw_squares(img, detections, ...)

Each detection is:
  (score, x, y, w, h, mean_val)
"""

import cv2
import numpy as np
from typing import List, Tuple, Optional
from itertools import combinations

# Basic tunables
MIN_AREA_DEFAULT = 150
MAX_AREA_RATIO_DEFAULT = 0.6
MAX_ASPECT_DEFAULT = 2.0  # how "non-square" a rectangle can be

# Shape quality defaults – tuned to favor nice, compact squares
MIN_FILL_RATIO_DEFAULT = 0.7       # contour area / bounding-rect area
MIN_HULL_RATIO_DEFAULT = 0.85      # contour area / convex-hull area
MIN_COMPACTNESS_DEFAULT = 0.6      # 4πA / P² (1 = perfect circle, rectangles ~0.7–0.9)


def _prepare_gray(img: np.ndarray) -> np.ndarray:
    """Convert BGR→GRAY and apply light smoothing."""
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    return gray


def _basic_dark_mask(gray: np.ndarray) -> np.ndarray:
    """
    Simple Otsu + morphology to get a dark-region mask.

    NOTE: Kept for compatibility; returns only the dark mask.
    """
    _, mask = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # Light morphology so we don't fuse distant blobs
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k, iterations=1)
    return mask


def _basic_masks(gray: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute both:
      - dark_mask   : dark regions as foreground (white)
      - bright_mask : bright regions as foreground (white)
    using Otsu and inversion, mirroring your notebook experiment.
    """
    dark_mask = _basic_dark_mask(gray)
    bright_mask = cv2.bitwise_not(dark_mask)
    return dark_mask, bright_mask


# ──────────────────────────────────────────────────────────────
# Calibration pattern helper: 4 inner squares in a 2×2 grid + outer square
# ──────────────────────────────────────────────────────────────

def _find_calibration_pattern(
    candidates: List[Tuple[float, int, int, int, int, float]],
    grid_aspect_max: float = 1.3,
    area_ratio_max: float = 1.25,
    size_spacing_ratio_min: float = 0.5,
    outer_area_factor_min: float = 4.0,
    margin_frac: float = 0.05,
    debug: bool = False,
) -> Optional[List[Tuple[float, int, int, int, int, float]]]:
    """
    Attempt to find a calibration pattern:
      - 4 inner squares of similar size
      - their centers form a 2×2 grid with roughly equal spacing
      - 1 larger outer square that tightly contains them

    Returns:
      [outer_candidate, inner1, inner2, inner3, inner4]  (if found)
      or None if no good pattern is found.
    """
    n = len(candidates)
    if n < 5:
        return None

    # Precompute geometry for all candidates
    xs = []
    ys = []
    ws = []
    hs = []
    centers = []
    areas = []
    scores = []
    for (score, x, y, w, h, mean_val) in candidates:
        xs.append(x)
        ys.append(y)
        ws.append(w)
        hs.append(h)
        areas.append(w * h)
        centers.append((x + 0.5 * w, y + 0.5 * h))
        scores.append(score)

    xs = np.array(xs, dtype=float)
    ys = np.array(ys, dtype=float)
    ws = np.array(ws, dtype=float)
    hs = np.array(hs, dtype=float)
    areas = np.array(areas, dtype=float)
    centers = np.array(centers, dtype=float)

    best_group = None
    best_group_score = -1.0

    idx_all = list(range(n))

    # ----------------------------------------------------------
    # 1) Search over all 4-combinations for a nice 2×2 grid group
    # ----------------------------------------------------------
    for inner_idxs in combinations(idx_all, 4):
        inner_idxs = list(inner_idxs)

        inner_areas = areas[inner_idxs]
        inner_ws = ws[inner_idxs]
        inner_hs = hs[inner_idxs]
        inner_centers = centers[inner_idxs]

        # Areas should be similar (within area_ratio_max)
        area_min = float(inner_areas.min())
        area_max = float(inner_areas.max())
        if area_min <= 0:
            continue
        if area_max / area_min > area_ratio_max:
            continue

        # Average size for spacing sanity checks
        avg_size = 0.5 * (float(inner_ws.mean()) + float(inner_hs.mean()))

        # ---- Columns (x) ----
        # Sort by x to split into left and right columns
        sorted_by_x = sorted(inner_idxs, key=lambda i: centers[i][0])
        left = sorted_by_x[:2]
        right = sorted_by_x[2:]

        mean_x_left = float(np.mean([centers[i][0] for i in left]))
        mean_x_right = float(np.mean([centers[i][0] for i in right]))
        dx = abs(mean_x_right - mean_x_left)
        if dx <= 0:
            continue

        # ---- Rows (y) ----
        # Sort by y to split into top and bottom rows
        sorted_by_y = sorted(inner_idxs, key=lambda i: centers[i][1])
        top = sorted_by_y[:2]
        bottom = sorted_by_y[2:]

        mean_y_top = float(np.mean([centers[i][1] for i in top]))
        mean_y_bottom = float(np.mean([centers[i][1] for i in bottom]))
        dy = abs(mean_y_bottom - mean_y_top)
        if dy <= 0:
            continue

        # Ensure we truly have a 2×2 grid: one square per (row, col) combo
        col_label = {i: ("L" if i in left else "R") for i in inner_idxs}
        row_label = {i: ("T" if i in top else "B") for i in inner_idxs}
        combos = {(col_label[i], row_label[i]) for i in inner_idxs}
        if combos != {("L", "T"), ("L", "B"), ("R", "T"), ("R", "B")}:
            continue

        # Spacing in x and y should be similar (grid_aspect_max ≈ 1.3)
        grid_aspect = max(dx, dy) / min(dx, dy)
        if grid_aspect > grid_aspect_max:
            continue

        # Spacing should be at least some fraction of square size
        if dx < size_spacing_ratio_min * avg_size:
            continue
        if dy < size_spacing_ratio_min * avg_size:
            continue

        # ------------------------------------------------------
        # 2) Find an outer square that tightly contains the 4
        # ------------------------------------------------------
        inner_x_min = float((xs[inner_idxs]).min())
        inner_y_min = float((ys[inner_idxs]).min())
        inner_x_max = float((xs[inner_idxs] + ws[inner_idxs]).max())
        inner_y_max = float((ys[inner_idxs] + hs[inner_idxs]).max())
        inner_total_area = float(inner_areas.sum())

        # Slight margin to avoid degenerate boundary issues
        margin = margin_frac * max(dx, dy)

        outer_candidates = []
        for j in idx_all:
            if j in inner_idxs:
                continue

            xj = xs[j]
            yj = ys[j]
            wj = ws[j]
            hj = hs[j]
            aj = areas[j]

            # Area must be significantly larger than the sum of inner squares
            if aj < outer_area_factor_min * inner_total_area:
                continue

            xj_min = xj
            yj_min = yj
            xj_max = xj + wj
            yj_max = yj + hj

            # Must contain the union of the inner squares with some margin
            if (
                xj_min <= inner_x_min - margin
                and yj_min <= inner_y_min - margin
                and xj_max >= inner_x_max + margin
                and yj_max >= inner_y_max + margin
            ):
                outer_candidates.append(j)

        if not outer_candidates:
            continue

        # Pick the tightest outer candidate (smallest area)
        outer_idx = min(outer_candidates, key=lambda j: areas[j])

        # Compute a simple group score based on detection scores
        group_score = float(np.mean([scores[i] for i in inner_idxs + [outer_idx]]))
        if group_score > best_group_score:
            best_group_score = group_score
            best_group = (outer_idx, inner_idxs)

    if best_group is None:
        if debug:
            print("[calibration] No valid 4-square pattern found.")
        return None

    outer_idx, inner_idxs = best_group
    inner_idxs = list(inner_idxs)

    if debug:
        print(f"[calibration] Found pattern: outer={outer_idx}, inner={inner_idxs}")

    # Return [outer, inner1, inner2, inner3, inner4] in a stable order:
    # outer first, then inner squares sorted by (row, col) grid position.
    # Sort inner by (y, x) to get TL, TR, BL, BR-ish ordering.
    inner_idxs_sorted = sorted(inner_idxs, key=lambda i: (centers[i][1], centers[i][0]))

    result = [candidates[outer_idx]] + [candidates[i] for i in inner_idxs_sorted]
    return result


# ──────────────────────────────────────────────────────────────
# Main detector
# ──────────────────────────────────────────────────────────────

def detect_dark_squares(
    img: np.ndarray,
    min_area: float = MIN_AREA_DEFAULT,
    max_area_ratio: float = MAX_AREA_RATIO_DEFAULT,
    max_aspect: float = MAX_ASPECT_DEFAULT,
    brightness_thresh: Optional[int] = None,
    approx_eps: float = 0.02,
    max_results: int = 25,
    clahe_clip: float = 2.0,          # kept for compatibility (not heavily used)
    use_multi_scale: bool = False,    # ignored in this version
    scale_factors: Tuple[float, ...] = (1.0, 0.8, 0.6),  # kept for compat
    check_nested_pattern: bool = False,  # ignored
    min_nested_squares: int = 0,         # ignored
    # Shape quality knobs:
    min_fill_ratio: float = MIN_FILL_RATIO_DEFAULT,
    min_hull_ratio: float = MIN_HULL_RATIO_DEFAULT,
    min_compactness: float = MIN_COMPACTNESS_DEFAULT,
    border_margin_frac: float = 0.0,     # e.g. 0.02 to reject border-hugging blobs
    # Polarity selection:
    #   "dark"   -> only inner dark squares
    #   "bright" -> only bright squares
    #   "both"   -> outer + inner, or general usage
    polarity: str = "both",
    # NEW: calibration pattern enforcement:
    #   - If True, try to find 4-squared grid + outer square.
    #   - If found, return only that pattern (outer + 4 inner).
    #   - If not found, fall back to all candidates.
    enforce_calibration_pattern: bool = False,
    # If True and a calibration pattern is found, return ONLY the outer square.
    calibration_outer_only: bool = False,
    debug: bool = False,
) -> List[Tuple[float, int, int, int, int, float]]:
    """
    Calibration-square detector with dual-polarity Otsu masks and optional
    4-inner-squares-in-a-grid pattern logic.

    Steps:
      1. Convert to gray, blur.
      2. Otsu threshold (inverted) + morphology → dark_mask.
         Invert → bright_mask.
      3. Find contours (with hierarchy) in selected masks.
      4. Filter by area, aspect ratio, fill ratio, convexity, compactness,
         and brightness (dark or bright mode).
      5. Score by intensity + geometric quality.
      6. Optional: search for 4-square grid + outer square pattern.
      7. Sort & deduplicate by IoU.
    """
    if img is None or img.size == 0:
        return []

    gray = _prepare_gray(img)
    h_img, w_img = gray.shape[:2]
    frame_area = float(h_img * w_img)

    # Dual masks: one that highlights dark regions, one for bright
    dark_mask, bright_mask = _basic_masks(gray)

    # Global brightness reference
    global_mean = float(np.mean(gray))
    if brightness_thresh is None:
        # Dark squares must be well below mean; bright squares clearly above
        brightness_thresh_dark = int(global_mean * 0.85)
        brightness_thresh_bright = int(global_mean * 1.05)
    else:
        brightness_thresh_dark = int(brightness_thresh)
        brightness_thresh_bright = int(brightness_thresh)

    # Optional margin to reject border-hugging blobs
    margin_x = border_margin_frac * w_img
    margin_y = border_margin_frac * h_img

    candidates: List[Tuple[float, int, int, int, int, float]] = []
    eps = 1e-6

    polarity = polarity.lower()
    use_dark = polarity in ("dark", "both")
    use_bright = polarity in ("bright", "both")

    # ------------------------------------------------------------------
    # Helper: shared contour → candidate logic, parameterized by polarity
    # ------------------------------------------------------------------
    def process_contours(contours, mode: str):
        nonlocal candidates

        for c in contours:
            area = cv2.contourArea(c)
            if area <= 0:
                continue

            # Area filter (absolute + relative)
            if area < min_area:
                continue
            if area / frame_area > max_area_ratio:
                continue

            x, y, w, h = cv2.boundingRect(c)
            if w <= 0 or h <= 0:
                continue

            # Skip blobs touching the frame border if margin is set
            if border_margin_frac > 0.0:
                if (
                    x <= margin_x
                    or y <= margin_y
                    or x + w >= w_img - margin_x
                    or y + h >= h_img - margin_y
                ):
                    continue

            # Aspect ratio filter
            aspect = max(w, h) / float(min(w, h))
            if aspect > max_aspect:
                continue

            # Polygon approximation to favor quadrilateral-ish things
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, approx_eps * peri, True)
            if len(approx) < 4:
                # Reject blobs that aren't reasonably polygon-like
                continue

            # --- Shape quality metrics ---
            rect_area = float(w * h)
            fill_ratio = area / (rect_area + eps)

            hull = cv2.convexHull(c)
            hull_area = cv2.contourArea(hull)
            hull_ratio = area / (hull_area + eps) if hull_area > 0 else 0.0

            compactness = 0.0
            if peri > 0:
                compactness = 4.0 * np.pi * area / (peri * peri + eps)

            # Strong filters to kill messy blobs and keep nice squares
            if fill_ratio < min_fill_ratio:
                continue
            if hull_ratio < min_hull_ratio:
                continue
            if compactness < min_compactness:
                continue

            # Mean brightness inside ROI
            roi = gray[y : y + h, x : x + w]
            if roi.size == 0:
                continue
            mean_val = float(np.mean(roi))

            # Polarity-dependent brightness test
            if mode == "dark":
                if mean_val > brightness_thresh_dark:
                    # Too bright to be a dark calibration square
                    continue
                intensity_score = max(0.0, 1.0 - mean_val / 255.0)  # darker better
            else:  # "bright"
                if mean_val < brightness_thresh_bright:
                    # Too dark to be a bright calibration square
                    continue
                intensity_score = max(0.0, mean_val / 255.0)        # brighter better

            # Simple scores
            size_ratio = area / frame_area
            size_score = min(1.0, size_ratio / 0.1)        # favor moderately large
            geom_score = (fill_ratio + hull_ratio + compactness) / 3.0

            score = 0.4 * intensity_score + 0.4 * geom_score + 0.2 * size_score
            candidates.append((score, x, y, w, h, mean_val))

    # ------------------------------------------------------------------
    # Run on dark mask (inner black squares)
    # ------------------------------------------------------------------
    if use_dark:
        contours_dark, _ = cv2.findContours(
            dark_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
        )
        if debug:
            print(f"[detect_dark_squares] dark contours: {len(contours_dark)}")
        process_contours(contours_dark, mode="dark")

    # ------------------------------------------------------------------
    # Run on bright mask (outer white square + inner bright squares)
    # ------------------------------------------------------------------
    if use_bright:
        contours_bright, _ = cv2.findContours(
            bright_mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE
        )
        if debug:
            print(f"[detect_dark_squares] bright contours: {len(contours_bright)}")
        process_contours(contours_bright, mode="bright")

    # Sort by score descending
    candidates.sort(key=lambda t: t[0], reverse=True)

    # Deduplicate with simple IoU
    final_candidates: List[Tuple[float, int, int, int, int, float]] = []
    for cand in candidates:
        _, x, y, w, h, _ = cand
        overlap = False
        for existing in final_candidates:
            _, ex, ey, ew, eh, _ = existing

            x1 = max(x, ex)
            y1 = max(y, ey)
            x2 = min(x + w, ex + ew)
            y2 = min(y + h, ey + eh)

            if x2 > x1 and y2 > y1:
                inter = (x2 - x1) * (y2 - y1)
                union = w * h + ew * eh - inter
                iou = inter / union if union > 0 else 0.0
                if iou > 0.6:
                    overlap = True
                    break
        if not overlap:
            final_candidates.append(cand)

    if debug:
        print(f"[detect_dark_squares] kept (before calibration): {len(final_candidates)}")

    # Optional: enforce 4-square grid + outer square pattern
    if enforce_calibration_pattern:
        pattern = _find_calibration_pattern(
            final_candidates,
            debug=debug,
        )
        if pattern is not None:
            if calibration_outer_only:
                final_candidates = [pattern[0]]  # only the outer square
            else:
                final_candidates = pattern
            if debug:
                print(f"[detect_dark_squares] using calibration pattern: "
                      f"{len(final_candidates)} candidates")

    if debug:
        print(f"[detect_dark_squares] kept (final): {len(final_candidates)}")

    return final_candidates[:max_results]


def detect_dark_squares_robust(img, edge_mm: float = 30.0, **kwargs):
    """
    Compatibility wrapper used by the rest of the app.
    The edge_mm argument is ignored here; everything is pixel-based.
    """
    return detect_dark_squares(img, **kwargs)


def draw_squares(
    img: np.ndarray,
    detections,
    color=(0, 255, 0),
    thickness: int = 4,
    label: bool = True,
):
    """
    Simple visualization helper.

    Expects detections in the same format returned by detect_dark_squares:
      (score, x, y, w, h, mean_val)
    """
    if img is None or not hasattr(img, "shape"):
        return img

    result = img.copy()

    for rank, (_, x, y, w, h, mean_val) in enumerate(detections, start=1):
        cv2.rectangle(
            result,
            (x, y),
            (x + w, y + h),
            color,
            thickness,
        )
        if label:
            text = f"S{rank}"
            cv2.putText(
                result,
                text,
                (x + 10, y + 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

    return result
