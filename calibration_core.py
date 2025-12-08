import os, cv2, json, time, tempfile
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

# Artifacts policy
SAVE_OVERLAY_IMAGE: bool = False    # keep False to avoid writing overlay jpgs
SAVE_DEBUG_IMAGES: bool = True      # Save annotated debug images showing detection results

# Cleanup policy
DELETE_ALL_OTHER_UPLOADS: bool = False  # keep previous uploads; clean up with separate job
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

def _delete_other_uploads(out_dir: str, keep_base: str) -> None:
    """
    Delete all image files in out_dir whose base name != keep_base.
    This keeps the folder tidy and avoids serving a previous run's image.
    """
    if not DELETE_ALL_OTHER_UPLOADS:
        return
    try:
        for name in os.listdir(out_dir):
            p = os.path.join(out_dir, name)
            if not os.path.isfile(p):
                continue
            base, ext = os.path.splitext(name)
            if ext.lower() not in IMAGE_EXTS:
                continue
            if base == keep_base:
                continue
            try:
                os.remove(p)
                print(f"[Calibration] Deleted old upload: {p}")
            except Exception as e:
                print(f"[Calibration] ⚠️ Failed to delete {p}: {e}")
    except FileNotFoundError:
        pass


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

        # Strategy 2: Try "both" polarity if Strategy 1 found < 5 squares
        if len(rects) < 5:
            print(f"[Calibration] Strategy 2: Both polarities (got {len(rects)}, need 5)")
            rects.clear()  # Clear and retry with different polarity
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
            for (_score, x, y, w, h, _mean) in dets:
                rects.append((x, y, w, h))

        # Strategy 3: Very relaxed for distant/angled shots
        if len(rects) < 5:
            print(f"[Calibration] Strategy 3: Very relaxed (got {len(rects)}, need 5)")
            rects.clear()
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
            for (_score, x, y, w, h, _mean) in dets:
                rects.append((x, y, w, h))

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

    # Save debug image showing detected rectangles BEFORE edge refinement
    if SAVE_DEBUG_IMAGES and len(rects) > 0:
        debug_img = img_bgr.copy()
        for idx, (x, y, w, h) in enumerate(rects):
            color = (0, 255, 255) if idx == 0 else (255, 255, 0)  # Yellow for outer, cyan for inner
            cv2.rectangle(debug_img, (x, y), (x+w, y+h), color, 3)
            cv2.putText(debug_img, f"#{idx}", (x+5, y+25),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        uploads_dir = os.path.join(os.path.dirname(__file__), "uploads")
        os.makedirs(uploads_dir, exist_ok=True)
        debug_path = os.path.join(uploads_dir, "debug_detected_rects.jpg")
        cv2.imwrite(debug_path, debug_img)
        print(f"  💾 Debug image saved: {debug_path}")
        print(f"     View at: http://localhost:8050/uploads/debug_detected_rects.jpg")

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
        edge_vis, _n_edges, warped, corners_local = find_main_edges(
            crop_ds, MAX_EDGES, warp=True, polarity="dark"
        )
        print(f"     Edge detection found {_n_edges} contours, corners={'found' if corners_local else 'NOT FOUND'}")

        if corners_local:
            # Map corners back to full image coords
            denom = (DOWNSCALE_FACTOR if DOWNSCALE_FACTOR > 0 else 1.0)
            mapped: List[Tuple[int,int]] = [
                (ox + int(cx / denom), oy + int(cy / denom))
                for (cx, cy) in corners_local
            ]

            # Compute scale
            px_edge = _avg_side_len(mapped)
            if px_edge > 0:
                mm_per_px = float(edge_len_mm) / float(px_edge)

                # Draw outer square in YELLOW (0,255,255)
                cv2.polylines(overlay, [np.array(mapped, np.int32)], True, (0,255,255), line_thickness)
                for (gx, gy) in mapped:
                    cv2.circle(overlay, (gx, gy), 10, (0,0,0), -1)
                    cv2.circle(overlay, (gx, gy), 7, (0,255,255), -1)

                # Record calibration data
                markers.append({
                    "edge_mm": float(edge_len_mm),
                    "mm_per_px": float(mm_per_px),
                    "corners": [{"x": int(a), "y": int(b)} for (a,b) in mapped]
                })
                print(f"  ✅ Outer square calibrated: {px_edge:.1f}px = {edge_len_mm}mm → {mm_per_px:.4f} mm/px")
        else:
            refinement_failures += 1
            print(f"  ⚠️  Outer square failed corner refinement")

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

    cal_data: Dict[str, Any] = {
        "image": None,  # filled in save_outputs() if original file exists
        "image_size": {"width": int(W), "height": int(H)},
        "marker_size_mm": float(edge_len_mm),   # <-- always the runtime value
        "mm_per_px": mm_per_px_avg,
        "pixels_per_mm": px_per_mm_avg,
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
    - Deletes ALL other images immediately to avoid "previous run" leftovers.
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

    # Write JSON atomically
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=f"{base}.calibration.", suffix=".json")
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

    # Delete ALL other uploads except the current one
    _delete_other_uploads(out_dir, keep_base=base)

    return json_path, overlay_path
