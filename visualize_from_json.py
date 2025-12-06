#!/usr/bin/env python3
"""
Visualize calibration markers using the JSON data.
This shows us what SHOULD be detected.
"""
import cv2
import numpy as np
import json
import os

def visualize_calibration_json(img_path, json_path, output_path='uploads/debug/calibration_overlay.jpg'):
    """Create visualization from calibration JSON file."""
    print(f"\n{'='*70}")
    print(f"Visualizing calibration from JSON")
    print(f"{'='*70}\n")

    # Load image
    img = cv2.imread(img_path)
    if img is None:
        print(f"❌ Could not load {img_path}")
        return

    # Load JSON
    with open(json_path, 'r') as f:
        calib_data = json.load(f)

    print(f"Image: {calib_data['image']}")
    print(f"Image size: {calib_data['image_size']['width']}x{calib_data['image_size']['height']}")
    print(f"Marker size: {calib_data['marker_size_mm']} mm")
    print(f"Found {len(calib_data['markers'])} markers\n")

    # Create overlay
    overlay = img.copy()

    for idx, marker in enumerate(calib_data['markers']):
        corners = marker['corners']
        print(f"Marker #{idx+1}:")
        print(f"  Edge: {marker['edge_mm']} mm")
        print(f"  mm/px: {marker['mm_per_px']:.6f}")
        print(f"  Corners: {[(c['x'], c['y']) for c in corners]}")

        # Convert corners to numpy array
        pts = np.array([[c['x'], c['y']] for c in corners], dtype=np.int32)

        # Draw the outer square boundary (GREEN)
        cv2.polylines(overlay, [pts], True, (0, 255, 0), 4)

        # Draw corner points (MAGENTA)
        for c in corners:
            cv2.circle(overlay, (c['x'], c['y']), 8, (255, 0, 255), -1)
            cv2.circle(overlay, (c['x'], c['y']), 10, (0, 255, 255), 2)

        # Calculate bounding box
        x_coords = [c['x'] for c in corners]
        y_coords = [c['y'] for c in corners]
        x_min, x_max = min(x_coords), max(x_coords)
        y_min, y_max = min(y_coords), max(y_coords)

        # Draw label
        cv2.putText(overlay, f"Marker {idx+1}", (x_min, y_min-15),
                   cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)

        # Extract and analyze the ROI
        roi = img[y_min:y_max, x_min:x_max]
        if roi.size > 0:
            # Try to detect the inner white squares
            gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

            # Threshold to find white squares
            _, white_mask = cv2.threshold(gray_roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # Find contours of white regions
            contours, _ = cv2.findContours(white_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            # Filter for roughly square contours
            inner_squares = []
            for c in contours:
                area = cv2.contourArea(c)
                if area < 100 or area > (roi.shape[0] * roi.shape[1]) * 0.2:
                    continue
                x, y, w, h = cv2.boundingRect(c)
                aspect = max(w/max(1, h), h/max(1, w))
                if aspect < 2.0:  # Roughly square
                    inner_squares.append(c)

            print(f"  Inner white squares detected: {len(inner_squares)}")

            # Draw inner squares (RED)
            for c in inner_squares:
                # Offset back to full image coordinates
                c_offset = c + np.array([x_min, y_min])
                cv2.polylines(overlay, [c_offset], True, (0, 0, 255), 2)

                # Draw corner points for inner squares
                for pt in c_offset:
                    px, py = pt[0]
                    cv2.circle(overlay, (int(px), int(py)), 4, (255, 0, 0), -1)

        print()

    # Save result
    cv2.imwrite(output_path, overlay)
    print(f"💾 Saved visualization to: {output_path}")

    # Create comparison
    comparison = np.hstack([img, overlay])
    comparison_path = output_path.replace('.jpg', '_comparison.jpg')
    cv2.imwrite(comparison_path, comparison)
    print(f"💾 Saved comparison to: {comparison_path}")

    print("\n" + "="*70)
    print("Legend:")
    print("  🟢 Green polylines = Outer calibration marker boundary")
    print("  🔴 Red polylines = Inner white squares")
    print("  🟣 Magenta circles = Corner points")
    print("  🔵 Cyan outline = Corner point highlight")
    print("="*70)

    return overlay

if __name__ == '__main__':
    img_path = 'uploads/17638603313972476237896658040938-91125d35.jpg'
    json_path = 'uploads/17638603313972476237896658040938-91125d35.calibration.json'

    if os.path.exists(img_path) and os.path.exists(json_path):
        visualize_calibration_json(img_path, json_path)
    else:
        print("Required files not found!")
