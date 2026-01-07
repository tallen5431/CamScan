"""
Circle detection module for CamScan - detects circles and arcs in calibrated images.

Provides:
  - detect_circles(img, ...) → list of circle detections
  - fit_arc(points) → arc parameters
  - export_to_dxf(circles, lines, filename) → DXF file for CAD software
"""

import cv2
import numpy as np
from typing import List, Tuple, Dict, Any, Optional
import math

# Circle detection defaults
MIN_CIRCLE_RADIUS = 10      # pixels
MAX_CIRCLE_RADIUS = 2000    # pixels
MIN_CIRCULARITY = 0.7       # how round the contour must be (0-1)
HOUGH_PARAM1 = 50           # Canny edge threshold
HOUGH_PARAM2 = 30           # Circle detection sensitivity


def detect_circles_hough(
    img: np.ndarray,
    min_radius: int = MIN_CIRCLE_RADIUS,
    max_radius: int = MAX_CIRCLE_RADIUS,
    param1: int = HOUGH_PARAM1,
    param2: int = HOUGH_PARAM2,
    debug: bool = False
) -> List[Tuple[int, int, int]]:
    """
    Detect circles using Hough Circle Transform.

    Args:
        img: Input image (BGR or grayscale)
        min_radius: Minimum circle radius in pixels
        max_radius: Maximum circle radius in pixels
        param1: Canny edge detection threshold
        param2: Accumulator threshold (lower = more circles detected)
        debug: Print debug info

    Returns:
        List of (center_x, center_y, radius) tuples
    """
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # Apply CLAHE for better contrast
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Gaussian blur to reduce noise
    blurred = cv2.GaussianBlur(enhanced, (9, 9), 2)

    # Detect circles
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1,
        minDist=max(min_radius * 2, 20),
        param1=param1,
        param2=param2,
        minRadius=min_radius,
        maxRadius=max_radius
    )

    if circles is None:
        if debug:
            print(f"[CircleDetect] No circles found with param1={param1}, param2={param2}")
        return []

    circles = np.round(circles[0, :]).astype(int)

    if debug:
        print(f"[CircleDetect] Found {len(circles)} circles")
        for i, (x, y, r) in enumerate(circles[:5]):
            print(f"  Circle #{i+1}: center=({x},{y}) radius={r}px")

    return [(int(x), int(y), int(r)) for x, y, r in circles]


def detect_circles_contour(
    img: np.ndarray,
    min_radius: int = MIN_CIRCLE_RADIUS,
    max_radius: int = MAX_CIRCLE_RADIUS,
    min_circularity: float = MIN_CIRCULARITY,
    debug: bool = False
) -> List[Dict[str, Any]]:
    """
    Detect circles by analyzing contours for circularity.

    Returns more detailed info than Hough method including contour points.

    Args:
        img: Input image (BGR or grayscale)
        min_radius: Minimum circle radius in pixels
        max_radius: Maximum circle radius in pixels
        min_circularity: Minimum circularity score (0-1)
        debug: Print debug info

    Returns:
        List of circle dicts with keys:
          - center: (x, y) tuple
          - radius: float
          - circularity: float (0-1, 1=perfect circle)
          - area: float (pixels²)
          - contour: numpy array of contour points
    """
    if img.ndim == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img.copy()

    # Apply CLAHE
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Adaptive threshold for better edge detection
    thresh = cv2.adaptiveThreshold(
        enhanced, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        11, 2
    )

    # Find contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    circles = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < math.pi * min_radius**2:
            continue
        if area > math.pi * max_radius**2:
            continue

        # Calculate circularity: 4π*area / perimeter²
        # Perfect circle = 1.0, lower values = less circular
        perimeter = cv2.arcLength(contour, True)
        if perimeter == 0:
            continue

        circularity = 4 * math.pi * area / (perimeter ** 2)

        if circularity < min_circularity:
            continue

        # Fit minimum enclosing circle
        (x, y), radius = cv2.minEnclosingCircle(contour)

        # Additional validation: radius should be reasonable for the area
        expected_radius = math.sqrt(area / math.pi)
        if abs(radius - expected_radius) / expected_radius > 0.3:  # 30% tolerance
            continue

        circles.append({
            "center": (float(x), float(y)),
            "radius": float(radius),
            "circularity": float(circularity),
            "area": float(area),
            "contour": contour
        })

    # Sort by circularity (most circular first)
    circles.sort(key=lambda c: c["circularity"], reverse=True)

    if debug:
        print(f"[CircleContour] Found {len(circles)} circular contours")
        for i, circ in enumerate(circles[:5]):
            print(f"  Circle #{i+1}: center=({circ['center'][0]:.1f},{circ['center'][1]:.1f}) "
                  f"radius={circ['radius']:.1f}px circularity={circ['circularity']:.3f}")

    return circles


def fit_circle_to_points(points: np.ndarray) -> Optional[Tuple[float, float, float]]:
    """
    Fit a circle to a set of points using least squares.

    Args:
        points: Nx2 array of (x, y) points

    Returns:
        (center_x, center_y, radius) or None if fit fails
    """
    if len(points) < 3:
        return None

    # Use OpenCV's minEnclosingCircle as initial estimate
    (x, y), r = cv2.minEnclosingCircle(points)

    # Refine with least squares if we have many points
    if len(points) >= 5:
        try:
            # Algebraic circle fit
            x_m = np.mean(points[:, 0])
            y_m = np.mean(points[:, 1])

            u = points[:, 0] - x_m
            v = points[:, 1] - y_m

            Suu = np.sum(u * u)
            Suv = np.sum(u * v)
            Svv = np.sum(v * v)
            Suuu = np.sum(u * u * u)
            Suvv = np.sum(u * v * v)
            Svvv = np.sum(v * v * v)
            Svuu = np.sum(v * u * u)

            A = np.array([[Suu, Suv], [Suv, Svv]])
            b = np.array([0.5 * (Suuu + Suvv), 0.5 * (Svvv + Svuu)])

            uc, vc = np.linalg.solve(A, b)

            xc = uc + x_m
            yc = vc + y_m
            rc = np.sqrt(uc**2 + vc**2 + (Suu + Svv) / len(points))

            return (float(xc), float(yc), float(rc))
        except np.linalg.LinAlgError:
            pass

    return (float(x), float(y), float(r))


def circles_to_json(
    circles: List[Dict[str, Any]],
    mm_per_px: float = 1.0
) -> List[Dict[str, Any]]:
    """
    Convert circle detections to JSON-serializable format with measurements.

    Args:
        circles: List of circle dicts from detect_circles_contour
        mm_per_px: Calibration scale (mm per pixel)

    Returns:
        List of dicts suitable for JSON export with:
          - center_x, center_y (pixels)
          - radius_px, radius_mm
          - diameter_px, diameter_mm
          - circumference_px, circumference_mm
          - area_px2, area_mm2
          - circularity
    """
    result = []
    for circ in circles:
        cx, cy = circ["center"]
        r_px = circ["radius"]
        r_mm = r_px * mm_per_px
        d_px = 2 * r_px
        d_mm = 2 * r_mm
        circ_px = 2 * math.pi * r_px
        circ_mm = 2 * math.pi * r_mm
        area_px2 = math.pi * r_px ** 2
        area_mm2 = math.pi * r_mm ** 2

        result.append({
            "type": "circle",
            "center_x": float(cx),
            "center_y": float(cy),
            "radius_px": float(r_px),
            "radius_mm": float(r_mm),
            "diameter_px": float(d_px),
            "diameter_mm": float(d_mm),
            "circumference_px": float(circ_px),
            "circumference_mm": float(circ_mm),
            "area_px2": float(area_px2),
            "area_mm2": float(area_mm2),
            "circularity": float(circ["circularity"])
        })

    return result


def export_to_dxf(
    geometry: List[Dict[str, Any]],
    filename: str,
    mm_per_px: float = 1.0
):
    """
    Export geometry to DXF file for CAD software (AutoCAD, FreeCAD, etc).

    Args:
        geometry: List of geometry dicts (circles, lines, rectangles, etc.)
        filename: Output DXF filename
        mm_per_px: Calibration scale

    Geometry dict format:
      - type: "circle", "line", "rectangle", "polyline"
      - (type-specific fields)
    """
    try:
        import ezdxf
    except ImportError:
        print("[DXF Export] Error: ezdxf not installed. Install with: pip install ezdxf")
        return False

    doc = ezdxf.new('R2010')
    msp = doc.modelspace()

    # Set document units to millimeters (critical for CAD software)
    # Without this, values are interpreted as unitless and may scale incorrectly
    doc.units = ezdxf.units.MM

    # Also set drawing units in header for maximum compatibility
    doc.header['$INSUNITS'] = 4  # 4 = millimeters in AutoCAD

    for item in geometry:
        if item["type"] == "circle":
            cx = item["center_x"] * mm_per_px
            cy = item["center_y"] * mm_per_px
            r = item["radius_px"] * mm_per_px
            msp.add_circle((cx, cy), r, dxfattribs={'layer': 'CIRCLES'})

        elif item["type"] == "line":
            x1 = item["x1"] * mm_per_px
            y1 = item["y1"] * mm_per_px
            x2 = item["x2"] * mm_per_px
            y2 = item["y2"] * mm_per_px
            msp.add_line((x1, y1), (x2, y2), dxfattribs={'layer': 'LINES'})

        elif item["type"] == "rectangle":
            x1 = item["x1"] * mm_per_px
            y1 = item["y1"] * mm_per_px
            x2 = item["x2"] * mm_per_px
            y2 = item["y2"] * mm_per_px
            points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
            msp.add_lwpolyline(points, dxfattribs={'layer': 'RECTANGLES'})

        elif item["type"] == "polyline":
            points = [(p[0] * mm_per_px, p[1] * mm_per_px) for p in item["points"]]
            msp.add_lwpolyline(points, dxfattribs={'layer': 'POLYLINES'})

    doc.saveas(filename)
    print(f"[DXF Export] Saved to {filename}")
    return True


if __name__ == "__main__":
    # Test with a sample image
    import sys
    if len(sys.argv) < 2:
        print("Usage: python circle_detection.py <image_path>")
        sys.exit(1)

    img = cv2.imread(sys.argv[1])
    if img is None:
        print(f"Error: Could not load {sys.argv[1]}")
        sys.exit(1)

    print("Testing Hough Circle Detection:")
    hough_circles = detect_circles_hough(img, debug=True)

    print("\nTesting Contour Circle Detection:")
    contour_circles = detect_circles_contour(img, debug=True)

    # Visualize
    vis = img.copy()
    for x, y, r in hough_circles:
        cv2.circle(vis, (x, y), r, (0, 255, 0), 2)
        cv2.circle(vis, (x, y), 2, (0, 0, 255), 3)

    cv2.imwrite("circle_detection_result.jpg", vis)
    print("\nVisualization saved to circle_detection_result.jpg")
