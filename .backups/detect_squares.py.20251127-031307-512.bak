import cv2
import numpy as np
from typing import List, Tuple, Optional


def _detect_nested_pattern(
    gray: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    min_white_squares: int = 2,
) -> Tuple[bool, int]:
    """
    Verify that a dark square contains lighter/white squares inside.
    
    Returns:
        (has_nested_pattern, num_white_squares)
    """
    roi = gray[y : y + h, x : x + w]
    if roi.size == 0:
        return False, 0
    
    # Detect bright regions inside the dark square
    _, white_mask = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    # Find connected components of white regions
    contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    # Count square-like white regions (reasonable size and aspect ratio)
    white_squares = sum(
        1 for c in contours
        if cv2.contourArea(c) > (w * h * 0.02) and cv2.contourArea(c) < (w * h * 0.35)
    )
    
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
    Second-pass check inside a candidate ROI.

    Uses edges + minAreaRect on the largest contour in the ROI to verify
    that it looks like a solid square-ish region.

    Returns:
        (ok, box_global)
        ok: bool
        box_global: (4, 2) int32 array of rotated rectangle points in *global*
                    image coordinates, or None if not valid.
    """
    # First check for nested pattern if requested
    if check_nested:
        has_pattern, num_squares = _detect_nested_pattern(gray, x, y, w, h, min_nested_squares)
        if not has_pattern:
            return False, None
    
    roi = gray[y : y + h, x : x + w]
    if roi.size == 0:
        return False, None

    # Local pre-processing inside ROI
    roi_blur = cv2.GaussianBlur(roi, (5, 5), 0)
    _, roi_bin = cv2.threshold(
        roi_blur,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )

    # Edges help stabilize shape even with inner white squares
    # Use adaptive thresholding for better edge detection in varying lighting
    roi_adaptive = cv2.adaptiveThreshold(roi, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY_INV, 11, 2)
    roi_edges = cv2.Canny(roi_adaptive, 40, 120)

    contours, _ = cv2.findContours(
        roi_edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return False, None

    # Use largest contour in ROI
    c = max(contours, key=cv2.contourArea)
    hull = cv2.convexHull(c)
    contour_area = cv2.contourArea(hull)
    if contour_area <= 0:
        return False, None

    rot_rect = cv2.minAreaRect(hull)
    box = cv2.boxPoints(rot_rect)  # (4, 2) float32
    box = box.astype(np.float32)

    # Side lengths and aspect of rotated box
    side_lengths = [
        float(np.linalg.norm(box[i] - box[(i + 1) % 4])) for i in range(4)
    ]
    max_side = max(side_lengths)
    min_side = max(1e-3, min(side_lengths))
    aspect = max_side / min_side  # >= 1

    # Reject clearly non-square shapes
    if aspect > max_aspect:
        return False, None

    # Fill ratio: contour area vs rotated box area
    box_area = max_side * min_side
    fill = contour_area / (box_area + 1e-6)
    if fill < min_fill:
        return False, None

    # Convert box coordinates from ROI space to global image space
    box_global = box.copy()
    box_global[:, 0] += float(x)
    box_global[:, 1] += float(y)
    box_global = box_global.astype(np.int32)

    if debug:
        dbg = cv2.cvtColor(roi, cv2.COLOR_GRAY2BGR)
        cv2.drawContours(dbg, [box.astype(np.int32)], -1, (0, 255, 0), 2)
        cv2.imshow("refine_roi", dbg)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    return True, box_global


def detect_dark_squares(
    img,
    min_area: float = 800.0,
    max_area_ratio: float = 0.6,
    max_aspect: float = 1.5,
    brightness_thresh: Optional[int] = None,
    approx_eps: float = 0.02,
    max_results: int = 25,
    clahe_clip: float = 2.0,
    use_multi_scale: bool = True,
    scale_factors: Tuple[float, ...] = (1.0, 0.75, 0.5),
    check_nested_pattern: bool = False,
    min_nested_squares: int = 0,
    clahe_grid: Tuple[int, int] = (8, 8),
    blur_ksize: int = 5,
    morph_kernel_size: int = 5,
    morph_iterations: int = 2,
    dark_weight: float = 0.6,
    shape_weight: float = 0.4,
    debug: bool = False,
) -> List[Tuple[float, int, int, int, int, float]]:
    """Detect dark, nearly-square regions in a BGR image.

    Two-pass approach:
      1) Global: find dark-ish, roughly square candidates over entire frame.
      2) Per-candidate: re-check each ROI with a more detailed square test.

    Args:
        img: Input BGR image
        min_area: Minimum contour area in pixels
        max_area_ratio: Maximum ratio of contour area to frame area
        max_aspect: Maximum aspect ratio for square-like shapes
        brightness_thresh: Fixed threshold for dark regions (None = auto with Otsu)
        approx_eps: Epsilon for polygon approximation
        max_results: Maximum number of candidates to return
        clahe_clip: CLAHE clip limit for contrast enhancement
        use_multi_scale: Enable multi-scale detection for robustness
        scale_factors: Scales to process if multi-scale is enabled
        check_nested_pattern: Verify nested white squares inside dark square
        min_nested_squares: Minimum number of nested white squares required
        
    Returns a list of candidates sorted by descending score:
        (score, x, y, w, h, mean_val)

    Tuned for a dark outer square marker against a lighter background,
    e.g. your black calibration square with inner white squares.
    """
    if img is None or not hasattr(img, "shape"):
        return []

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h_img, w_img = gray.shape[:2]
    frame_area = float(h_img * w_img)

    all_candidates = []
    
    # Multi-scale detection for better robustness
    scales = scale_factors if use_multi_scale else (1.0,)
    
    for scale in scales:
        if scale != 1.0:
            scaled_w = int(w_img * scale)
            scaled_h = int(h_img * scale)
            gray_scaled = cv2.resize(gray, (scaled_w, scaled_h), interpolation=cv2.INTER_AREA)
        else:
            gray_scaled = gray
            scale = 1.0
            
        # --- Step 1: Enhanced preprocessing with bilateral filter ---
        # Bilateral filter preserves edges while smoothing noise
        blur = cv2.bilateralFilter(gray_scaled, 9, 75, 75)
        
        # Apply CLAHE for local contrast enhancement
        clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=clahe_grid)
        enhanced = clahe.apply(blur)
        
        # --- Step 2: Adaptive dark region detection ---
        if brightness_thresh is None:
            # Use Otsu's method for automatic threshold selection
            thresh_val, base_mask = cv2.threshold(
                enhanced,
                0,
                255,
                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
            )
        else:
            _, base_mask = cv2.threshold(
                enhanced,
                brightness_thresh,
                255,
                cv2.THRESH_BINARY_INV,
            )
            thresh_val = brightness_thresh
        
        # Adaptive threshold as complement for varying lighting
        adaptive_mask = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 21, 5
        )
        
        # Combine global and adaptive masks
        combined_mask = cv2.bitwise_or(base_mask, adaptive_mask)
        
        # Multi-scale edge detection with different parameters
        edges1 = cv2.Canny(enhanced, 30, 90)
        edges2 = cv2.Canny(enhanced, 50, 150)
        edges_combined = cv2.bitwise_or(edges1, edges2)
        
        # Combine masks with edges
        dark_mask = cv2.bitwise_or(combined_mask, edges_combined)
        
        # Morphological operations to clean up and connect regions
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (morph_kernel_size, morph_kernel_size))
        dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, k, iterations=morph_iterations)
        
        # Additional opening to remove small noise
        k_open = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_OPEN, k_open, iterations=1)
        
        if debug:
            cv2.imshow(f"enhanced_{scale}", enhanced)
            cv2.imshow(f"dark_mask_{scale}", dark_mask)
            cv2.waitKey(0)

        # --- Step 3: Find contours (candidate generation) ---
        contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        # Scale-adjusted minimum area
        scale_factor_area = scale * scale
        eff_min_area = max(min_area * scale_factor_area, 0.0003 * (gray_scaled.shape[0] * gray_scaled.shape[1]))
        
        scale_candidates = []
        
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
                x, y, w, h = cv2.boundingRect(np.intp(quad))
                
                # Scale back to original coordinates
                x_orig = int(x / scale)
                y_orig = int(y / scale)
                w_orig = int(w / scale)
                h_orig = int(h / scale)
                
                if w_orig <= 0 or h_orig <= 0:
                    continue

                # Rough aspect filter
                long_side = float(max(w_orig, h_orig))
                short_side = float(max(1, min(w_orig, h_orig)))
                aspect = long_side / short_side
                if aspect > max_aspect:
                    continue

                # Skip frame-border artifacts
                border_tol = 5
                if (
                    x_orig <= border_tol
                    or y_orig <= border_tol
                    or x_orig + w_orig >= w_img - border_tol
                    or y_orig + h_orig >= h_img - border_tol
                ):
                    continue

                # --- Step 4: Per-candidate refinement with nested pattern check ---
                ok, _ = _refine_square_in_roi(
                    gray,
                    x_orig,
                    y_orig,
                    w_orig,
                    h_orig,
                    min_fill=0.30,
                    max_aspect=1.4,
                    check_nested=check_nested_pattern,
                    min_nested_squares=min_nested_squares,
                    debug=False,
                )
                if not ok:
                    continue

                roi = gray[y_orig : y_orig + h_orig, x_orig : x_orig + w_orig]
                mean_val = float(np.mean(roi))
                
                # Enhanced scoring system
                # Darkness score
                dark_score = max(0.0, 1.0 - mean_val / 255.0)
                
                # Shape score (closer to square = higher score)
                shape_score = max(0.0, 1.0 - (aspect - 1.0) * 0.5)
                
                # Size score (prefer larger markers, normalized by frame)
                size_ratio = (w_orig * h_orig) / frame_area
                size_score = min(1.0, size_ratio / 0.1)  # Optimal around 10% of frame
                
                # Nested pattern bonus
                has_nested, num_nested = _detect_nested_pattern(
                    gray, x_orig, y_orig, w_orig, h_orig, min_nested_squares
                )
                nested_score = 1.0 if has_nested else 0.9
                
                # Weighted final score
                final_score = (
                    dark_weight * dark_score +
                    shape_weight * shape_score +
                    0.15 * size_score +
                    0.05 * nested_score
                )
                
                scale_candidates.append((final_score, x_orig, y_orig, w_orig, h_orig, mean_val, scale))
        
        all_candidates.extend(scale_candidates)
    
    # Deduplicate across scales using NMS-like approach
    if len(all_candidates) > 1:
        # Sort by score
        all_candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Non-maximum suppression
        final_candidates = []
        for candidate in all_candidates:
            score, x, y, w, h, mean_val, scale_used = candidate
            
            # Check if this overlaps significantly with existing candidates
            overlap = False
            for existing in final_candidates:
                _, ex, ey, ew, eh, _, _ = existing
                
                # Compute IoU
                x1 = max(x, ex)
                y1 = max(y, ey)
                x2 = min(x + w, ex + ew)
                y2 = min(y + h, ey + eh)
                
                if x2 > x1 and y2 > y1:
                    intersection = (x2 - x1) * (y2 - y1)
                    union = w * h + ew * eh - intersection
                    iou = intersection / union if union > 0 else 0
                    
                    if iou > 0.5:
                        overlap = True
                        break
            
            if not overlap:
                final_candidates.append(candidate)
                
        # Convert back to original format (without scale info)
        candidates = [(score, x, y, w, h, mean_val) for score, x, y, w, h, mean_val, _ in final_candidates]
    else:
        candidates = [(score, x, y, w, h, mean_val) for score, x, y, w, h, mean_val, _ in all_candidates]

    return candidates[:max_results]


def detect_dark_squares_robust(
    img,
    edge_mm: float = 30.0,
    **kwargs
) -> List[Tuple[float, int, int, int, int, float]]:
    """
    Wrapper for detect_dark_squares with robust multi-threshold detection.
    
    Combines results from multiple detection passes with different parameters
    to handle varying lighting conditions and marker appearances.
    
    Args:
        img: Input BGR image
        edge_mm: Expected edge length in mm (used for size priors)
        **kwargs: Additional arguments passed to detect_dark_squares
        
    Returns:
        Combined and deduplicated list of candidates
    """
    if img is None:
        return []
    
    # Estimate expected marker size in pixels based on image dimensions
    h, w = img.shape[:2]
    diagonal_px = np.sqrt(w*w + h*h)
    
    # Assume marker is roughly 5-20% of image diagonal
    estimated_marker_diagonal = diagonal_px * 0.10
    estimated_marker_area = (estimated_marker_diagonal / np.sqrt(2)) ** 2
    
    all_detections = []
    
    # Pass 1: Standard detection with auto-threshold
    detections1 = detect_dark_squares(
        img,
        brightness_thresh=None,  # Auto
        min_area=max(800, estimated_marker_area * 0.3),
        **kwargs
    )
    all_detections.extend(detections1)
    
    # Pass 2: Dark markers (low threshold)
    detections2 = detect_dark_squares(
        img,
        brightness_thresh=70,
        min_area=max(800, estimated_marker_area * 0.3),
        **kwargs
    )
    all_detections.extend(detections2)
    
    # Pass 3: Very dark markers
    detections3 = detect_dark_squares(
        img,
        brightness_thresh=50,
        min_area=max(800, estimated_marker_area * 0.3),
        **kwargs
    )
    all_detections.extend(detections3)
    
    # Deduplicate using IoU threshold
    if not all_detections:
        return []
    
    all_detections.sort(key=lambda x: x[0], reverse=True)
    
    unique_detections = []
    for detection in all_detections:
        score, x, y, w, h, mean_val = detection
        
        # Check overlap with existing detections
        is_duplicate = False
        for existing in unique_detections:
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
                    is_duplicate = True
                    break
        
        if not is_duplicate:
            unique_detections.append(detection)
    
    return unique_detections


def draw_squares(
    img,
    detections: List[Tuple[float, int, int, int, int, float]],
    color: Tuple[int, int, int] = (0, 255, 0),
    thickness: int = 10,
    label: bool = True,
):
    """
    Draw detected dark squares with a rotated outline that follows edges.

    We re-run the refinement on each ROI to get a rotated min-area rectangle,
    then draw that box on the full image. This hugs the marker edges much more
    closely than a simple axis-aligned bounding box.
    """
    if img is None or not hasattr(img, "shape"):
        return img

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    result = img.copy()

    for rank, (_, x, y, w, h, mean_val) in enumerate(detections, start=1):
        ok, box_global = _refine_square_in_roi(
            gray,
            x,
            y,
            w,
            h,
            min_fill=0.35,
            max_aspect=1.3,
            debug=False,
        )

        if ok and box_global is not None:
            cv2.drawContours(result, [box_global], -1, color, thickness)
        else:
            # Fallback: simple rectangle if refinement fails
            cv2.rectangle(result, (x, y), (x + w, y + h), color, thickness)

        if label:
            # Enhanced label with confidence score
            lx = x + 15
            ly = y + 45
            label_text = f"S{rank}"
            cv2.putText(
                result,
                label_text,
                (lx, ly),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 0, 0),
                3,
                cv2.LINE_AA,
            )
            cv2.putText(
                result,
                label_text,
                (lx, ly),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

    return result
