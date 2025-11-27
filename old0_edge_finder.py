import cv2, numpy as np

def _auto_canny(image, sigma=0.33):
    """Automatic Canny edge detection using median-based thresholds."""
    v = np.median(image)
    lower = int(max(0, (1.0 - sigma) * v))
    upper = int(min(255, (1.0 + sigma) * v))
    return cv2.Canny(image, lower, upper)

def _order_quad(pts):
    """Orders four points consistently (top-left, top-right, bottom-right, bottom-left)."""
    pts = np.array(pts).reshape(-1, 2)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1)
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(s)]
    ordered[2] = pts[np.argmax(s)]
    ordered[1] = pts[np.argmin(diff)]
    ordered[3] = pts[np.argmax(diff)]
    return ordered

def find_main_edges(crop, max_edges=10, warp=False, warp_size=512):
    """
    Finds the dominant square-like contour in the crop, draws it,
    caps extra edges, and optionally performs a perspective warp.

    Returns:
        overlay: annotated image with contours and corner dots
        n_contours: number of detected contours
        warped: rectified (top-down) square if available
        corners: [(x, y), ...] of best quadrilateral (local crop coords)
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 50, 50)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    eq = clahe.apply(gray)

    # --- Multi-sigma Canny fusion ---
    edges = np.zeros_like(eq)
    for s in (0.20, 0.33, 0.50):
        edges = cv2.bitwise_or(edges, _auto_canny(eq, sigma=s))

    # --- Morphological close (connect broken edges) ---
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, k, iterations=2)

    # --- Contour detection ---
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:max_edges]

    overlay = crop.copy()
    best_quad = None
    best_area = 0

    for i, c in enumerate(contours):
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        area = cv2.contourArea(approx)

        if len(approx) == 4 and cv2.isContourConvex(approx):
            if area > best_area and area > 2000:
                best_area = area
                best_quad = _order_quad(approx)

    # --- Draw all contours lightly for context ---
    for c in contours[:max_edges]:
        cv2.drawContours(overlay, [c], -1, (0, 255, 0), 1)

    # --- Highlight best quad if found ---
    if best_quad is not None:
        cv2.polylines(overlay, [best_quad.astype(int)], True, (255, 0, 0), 3)
        for j, (x, y) in enumerate(best_quad, start=1):
            cv2.circle(overlay, (int(x), int(y)), 7, (0, 255, 255), -1)
            cv2.putText(overlay, str(j), (int(x)+6, int(y)-6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)

    # --- Optional perspective warp ---
    warped = None
    if warp and best_quad is not None:
        pts_src = np.float32(best_quad)
        pts_dst = np.float32([
            [0, 0],
            [warp_size-1, 0],
            [warp_size-1, warp_size-1],
            [0, warp_size-1]
        ])
        M = cv2.getPerspectiveTransform(pts_src, pts_dst)
        warped = cv2.warpPerspective(crop, M, (warp_size, warp_size))

    corners = [(int(x), int(y)) for (x, y) in best_quad] if best_quad is not None else None
    return overlay, len(contours), warped, corners
