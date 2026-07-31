"""
Circle detection module for CamScan - detects circles and arcs in calibrated images.

Provides:
  - detect_circles_hough(img, ...) / detect_circles_contour(img, ...) → circle detections
  - export_to_dxf(geometry, filename, mm_per_px, ...) → DXF file for CAD software
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
    if img is None or getattr(img, "size", 0) == 0:   # match detect_dark_squares/detect_paper_sheet
        return []
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
    if img is None or getattr(img, "size", 0) == 0:   # match detect_dark_squares/detect_paper_sheet
        return []
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

    # RETR_LIST returns BOTH the inner and outer edge of a ring/stroke, so one physical circle
    # can surface as two near-concentric detections. Keep the most-circular of each cluster
    # (centres within a few px AND radii within ~15%) and drop the duplicates.
    deduped: List[Dict[str, Any]] = []
    for c in circles:
        cx, cy = c["center"]; r = c["radius"]
        if any(math.hypot(cx - k["center"][0], cy - k["center"][1]) <= max(4.0, 0.15 * r)
               and abs(r - k["radius"]) <= 0.15 * max(r, k["radius"]) for k in deduped):
            continue
        deduped.append(c)
    circles = deduped

    if debug:
        print(f"[CircleContour] Found {len(circles)} circular contours")
        for i, circ in enumerate(circles[:5]):
            print(f"  Circle #{i+1}: center=({circ['center'][0]:.1f},{circ['center'][1]:.1f}) "
                  f"radius={circ['radius']:.1f}px circularity={circ['circularity']:.3f}")

    return circles


def export_to_dxf(
    geometry: List[Dict[str, Any]],
    filename: str,
    mm_per_px: float = 1.0,
    image_height_px: Optional[float] = None,
):
    """
    Export geometry to DXF file for CAD software (AutoCAD, FreeCAD, etc).

    Args:
        geometry: List of geometry dicts (circles, lines, rectangles, etc.), in
            image pixel coordinates.
        filename: Output DXF filename
        mm_per_px: Default calibration scale. Each geometry item may carry its own
            ``mm_per_px`` (captured against the marker used when it was drawn); that
            value takes precedence so measurements stay correct with multiple markers.
        image_height_px: Height of the source image in pixels. When provided, the Y
            axis is flipped (y_out = height - y_in) so the exported geometry has the
            same orientation as the photo — image Y grows downward but CAD Y grows
            upward, so without this the part would be mirrored top-to-bottom.

    Geometry dict format:
      - type: "circle", "line", "rectangle", "polyline"
      - (type-specific fields), all in pixels
    """
    try:
        import ezdxf
    except ImportError:
        print("[DXF Export] Error: ezdxf not installed. Install with: pip install ezdxf")
        return False

    doc = ezdxf.new('R2010')
    msp = doc.modelspace()

    # Set document units to millimeters (critical for CAD software).
    # Without this, values are interpreted as unitless and may scale incorrectly.
    doc.units = ezdxf.units.MM
    doc.header['$INSUNITS'] = 4  # 4 = millimeters in AutoCAD

    # Create layers up front with distinct ACI colors so entities are filterable and
    # visually separable by type in CAD (they defaulted to white/color-7 before). ELLIPSES,
    # ANGLES and MARKER have no emitting branch below — they exist so a caller can still target
    # them via item['layer'] (and for parity with future entity types), not because this
    # writer produces them.
    layer_colors = {
        'LINES': 3,       # green
        'CIRCLES': 1,     # red (holes)
        'ELLIPSES': 1,    # red (holes) — caller-supplied item['layer'] only
        'RECTANGLES': 5,  # blue
        'POLYLINES': 4,   # cyan
        'ANGLES': 6,      # magenta — caller-supplied item['layer'] only
        'NOTES': 2,       # yellow
        'DIMTEXT': 2,     # yellow
        'MARKER': 8,      # grey (reference) — caller-supplied item['layer'] only
    }
    for _name, _color in layer_colors.items():
        if _name not in doc.layers:
            doc.layers.add(_name, color=_color)

    if image_height_px is not None and not math.isfinite(image_height_px):
        print(f"[DXF Export] Ignoring non-finite image_height_px {image_height_px!r} (no Y flip)")
        image_height_px = None

    def _flip_y(y_px: float) -> float:
        # `is not None`, not truthiness: a genuine image_height_px of 0 would otherwise skip
        # the flip silently. (0 is degenerate but should flip about 0, not be ignored.)
        return (image_height_px - y_px) if image_height_px is not None else y_px

    def _layer(item, default):
        return item.get('layer') or default

    # Positions (and the Y-flip origin) use ONE uniform scale so the drawing stays spatially
    # coherent. Scaling each item's POSITION by its own local mm_per_px would place features
    # measured against different markers at inconsistent coordinates — a hole could land
    # outside the part it belongs to. Per-item mm_per_px is used only for a shape's intrinsic
    # SIZE (radius, text height), where the item's local scale is the right one.
    pos_s = mm_per_px if mm_per_px else 1.0
    if not math.isfinite(pos_s):
        print(f"[DXF Export] Error: non-finite mm_per_px {mm_per_px!r}")
        return False

    def _num(v):
        """A geometry field as a FINITE float. NaN/Infinity were written into the DXF
        verbatim — ezdxf accepts them — so the export reported success while producing a
        file CAD refuses to open. Raising here drops just that item via the guard below."""
        f = float(v)
        if not math.isfinite(f):
            raise ValueError(f"non-finite coordinate {v!r}")
        return f

    for item in geometry:
        # A non-dict entry has no .get(), and the AttributeError it raises would escape the
        # per-item guard below and abort the whole export — skip it here instead.
        if not isinstance(item, dict):
            print(f"[DXF Export] Skipping non-dict geometry entry: {type(item).__name__}")
            continue
        try:
            size_s = float(item.get("mm_per_px") or mm_per_px)
        except (TypeError, ValueError):
            size_s = mm_per_px
        if not size_s or not math.isfinite(size_s):
            size_s = pos_s

        # Skip (don't abort) on a single malformed item — one bad entry must not
        # drop every other valid shape from the export.
        try:
            itype = item.get("type")
            if itype == "circle":
                cx = _num(item["center_x"]) * pos_s
                cy = _flip_y(_num(item["center_y"])) * pos_s
                r = _num(item["radius_px"]) * size_s
                msp.add_circle((cx, cy), r, dxfattribs={'layer': _layer(item, 'CIRCLES')})

            elif itype == "line":
                x1 = _num(item["x1"]) * pos_s
                y1 = _flip_y(_num(item["y1"])) * pos_s
                x2 = _num(item["x2"]) * pos_s
                y2 = _flip_y(_num(item["y2"])) * pos_s
                msp.add_line((x1, y1), (x2, y2), dxfattribs={'layer': _layer(item, 'LINES')})

            elif itype == "rectangle":
                x1 = _num(item["x1"]) * pos_s
                x2 = _num(item["x2"]) * pos_s
                y1 = _flip_y(_num(item["y1"])) * pos_s
                y2 = _flip_y(_num(item["y2"])) * pos_s
                # Closed LWPOLYLINE so the profile is directly extrudable in CAD
                # (a duplicate coincident vertex is NOT a closed profile).
                points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                msp.add_lwpolyline(points, close=True, dxfattribs={'layer': _layer(item, 'RECTANGLES')})

            elif itype == "polyline":
                points = [(_num(p[0]) * pos_s, _flip_y(_num(p[1])) * pos_s) for p in item.get("points", [])]
                if points:
                    msp.add_lwpolyline(points, close=bool(item.get("closed")),
                                       dxfattribs={'layer': _layer(item, 'POLYLINES')})

            elif itype == "text":
                tx = _num(item["x"]) * pos_s
                ty = _flip_y(_num(item["y"])) * pos_s
                # `or 3.0` so an explicit height:None (key present, value None) still gets the
                # default instead of raising and dropping the whole text entity.
                h = _num(item.get("height") or 3.0) * size_s
                if h <= 0:
                    h = 2.5 * size_s
                t = msp.add_text(str(item.get("text", "")),
                                 dxfattribs={'layer': _layer(item, 'DIMTEXT'), 'height': h})
                # set_placement across ezdxf versions; fall back to the raw insert point.
                try:
                    t.set_placement((tx, ty))
                except Exception:
                    t.dxf.insert = (tx, ty)
        except (KeyError, TypeError, ValueError, IndexError) as e:
            print(f"[DXF Export] Skipping malformed {item.get('type', '?')} item: {e}")
            continue

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
