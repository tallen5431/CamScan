#!/usr/bin/env python3
"""
Alternative detection approach:
1. Find white/bright squares (the outer border)
2. Look inside for the black square
3. Find the 4 white squares inside the black square
"""
import cv2
import numpy as np
import os

def detect_calibration_markers(img_path, output_dir='uploads/debug'):
    """Detect calibration markers by finding white borders first."""
    print(f"\n{'='*70}")
    print(f"Detecting calibration markers (white border approach)")
    print(f"{'='*70}\n")

    # Load image
    img = cv2.imread(img_path)
    if img is None:
        print(f"❌ Could not load {img_path}")
        return

    h, w = img.shape[:2]
    print(f"Image size: {w}x{h}\n")

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Preprocessing
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    # Find BRIGHT regions (white border)
    print("🔍 Step 1: Finding white/bright regions...")
    _, bright_mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Clean up the mask
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    bright_mask = cv2.morphologyEx(bright_mask, cv2.MORPH_CLOSE, k, iterations=2)
    bright_mask = cv2.morphologyEx(bright_mask, cv2.MORPH_OPEN, k, iterations=1)

    # Find contours of bright regions
    contours, _ = cv2.findContours(bright_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print(f"   Found {len(contours)} bright contours\n")

    # Filter for square-like contours
    min_area = 10000  # Minimum marker size in pixels
    max_area = (w * h) * 0.3  # Maximum 30% of image

    calibration_markers = []

    print("🔍 Step 2: Filtering for square markers...")
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area or area > max_area:
            continue

        # Check if roughly square
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)

        # Get bounding box
        x, y, w_box, h_box = cv2.boundingRect(c)
        aspect_ratio = max(w_box / max(1, h_box), h_box / max(1, w_box))

        # Look for roughly square shapes
        if aspect_ratio < 1.5 and len(approx) >= 4:
            calibration_markers.append({
                'contour': c,
                'approx': approx,
                'bbox': (x, y, w_box, h_box),
                'area': area,
                'aspect': aspect_ratio
            })

    print(f"   Found {len(calibration_markers)} potential markers\n")

    # Create visualization
    overlay = img.copy()
    os.makedirs(output_dir, exist_ok=True)

    for idx, marker in enumerate(calibration_markers):
        x, y, w_box, h_box = marker['bbox']

        print(f"Marker #{idx+1}:")
        print(f"   BBox: ({x}, {y}) {w_box}x{h_box}")
        print(f"   Area: {marker['area']:.0f} pixels")
        print(f"   Aspect ratio: {marker['aspect']:.2f}")

        # Draw outer white border (GREEN)
        cv2.polylines(overlay, [marker['approx']], True, (0, 255, 0), 4)
        cv2.rectangle(overlay, (x, y), (x+w_box, y+h_box), (0, 255, 255), 2)

        # Extract ROI to find inner structure
        roi = img[y:y+h_box, x:x+w_box]
        if roi.size == 0:
            continue

        # Step 3: Find the black square inside
        gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)

        # Threshold for dark regions
        _, dark_mask = cv2.threshold(gray_roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Clean up
        k_small = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, k_small, iterations=1)

        # Find dark contours
        dark_contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # Find the largest dark square (should be the black center square)
        largest_dark = None
        largest_dark_area = 0
        for dc in dark_contours:
            da = cv2.contourArea(dc)
            if da > largest_dark_area and da > (w_box * h_box) * 0.1:  # At least 10% of ROI
                largest_dark = dc
                largest_dark_area = da

        if largest_dark is not None:
            # Draw the black square boundary (CYAN)
            dark_offset = largest_dark + np.array([x, y])
            cv2.polylines(overlay, [dark_offset], True, (255, 255, 0), 3)
            print(f"   Black square area: {largest_dark_area:.0f} pixels")

            # Get bbox of black square
            bx, by, bw, bh = cv2.boundingRect(largest_dark)

            # Step 4: Find white squares inside the black square
            black_roi = gray_roi[by:by+bh, bx:bx+bw]

            if black_roi.size > 0:
                # Threshold for bright regions within the black square
                _, white_inside_mask = cv2.threshold(black_roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

                # Clean up
                white_inside_mask = cv2.morphologyEx(white_inside_mask, cv2.MORPH_OPEN, k_small, iterations=1)

                # Find white square contours
                white_contours, _ = cv2.findContours(white_inside_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                # Filter for square-like white regions
                inner_squares = []
                for wc in white_contours:
                    wa = cv2.contourArea(wc)
                    if wa < 100 or wa > (bw * bh) * 0.25:  # Between 100px and 25% of black square
                        continue

                    wx, wy, ww, wh = cv2.boundingRect(wc)
                    w_aspect = max(ww / max(1, wh), wh / max(1, ww))
                    if w_aspect < 2.0:  # Roughly square
                        inner_squares.append(wc)

                print(f"   Inner white squares: {len(inner_squares)}")

                # Draw inner white squares (RED)
                for ws in inner_squares:
                    # Offset to full image coordinates
                    ws_offset = ws + np.array([x + bx, y + by])
                    cv2.polylines(overlay, [ws_offset], True, (0, 0, 255), 2)

                    # Draw corner points
                    for pt in ws_offset:
                        px, py = pt[0]
                        cv2.circle(overlay, (int(px), int(py)), 4, (255, 0, 0), -1)

        # Label the marker
        cv2.putText(overlay, f"Marker {idx+1}", (x, y-10),
                   cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        print()

    # Save results
    output_path = os.path.join(output_dir, 'white_border_detection.jpg')
    cv2.imwrite(output_path, overlay)
    print(f"💾 Saved overlay to: {output_path}")

    # Save comparison
    comparison = np.hstack([img, overlay])
    comp_path = os.path.join(output_dir, 'white_border_comparison.jpg')
    cv2.imwrite(comp_path, comparison)
    print(f"💾 Saved comparison to: {comp_path}")

    print("\n" + "="*70)
    print("Legend:")
    print("  🟢 Green polyline = Outer white border boundary")
    print("  🔵 Cyan polyline = Inner black square boundary")
    print("  🔴 Red polylines = White squares inside black square")
    print("  🔵 Yellow box = Bounding box")
    print("="*70)

    return calibration_markers

if __name__ == '__main__':
    img_path = 'uploads/17638603313972476237896658040938-91125d35.jpg'
    if os.path.exists(img_path):
        markers = detect_calibration_markers(img_path)
        print(f"\n✅ Successfully detected {len(markers)} calibration marker(s)!")
    else:
        print("Image not found!")
