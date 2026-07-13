#!/usr/bin/env python3
"""
Test script to detect calibration squares and visualize both:
- The outer black square edges
- The inner white squares edges
"""
import os
import sys
import cv2
import numpy as np
from pathlib import Path

# Import our detection modules
from detect_squares import detect_dark_squares, detect_dark_squares_robust
from edge_finder import find_main_edges

def draw_outer_square(img, det, color=(0, 255, 0), thickness=3):
    """Draw the outer black square boundary."""
    x, y, w, h = det['bbox']
    cv2.rectangle(img, (x, y), (x+w, y+h), color, thickness)

    # If we have refined corners, draw those too
    if 'corners' in det and det['corners']:
        corners = np.array(det['corners'], dtype=np.int32)
        cv2.polylines(img, [corners], True, (0, 255, 255), thickness)
        # Draw corner points
        for cx, cy in corners:
            cv2.circle(img, (int(cx), int(cy)), 6, (255, 0, 255), -1)

    return img

def draw_inner_squares(img, det, color=(255, 0, 0), thickness=2):
    """Draw the inner white squares if detected."""
    if 'nested_squares' in det and det['nested_squares']:
        for inner in det['nested_squares']:
            # Draw each inner square
            pts = np.array(inner, dtype=np.int32)
            cv2.polylines(img, [pts], True, color, thickness)
            # Draw corner points
            for px, py in pts:
                cv2.circle(img, (int(px), int(py)), 4, (0, 0, 255), -1)

    # Also check for nested_pattern_count or white squares data
    if 'white_squares' in det:
        for ws in det['white_squares']:
            if isinstance(ws, dict) and 'contour' in ws:
                pts = np.array(ws['contour'], dtype=np.int32)
                cv2.polylines(img, [pts], True, color, thickness)

    return img

def visualize_detection(img_path, output_dir='uploads/debug'):
    """Run detection and create visualization."""
    print(f"\n{'='*60}")
    print(f"Testing calibration square detection on:")
    print(f"  {img_path}")
    print(f"{'='*60}\n")

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Load image
    img = cv2.imread(img_path)
    if img is None:
        print(f"❌ Failed to load image: {img_path}")
        return None

    h, w = img.shape[:2]
    print(f"📐 Image size: {w}x{h} pixels\n")

    # Run detection
    print("🔍 Running detection...")
    detections = detect_dark_squares_robust(img, debug=True)

    print(f"\n✅ Found {len(detections)} calibration marker(s)\n")

    if not detections:
        print("⚠️  No calibration markers detected!")
        return None

    # Create visualization
    overlay = img.copy()

    for idx, det in enumerate(detections):
        print(f"Marker #{idx + 1}:")
        print(f"  BBox: {det['bbox']}")

        if 'score' in det:
            print(f"  Score: {det['score']:.3f}")

        if 'nested_pattern_count' in det:
            print(f"  Nested squares detected: {det['nested_pattern_count']}")

        if 'has_white_border' in det:
            print(f"  White border: {det['has_white_border']}")

        # Draw outer square (green)
        draw_outer_square(overlay, det, color=(0, 255, 0), thickness=4)

        # Draw inner squares (red)
        draw_inner_squares(overlay, det, color=(255, 0, 0), thickness=2)

        # Add label
        x, y, w, h = det['bbox']
        label = f"Marker {idx+1}"
        cv2.putText(overlay, label, (x, y-10),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        print()

    # Save overlay
    base_name = Path(img_path).stem
    output_path = os.path.join(output_dir, f"{base_name}_detection_overlay.jpg")
    cv2.imwrite(output_path, overlay)
    print(f"💾 Saved overlay to: {output_path}")

    # Create a side-by-side comparison
    comparison = np.hstack([img, overlay])
    comparison_path = os.path.join(output_dir, f"{base_name}_comparison.jpg")
    cv2.imwrite(comparison_path, comparison)
    print(f"💾 Saved comparison to: {comparison_path}")

    return detections, overlay

def test_with_refinement(img_path, output_dir='uploads/debug'):
    """Test detection with edge refinement."""
    print(f"\n{'='*60}")
    print(f"Testing WITH edge refinement:")
    print(f"{'='*60}\n")

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Load image
    img = cv2.imread(img_path)
    if img is None:
        print(f"❌ Failed to load image: {img_path}")
        return None

    # Run detection
    detections = detect_dark_squares_robust(img, debug=True)

    if not detections:
        print("⚠️  No markers detected for refinement test")
        return None

    # Create visualization with refined edges
    overlay = img.copy()

    for idx, det in enumerate(detections):
        x, y, w, h = det['bbox']

        # Extract the region
        roi = img[y:y+h, x:x+w]

        # Try to find refined edges. find_main_edges returns
        # (overlay, n_contours, warped, corners); we only need the corners.
        print(f"\n🔧 Refining edges for marker #{idx + 1}...")
        _vis, _n, _warped, refined_corners = find_main_edges(
            roi,
            polarity="dark",
            debug=True
        )

        if refined_corners is not None and len(refined_corners) == 4:
            # Offset corners back to full image coordinates
            refined_corners_abs = refined_corners + np.array([x, y])

            # Draw refined boundary (cyan)
            pts = np.array(refined_corners_abs, dtype=np.int32)
            cv2.polylines(overlay, [pts], True, (255, 255, 0), 3)

            # Draw corner points (magenta)
            for cx, cy in refined_corners_abs:
                cv2.circle(overlay, (int(cx), int(cy)), 8, (255, 0, 255), -1)
                cv2.circle(overlay, (int(cx), int(cy)), 10, (255, 255, 0), 2)

            print(f"  ✅ Refined corners: {refined_corners_abs.tolist()}")
        else:
            print(f"  ⚠️  Could not refine edges")
            # Draw original bbox
            cv2.rectangle(overlay, (x, y), (x+w, y+h), (0, 255, 0), 3)

    # Save result
    base_name = Path(img_path).stem
    output_path = os.path.join(output_dir, f"{base_name}_refined_overlay.jpg")
    cv2.imwrite(output_path, overlay)
    print(f"\n💾 Saved refined overlay to: {output_path}")

    return overlay

def main():
    # Find a sample image
    uploads_dir = Path('uploads')

    # Look for the first JPG file
    sample_images = list(uploads_dir.glob('*.jpg'))

    if not sample_images:
        print("❌ No sample images found in uploads/")
        print("Please upload an image with calibration markers first.")
        return 1

    # Use the first image
    test_image = str(sample_images[0])

    print("\n" + "="*60)
    print("CALIBRATION SQUARE DETECTION TEST")
    print("="*60)
    print(f"\nLegend:")
    print("  🟢 Green box/polyline = Outer black square")
    print("  🔴 Red polylines = Inner white squares")
    print("  🟣 Magenta circles = Refined corner points")
    print("  🔵 Cyan polyline = Refined edge boundary")
    print()

    # Run basic detection
    detections, overlay = visualize_detection(test_image)

    if detections:
        # Run edge refinement test
        test_with_refinement(test_image)

    print("\n" + "="*60)
    print("✅ Detection test complete!")
    print("="*60)
    print(f"\nCheck uploads/debug/ for visualization outputs")

    return 0

if __name__ == '__main__':
    sys.exit(main())
