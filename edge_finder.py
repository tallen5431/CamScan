import os
import cv2
import numpy as np
from typing import Tuple, List, Optional

DEBUG = bool(os.getenv("CAMSCAN_DEBUG", "0") == "1")

# Edge detection configuration
EDGE_FINDER_CONFIG = {
    'min_area': 800.0,  # Lowered from 1500 for better detection
    'warp_size': 512,
    'corner_search_window_ratio': 0.08,  # 8% of marker size
    'corner_search_window_min': 8,       # Minimum 8 pixels
    'corner_max_distance_ratio': 0.8,    # Max distance from expected corner
    'harris_block_size': 2,
    'harris_ksize': 3,
    'harris_k': 0.04,
    'good_features_max_corners': 5,
    'good_features_quality': 0.03,
    'good_features_min_distance': 3,
    'edge_sample_threshold': 30,
    'subpix_window_size': (7, 7),
    'subpix_max_iterations': 50,
    'subpix_epsilon': 0.001,
}


def _auto_canny(image: np.ndarray, sigma: float = 0.33) -> np.ndarray:
    v = float(np.median(image))
    lower = int(max(0, (1.0 - sigma) * v))
    upper = int(min(255, (1.0 + sigma) * v))
    return cv2.Canny(image, lower, upper)


def _order_quad(pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float32).reshape(-1, 2)
    if pts.shape[0] != 4:
        raise ValueError(f"_order_quad expected 4 points, got {pts.shape[0]}")
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(s)]
    ordered[2] = pts[np.argmax(s)]
    ordered[1] = pts[np.argmin(diff)]
    ordered[3] = pts[np.argmax(diff)]
    return ordered


def _angle_score(quad: np.ndarray) -> float:
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
    quad = np.asarray(quad, dtype=np.float32).reshape(-1, 2)
    contour = quad.reshape(-1, 1, 2)
    area = cv2.contourArea(contour)
    if area <= 0:
        return 0.0
    x, y, w, h = cv2.boundingRect(contour)
    box_area = float(w * h) if w > 0 and h > 0 else 1.0
    fill_ratio = float(area) / box_area
    aspect = max(w, h) / float(max(1, min(w, h)))
    aspect_penalty = max(0.0, aspect - 1.0)
    angle_score = _angle_score(quad)
    center_x, center_y = img_shape[1] / 2, img_shape[0] / 2
    quad_center = np.mean(quad, axis=0)
    dist_to_center = np.linalg.norm(quad_center - [center_x, center_y])
    max_dist = np.sqrt(center_x**2 + center_y**2)
    centrality_bonus = 1.0 - (dist_to_center / max_dist) * 0.25
    score = float(area * fill_ratio * (0.75 * angle_score + 0.25) / (1.0 + aspect_penalty))
    score *= centrality_bonus
    return score


def _strong_preprocess(crop_gray: np.ndarray, focus_on_dark_square: bool = True) -> np.ndarray:
    """
    Preprocess image to find edges, with option to focus on dark square boundary.

    Args:
        crop_gray: Grayscale image
        focus_on_dark_square: If True, focuses on finding the outer boundary of dark regions
                             (useful for calibration markers with white squares inside)
    """
    # Enhanced preprocessing with bilateral filter to preserve edges
    blur = cv2.bilateralFilter(crop_gray, 9, 75, 75)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    eq = clahe.apply(blur)

    if focus_on_dark_square:
        # For calibration markers: find the DARK square boundary, not internal white squares
        # Invert to make dark regions bright, then find edges
        inverted = cv2.bitwise_not(eq)

        # Threshold to isolate dark regions
        _, dark_mask = cv2.threshold(eq, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Find the largest dark contour
        contours_dark, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours_dark:
            # Get largest dark region
            largest = max(contours_dark, key=cv2.contourArea)
            mask = np.zeros_like(dark_mask)
            cv2.drawContours(mask, [largest], -1, 255, thickness=cv2.FILLED)

            # Get edges of this dark region
            edges = cv2.Canny(mask, 100, 200)

            # Combine with gradient edges for robustness
            sx = cv2.Sobel(eq, cv2.CV_32F, 1, 0, ksize=3)
            sy = cv2.Sobel(eq, cv2.CV_32F, 0, 1, ksize=3)
            sob = cv2.magnitude(sx, sy)
            sob = np.uint8(np.clip(sob / (sob.max() + 1e-6) * 255.0, 0, 255))
            _, thr = cv2.threshold(sob, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            combined = cv2.bitwise_or(edges, thr)
        else:
            # Fallback to gradient-based detection
            sx = cv2.Sobel(eq, cv2.CV_32F, 1, 0, ksize=3)
            sy = cv2.Sobel(eq, cv2.CV_32F, 0, 1, ksize=3)
            sob = cv2.magnitude(sx, sy)
            sob = np.uint8(np.clip(sob / (sob.max() + 1e-6) * 255.0, 0, 255))
            can1 = _auto_canny(eq, sigma=0.33)
            _, thr = cv2.threshold(sob, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            combined = cv2.bitwise_or(can1, thr)
    else:
        # Original gradient-based approach
        sx = cv2.Sobel(eq, cv2.CV_32F, 1, 0, ksize=3)
        sy = cv2.Sobel(eq, cv2.CV_32F, 0, 1, ksize=3)
        sob = cv2.magnitude(sx, sy)
        sob = np.uint8(np.clip(sob / (sob.max() + 1e-6) * 255.0, 0, 255))
        can1 = _auto_canny(eq, sigma=0.33)
        _, thr = cv2.threshold(sob, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        combined = cv2.bitwise_or(can1, thr)

    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    combined = cv2.morphologyEx(combined, cv2.MORPH_CLOSE, k, iterations=2)
    combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, k, iterations=1)
    combined = cv2.dilate(combined, k, iterations=1)
    return combined


def find_main_edges(
    crop: np.ndarray,
    max_edges: int = 10,
    warp: bool = False,
    warp_size: int = None,
    min_area: float = None,
    debug: bool = False,
    use_enhanced_preprocessing: bool = True,
):
    cfg = EDGE_FINDER_CONFIG
    if warp_size is None:
        warp_size = cfg['warp_size']
    if min_area is None:
        min_area = cfg['min_area']

    if crop is None or crop.size == 0:
        return crop, 0, None, None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if use_enhanced_preprocessing:
        gray_b = cv2.bilateralFilter(gray, 9, 75, 75)
        preprocess_map = _strong_preprocess(gray_b, focus_on_dark_square=True)
    else:
        preprocess_map = _auto_canny(gray)
    if debug and DEBUG:
        try:
            cv2.imshow("preprocess_map", preprocess_map)
            cv2.waitKey(1)
        except Exception:
            pass
    contours, _ = cv2.findContours(preprocess_map, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[: max_edges]
    overlay = crop.copy()
    best_quad = None
    best_score = 0.0
    for i, c in enumerate(contours):
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        hull = cv2.convexHull(c)
        peri = cv2.arcLength(hull, True)
        approx = cv2.approxPolyDP(hull, 0.02 * peri, True)
        candidate_quads = []
        if len(approx) == 4 and cv2.isContourConvex(approx):
            candidate_quads.append(approx.reshape(4, 2))
        else:
            rect = cv2.minAreaRect(hull)
            box = cv2.boxPoints(rect)
            candidate_quads.append(box)
        for quad in candidate_quads:
            try:
                ordered = _order_quad(quad)
            except ValueError:
                continue
            try:
                # Refine corners by searching NEAR the quad vertices, not globally
                # This prevents picking up internal white square corners
                refined = []
                x, y, w, h = cv2.boundingRect(np.intp(ordered))

                # For each quad corner, search in a small local window
                for idx, p in enumerate(ordered):
                    px, py = int(p[0]), int(p[1])
                    # Smaller, more focused window
                    window_size = max(cfg['corner_search_window_min'],
                                    int(min(w, h) * cfg['corner_search_window_ratio']))
                    wx0 = max(0, px - window_size)
                    wy0 = max(0, py - window_size)
                    wx1 = min(crop.shape[1], px + window_size)
                    wy1 = min(crop.shape[0], py + window_size)

                    # Validate window has content
                    if wx1 <= wx0 or wy1 <= wy0:
                        refined.append(p)
                        continue

                    window = cv2.cvtColor(crop[wy0:wy1, wx0:wx1], cv2.COLOR_BGR2GRAY)

                    # Use edge-based corner detection for better boundary detection
                    window_edges = cv2.Canny(window, 50, 150)

                    # Find corners using Harris corner detector (better for square corners)
                    harris = cv2.cornerHarris(window,
                                            blockSize=cfg['harris_block_size'],
                                            ksize=cfg['harris_ksize'],
                                            k=cfg['harris_k'])
                    harris_dilated = cv2.dilate(harris, None)

                    # Also use goodFeaturesToTrack as fallback
                    local_corners = cv2.goodFeaturesToTrack(
                        window,
                        maxCorners=cfg['good_features_max_corners'],
                        qualityLevel=cfg['good_features_quality'],
                        minDistance=cfg['good_features_min_distance'],
                        blockSize=3,
                        useHarrisDetector=True,
                        k=cfg['harris_k']
                    )

                    corner_found = False
                    if local_corners is not None and len(local_corners) > 0:
                        # Map back to full crop coordinates
                        local_corners = local_corners.reshape(-1, 2) + np.array([wx0, wy0], dtype=np.float32)

                        # Filter corners: prefer those on edges
                        valid_corners = []
                        for lc in local_corners:
                            lc_win_x = int(lc[0] - wx0)
                            lc_win_y = int(lc[1] - wy0)
                            # Check if corner is near an edge
                            if (0 <= lc_win_x < window_edges.shape[1] and
                                0 <= lc_win_y < window_edges.shape[0]):
                                # Sample 3x3 region around corner for edge presence
                                x0 = max(0, lc_win_x - 1)
                                y0 = max(0, lc_win_y - 1)
                                x1 = min(window_edges.shape[1], lc_win_x + 2)
                                y1 = min(window_edges.shape[0], lc_win_y + 2)
                                edge_sample = window_edges[y0:y1, x0:x1]
                                if edge_sample.size > 0 and np.mean(edge_sample) > cfg['edge_sample_threshold']:
                                    valid_corners.append(lc)

                        # Use valid corners if found, otherwise use all
                        candidates = valid_corners if len(valid_corners) > 0 else local_corners

                        # Pick the corner closest to original quad vertex
                        dists = np.linalg.norm(candidates - p, axis=1)
                        best_idx = int(np.argmin(dists))

                        # Only accept if distance is reasonable (not too far from expected)
                        if dists[best_idx] < window_size * cfg['corner_max_distance_ratio']:
                            refined.append(candidates[best_idx])
                            corner_found = True

                    if not corner_found:
                        # No good corner found, use original quad vertex
                        refined.append(p)

                refined = np.array(refined, dtype=np.float32)

                # Sub-pixel refinement with better parameters
                try:
                    initial_pts = refined.reshape(-1, 1, 2).astype(np.float32)
                    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                              cfg['subpix_max_iterations'],
                              cfg['subpix_epsilon'])
                    cv2.cornerSubPix(gray, initial_pts,
                                   cfg['subpix_window_size'],
                                   (-1, -1), criteria)
                    ordered = initial_pts.reshape(-1, 2)
                except Exception as e:
                    if debug and DEBUG:
                        print(f"[EdgeFinder] Sub-pixel refinement failed: {e}")
                    ordered = refined
            except Exception as e:
                if debug and DEBUG:
                    print(f"[EdgeFinder] Corner refinement failed: {e}")
                pass
            score = _score_quad(ordered, crop.shape)
            center_x, center_y = crop.shape[1] / 2, crop.shape[0] / 2
            quad_center = np.mean(ordered, axis=0)
            dist_to_center = np.linalg.norm(quad_center - [center_x, center_y])
            max_dist = np.sqrt(center_x**2 + center_y**2)
            centrality_bonus = 1.0 - (dist_to_center / max_dist) * 0.3
            score *= centrality_bonus
            if score > best_score:
                best_score = score
                best_quad = ordered
        cv2.drawContours(overlay, [hull], -1, (80, 80, 80), 1)
    if best_quad is None and use_enhanced_preprocessing:
        try:
            lines = cv2.HoughLinesP(preprocess_map, 1, np.pi/180.0, threshold=80, minLineLength=30, maxLineGap=20)
            if lines is not None and len(lines) >= 4:
                pts = []
                for l in lines[:12]:
                    x1, y1, x2, y2 = l[0]
                    pts.append([x1, y1])
                    pts.append([x2, y2])
                pts = np.array(pts, dtype=np.float32)
                if pts.shape[0] >= 4:
                    _, _, centers = cv2.kmeans(pts, 4, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.1), 10, cv2.KMEANS_PP_CENTERS)
                    centers = centers.astype(np.float32)
                    best_quad = centers
        except Exception:
            pass
    if best_quad is not None:
        pts = best_quad.reshape(-1, 2)
        cv2.polylines(overlay, [pts.astype(np.int32)], isClosed=True, color=(0, 200, 255), thickness=3, lineType=cv2.LINE_AA)
        for j, (x, y) in enumerate(pts):
            cv2.circle(overlay, (int(x), int(y)), 10, (0, 0, 0), -1)
            cv2.circle(overlay, (int(x), int(y)), 6, (0, 200, 255), -1)
            cv2.putText(overlay, str(j), (int(x) + 8, int(y) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    warped = None
    if warp and best_quad is not None:
        pts_src = np.float32(best_quad)
        pts_dst = np.float32([[0,0],[warp_size-1,0],[warp_size-1,warp_size-1],[0,warp_size-1]])
        try:
            M = cv2.getPerspectiveTransform(pts_src, pts_dst)
            warped = cv2.warpPerspective(crop, M, (warp_size, warp_size))
        except Exception:
            warped = None
    corners = ([(int(float(x)), int(float(y))) for (x, y) in best_quad] if best_quad is not None else None)
    if debug and DEBUG:
        try:
            cv2.imshow("overlay", overlay)
            cv2.waitKey(1)
        except Exception:
            pass
    return overlay, len(contours), warped, corners
