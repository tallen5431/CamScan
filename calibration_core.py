import os, cv2, json, time, tempfile, uuid
import numpy as np
from typing import Dict, Any, Tuple, List, Optional

# External helpers (already in your project)
from detect_squares import detect_dark_squares, detect_dark_squares_robust, MIN_AREA_DEFAULT
from edge_finder import find_main_edges

# ----------------------------
# Tunables / Defaults
# ----------------------------
EDGE_MM_DEFAULT: float = 30.0   # default marker edge in millimeters
PADDING_PX: int = 80            # crop padding around candidate square
DOWNSCALE_FACTOR: float = 1.0   # speed-up for edge finder (1.0 = off)
MAX_EDGES: int = 50             # contours to analyze inside edge_finder (increased for inner squares)
LINE_THICKNESS: int = 3         # overlay poly thickness

# Above this perspective foreshortening (degrees the marker's corners deviate from
# 90°) a single mm/px scale is no longer reliable, so we mark the result low
# confidence and prompt the user to verify. ~2.5° corresponds to ≳8% scale error on
# a tilted square in testing.
PERSPECTIVE_MAX_DEG: float = 2.5

# Photo-quality thresholds (on an image downscaled so its long side is ~1000 px, to make
# the numbers resolution-independent). Conservative on purpose — we only NUDGE, never
# block, and a false "blurry" on a low-texture part is more annoying than a missed one.
BLUR_MIN_VAR: float = float(os.getenv("BLUR_MIN_VAR", 35))   # Laplacian variance; below = soft
DARK_MEAN_MAX: float = float(os.getenv("DARK_MEAN_MAX", 45)) # mean 0-255 brightness; below = dark


def _image_quality(img_bgr: np.ndarray) -> Dict[str, Any]:
    """Cheap capture-quality signals so the client can nudge before the user measures a
    photo that will measure poorly. Blur via Laplacian variance (sharp photos have strong
    edge energy; a soft/out-of-focus one does not), brightness via the mean. Best-effort:
    any failure returns 'unknown' rather than breaking the upload."""
    try:
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
        h, w = gray.shape[:2]
        long_side = max(h, w)
        if long_side > 1000:
            s = 1000.0 / long_side
            gray = cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        mean = float(np.mean(gray))
        return {
            "blur_score": round(blur, 1),
            "blurry": blur < BLUR_MIN_VAR,
            "brightness": round(mean, 1),
            "dark": mean < DARK_MEAN_MAX,
        }
    except Exception:
        return {"blur_score": None, "blurry": False, "brightness": None, "dark": False}

# Artifacts policy
SAVE_OVERLAY_IMAGE: bool = False    # keep False to avoid writing overlay jpgs
# Debug images are written into the web-served uploads/ dir under a FIXED name, so leaving
# this on in production means every user's photo is copied to a constant, world-readable URL
# (and concurrent uploads race on the one filename). Off by default; opt in for local
# debugging with CAMSCAN_DEBUG_IMAGES=1.
SAVE_DEBUG_IMAGES: bool = os.getenv("CAMSCAN_DEBUG_IMAGES", "0").strip().lower() in ("1", "true", "yes", "on")

# Known upload image extensions (used to locate the original raw upload for the viewer).
# Retention/cleanup of old uploads lives in the app-level reaper (app.py), not here.
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")


# ----------------------------
# Small helpers
# ----------------------------
def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax+aw, bx+bw), min(ay+ah, by+bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2-x1)*(y2-y1)
    union = aw*ah + bw*bh - inter
    return inter/union if union > 0 else 0.0

def _dedup_rects(rects: List[Tuple[int,int,int,int]], iou_thresh: float=0.6):
    """Remove duplicate detections using IoU threshold (increased from 0.45 to 0.6)."""
    out: List[Tuple[int,int,int,int]] = []
    for r in rects:
        if all(_iou(r, s) < iou_thresh for s in out):
            out.append(r)
    return out

def _pattern_score(rlist: List[Tuple[int,int,int,int]]):
    """Rank a strategy's candidate rects against the ideal 5-square calibration pattern
    (1 outer + 4 pads). Higher is better. Used so a stricter fallback strategy that
    returns FEWER/worse squares never silently replaces a better earlier detection.
    Returns a tuple compared lexicographically:
      (reached_five, closeness_to_five, count)."""
    n = len(rlist)
    if n == 0:
        return (0, -99, 0)
    # Reaching the full 5-pattern beats anything short of it; among sets that did, the
    # one closest to exactly 5 wins; ties break toward more squares.
    return (1 if n >= 5 else 0, -abs(n - 5), n)

def _crop_region(img, x, y, w, h, pad: int):
    H, W = img.shape[:2]
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(W, x + w + pad)
    y1 = min(H, y + h + pad)
    return img[y0:y1, x0:x1], x0, y0

def _avg_side_len(pts: List[Tuple[float,float]]) -> float:
    if len(pts) < 4:
        return 0.0
    p = np.array(pts, dtype=np.float32)
    d = 0.0
    for i in range(4):
        a, b = p[i], p[(i+1) % 4]
        d += float(np.linalg.norm(a-b))
    return d / 4.0


def _marker_confidence(source: str, distortion_deg: float = 0.0,
                       has_homography: bool = False) -> str:
    """Trust level ("high"/"low") for a calibration marker, from how it was measured.

    - "refined": precise edge_finder quad — trustworthy. Perspective tilt no longer
                 forces "low" WHEN a rectifying homography was produced (linear
                 measurements are then corrected downstream). Only refined markers
                 that are strongly foreshortened AND lack a homography stay "low".
    - "bbox":    rough rotated-bounding-box fallback used when edge refinement
                 failed (low-contrast/dark backgrounds); can be ~10%+ off → "low".
    - "plain":   a solid square found on the full frame as a last resort. Accurate
                 on a real target, but we are less certain the object we latched
                 onto IS the calibration square → "low" (nudge the user to verify).
    """
    if source == "refined":
        if has_homography or distortion_deg <= PERSPECTIVE_MAX_DEG:
            return "high"
    return "low"


def _record_marker(overlay: np.ndarray,
                   mapped: List[Tuple[int, int]],
                   edge_len_mm: float,
                   source: str,
                   distortion_deg: float,
                   line_thickness: int,
                   homography_corners: Optional[List[Tuple[int, int]]] = None) -> Optional[Dict[str, Any]]:
    """Draw a calibration marker on the overlay and build its data dict.

    Shared by the refined-outer-square and plain-square-fallback paths so both
    always emit the same schema (incl. ``distortion_deg``) and identical overlay
    styling. Returns None when the corners don't yield a positive edge length.

    ``homography_corners`` are the marker's TRUE projected corners (the trapezoid
    from the raw contour, ordered TL,TR,BR,BL), used to build the rectifying
    homography. ``mapped`` (minAreaRect) is still used for the drawn overlay,
    edge_px and stored corners so existing measurements are unchanged.
    """
    px_edge = _avg_side_len(mapped)
    if px_edge <= 0:
        return None
    mm_per_px = float(edge_len_mm) / float(px_edge)

    # Draw the square in YELLOW (0,255,255) with black-outlined corner dots.
    cv2.polylines(overlay, [np.array(mapped, np.int32)], True, (0, 255, 255), line_thickness)
    for (gx, gy) in mapped:
        cv2.circle(overlay, (gx, gy), 10, (0, 0, 0), -1)
        cv2.circle(overlay, (gx, gy), 7, (0, 255, 255), -1)

    # Perspective homography: image px -> UNIT square (the marker's TRUE corners
    # map to the unit square). Multiplying its output by the real edge_mm gives
    # plane coordinates in mm, so a tilted shot can be measured without
    # foreshortening error, and changing the cube size later needs no recompute.
    # Built from the trapezoidal contour corners; minAreaRect corners would hide
    # the perspective and give a wrong transform.
    homography = _unit_homography(homography_corners) if homography_corners else None

    marker: Dict[str, Any] = {
        "edge_mm": float(edge_len_mm),
        "edge_px": float(px_edge),
        "mm_per_px": float(mm_per_px),
        "source": source,
        "confidence": _marker_confidence(source, distortion_deg, homography is not None),
        "distortion_deg": round(float(distortion_deg), 1),
        "corners": [{"x": int(a), "y": int(b)} for (a, b) in mapped],
    }
    if homography is not None:
        marker["homography"] = homography

    return marker


def _unit_homography(mapped: List[Tuple[int, int]]) -> Optional[List[List[float]]]:
    """3x3 transform mapping the 4 image-space corners (TL,TR,BR,BL) to the unit
    square [(0,0),(1,0),(1,1),(0,1)]. Returns a nested list, or None on failure."""
    if len(mapped) != 4:
        return None
    src = np.array(mapped, dtype=np.float32)
    dst = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    try:
        H = cv2.getPerspectiveTransform(src, dst)
    except cv2.error:
        return None
    if not np.all(np.isfinite(H)):
        return None
    return [[float(v) for v in row] for row in H]


def _fallback_square_corners(crop_bgr: np.ndarray, rect_local, polarity: str = "dark"):
    """Best-effort 4 corners for a square when precise edge refinement fails.

    Thresholds the square's own bounding box (``rect_local`` = (x, y, w, h) in crop
    coordinates, from the detector) for the given polarity, takes the largest contour
    and returns its rotated bounding box corners (minAreaRect). Restricting to the
    detected box avoids latching onto neighbouring dark/bright objects in the padded
    crop. Less exact than edge_finder.find_main_edges, but keeps calibration working
    (non-zero scale) instead of giving up. Returns corners in crop coords, or None.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    H, W = crop_bgr.shape[:2]
    rx, ry, rw, rh = rect_local
    # Expand the detected box slightly to include the full (possibly soft) edge.
    m = max(4, int(0.04 * max(rw, rh)))
    x0 = max(0, int(rx) - m); y0 = max(0, int(ry) - m)
    x1 = min(W, int(rx + rw) + m); y1 = min(H, int(ry + rh) + m)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    sub = crop_bgr[y0:y1, x0:x1]
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    if polarity == "bright":
        _t, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        _t, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < 25:
        return None
    box = cv2.boxPoints(cv2.minAreaRect(c))  # 4 (x, y) points, in `sub` coords
    return [(float(px) + x0, float(py) + y0) for (px, py) in box]


# ----------------------------
# Main entry point (used by app)
# ----------------------------
def calibrate_image(img_bgr: np.ndarray,
                    edge_mm: Optional[float] = None,
                    thresholds: Tuple[int,int,int] = (60, 80, 100),
                    use_robust_detection: bool = True,
                    min_nested_squares: int = 0,
                    line_thickness: int = LINE_THICKNESS
                    ) -> Tuple[Dict[str, Any], np.ndarray]:
    """
    Detect near-square dark regions -> refine with edge_finder -> compute mm/px.
    Returns (calibration_dict, overlay_image_bgr).
    """
    start_time = time.time()

    if img_bgr is None or not hasattr(img_bgr, "shape"):
        raise ValueError("calibrate_image: expected a BGR ndarray")

    H, W = img_bgr.shape[:2]
    overlay = img_bgr.copy()

    # pick runtime edge length (argument wins over module default)
    edge_len_mm = float(edge_mm) if edge_mm is not None else float(EDGE_MM_DEFAULT)
    print(f"[Calibration] Using edge length: {edge_len_mm} mm  (module default={EDGE_MM_DEFAULT})")

    # 1) Enhanced multi-threshold detection with fallback strategies
    rects: List[Tuple[int,int,int,int]] = []

    if use_robust_detection:
        # Strategy 1: IMPROVED calibration pattern detection
        # Uses enhanced detect_squares.py with CLAHE, adaptive thresholds, relaxed matching
        print(f"[Calibration] Strategy 1: IMPROVED 4-pad pattern detection")
        dets = detect_dark_squares_robust(
            img_bgr,
            edge_mm=edge_len_mm,
            polarity="bright",                    # Bright regions (works on dark backgrounds)
            enforce_calibration_pattern=True,     # Find 4-pad pattern + outer square
            calibration_outer_only=False,         # Return ALL: outer + 4 pads
            min_area=30,                          # VERY LOW for tiny squares at distance
            max_area_ratio=0.5,
            max_aspect=2.0,                       # More permissive for angled shots
            # Leverages improved defaults from detect_squares.py:
            # - MIN_AREA_DEFAULT: 150 → 50
            # - CLAHE preprocessing for better contrast
            # - Adaptive percentile-based brightness thresholds
            # - Relaxed 4-pad pattern matching (size_ratio_max: 2.0 → 2.5)
            border_margin_frac=0.01,
            debug=True,
        )
        print(f"[Calibration] Strategy 1 found {len(dets)} detections")
        for (_score, x, y, w, h, _mean) in dets:
            rects.append((x, y, w, h))

        # Strategies 2/3 re-run detection with different/stricter params when Strategy 1
        # fell short of the full 5-square pattern. They must NOT blindly overwrite a better
        # earlier result: a stricter pass can return fewer or wrong squares, and rects[0]
        # becomes the sole scale reference reported at high confidence. So we keep the best
        # candidate set seen so far and only adopt a new one when it scores strictly higher.
        best_rects = list(rects)

        # Strategy 2: Try "both" polarity if the best so far is short of the 5-pattern
        if len(best_rects) < 5:
            print(f"[Calibration] Strategy 2: Both polarities (best so far {len(best_rects)}, need 5)")
            dets = detect_dark_squares_robust(
                img_bgr,
                edge_mm=edge_len_mm,
                polarity="both",                  # Both dark and bright
                enforce_calibration_pattern=True,
                calibration_outer_only=False,
                min_area=100,
                max_area_ratio=0.6,
                max_aspect=1.5,
                min_fill_ratio=0.5,
                min_hull_ratio=0.8,
                min_compactness=0.4,
                border_margin_frac=0.01,
                debug=True,
            )
            print(f"[Calibration] Strategy 2 found {len(dets)} detections")
            cand = [(x, y, w, h) for (_score, x, y, w, h, _mean) in dets]
            if _pattern_score(cand) > _pattern_score(best_rects):
                best_rects = cand

        # Strategy 3: Very relaxed for distant/angled shots
        if len(best_rects) < 5:
            print(f"[Calibration] Strategy 3: Very relaxed (best so far {len(best_rects)}, need 5)")
            dets = detect_dark_squares_robust(
                img_bgr,
                edge_mm=edge_len_mm,
                polarity="both",
                enforce_calibration_pattern=True,
                calibration_outer_only=False,
                min_area=50,                      # Very low for distant shots
                max_area_ratio=0.7,
                max_aspect=2.0,                   # Very permissive for angles
                min_fill_ratio=0.4,
                min_hull_ratio=0.75,
                min_compactness=0.3,
                clahe_clip=3.0,
                debug=True,
            )
            print(f"[Calibration] Strategy 3 found {len(dets)} detections")
            cand = [(x, y, w, h) for (_score, x, y, w, h, _mean) in dets]
            if _pattern_score(cand) > _pattern_score(best_rects):
                best_rects = cand

        # Adopt the best candidate set the strategies produced.
        rects[:] = best_rects

        # Strategy 4: Last resort - general square detection without pattern enforcement
        if len(rects) == 0:
            print(f"[Calibration] Strategy 4: General square detection (no pattern enforcement)")
            dets = detect_dark_squares_robust(
                img_bgr,
                edge_mm=edge_len_mm,
                polarity="both",
                enforce_calibration_pattern=False,  # Disable pattern requirement
                min_area=200,
                max_area_ratio=0.6,
                max_aspect=1.5,
            )
            for (_score, x, y, w, h, _mean) in dets:
                rects.append((x, y, w, h))
    else:
        # Original multi-threshold approach
        for t in thresholds:
            dets = detect_dark_squares(img_bgr, brightness_thresh=t)
            for (_score, x, y, w, h, _mean) in dets:
                rects.append((x, y, w, h))
    
    # Store pre-dedup count for logging
    pre_dedup_count = len(rects)
    rects = _dedup_rects(rects, iou_thresh=0.6)
    rects = sorted(rects, key=lambda r: r[2]*r[3], reverse=True)

    # Enhanced debug logging
    print(f"[Calibration] Detection summary:")
    print(f"  - Image size: {W}x{H} ({W*H:,} pixels)")
    print(f"  - Min area threshold: {MIN_AREA_DEFAULT} px² ({MIN_AREA_DEFAULT/(W*H)*100:.3f}%)")
    print(f"  - Candidates before dedup: {pre_dedup_count}")
    print(f"  - Candidates after dedup: {len(rects)}")
    if len(rects) > 0:
        for i, (x, y, w, h) in enumerate(rects[:5], 1):
            print(f"  - Candidate #{i}: pos=({x},{y}) size={w}x{h} area={w*h:,}px² ({w*h/(W*H)*100:.2f}%)")
    else:
        print(f"  ⚠️  No candidates found - try lowering min_area or adjusting lighting")


    markers: List[Dict[str, Any]] = []
    refinement_failures = 0

    # Separate outer square (largest, first) from inner squares (rest)
    # When calibration_outer_only=False with successful pattern matching:
    #   - rects[0] = outer square (black square)
    #   - rects[1:5] = 4 inner white squares
    # If pattern matching failed, rects might contain unorganized squares

    outer_rect = rects[0] if len(rects) > 0 else None
    inner_rects = rects[1:] if len(rects) > 1 else []

    print(f"[Calibration] Square separation:")
    print(f"  - Total rects: {len(rects)}")
    print(f"  - Outer square: {'Found' if outer_rect else 'Not found'}")
    print(f"  - Inner squares: {len(inner_rects)} detected")

    if len(rects) == 5:
        print(f"  ✅ Full calibration pattern detected (1 outer + 4 inner)")
    elif len(rects) > 0:
        print(f"  ⚠️  Incomplete pattern (expected 5, got {len(rects)}) - pattern matching may have failed")
    else:
        print(f"  ❌ No squares detected")

    # Save debug image showing detected rectangles BEFORE edge refinement.
    # Written OUTSIDE the web-served uploads/ dir, under a unique name, so enabling debug
    # never exposes a user's photo at a public URL and concurrent uploads don't collide.
    if SAVE_DEBUG_IMAGES and len(rects) > 0:
        debug_img = img_bgr.copy()
        for idx, (x, y, w, h) in enumerate(rects):
            color = (0, 255, 255) if idx == 0 else (255, 255, 0)  # Yellow for outer, cyan for inner
            cv2.rectangle(debug_img, (x, y), (x+w, y+h), color, 3)
            cv2.putText(debug_img, f"#{idx}", (x+5, y+25),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        debug_dir = os.path.join(os.path.dirname(__file__), "debug_out")
        os.makedirs(debug_dir, exist_ok=True)
        debug_path = os.path.join(debug_dir, f"debug_detected_rects_{uuid.uuid4().hex[:8]}.jpg")
        cv2.imwrite(debug_path, debug_img)
        print(f"  💾 Debug image saved: {debug_path}")

    # Process outer black square for calibration measurement
    print(f"\n[Calibration] Processing squares for overlay:")
    if outer_rect is not None:
        x, y, w, h = outer_rect
        print(f"  📦 Outer square: pos=({x},{y}) size={w}x{h} area={w*h:,}px²")

        # Crop + optional downscale
        crop, ox, oy = _crop_region(img_bgr, x, y, w, h, PADDING_PX)
        print(f"     Cropped region: {crop.shape[1]}x{crop.shape[0]}px at offset=({ox},{oy})")

        crop_ds = (cv2.resize(
            crop,
            (max(1, int(crop.shape[1]*DOWNSCALE_FACTOR)),
             max(1, int(crop.shape[0]*DOWNSCALE_FACTOR))),
            interpolation=cv2.INTER_AREA
        ) if DOWNSCALE_FACTOR < 1.0 else crop)

        # Find principal quad and corners (using dark polarity for black square)
        print(f"     Finding edges with polarity='dark', max_edges={MAX_EDGES}")
        edge_metrics: Dict[str, Any] = {}
        edge_vis, _n_edges, warped, corners_local = find_main_edges(
            crop_ds, MAX_EDGES, warp=True, polarity="dark", metrics=edge_metrics
        )
        print(f"     Edge detection found {_n_edges} contours, corners={'found' if corners_local else 'NOT FOUND'}")

        corner_source = "refined"
        if not corners_local:
            # Precise refinement failed — fall back to a rotated bounding box so we still
            # produce a (slightly less exact) scale instead of leaving the image uncalibrated.
            # The detected square sits at (x-ox, y-oy) within the crop; scale by DOWNSCALE.
            sc = DOWNSCALE_FACTOR if DOWNSCALE_FACTOR > 0 else 1.0
            rect_local = ((x - ox) * sc, (y - oy) * sc, w * sc, h * sc)
            corners_local = _fallback_square_corners(crop_ds, rect_local, polarity="dark")
            corner_source = "bbox"
            if corners_local:
                print(f"     ↩︎ Using minAreaRect fallback for outer square corners")

        if corners_local:
            # Map corners back to full image coords
            denom = (DOWNSCALE_FACTOR if DOWNSCALE_FACTOR > 0 else 1.0)
            mapped: List[Tuple[int,int]] = [
                (ox + int(cx / denom), oy + int(cy / denom))
                for (cx, cy) in corners_local
            ]

            # A precisely-refined but strongly-foreshortened (tilted) marker still
            # yields a clean rectangle from minAreaRect, so its scale can be off
            # without the corners looking wrong; the distortion drives confidence.
            distortion = float(edge_metrics.get("distortion_deg", 0.0))
            # Map the TRUE trapezoid corners (crop coords) to the full frame for the
            # rectifying homography — same offset/downscale mapping as `mapped`.
            quad_crop = edge_metrics.get("quad")
            homography_corners = (
                [(ox + int(qx / denom), oy + int(qy / denom)) for (qx, qy) in quad_crop]
                if quad_crop else None
            )
            marker = _record_marker(overlay, mapped, edge_len_mm, corner_source, distortion, line_thickness, homography_corners)
            if marker is not None:
                markers.append(marker)
                if marker["confidence"] == "low" and corner_source == "refined":
                    print(f"     ⚠️  Perspective distortion {distortion:.1f}° > {PERSPECTIVE_MAX_DEG}° — flagging low confidence")
                print(f"  ✅ Outer square calibrated ({corner_source}): {marker['edge_px']:.1f}px = {edge_len_mm}mm → {marker['mm_per_px']:.4f} mm/px")
        else:
            refinement_failures += 1
            print(f"  ⚠️  Outer square failed corner refinement (no fallback contour)")

    # Process inner white squares for visualization only (no calibration measurement)
    print(f"\n  🔲 Processing {len(inner_rects)} inner white squares:")
    for idx, (x, y, w, h) in enumerate(inner_rects, 1):
        print(f"     Inner square #{idx}: pos=({x},{y}) size={w}x{h} area={w*h:,}px²")

        # Crop + optional downscale
        crop, ox, oy = _crop_region(img_bgr, x, y, w, h, PADDING_PX)
        print(f"       Cropped: {crop.shape[1]}x{crop.shape[0]}px at offset=({ox},{oy})")

        crop_ds = (cv2.resize(
            crop,
            (max(1, int(crop.shape[1]*DOWNSCALE_FACTOR)),
             max(1, int(crop.shape[0]*DOWNSCALE_FACTOR))),
            interpolation=cv2.INTER_AREA
        ) if DOWNSCALE_FACTOR < 1.0 else crop)

        # Find principal quad and corners (using bright polarity for white squares)
        print(f"       Finding edges with polarity='bright', max_edges={MAX_EDGES}")
        edge_vis, _n_edges, warped, corners_local = find_main_edges(
            crop_ds, MAX_EDGES, warp=True, polarity="bright"
        )
        print(f"       Edge detection found {_n_edges} contours, corners={'found' if corners_local else 'NOT FOUND'}")

        if corners_local:
            # Map corners back to full image coords
            denom = (DOWNSCALE_FACTOR if DOWNSCALE_FACTOR > 0 else 1.0)
            mapped: List[Tuple[int,int]] = [
                (ox + int(cx / denom), oy + int(cy / denom))
                for (cx, cy) in corners_local
            ]

            # Draw inner square in CYAN (255,255,0)
            cv2.polylines(overlay, [np.array(mapped, np.int32)], True, (255,255,0), line_thickness)
            for (gx, gy) in mapped:
                cv2.circle(overlay, (gx, gy), 8, (0,0,0), -1)
                cv2.circle(overlay, (gx, gy), 5, (255,255,0), -1)

            print(f"  ✅ Inner square #{idx} visualized")
        else:
            refinement_failures += 1
            print(f"  ⚠️  Inner square #{idx} failed corner refinement")

    # Last-resort fallback: no bright-pad pattern yielded a usable scale. Try to
    # detect a plain (unpadded) solid dark square directly on the full frame. This
    # matches the documented target ("a solid black square, optionally with 4 white
    # inner pads") so calibration still works when the pads are absent or unreadable.
    if len(markers) == 0:
        print(f"\n[Calibration] Fallback: plain dark-square detection on full frame")
        plain_metrics: Dict[str, Any] = {}
        _v, _n, _w, corners_plain = find_main_edges(
            img_bgr, MAX_EDGES, warp=True, polarity="dark", metrics=plain_metrics,
            allow_border=True,  # a full-frame square legitimately touches the edge
        )
        if corners_plain:
            mapped = [(int(cx), int(cy)) for (cx, cy) in corners_plain]
            # Full-frame detection → no crop offset/downscale, so the true corners
            # map with identity.
            quad_plain = plain_metrics.get("quad")
            homography_corners = [(int(qx), int(qy)) for (qx, qy) in quad_plain] if quad_plain else None
            plain_distortion = float(plain_metrics.get("distortion_deg", 0.0))
            marker = _record_marker(overlay, mapped, edge_len_mm, "plain", plain_distortion, line_thickness, homography_corners)
            if marker is not None:
                markers.append(marker)
                print(f"  ✅ Plain square calibrated: {marker['edge_px']:.1f}px = {edge_len_mm}mm → {marker['mm_per_px']:.4f} mm/px (verify)")
        else:
            print(f"  ⚠️  No plain square found either — image will be uncalibrated")

    elapsed = time.time() - start_time
    print(f"[Calibration] Processing complete:")
    print(f"  - Successfully calibrated: {len(markers)} marker(s)")
    print(f"  - Failed corner refinement: {refinement_failures}")
    print(f"  - Total processing time: {elapsed:.3f}s")
    if len(markers) > 0:
        print(f"  - Time per marker: {elapsed/len(markers):.3f}s")
    if len(markers) == 0 and len(rects) > 0:
        print(f"  ⚠️  All candidates failed corner refinement - check image quality and marker clarity")
    # Global averages
    mm_per_px_vals = [m["mm_per_px"] for m in markers] or [0.0]
    mm_per_px_avg = float(np.mean(mm_per_px_vals))
    px_per_mm_avg = (1.0 / mm_per_px_avg) if mm_per_px_avg > 0 else 0.0

    # Overall confidence: "high" only if a precisely-refined marker was found;
    # "low" if we had to fall back (bbox/plain) — the UI warns and suggests Set Scale.
    if markers:
        calibration_confidence = "high" if any(m.get("confidence") == "high" for m in markers) else "low"
    else:
        calibration_confidence = "none"

    # Primary perspective homography (image px -> unit square) for the viewer to
    # rectify measurements. Uses the first marker that carries one.
    homography = next((m["homography"] for m in markers if m.get("homography")), None)

    # No RELIABLE calibration square? Look for a sheet of paper as a fallback reference.
    # We run whenever confidence isn't "high" — including the trap case where the detector
    # greedily latched onto a big bright rectangle (e.g. the sheet itself, or the part) as
    # a low-confidence "square" and produced a bogus 30 mm scale. The paper detector's
    # aspect filter rejects a real ~square, so it only fires on a genuinely paper-shaped
    # quad. We return only its corners; the client confirms the real size (A4/Letter) —
    # four corners yield a perspective homography, so a tilted photo still measures true
    # (see detect_paper). Never auto-applied.
    detected_rectangle = None
    if calibration_confidence != "high":
        try:
            from detect_paper import detect_paper_sheet
            detected_rectangle = detect_paper_sheet(img_bgr)
            if detected_rectangle:
                print(f"[Calibration] No square found; detected a paper sheet "
                      f"(guess={detected_rectangle.get('guess')}, "
                      f"aspect={detected_rectangle.get('aspect'):.2f}) — offering it as scale")
        except Exception as e:
            print(f"[Calibration] Paper-sheet detection skipped: {e}")

    cal_data: Dict[str, Any] = {
        "image": None,  # filled in save_outputs() if original file exists
        "image_size": {"width": int(W), "height": int(H)},
        "marker_size_mm": float(edge_len_mm),   # <-- always the runtime value
        "mm_per_px": mm_per_px_avg,
        "pixels_per_mm": px_per_mm_avg,
        "calibration_confidence": calibration_confidence,
        "homography": homography,
        "detected_rectangle": detected_rectangle,  # fallback paper corners (client confirms size)
        "quality": _image_quality(img_bgr),        # blur/brightness nudges for the client
        "markers": markers
    }

    return cal_data, overlay


# ----------------------------
# Output helper (used by app)
# ----------------------------

def save_outputs(image_name: str,
                 cal_data: Dict[str, Any],
                 overlay_img: np.ndarray,
                 out_dir: str) -> Tuple[str, Optional[str]]:
    """
    Persist artifacts required by the single-page viewer.

    Returns (json_path, overlay_path or None)

    - Writes ONLY the JSON (tiny), so the viewer can fetch it.
    - Overlay JPG remains disabled by default.
    - Keeps the current raw upload (so the browser can fetch it).
    Retention of old uploads is handled by the app-level reaper (app.py), not here.
    """
    os.makedirs(out_dir, exist_ok=True)

    base = os.path.splitext(os.path.basename(image_name))[0]
    json_path = os.path.join(out_dir, f"{base}.calibration.json")
    overlay_path = os.path.join(out_dir, f"{base}_overlay.jpg")

    # Include the original image filename if it still exists
    orig_any = None
    for ext in IMAGE_EXTS:
        p = os.path.join(out_dir, base + ext)
        if os.path.exists(p):
            orig_any = os.path.basename(p)
            break

    # Prepare JSON (ensure correct top-level fields)
    cal = dict(cal_data)
    # Always enforce runtime marker size (do not keep stale values)
    if "marker_size_mm" not in cal or cal["marker_size_mm"] is None:
        # If upstream forgot, fall back to default
        cal["marker_size_mm"] = float(EDGE_MM_DEFAULT)
    else:
        cal["marker_size_mm"] = float(cal["marker_size_mm"])

    cal["image"] = orig_any  # viewer can load the current file, or ignore if None

    # Write JSON atomically. Dot-prefix the temp so the uploads reaper (which skips
    # dot-prefixed in-flight files) can't unlink a concurrent request's temp before its
    # os.replace — the visible ".calibration.<rand>.json" name otherwise matched its
    # reapable .json suffix.
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f".{base}.calibration.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cal, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, json_path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise

    # Optionally save overlay
    if SAVE_OVERLAY_IMAGE and overlay_img is not None:
        try:
            fd2, tmp_over = tempfile.mkstemp(dir=out_dir, prefix=f".{base}_overlay.", suffix=".jpg")
            os.close(fd2)
            cv2.imwrite(tmp_over, overlay_img)
            os.replace(tmp_over, overlay_path)
        except Exception:
            overlay_path = None
    else:
        overlay_path = None

    return json_path, overlay_path
