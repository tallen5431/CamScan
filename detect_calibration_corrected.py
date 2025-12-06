#!/usr/bin/env python3
"""
Calibration marker detection using the UPDATED detect_squares.py and edge_finder.py.

This script properly uses:
- polarity="bright" to detect bright squares
- enforce_calibration_pattern=True to find the 4-inner-squares pattern
- edge_finder with polarity for both dark and bright squares
"""
import cv2
import numpy as np
import os
import sys

from detect_squares import detect_dark_squares_robust
from edge_finder import find_main_edges

def load_image(img_path):
    """Load image in BGR format."""
    img = cv2.imread(img_path)
    if img is None:
        raise ValueError(f"Could not load image: {img_path}")
    return img

def detect_calibration_pattern(img_bgr, debug=False):
    """
    Detect calibration pattern using the updated API.
    Returns: (outer_box, inner_boxes, detections)
    """
    print(f"\n{'='*70}")
    print("Detecting calibration pattern with updated detect_squares API")
    print(f"{'='*70}\n")

    # Use the correct parameters for the updated detect_squares
    detections = detect_dark_squares_robust(
        img_bgr,
        min_area=400,
        max_area_ratio=0.5,
        max_aspect=1.3,
        min_fill_ratio=0.7,
        min_hull_ratio=0.9,
        min_compactness=0.6,
        border_margin_frac=0.02,
        polarity="bright",                    # KEY: looking for bright squares
        enforce_calibration_pattern=True,     # KEY: find the 4-square pattern
        calibration_outer_only=False,         # Keep outer + 4 inner
        max_results=20,
        debug=debug,
    )

    print(f"✅ Detections returned: {len(detections)}")

    outer_box = None
    inner_boxes = []

    if len(detections) > 0:
        # First detection is the outer square
        _, x0, y0, w0, h0, _ = detections[0]
        outer_box = (x0, y0, w0, h0)
        print(f"\nOuter square: ({x0}, {y0}) {w0}x{h0}")

        # Rest are inner white squares
        for idx, (_, x, y, w, h, _) in enumerate(detections[1:], start=1):
            inner_boxes.append((x, y, w, h))
            print(f"Inner square #{idx}: ({x}, {y}) {w}x{h}")

    return outer_box, inner_boxes, detections

def draw_detection_overlay(img_bgr, outer_box, inner_boxes):
    """Draw colored overlay showing detected squares."""
    overlay = img_bgr.copy()

    if outer_box is not None:
        x0, y0, w0, h0 = outer_box
        # Draw outer square in RED
        cv2.rectangle(overlay, (x0, y0), (x0 + w0, y0 + h0), (0, 0, 255), 8)
        cv2.putText(overlay, "OUTER", (x0 + 10, y0 + 40),
                   cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3, cv2.LINE_AA)

    # Draw inner squares in GREEN
    for idx, (x, y, w, h) in enumerate(inner_boxes, start=1):
        cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 255, 0), 6)
        cv2.putText(overlay, f"#{idx}", (x + 8, y + 32),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2, cv2.LINE_AA)

    return overlay

def refine_edges_with_padding(img_bgr, outer_box, inner_boxes, pad_frac=0.18):
    """
    Refine edges using edge_finder on padded crop.
    Returns: (padded_overlay, warped_outer, outer_corners, inner_corners_list)
    """
    print(f"\n{'='*70}")
    print("Refining edges with edge_finder")
    print(f"{'='*70}\n")

    if outer_box is None:
        print("⚠️  No outer box to refine")
        return None, None, None, []

    x0, y0, w0, h0 = outer_box
    h_img, w_img = img_bgr.shape[:2]

    # Create padded crop around outer box
    pad = int(max(w0, h0) * pad_frac)
    x_pad0 = max(0, x0 - pad)
    y_pad0 = max(0, y0 - pad)
    x_pad1 = min(w_img, x0 + w0 + pad)
    y_pad1 = min(h_img, y0 + h0 + pad)

    padded_crop = img_bgr[y_pad0:y_pad1, x_pad0:x_pad1].copy()
    padded_overlay = padded_crop.copy()

    # --- Refine outer black square ---
    # Map outer box into padded-crop coordinates
    black_x0_rel = max(0, x0 - x_pad0)
    black_y0_rel = max(0, y0 - y_pad0)
    black_x1_rel = min(padded_crop.shape[1], black_x0_rel + w0)
    black_y1_rel = min(padded_crop.shape[0], black_y0_rel + h0)

    black_crop = padded_crop[black_y0_rel:black_y1_rel, black_x0_rel:black_x1_rel].copy()

    warped_outer = None
    outer_corners_padded = None

    if black_crop.size > 0:
        print("🔧 Refining outer black square...")
        gray_black = cv2.cvtColor(black_crop, cv2.COLOR_BGR2GRAY)

        # Use OTSU for dark regions
        _, mask_dark = cv2.threshold(gray_black, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Find largest dark contour
        contours, _ = cv2.findContours(mask_dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            c_big = max(contours, key=cv2.contourArea)
            rect = cv2.minAreaRect(c_big)
            box = cv2.boxPoints(rect)

            # Order quad corners
            ordered = box.astype(np.float32)

            # Warp to square
            warp_size = 256
            pts_src = np.float32(ordered)
            pts_dst = np.float32([[0, 0], [warp_size-1, 0],
                                 [warp_size-1, warp_size-1], [0, warp_size-1]])
            M = cv2.getPerspectiveTransform(pts_src, pts_dst)
            warped_outer = cv2.warpPerspective(black_crop, M, (warp_size, warp_size))

            # Map corners back to padded-crop coordinates
            outer_corners_padded = [
                (int(cx + black_x0_rel), int(cy + black_y0_rel))
                for (cx, cy) in ordered
            ]

            # Draw outer square (MAGENTA)
            pts_outer = np.array(outer_corners_padded, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(padded_overlay, [pts_outer], True, (255, 0, 255), 4, cv2.LINE_AA)

            for j, (cx, cy) in enumerate(outer_corners_padded):
                cv2.circle(padded_overlay, (cx, cy), 6, (0, 0, 0), -1)
                cv2.circle(padded_overlay, (cx, cy), 4, (255, 0, 255), -1)
                cv2.putText(padded_overlay, f"O{j}", (cx + 6, cy - 6),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)

            print(f"   ✅ Found {len(outer_corners_padded)} corners")

    # --- Refine inner white squares ---
    inner_corners_list = []
    INNER_PAD_FRAC = 0.25

    print(f"\n🔧 Refining {len(inner_boxes)} inner white squares...")
    for idx, (ix, iy, iw, ih) in enumerate(inner_boxes, start=1):
        # Map inner box into padded-crop coordinates
        rel_x = ix - x_pad0
        rel_y = iy - y_pad0

        pad_inner = int(max(iw, ih) * INNER_PAD_FRAC)
        cx0 = max(0, rel_x - pad_inner)
        cy0 = max(0, rel_y - pad_inner)
        cx1 = min(padded_crop.shape[1], rel_x + iw + pad_inner)
        cy1 = min(padded_crop.shape[0], rel_y + ih + pad_inner)

        if cx1 <= cx0 or cy1 <= cy0:
            continue

        inner_crop = padded_crop[cy0:cy1, cx0:cx1].copy()

        # Use edge_finder with polarity="bright" for white squares
        _, _, _, corners_inner_crop = find_main_edges(
            inner_crop,
            max_edges=8,
            warp=False,
            warp_size=256,
            min_area=None,
            debug=False,
            polarity="bright",  # KEY: bright polarity for white squares
        )

        if corners_inner_crop is None or len(corners_inner_crop) != 4:
            print(f"   ⚠️  Inner square #{idx}: Could not refine edges")
            continue

        # Map corners back to padded-crop coordinates
        inner_corners_padded = [
            (int(cx + cx0), int(cy + cy0)) for (cx, cy) in corners_inner_crop
        ]
        inner_corners_list.append(inner_corners_padded)

        # Draw inner square (CYAN)
        pts_inner = np.array(inner_corners_padded, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(padded_overlay, [pts_inner], True, (255, 255, 0), 3, cv2.LINE_AA)

        for j, (cx, cy) in enumerate(inner_corners_padded):
            cv2.circle(padded_overlay, (cx, cy), 5, (0, 0, 0), -1)
            cv2.circle(padded_overlay, (cx, cy), 3, (255, 255, 0), -1)

        print(f"   ✅ Inner square #{idx}: Found 4 corners")

    return padded_overlay, warped_outer, outer_corners_padded, inner_corners_list

def save_visualization(img_bgr, overlay, padded_overlay, warped_outer, output_dir='uploads/debug'):
    """Save all visualizations."""
    os.makedirs(output_dir, exist_ok=True)

    # Original vs detection overlay
    comparison = np.hstack([img_bgr, overlay])
    cv2.imwrite(f'{output_dir}/01_detection_overlay.jpg', overlay)
    cv2.imwrite(f'{output_dir}/02_detection_comparison.jpg', comparison)
    print(f"\n💾 Saved detection overlay to {output_dir}/01_detection_overlay.jpg")

    # Padded crop with refined edges
    if padded_overlay is not None:
        cv2.imwrite(f'{output_dir}/03_refined_edges.jpg', padded_overlay)
        print(f"💾 Saved refined edges to {output_dir}/03_refined_edges.jpg")

    # Warped outer square
    if warped_outer is not None:
        cv2.imwrite(f'{output_dir}/04_warped_outer.jpg', warped_outer)
        print(f"💾 Saved warped outer square to {output_dir}/04_warped_outer.jpg")

def main():
    img_path = 'uploads/17638603313972476237896658040938-91125d35.jpg'

    if not os.path.exists(img_path):
        print(f"❌ Image not found: {img_path}")
        print("Please update img_path in the script")
        return 1

    print(f"\n{'='*70}")
    print(f"CALIBRATION MARKER DETECTION (Updated API)")
    print(f"{'='*70}")
    print(f"Image: {img_path}\n")

    # Load image
    img_bgr = load_image(img_path)
    h, w = img_bgr.shape[:2]
    print(f"Image size: {w}x{h} ({w*h:,} pixels)")

    # Step 1: Detect calibration pattern
    outer_box, inner_boxes, detections = detect_calibration_pattern(img_bgr, debug=False)

    if outer_box is None:
        print("\n❌ No calibration pattern detected!")
        return 1

    # Step 2: Draw detection overlay
    overlay = draw_detection_overlay(img_bgr, outer_box, inner_boxes)

    # Step 3: Refine edges with padding
    padded_overlay, warped_outer, outer_corners, inner_corners_list = \
        refine_edges_with_padding(img_bgr, outer_box, inner_boxes)

    # Step 4: Save visualizations
    save_visualization(img_bgr, overlay, padded_overlay, warped_outer)

    print(f"\n{'='*70}")
    print("✅ Detection complete!")
    print(f"{'='*70}")
    print("\nLegend:")
    print("  🔴 Red box = Outer white border")
    print("  🟢 Green boxes = Inner white squares (4 detected)")
    print("  🟣 Magenta polyline = Refined outer black square edges")
    print("  🟡 Yellow/Cyan polylines = Refined inner white square edges")
    print(f"\n📁 All outputs saved to: uploads/debug/")

    return 0

if __name__ == '__main__':
    sys.exit(main())
