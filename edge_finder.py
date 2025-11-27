import cv2
import numpy as np
from typing import Tuple, List, Optional


def _auto_canny(image: np.ndarray, sigma: float = 0.33) -> np.ndarray:
    """Automatic Canny edge detection using median-based thresholds."""
    v = float(np.median(image))
    lower = int(max(0, (1.0 - sigma) * v))
    upper = int(min(255, (1.0 + sigma) * v))
    return cv2.Canny(image, lower, upper)


def _order_quad(pts: np.ndarray) -> np.ndarray:
    """
    Order four points consistently as (top-left, top-right, bottom-right, bottom-left).

    This makes downstream geometry (drawing, warping) predictable even when
    OpenCV returns vertices in an arbitrary order.
    """
    pts = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
    if pts.shape[0] != 4:
        raise ValueError(f"_order_quad expected 4 points, got {pts.shape[0]}")

    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)

    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(s)]       # top-left  (smallest x+y)
    ordered[2] = pts[np.argmax(s)]       # bottom-right (largest x+y)
    ordered[1] = pts[np.argmin(diff)]    # top-right (smallest x-y)
    ordered[3] = pts[np.argmax(diff)]    # bottom-left (largest x-y)
    return ordered


def _score_quad(quad: np.ndarray, img_shape) -> float:
    """
    Heuristic quality score for a quadrilateral:

    - larger area is better
    - shapes that fill their bounding box are better
    - strong penalty for very elongated rectangles
    """
    quad = np.asarray(quad, dtype=np.float32).reshape(-1, 2)
    contour = quad.reshape(-1, 1, 2)

    area = cv2.contourArea(contour)
    if area <= 0:
        return 0.0

    x, y, w, h = cv2.boundingRect(contour)
    box_area = float(w * h) if w > 0 and h > 0 else 1.0
    fill_ratio = float(area) / box_area

    aspect = max(w, h) / float(max(1, min(w, h)))
    aspect_penalty = max(0.0, aspect - 1.0)  # 0 for perfect square

    return float(area * fill_ratio / (1.0 + aspect_penalty))


def find_main_edges(
    crop: np.ndarray,
    max_edges: int = 10,
    warp: bool = False,
    warp_size: int = 512,
    min_area: float = 1500.0,
    debug: bool = False,
    use_enhanced_preprocessing: bool = True,
):
    """
    Find the dominant square-like contour in the crop and return its corners.

    Args
    ----
    crop:
        BGR image region around a candidate marker (e.g. from detect_dark_squares).
    max_edges:
        Maximum number of largest contours to inspect.
    warp:
        If True, also return a perspective-warped square view.
    warp_size:
        Size of the warped output (warp_size x warp_size).
    min_area:
        Minimum contour area (in pixels) required to be considered.
    use_enhanced_preprocessing:
        Enable enhanced preprocessing with bilateral filtering and adaptive methods
    debug:
        If True, shows intermediate debug windows (desktop only).

    Returns
    -------
    overlay : np.ndarray
        Copy of the crop with contours and final quad drawn.
    n_contours : int
        Number of contours considered.
    warped : Optional[np.ndarray]
        Rectified top-down view of the marker (or None).
    corners : Optional[List[Tuple[int, int]]]
        Four corner coordinates in local crop coordinates, ordered TL,TR,BR,BL.
    """
    if crop is None or crop.size == 0:
        return crop, 0, None, None

    # --- Enhanced Pre-processing ---
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    
    if use_enhanced_preprocessing:
        # Bilateral filter preserves edges while reducing noise
        gray = cv2.bilateralFilter(gray, 9, 75, 75)
        
        # Enhanced CLAHE for better contrast
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        eq = clahe.apply(gray)
        
        # Combine with adaptive histogram equalization
        eq_global = cv2.equalizeHist(gray)
        eq = cv2.addWeighted(eq, 0.7, eq_global, 0.3, 0)
    else:
        gray = cv2.bilateralFilter(gray, 7, 50, 50)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        eq = clahe.apply(gray)

    # Multi-scale, multi-sigma Canny edge detection
    edges = np.zeros_like(eq)
    for s in (0.15, 0.25, 0.33, 0.45):
        edges = cv2.bitwise_or(edges, _auto_canny(eq, sigma=s))
    
    # Add structured edge detection using morphological gradient
    morph_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    gradient = cv2.morphologyEx(eq, cv2.MORPH_GRADIENT, morph_kernel)
    _, gradient_thresh = cv2.threshold(gradient, 20, 255, cv2.THRESH_BINARY)
    edges = cv2.bitwise_or(edges, gradient_thresh)

    # Connect broken edges with larger kernel
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k, iterations=2)
    
    # Dilate slightly to connect nearby edges
    edges = cv2.dilate(edges, k, iterations=1)

    # Fallback: if very few edge pixels, try threshold-based mask as well
    if cv2.countNonZero(edges) < 0.01 * edges.size:
        _, mask = cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
        edges = cv2.bitwise_or(edges, mask)

    if debug:
        cv2.imshow("edges", edges)
        cv2.waitKey(1)

    # --- Contour detection ---
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[: max_edges]

    overlay = crop.copy()
    best_quad = None
    best_score = 0.0

    # --- Evaluate candidate contours ---
    for i, c in enumerate(contours):
        area = cv2.contourArea(c)
        if area < min_area:
            continue

        # Use the convex hull to avoid notches / small gaps
        hull = cv2.convexHull(c)
        peri = cv2.arcLength(hull, True)

        # First try polygon approximation
        approx = cv2.approxPolyDP(hull, 0.02 * peri, True)

        candidate_quads = []

        if len(approx) == 4 and cv2.isContourConvex(approx):
            candidate_quads.append(approx.reshape(4, 2))
        elif len(approx) > 4:
            # Try to fit a rectangle to multi-sided polygon
            # Fallback: rotated bounding box
            rect = cv2.minAreaRect(hull)
            box = cv2.boxPoints(rect)
            candidate_quads.append(box)
        else:
            # For very irregular contours, use rotated rect
            rect = cv2.minAreaRect(hull)
            box = cv2.boxPoints(rect)
            candidate_quads.append(box)

        for quad in candidate_quads:
            try:
                ordered = _order_quad(quad)
            except ValueError:
                continue

            score = _score_quad(ordered, crop.shape)
            
            # Bonus for quads that are more central in the crop
            center_x, center_y = crop.shape[1] / 2, crop.shape[0] / 2
            quad_center = np.mean(ordered, axis=0)
            dist_to_center = np.linalg.norm(quad_center - [center_x, center_y])
            max_dist = np.sqrt(center_x**2 + center_y**2)
            centrality_bonus = 1.0 - (dist_to_center / max_dist) * 0.3
            score *= centrality_bonus
            
            if score > best_score:
                best_score = score
                best_quad = ordered

        # Draw all candidate contours lightly for context
        cv2.drawContours(overlay, [hull], -1, (80, 80, 80), 1)

    # Highlight the best quad (if any)
    if best_quad is not None:
        pts = best_quad.reshape(-1, 2)
        cv2.polylines(
            overlay,
            [pts.astype(np.int32)],
            isClosed=True,
            color=(0, 255, 255),
            thickness=2,
            lineType=cv2.LINE_AA,
        )
        for j, (x, y) in enumerate(pts):
            cv2.circle(overlay, (int(x), int(y)), 8, (0, 0, 0), -1)
            cv2.circle(overlay, (int(x), int(y)), 5, (0, 255, 255), -1)
            cv2.putText(
                overlay,
                str(j),
                (int(x) + 6, int(y) - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

    # Optional perspective warp
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
        M = cv2.getPerspectiveTransform(pts_src, pts_dst)
        warped = cv2.warpPerspective(crop, M, (warp_size, warp_size))

    corners = (
        [(int(x), int(y)) for (x, y) in best_quad]
        if best_quad is not None
        else None
    )

    if debug:
        cv2.imshow("overlay", overlay)
        cv2.waitKey(1)

    return overlay, len(contours), warped, corners
