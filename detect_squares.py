import os
import cv2
import numpy as np
from typing import List, Tuple, Optional

# Tunables (easy to adjust)
MIN_AREA_DEFAULT = 800
MAX_AREA_RATIO_DEFAULT = 0.6
MAX_ASPECT_DEFAULT = 1.5

DEBUG_DUMP_DIR = os.getenv("CAMSCAN_DEBUG_DUMP", "uploads/_debug")
DUMP_DEBUG_IMAGES = bool(os.getenv("CAMSCAN_DEBUG", "0") == "1")


def _ensure_debug_dir():
    if not DUMP_DEBUG_IMAGES:
        return
    try:
        os.makedirs(DEBUG_DUMP_DIR, exist_ok=True)
    except Exception:
        pass


def _dump(name: str, img) -> None:
    if not DUMP_DEBUG_IMAGES:
        return
    _ensure_debug_dir()
    try:
        fn = os.path.join(DEBUG_DUMP_DIR, f"{int(time.time()*1000)}-{name}.png")
        cv2.imwrite(fn, img)
    except Exception:
        pass


# ------------------ Helpers for nested pattern and ROI refinement ------------------

def _detect_nested_pattern(
    gray: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    min_white_squares: int = 2,
) -> Tuple[bool, int]:
    """
    Check whether a dark candidate square contains lighter/white squares inside.
    This helps disambiguate textured dark regions from calibration markers.
    """
    roi = gray[y : y + h, x : x + w]
    if roi.size == 0:
        return False, 0

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(cv2.GaussianBlur(roi, (5, 5), 0))

    block = max(11, (min(w, h) // 10) | 1)
    c_val = -3
    white_mask = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY, block, c_val)

    k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    white_mask = cv2.morphologyEx(white_mask, cv2.MORPH_OPEN, k, iterations=1)

    contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    white_squares = 0
    for c in contours:
        a = cv2.contourArea(c)
        if a < 0.008 * w * h:
            continue
        x0, y0, ww, hh = cv2.boundingRect(c)
        aspect = max(ww / max(1, hh), hh / max(1, ww))
        if aspect > 2.5:
            continue
        white_squares += 1

    return white_squares >= min_white_squares, white_squares


def _refine_square_in_roi(
    gray: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    min_fill: float = 0.35,
    max_aspect: float = 1.4,
    check_nested: bool = False,
    min_nested_squares: int = 0,
    debug: bool = False,
):
    """
    Refine a candidate rectangle inside ROI and return (ok, box_global)
    - Uses CLAHE + Canny + morphological cleanup to find the main contour
    - Attempts sub-pixel corner refinement with goodFeaturesToTrack + cornerSubPix
    - Returns box coordinates in global image space (int32) if successful
    """
    if check_nested:
        has_pattern, _ = _detect_nested_pattern(gray, x, y, w, h, min_nested_squares)
        if not has_pattern:
            return False, None

    roi = gray[y : y + h, x : x + w]
    if roi.size == 0:
        return False, None

    # Preprocess for robust contour extraction
    roi_blur = cv2.GaussianBlur(roi, (5, 5), 0)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(roi_blur)

    # Binary + edges combined
    _, roi_bin = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    roi_edges = cv2.Canny(enhanced, 30, 120)
    combined = cv2.bitwise_or(roi_bin, roi_edges)

    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, k, iterations=2)
    combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, k, iterations=1)

    if DUMP_DEBUG_IMAGES and debug:
        _ensure_debug_dir()
        cv2.imwrite(os.path.join(DEBUG_DUMP_DIR, f"roi-{x}-{y}-{w}-{h}-combined.png"), combined)

    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False, None

    # Pick largest contour
    c = max(contours, key=cv2.contourArea)
    hull = cv2.convexHull(c)
    contour_area = cv2.contourArea(hull)
    if contour_area <= 0:
        return False, None

    rot_rect = cv2.minAreaRect(hull)
    box = cv2.boxPoints(rot_rect).astype(np.float32)

    # Side-length aspect check
    side_lengths = [float(np.linalg.norm(box[i] - box[(i + 1) % 4])) for i in range(4)]
    max_side = max(side_lengths)
    min_side = max(1e-3, min(side_lengths))
    aspect = max_side / min_side
    if aspect > max_aspect:
        return False, None

    box_area = max_side * min_side
    fill = contour_area / (box_area + 1e-6)
    if fill < min_fill:
        return False, None

    # Attempt sub-pixel refinement in ROI-local coordinates
    try:
        # compute ROI bounds for refinement with padding
        pad = max(6, int(min(w, h) * 0.15))
        x0 = max(0, -pad)
        y0 = max(0, -pad)
        # for cornerSubPix we need the ROI image and initial points in ROI-local coords
        # Map box pts to ROI-local coordinates
        box_local = box.copy()
        # box points are relative to ROI already (box computed from contour in ROI)
        # prepare good initial guesses using goodFeaturesToTrack on ROI
        roi_gray = enhanced
        corners = cv2.goodFeaturesToTrack(roi_gray, maxCorners=8, qualityLevel=0.01, minDistance=6)
        if corners is not None and len(corners) >= 4:
            corners = corners.reshape(-1, 2)
            # choose the 4 corners closest to box points
            selected = []
            for p in box_local:
                dists = np.linalg.norm(corners - p, axis=1)
                idx = int(np.argmin(dists))
                selected.append(corners[idx])
            selected = np.array(selected, dtype=np.float32)
            # make shape (N,1,2)
            initial_pts = selected.reshape(-1, 1, 2).astype(np.float32)
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01)
            # cornerSubPix requires the image and initial points in ROI coords
            cv2.cornerSubPix(roi_gray, initial_pts, (5, 5), (-1, -1), criteria)
            refined = initial_pts.reshape(-1, 2)
            # map back to global coords
            box_global = refined.copy()
            box_global[:, 0] += float(x)
            box_global[:, 1] += float(y)
            return True, box_global.astype(np.int32)
    except Exception:
        # if refinement fails, fall back to rot_rect result
        pass

    # Fallback: return rot_rect mapped to global coordinates
    box_global = box.copy()
    box_global[:, 0] += float(x)
    box_global[:, 1] += float(y)
    return True, box_global.astype(np.int32)


# ------------------ Main detector (multi-scale, robust) ------------------
import time

def detect_dark_squares(
    img,
    min_area: float = MIN_AREA_DEFAULT,
    max_area_ratio: float = MAX_AREA_RATIO_DEFAULT,
    max_aspect: float = MAX_ASPECT_DEFAULT,
    brightness_thresh: Optional[int] = None,
    approx_eps: float = 0.02,
    max_results: int = 25,
    clahe_clip: float = 2.0,
    use_multi_scale: bool = True,
    scale_factors: Tuple[float, ...] = (1.0, 0.8, 0.6),
    check_nested_pattern: bool = False,
    min_nested_squares: int = 0,
    debug: bool = False,
) -> List[Tuple[float, int, int, int, int, float]]:
    """
    Robust dark-square detector (returns list of tuples (score, x, y, w, h, mean_val)).
    This is a restored, integrated implementation that other modules (calibration_core) expect.
    """
    if img is None or not hasattr(img, "shape"):
        return []

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h_img, w_img = gray.shape[:2]
    frame_area = float(h_img * w_img)

    all_candidates = []
    scales = scale_factors if use_multi_scale else (1.0,)

    for scale in scales:
        if scale != 1.0:
            scaled_w = max(2, int(w_img * scale))
            scaled_h = max(2, int(h_img * scale))
            gray_scaled = cv2.resize(gray, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)
        else:
            gray_scaled = gray
            scale = 1.0

        blur = cv2.GaussianBlur(gray_scaled, (5, 5), 0)
        clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=(8, 8))
        enhanced = clahe.apply(blur)

        if brightness_thresh is None:
            _, base_mask = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        else:
            _, base_mask = cv2.threshold(enhanced, brightness_thresh, 255, cv2.THRESH_BINARY_INV)

        adaptive_mask = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                              cv2.THRESH_BINARY_INV, 21, 5)

        edges1 = cv2.Canny(enhanced, 30, 90)
        edges2 = cv2.Canny(enhanced, 60, 150)
        edges_combined = cv2.bitwise_or(edges1, edges2)

        dark_mask = cv2.bitwise_or(base_mask, adaptive_mask)
        dark_mask = cv2.bitwise_or(dark_mask, edges_combined)

        k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, k, iterations=2)
        dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_OPEN, k, iterations=1)

        contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        scale_factor_area = scale * scale
        eff_min_area = max(min_area * scale_factor_area, 0.00025 * (gray_scaled.shape[0] * gray_scaled.shape[1]))

        for c in contours:
            area = cv2.contourArea(c)
            if area < eff_min_area:
                continue

            hull = cv2.convexHull(c)
            hull_area = cv2.contourArea(hull)
            if not (eff_min_area < hull_area < (gray_scaled.shape[0] * gray_scaled.shape[1]) * max_area_ratio):
                continue

            peri = cv2.arcLength(hull, True)
            approx = cv2.approxPolyDP(hull, approx_eps * peri, True)

            candidate_quads = []
            if len(approx) == 4 and cv2.isContourConvex(approx):
                candidate_quads.append(approx.reshape(4, 2).astype(np.float32))
            else:
                rect = cv2.minAreaRect(hull)
                box = cv2.boxPoints(rect)
                candidate_quads.append(box.astype(np.float32))

            for quad in candidate_quads:
                x0, y0, w0, h0 = cv2.boundingRect(np.intp(quad))
                if w0 <= 0 or h0 <= 0:
                    continue

                x_orig = int(x0 / scale)
                y_orig = int(y0 / scale)
                w_orig = int(w0 / scale)
                h_orig = int(h0 / scale)
                if w_orig <= 0 or h_orig <= 0:
                    continue

                long_side = float(max(w_orig, h_orig))
                short_side = float(max(1, min(w_orig, h_orig)))
                aspect = long_side / short_side
                if aspect > max_aspect:
                    continue

                ok, box_global = _refine_square_in_roi(
                    gray,
                    x_orig,
                    y_orig,
                    w_orig,
                    h_orig,
                    min_fill=0.30,
                    max_aspect=1.4,
                    check_nested=check_nested_pattern,
                    min_nested_squares=min_nested_squares,
                    debug=debug,
                )
                if not ok:
                    continue

                roi = gray[y_orig : y_orig + h_orig, x_orig : x_orig + w_orig]
                mean_val = float(np.mean(roi))

                dark_score = max(0.0, 1.0 - mean_val / 255.0)
                size_ratio = (w_orig * h_orig) / frame_area
                size_score = min(1.0, size_ratio / 0.1)

                # Simple geometry score using solidity/extent
                hull_local = cv2.convexHull(c)
                solidity = (cv2.contourArea(c) / (cv2.contourArea(hull_local) + 1e-6)) if cv2.contourArea(hull_local) > 0 else 0.0
                extent = (area / (w0 * h0 + 1e-6))
                geom_score = (solidity * 0.6 + extent * 0.4)

                final_score = 0.6 * dark_score + 0.4 * geom_score + 0.15 * size_score

                all_candidates.append((final_score, x_orig, y_orig, w_orig, h_orig, mean_val))

    if not all_candidates:
        return []

    all_candidates.sort(key=lambda x: x[0], reverse=True)

    final_candidates = []
    for candidate in all_candidates:
        score, x, y, w, h, mean_val = candidate
        overlap = False
        for existing in final_candidates:
            _, ex, ey, ew, eh, _ = existing
            x1 = max(x, ex)
            y1 = max(y, ey)
            x2 = min(x + w, ex + ew)
            y2 = min(y + h, ey + eh)
            if x2 > x1 and y2 > y1:
                intersection = (x2 - x1) * (y2 - y1)
                union = w * h + ew * eh - intersection
                iou = intersection / union if union > 0 else 0
                if iou > 0.4:
                    overlap = True
                    break
        if not overlap:
            final_candidates.append(candidate)

    return final_candidates[:max_results]


def detect_dark_squares_robust(img, edge_mm: float = 30.0, **kwargs):
    """Compatibility wrapper used by the rest of the app."""
    return detect_dark_squares(img, **kwargs)


# lightweight visualizer for debugging
import time as _t

def draw_squares(img, detections, color=(0, 255, 0), thickness=6, label=True):
    if img is None or not hasattr(img, "shape"):
        return img
    result = img.copy()
    for rank, (_, x, y, w, h, mean_val) in enumerate(detections, start=1):
        ok, box_global = _refine_square_in_roi(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), x, y, w, h, debug=False)
        if ok and box_global is not None:
            cv2.polylines(result, [box_global], True, (0, 220, 255), max(3, thickness), lineType=cv2.LINE_AA)
        else:
            cv2.rectangle(result, (x, y), (x + w, y + h), color, thickness)
        if label:
            label_text = f"S{rank}"
            cv2.putText(result, label_text, (x + 10, y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255,255,255), 2, cv2.LINE_AA)
    return result
