
import os, cv2, json, numpy as np
from typing import Dict, Any, Tuple
from detect_squares import detect_dark_squares
from edge_finder import find_main_edges

EDGE_MM_DEFAULT = 47.5
PADDING_PX = 50
DOWNSCALE_FACTOR = 0.8
MAX_EDGES = 10

def _sweep_detector(img, thresholds):
    seen, merged = set(), []
    for t in thresholds:
        dets = detect_dark_squares(img, brightness_thresh=t)
        for det in dets:
            _, x, y, w, h, *_ = det
            key = (round(x/10), round(y/10), round(w/10), round(h/10))
            if key not in seen:
                seen.add(key); merged.append(det)
    return merged

def _crop_region(img, x, y, w, h, pad=0):
    H, W = img.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    return img[y0:y1, x0:x1], x0, y0

def calibrate_image(img_bgr, edge_mm: float = EDGE_MM_DEFAULT, dark_thresholds=(60,80,100), line_thickness: int = 6):
    H, W = img_bgr.shape[:2]
    markers, overlay = [], img_bgr.copy()

    dark_squares = _sweep_detector(img_bgr, dark_thresholds)
    idx = 0
    for det in dark_squares:
        _, x, y, w, h, *_ = det
        crop, ox, oy = _crop_region(img_bgr, x, y, w, h, pad=PADDING_PX)
        crop_ds = cv2.resize(crop, (int(crop.shape[1]*DOWNSCALE_FACTOR), int(crop.shape[0]*DOWNSCALE_FACTOR))) if DOWNSCALE_FACTOR < 1.0 else crop
        edge_vis, n_edges, warped, corners_local = find_main_edges(crop_ds, MAX_EDGES, warp=True)
        if not corners_local: continue

        mapped = [(ox + int(cx / DOWNSCALE_FACTOR), oy + int(cy / DOWNSCALE_FACTOR)) for (cx,cy) in corners_local]
        px_edge = np.mean([np.linalg.norm(np.array(mapped[i]) - np.array(mapped[(i+1)%4])) for i in range(4)])
        if px_edge <= 0: continue
        mm_per_px = edge_mm / px_edge

        cv2.polylines(overlay, [np.array(mapped, np.int32)], True, (0,255,255), line_thickness)
        for (gx, gy) in mapped:
            cv2.circle(overlay, (gx, gy), 10, (0,0,0), -1)
            cv2.circle(overlay, (gx, gy), 7, (0,255,255), -1)

        idx += 1
        markers.append({
            "id": idx,
            "mm_per_px": float(mm_per_px),
            "edge_mm": float(edge_mm),
            "corners": [{"x": int(a), "y": int(b)} for (a,b) in mapped]
        })

    return ({"image": None, "image_size": {"width": int(W), "height": int(H)}, "markers": markers}, overlay)

def save_outputs(image_name: str, cal_data: Dict[str, Any], overlay_img, out_dir: str) -> Tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    base, _ = os.path.splitext(os.path.basename(image_name))
    json_path = os.path.join(out_dir, f"{base}.calibration.json")
    overlay_path = os.path.join(out_dir, f"{base}_overlay.jpg")
    cal = dict(cal_data); cal["image"] = os.path.basename(image_name)
    with open(json_path, "w", encoding="utf-8") as f: json.dump(cal, f, indent=2)
    cv2.imwrite(overlay_path, overlay_img)
    return json_path, overlay_path
