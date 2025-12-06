#!/usr/bin/env python3
"""
Quick diagnostic to test if inner squares are being detected.
"""
import cv2
import numpy as np
from detect_squares import detect_dark_squares_robust

# Load one of the test images (you'll need to provide path)
# For now, just test with a placeholder
img_path = "test_calibration_image.jpg"  # Replace with actual path

try:
    img_bgr = cv2.imread(img_path)
    if img_bgr is None:
        print(f"Could not load image: {img_path}")
        print("Please update img_path in test_inner_squares.py with a valid image path")
        exit(1)

    H, W = img_bgr.shape[:2]
    print(f"Image size: {W}x{H}")
    print()

    # Test detection with calibration_outer_only=False
    print("Testing with calibration_outer_only=False:")
    detections = detect_dark_squares_robust(
        img_bgr,
        polarity="bright",
        enforce_calibration_pattern=True,
        calibration_outer_only=False,  # Should return outer + 4 inner
        min_area=400,
        max_area_ratio=0.5,
        max_aspect=1.3,
        min_fill_ratio=0.7,
        min_hull_ratio=0.9,
        min_compactness=0.6,
        border_margin_frac=0.02,
    )

    print(f"Number of detections: {len(detections)}")
    for i, det in enumerate(detections):
        if len(det) >= 5:
            _, x, y, w, h = det[:5]
            print(f"  Detection #{i}: pos=({x},{y}) size={w}x{h} area={w*h:,}px²")

    print()
    print("Testing with calibration_outer_only=True:")
    detections_outer_only = detect_dark_squares_robust(
        img_bgr,
        polarity="bright",
        enforce_calibration_pattern=True,
        calibration_outer_only=True,  # Should return only outer
        min_area=400,
        max_area_ratio=0.5,
        max_aspect=1.3,
        min_fill_ratio=0.7,
        min_hull_ratio=0.9,
        min_compactness=0.6,
        border_margin_frac=0.02,
    )

    print(f"Number of detections: {len(detections_outer_only)}")
    for i, det in enumerate(detections_outer_only):
        if len(det) >= 5:
            _, x, y, w, h = det[:5]
            print(f"  Detection #{i}: pos=({x},{y}) size={w}x{h} area={w*h:,}px²")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
