#!/usr/bin/env python3
"""
Diagnostic script to understand why detection is failing.
Shows intermediate processing steps.
"""
import cv2
import numpy as np
import os

def diagnose_image(img_path):
    print(f"\n{'='*70}")
    print(f"Diagnosing: {img_path}")
    print(f"{'='*70}\n")

    # Load image
    img = cv2.imread(img_path)
    if img is None:
        print(f"❌ Could not load {img_path}")
        return

    h, w = img.shape[:2]
    print(f"Image size: {w}x{h} ({w*h:,} pixels)")

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Apply preprocessing similar to detect_squares.py
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(blur)

    # Try different thresholding methods
    print("\n📊 Thresholding Analysis:")
    print("-" * 70)

    # OTSU threshold (inverted for dark squares)
    _, otsu_mask = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    otsu_contours, _ = cv2.findContours(otsu_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print(f"OTSU (inverted):       {len(otsu_contours)} contours")

    # Adaptive threshold (inverted for dark squares)
    adaptive_mask = cv2.adaptiveThreshold(enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                          cv2.THRESH_BINARY_INV, 21, 5)
    adaptive_contours, _ = cv2.findContours(adaptive_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print(f"Adaptive (inverted):   {len(adaptive_contours)} contours")

    # Combined mask
    dark_mask = cv2.bitwise_or(otsu_mask, adaptive_mask)

    # Morphological operations
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_CLOSE, k, iterations=2)
    dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_OPEN, k, iterations=1)

    contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    print(f"After morphology:      {len(contours)} contours")

    # Analyze contour sizes
    if contours:
        areas = [cv2.contourArea(c) for c in contours]
        areas_sorted = sorted(areas, reverse=True)

        print(f"\n📏 Contour Size Analysis:")
        print("-" * 70)
        print(f"Total contours: {len(contours)}")
        print(f"Largest 10 areas: {areas_sorted[:10]}")

        # Calculate minimum area threshold
        frame_area = w * h
        min_area_default = 400
        min_area_pct = 0.00015 * frame_area
        min_area_used = max(min_area_default, min_area_pct)
        print(f"\nMin area threshold: {min_area_used:.0f} pixels ({min_area_pct/frame_area*100:.4f}% of image)")

        # Count how many pass the area filter
        passing_area = [a for a in areas if a >= min_area_used]
        print(f"Contours passing area filter: {len(passing_area)}")

    # Create visualizations
    os.makedirs('uploads/debug', exist_ok=True)

    # Save intermediate steps
    cv2.imwrite('uploads/debug/01_original.jpg', img)
    cv2.imwrite('uploads/debug/02_gray.jpg', gray)
    cv2.imwrite('uploads/debug/03_enhanced.jpg', enhanced)
    cv2.imwrite('uploads/debug/04_otsu_mask.jpg', otsu_mask)
    cv2.imwrite('uploads/debug/05_adaptive_mask.jpg', adaptive_mask)
    cv2.imwrite('uploads/debug/06_dark_mask_final.jpg', dark_mask)

    # Draw all contours on image
    vis_all = img.copy()
    cv2.drawContours(vis_all, contours, -1, (0, 255, 0), 2)
    cv2.imwrite('uploads/debug/07_all_contours.jpg', vis_all)

    # Draw only large contours
    if contours:
        min_area_used = max(400, 0.00015 * (w * h))
        large_contours = [c for c in contours if cv2.contourArea(c) >= min_area_used]

        if large_contours:
            vis_large = img.copy()
            cv2.drawContours(vis_large, large_contours, -1, (0, 255, 255), 3)

            # Draw bounding boxes
            for c in large_contours:
                x, y, w_box, h_box = cv2.boundingRect(c)
                cv2.rectangle(vis_large, (x, y), (x+w_box, y+h_box), (255, 0, 0), 2)

            cv2.imwrite('uploads/debug/08_large_contours.jpg', vis_large)

            print(f"\n💾 Saved {len(large_contours)} large contours visualization")

    print(f"\n💾 Saved diagnostic images to uploads/debug/")
    print("="*70)

    # Try the actual detection function
    print(f"\n🔍 Running actual detect_dark_squares()...")
    print("-" * 70)

    os.environ['CAMSCAN_DEBUG'] = '1'
    from detect_squares import detect_dark_squares

    dets = detect_dark_squares(
        img,
        min_area=100,  # Very permissive
        check_nested_pattern=False,  # Disable nested pattern check
        debug=True
    )

    print(f"\n✅ Detection returned: {len(dets)} results")

    if dets:
        vis_det = img.copy()
        for det in dets:
            if isinstance(det, tuple):
                score, x, y, w, h, mean_val = det
            else:
                x, y, w, h = det['bbox']

            cv2.rectangle(vis_det, (x, y), (x+w, y+h), (0, 255, 0), 4)

        cv2.imwrite('uploads/debug/09_detected_squares.jpg', vis_det)
        print("💾 Saved detected squares visualization")

if __name__ == '__main__':
    import glob
    images = glob.glob('uploads/*.jpg')
    if images:
        diagnose_image(images[0])
    else:
        print("No images found in uploads/")
