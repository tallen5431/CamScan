#!/usr/bin/env python3
"""
Debug script to understand detection behavior with different polarity settings.
"""
import cv2
import numpy as np
from detect_squares import detect_dark_squares_robust
import sys

def test_detection(img_path):
    """Test detection with different polarity settings."""
    img_bgr = cv2.imread(img_path)
    if img_bgr is None:
        print(f"ERROR: Could not load image: {img_path}")
        print("Usage: python debug_detection.py <path_to_calibration_image>")
        return

    H, W = img_bgr.shape[:2]
    print(f"Image size: {W}x{H} ({W*H:,} pixels)")
    print("=" * 80)

    # Test 1: polarity="bright" (user's working code)
    print("\n TEST 1: polarity='bright' + enforce_calibration_pattern=True")
    print("-" * 80)
    dets_bright = detect_dark_squares_robust(
        img_bgr,
        polarity="bright",
        enforce_calibration_pattern=True,
        calibration_outer_only=False,
        min_area=400,
        max_area_ratio=0.5,
        max_aspect=1.3,
        min_fill_ratio=0.7,
        min_hull_ratio=0.9,
        min_compactness=0.6,
        border_margin_frac=0.02,
        debug=True,
    )
    print(f"\nRESULT: {len(dets_bright)} detections")
    for i, (score, x, y, w, h, mean) in enumerate(dets_bright):
        print(f"  [{i}] pos=({x},{y}) size={w}x{h} area={w*h:,}px² mean={mean:.1f} score={score:.3f}")

    print("\n" + "=" * 80)

    # Test 2: polarity="both" (current code)
    print("\n TEST 2: polarity='both' + enforce_calibration_pattern=True")
    print("-" * 80)
    dets_both = detect_dark_squares_robust(
        img_bgr,
        polarity="both",
        enforce_calibration_pattern=True,
        calibration_outer_only=False,
        min_area=400,
        max_area_ratio=0.5,
        max_aspect=1.3,
        min_fill_ratio=0.7,
        min_hull_ratio=0.9,
        min_compactness=0.6,
        border_margin_frac=0.02,
        debug=True,
    )
    print(f"\nRESULT: {len(dets_both)} detections")
    for i, (score, x, y, w, h, mean) in enumerate(dets_both):
        print(f"  [{i}] pos=({x},{y}) size={w}x{h} area={w*h:,}px² mean={mean:.1f} score={score:.3f}")

    print("\n" + "=" * 80)

    # Test 3: polarity="both" without pattern enforcement
    print("\n TEST 3: polarity='both' WITHOUT enforce_calibration_pattern")
    print("-" * 80)
    dets_no_pattern = detect_dark_squares_robust(
        img_bgr,
        polarity="both",
        enforce_calibration_pattern=False,
        min_area=400,
        max_area_ratio=0.5,
        max_aspect=1.3,
        min_fill_ratio=0.7,
        min_hull_ratio=0.9,
        min_compactness=0.6,
        border_margin_frac=0.02,
        debug=True,
    )
    print(f"\nRESULT: {len(dets_no_pattern)} detections")
    for i, (score, x, y, w, h, mean) in enumerate(dets_no_pattern):
        print(f"  [{i}] pos=({x},{y}) size={w}x{h} area={w*h:,}px² mean={mean:.1f} score={score:.3f}")

    print("\n" + "=" * 80)
    print("\nSUMMARY:")
    print(f"  polarity='bright' + pattern: {len(dets_bright)} detections")
    print(f"  polarity='both' + pattern:   {len(dets_both)} detections")
    print(f"  polarity='both' no pattern:  {len(dets_no_pattern)} detections")
    print("=" * 80)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_detection.py <path_to_calibration_image>")
        print("\nExample:")
        print("  python debug_detection.py test_image.jpg")
        sys.exit(1)

    test_detection(sys.argv[1])
