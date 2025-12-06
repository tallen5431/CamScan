#!/usr/bin/env python3
"""
Simple test to visualize what the detection is finding.
"""
import cv2
import numpy as np
import os

# Set debug mode
os.environ['CAMSCAN_DEBUG'] = '1'

from detect_squares import detect_dark_squares, detect_dark_squares_robust

def test_image(img_path):
    print(f"\n{'='*70}")
    print(f"Testing: {img_path}")
    print(f"{'='*70}\n")

    # Load image
    img = cv2.imread(img_path)
    if img is None:
        print(f"❌ Could not load {img_path}")
        return

    h, w = img.shape[:2]
    print(f"Image size: {w}x{h}")

    # Try the basic detection first with minimal filtering
    print("\n📍 Testing basic detect_dark_squares()...")
    print("-" * 70)

    basic_dets = detect_dark_squares(
        img,
        min_area=100,  # Very low threshold
        debug=True
    )

    print(f"\n✅ Basic detection found: {len(basic_dets)} candidates\n")

    # Try robust detection
    print("\n📍 Testing detect_dark_squares_robust()...")
    print("-" * 70)

    robust_dets = detect_dark_squares_robust(
        img,
        edge_mm=30.0,
        debug=True
    )

    print(f"\n✅ Robust detection found: {len(robust_dets)} candidates\n")

    # Create visualizations
    if basic_dets:
        vis_basic = img.copy()
        for idx, det in enumerate(basic_dets):
            x, y, w, h = det['bbox']
            cv2.rectangle(vis_basic, (x, y), (x+w, y+h), (0, 255, 0), 3)
            cv2.putText(vis_basic, f"#{idx+1}", (x, y-10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        cv2.imwrite('uploads/debug/basic_detection.jpg', vis_basic)
        print("💾 Saved: uploads/debug/basic_detection.jpg")

    if robust_dets:
        vis_robust = img.copy()
        for idx, det in enumerate(robust_dets):
            x, y, w, h = det['bbox']
            # Draw bbox
            cv2.rectangle(vis_robust, (x, y), (x+w, y+h), (0, 255, 0), 4)

            # Draw corners if available
            if 'corners' in det and det['corners']:
                corners = np.array(det['corners'], dtype=np.int32)
                cv2.polylines(vis_robust, [corners], True, (0, 255, 255), 3)
                for cx, cy in corners:
                    cv2.circle(vis_robust, (int(cx), int(cy)), 6, (255, 0, 255), -1)

            cv2.putText(vis_robust, f"#{idx+1}", (x, y-10),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        cv2.imwrite('uploads/debug/robust_detection.jpg', vis_robust)
        print("💾 Saved: uploads/debug/robust_detection.jpg")

    print("\n" + "="*70)
    print("Check uploads/debug/ and uploads/_debug/ for visualization and debug images")
    print("="*70)

if __name__ == '__main__':
    os.makedirs('uploads/debug', exist_ok=True)

    # Test with first available image
    import glob
    images = glob.glob('uploads/*.jpg')
    if images:
        test_image(images[0])
    else:
        print("No images found in uploads/")
