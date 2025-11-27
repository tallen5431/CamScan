import cv2
import numpy as np

def detect_bright_rectangles(
    img,
    min_area=800,
    max_area_ratio=0.9,
    ratio_tol=(1.05, 8.0),
    brightness_thresh=180,
    approx_eps=0.03,
    max_results=25,
    clahe_clip=2.0,
    clahe_grid=(8, 8),
    blur_ksize=5,
    morph_kernel_size=3,
    morph_iterations=1,
    adaptive_mode=True,
    debug=False,
    inner_mode=False   # 👈 NEW: tells the detector it’s running inside a dark marker
):
    """
    Detect bright (white) rectangles robustly. Works both globally and inside dark squares.
    """

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h_img, w_img = gray.shape[:2]
    frame_area = h_img * w_img

    # --- Step 1: Local contrast normalization ---
    blur = cv2.GaussianBlur(gray, (blur_ksize, blur_ksize), 0)
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=clahe_grid)
    enhanced = clahe.apply(blur)

    # --- Step 2: Localized bright region detection ---
    if adaptive_mode:
        block = 15 if not inner_mode else 11
        c_val = -7 if not inner_mode else -3  # tighter threshold inside dark areas
        mask = cv2.adaptiveThreshold(
            enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, block, c_val
        )
    else:
        _, mask = cv2.threshold(enhanced, brightness_thresh, 255, cv2.THRESH_BINARY)

    # Edge reinforcement
    edges = cv2.Canny(enhanced, 40, 120)
    mask = cv2.bitwise_or(mask, edges)

    # Morphology
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (morph_kernel_size, morph_kernel_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=morph_iterations)

    if debug:
        cv2.imshow("Enhanced", enhanced)
        cv2.imshow("Mask", mask)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    # --- Step 3: Contour analysis ---
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []

    for c in contours:
        area = cv2.contourArea(c)
        if not (min_area < area < frame_area * max_area_ratio):
            continue

        rect = cv2.minAreaRect(c)
        (cx, cy), (w, h), _ = rect
        if w == 0 or h == 0:
            continue

        aspect = max(w / h, h / w)
        if not (ratio_tol[0] <= aspect <= ratio_tol[1]):
            continue

        box = cv2.boxPoints(rect)
        box = np.intp(box)
        x, y, w, h = cv2.boundingRect(box)

        # Skip contours touching the image border (likely external background)
        border_tol = 4
        if x <= border_tol or y <= border_tol or x + w >= w_img - border_tol or y + h >= h_img - border_tol:
            if inner_mode:
                continue

        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        if hull_area == 0:
            continue
        solidity = area / hull_area
        extent = area / (w * h)

        roi = gray[y:y + h, x:x + w]
        mean_val = np.mean(roi)

        # More lenient brightness cutoff when inside dark marker
        min_bright = 120 if inner_mode else 150
        if mean_val < min_bright:
            continue

        # Weighting: emphasize geometry when inner_mode=True
        bright_wt = 0.55 if inner_mode else 0.65
        geom_wt   = 1.0 - bright_wt
        final_score = bright_wt * (mean_val / 255.0) + geom_wt * ((solidity + extent) / 2)

        candidates.append((final_score, x, y, w, h, mean_val, aspect, solidity, extent))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[:max_results]


def draw_rectangles(img, detections, color=(0, 140, 255), thickness=6, label=True):
    """Draw rectangles with brightness labels."""
    result = img.copy()
    for rank, (score, x, y, w, h, mean_val, aspect, solidity, extent) in enumerate(detections, start=1):
        cv2.rectangle(result, (x, y), (x + w, y + h), color, thickness)
        if label:
            text = f"R{rank} ({mean_val:.0f})"
            cv2.putText(result, text, (x + 8, y + 32),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(result, text, (x + 8, y + 32),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
    return result
