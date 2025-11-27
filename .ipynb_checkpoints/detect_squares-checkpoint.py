import cv2
import numpy as np


def detect_dark_squares(
    img,
    min_area=5000,
    max_area_ratio=0.6,
    ratio_tol=(0.7, 1.3),
    brightness_thresh=100,
    approx_eps=0.04,
    max_results=20,
    clahe_clip=2.0,
    clahe_grid=(8, 8),
    blur_ksize=5,
    morph_kernel_size=5,
    morph_iterations=2,
    dark_weight=0.7,
    shape_weight=0.3,
    adaptive_mode=False,
    debug=False
):
    """
    Detect dark, roughly square regions in an image (tunable and lighting-robust).

    Parameters
    ----------
    img : np.ndarray
        Input BGR image.
    min_area : int
        Minimum contour area to consider.
    max_area_ratio : float
        Maximum area relative to frame size.
    ratio_tol : tuple
        Acceptable width/height ratio range for near-squares.
    brightness_thresh : int
        Fixed threshold for dark regions (0–255) if adaptive_mode=False.
    approx_eps : float
        Polygon approximation precision factor.
    max_results : int
        Maximum number of detections to return.
    clahe_clip : float
        CLAHE contrast limit (higher = more contrast).
    clahe_grid : tuple
        CLAHE tile grid size.
    blur_ksize : int
        Gaussian blur kernel size for noise smoothing.
    morph_kernel_size : int
        Morphology kernel size for closing gaps.
    morph_iterations : int
        Morphological close iterations.
    dark_weight : float
        Weight for darkness scoring (0–1).
    shape_weight : float
        Weight for shape/squareness scoring (0–1).
    adaptive_mode : bool
        Use adaptive thresholding if True (better for variable lighting).
    debug : bool
        Show intermediate visualization windows (gray, enhanced, mask).

    Returns
    -------
    list of tuples
        [(score, x, y, w, h, mean_val), ...]
    """

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h_img, w_img = gray.shape[:2]
    frame_area = h_img * w_img

    # --- Step 1: Preprocess for consistent lighting ---
    blur = cv2.GaussianBlur(gray, (blur_ksize, blur_ksize), 0)
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=clahe_grid)
    enhanced = clahe.apply(blur)

    # --- Step 2: Threshold dark regions ---
    if adaptive_mode:
        dark_mask = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 15, 5
        )
    else:
        _, dark_mask = cv2.threshold(enhanced, brightness_thresh, 255, cv2.THRESH_BINARY_INV)

    # --- Step 3: Morphological cleanup ---
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (morph_kernel_size, morph_kernel_size))
    dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, k, iterations=morph_iterations)

    # --- Optional Debug Visualization ---
    if debug:
        cv2.imshow("Gray", gray)
        cv2.imshow("Enhanced", enhanced)
        cv2.imshow("Dark Mask", dark_mask)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    # --- Step 4: Find contours ---
    contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []

    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, approx_eps * peri, True)
        area = cv2.contourArea(approx)

        # Must be a convex quadrilateral within area limits
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        if not (min_area < area < frame_area * max_area_ratio):
            continue

        x, y, w, h = cv2.boundingRect(approx)
        ratio = w / float(h)
        if not (ratio_tol[0] <= ratio <= ratio_tol[1]):
            continue

        roi = gray[y:y+h, x:x+w]
        mean_val = np.mean(roi)

        # Darkness score (how dark it is) and shape score (how square)
        dark_score = max(0, 1 - mean_val / 255.0)
        shape_score = 1 - abs(1 - ratio)
        final_score = dark_weight * dark_score + shape_weight * shape_score

        candidates.append((final_score, x, y, w, h, mean_val))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[:max_results]


def draw_squares(img, detections, color=(0, 255, 0), thickness=10, label=True):
    """
    Draw detected dark squares with configurable appearance.

    Args:
        img: BGR image.
        detections: list from detect_dark_squares().
        color: border color (BGR tuple).
        thickness: rectangle border thickness.
        label: whether to draw labels (True/False).
    """
    result = img.copy()
    for rank, (_, x, y, w, h, mean_val) in enumerate(detections, start=1):
        cv2.rectangle(result, (x, y), (x + w, y + h), color, thickness)
        if label:
            cv2.putText(result, f"S{rank}", (x + 15, y + 45),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(result, f"S{rank}", (x + 15, y + 45),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    return result
