import os
import cv2
import numpy as np
from typing import Tuple, List, Optional

DEBUG = bool(os.getenv("CAMSCAN_DEBUG", "0") == "1")


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


def _strong_preprocess(crop_gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    eq = clahe.apply(cv2.GaussianBlur(crop_gray, (5, 5), 0))
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
    warp_size: int = 512,
    min_area: float = 1500.0,
    debug: bool = False,
    use_enhanced_preprocessing: bool = True,
):
    if crop is None or crop.size == 0:
        return crop, 0, None, None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if use_enhanced_preprocessing:
        gray_b = cv2.bilateralFilter(gray, 9, 75, 75)
        preprocess_map = _strong_preprocess(gray_b)
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
                # try sub-pixel via goodFeaturesToTrack + cornerSubPix in a local ROI
                x, y, w, h = cv2.boundingRect(np.intp(ordered))
                pad = max(6, int(min(w, h) * 0.15))
                x0 = max(0, x - pad)
                y0 = max(0, y - pad)
                x1 = min(crop.shape[1], x + w + pad)
                y1 = min(crop.shape[0], y + h + pad)
                roi_gray = cv2.cvtColor(crop[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
                corners = cv2.goodFeaturesToTrack(roi_gray, maxCorners=8, qualityLevel=0.01, minDistance=6)
                if corners is not None and len(corners) >= 4:
                    corners = corners.reshape(-1, 2)
                    corners_global = corners + np.array([x0, y0], dtype=np.float32)
                    refined = []
                    for p in ordered:
                        dists = np.linalg.norm(corners_global - p, axis=1)
                        idx = int(np.argmin(dists))
                        refined.append(corners_global[idx])
                    refined = np.array(refined, dtype=np.float32)
                    initial_pts = refined.reshape(-1, 1, 2).astype(np.float32)
                    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01)
                    try:
                        cv2.cornerSubPix(roi_gray, initial_pts - np.array([x0, y0], dtype=np.float32), (5,5), (-1,-1), criteria)
                        # map back to global
                        refined_sub = (initial_pts.reshape(-1,2) + np.array([x0, y0], dtype=np.float32))
                        ordered = refined_sub
                    except Exception:
                        pass
            except Exception:
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
