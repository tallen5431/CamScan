"""Headless functional smoke test for CamScan.

Exercises the real runtime paths — module imports, the calibration pipeline,
JSON/DXF export, and circle detection — without needing a browser or a network.
Run it directly (`python tests/smoke_test.py`); exits non-zero if anything fails.
The SessionStart hook runs this after installing dependencies so every web
session starts from a known-good state.
"""
import os
import sys
import io
import tempfile
import pathlib
import contextlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import cv2

_failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


@contextlib.contextmanager
def _quiet():
    """Silence the pipeline's verbose debug prints so the summary stays readable."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _make_marker_image():
    """Synthetic calibration target: a 300px black outer square (= 30 mm) with
    four white inner pads, on a mid-gray background."""
    img = np.full((900, 1200, 3), 120, np.uint8)
    cv2.rectangle(img, (450, 300), (750, 600), (20, 20, 20), -1)
    for (px, py) in [(500, 350), (650, 350), (500, 500), (650, 500)]:
        cv2.rectangle(img, (px, py), (px + 80, py + 80), (240, 240, 240), -1)
    return img


def main():
    # 1) Import the whole module chain (catches import / callback-signature errors).
    try:
        import app  # noqa: F401
        from calibration_core import calibrate_image, save_outputs
        from detect_squares import detect_dark_squares_robust  # noqa: F401
        from edge_finder import find_main_edges  # noqa: F401
        from circle_detection import (
            export_to_dxf, detect_circles_hough, detect_circles_contour,
        )
        from app import _is_allowed_filename
        check("import module chain", True)
    except Exception as e:  # pragma: no cover - import failure is fatal
        check("import module chain", False, repr(e))
        print("\nSMOKE_RESULT: FAIL (imports)")
        return 1

    img = _make_marker_image()

    # 2) Full calibration pipeline returns a valid, correctly-scaled result.
    try:
        with _quiet():
            cal, overlay = calibrate_image(img, edge_mm=30.0)
        ok = (isinstance(cal, dict) and "mm_per_px" in cal and "markers" in cal
              and "calibration_confidence" in cal
              and overlay is not None and overlay.shape == img.shape)
        # 300 px square known to be 30 mm => ~0.1 mm/px when the marker is found.
        scale = cal.get("mm_per_px", 0)
        scale_ok = (len(cal.get("markers", [])) == 0) or (0.08 <= scale <= 0.12)
        # A clean, high-contrast target should refine precisely → high confidence.
        conf = cal.get("calibration_confidence")
        conf_ok = conf == "high"
        check("calibrate_image structure + scale + confidence", ok and scale_ok and conf_ok,
              f"markers={len(cal.get('markers', []))}, mm_per_px={scale}, confidence={conf}")
    except Exception as e:
        check("calibrate_image", False, repr(e))
        cal, overlay = {}, None

    # 3) No crash / no div-by-zero on an image with no calibration square.
    try:
        with _quiet():
            cal0, _ = calibrate_image(np.full((400, 400, 3), 60, np.uint8), edge_mm=30.0)
        check("calibrate_image on blank image (graceful)", isinstance(cal0, dict),
              f"markers={len(cal0.get('markers', []))}")
    except Exception as e:
        check("calibrate_image on blank image", False, repr(e))

    # 4) save_outputs writes calibration JSON atomically.
    if overlay is not None:
        try:
            with tempfile.TemporaryDirectory() as d, _quiet():
                jp, _ = save_outputs("smoke-test.jpg", cal, overlay, d)
                exists = os.path.exists(jp)
            check("save_outputs writes JSON", exists)
        except Exception as e:
            check("save_outputs", False, repr(e))

    # 5) DXF export of mixed geometry (line, rectangle, circle, polyline) + Y-flip.
    try:
        geom = [
            {"type": "line", "x1": 10, "y1": 20, "x2": 110, "y2": 20, "mm_per_px": 0.5},
            {"type": "rectangle", "x1": 0, "y1": 0, "x2": 50, "y2": 30, "mm_per_px": 0.5},
            {"type": "circle", "center_x": 100, "center_y": 100, "radius_px": 25, "mm_per_px": 0.5},
            {"type": "polyline", "points": [[0, 0], [10, 10], [20, 5]], "mm_per_px": 0.5},
        ]
        with tempfile.TemporaryDirectory() as d, _quiet():
            out = os.path.join(d, "smoke.dxf")
            ok = export_to_dxf(geom, out, mm_per_px=0.5, image_height_px=900)
            ok = bool(ok) and os.path.exists(out) and os.path.getsize(out) > 0
        check("export_to_dxf produces a DXF", ok)
    except Exception as e:
        check("export_to_dxf", False, repr(e))

    # 6) Circle detectors run without throwing.
    try:
        ci = _make_marker_image()
        cv2.circle(ci, (300, 700), 60, (240, 240, 240), 4)
        with _quiet():
            h = detect_circles_hough(ci)
            c = detect_circles_contour(ci)
        check("circle detectors run", isinstance(h, list) and isinstance(c, list),
              f"hough={len(h)}, contour={len(c)}")
    except Exception as e:
        check("circle detectors", False, repr(e))

    # 7) Filename validation handles both path separators and rejects bad types.
    try:
        ok = (_is_allowed_filename("photo.jpg")
              and _is_allowed_filename("C:\\Users\\me\\photo.PNG")
              and _is_allowed_filename("/home/me/photo.webp")
              and not _is_allowed_filename("evil.exe")
              and not _is_allowed_filename(""))
        check("_is_allowed_filename", ok)
    except Exception as e:
        check("_is_allowed_filename", False, repr(e))

    if _failures:
        print(f"\nSMOKE_RESULT: FAIL ({', '.join(_failures)})")
        return 1
    print("\nSMOKE_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
