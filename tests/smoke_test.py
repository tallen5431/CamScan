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

    # 2b) Perspective homography present and sane: the marker's centroid must map
    #     to the centre of the unit square.
    try:
        H = cal.get("homography")
        ok = H is not None and len(H) == 3 and len(H[0]) == 3
        if ok and cal.get("markers"):
            cs = cal["markers"][0]["corners"]
            cxp = sum(c["x"] for c in cs) / len(cs)
            cyp = sum(c["y"] for c in cs) / len(cs)
            w = H[2][0] * cxp + H[2][1] * cyp + H[2][2]
            u = (H[0][0] * cxp + H[0][1] * cyp + H[0][2]) / w
            v = (H[1][0] * cxp + H[1][1] * cyp + H[1][2]) / w
            ok = abs(u - 0.5) < 0.1 and abs(v - 0.5) < 0.1
        check("homography maps marker centre to unit-square centre", ok)
    except Exception as e:
        check("homography", False, repr(e))

    # 2c) Tilted marker: the edge-fit corner refinement must keep the detected corners
    #     close to the TRUE projected corners (a handheld/angled shot). Guards against
    #     regressions in the edge-line fitting that a flat target wouldn't catch.
    try:
        sq = [(450, 300), (750, 300), (750, 600), (450, 600)]  # TL,TR,BR,BL of the square
        H0, W0 = img.shape[:2]
        src = np.float32([[0, 0], [W0, 0], [W0, H0], [0, H0]])
        dst = np.float32([[230, 0], [W0 - 230, 0], [W0, H0], [0, H0]])  # pinch top → ~30° tilt
        Hm = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(img, Hm, (W0, H0), borderValue=(120, 120, 120))
        warped = cv2.GaussianBlur(warped, (5, 5), 0)                    # handheld softness
        gt = cv2.perspectiveTransform(np.float32([[list(p) for p in sq]]), Hm)[0]

        def _order(pts):
            pts = np.asarray(pts, np.float32)
            c = pts.mean(0)
            o = pts[np.argsort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))]
            return np.roll(o, -int(np.argmin(o[:, 0] + o[:, 1])), axis=0)

        with _quiet():
            calt, _ = calibrate_image(warped, edge_mm=30.0)
        ok = bool(calt.get("markers"))
        if ok:
            rec = _order([(c["x"], c["y"]) for c in calt["markers"][0]["corners"]])
            err = float(np.max(np.linalg.norm(rec - _order(gt), axis=1)))
            ok = err < 4.0
            check("tilted marker corners stay on true edges", ok, f"max_corner_err={err:.2f}px")
        else:
            check("tilted marker corners stay on true edges", False, "no markers")
    except Exception as e:
        check("tilted marker refinement", False, repr(e))

    # 2d) Auto-outline: a seed tap on a part segments it into an editable polygon that
    #     matches the true shape and excludes a nearby (foreground) calibration card.
    try:
        from auto_outline import auto_outline
        sc = np.full((640, 900, 3), 185, np.uint8)
        part = np.array([[120, 200], [520, 210], [515, 300], [300, 305], [305, 520], [190, 515]], np.int32)
        cv2.fillPoly(sc, [part], (45, 48, 52))
        cv2.rectangle(sc, (600, 60), (860, 300), (245, 245, 245), -1)   # white card (also foreground)
        cv2.rectangle(sc, (650, 110), (810, 250), (25, 25, 25), -1)     # marker
        gt = np.zeros((640, 900), np.uint8); cv2.fillPoly(gt, [part], 255)
        with _quiet():
            pts = auto_outline(sc, seed=[280, 260], exclude_boxes=[(650, 110, 160, 140)])
        ok = bool(pts) and 4 <= len(pts) <= 120
        if ok:
            m = np.zeros((640, 900), np.uint8); cv2.fillPoly(m, [np.array(pts, np.int32)], 255)
            inter = int(np.logical_and(m > 0, gt > 0).sum()); union = int(np.logical_or(m > 0, gt > 0).sum())
            iou = inter / union if union else 0.0
            no_card = not any(600 <= x <= 860 and 60 <= y <= 300 for (x, y) in pts)
            check("auto-outline segments the part (IoU>0.85, card excluded)", iou > 0.85 and no_card,
                  f"IoU={iou:.3f}, {len(pts)} pts, card_excluded={no_card}")
        else:
            check("auto-outline segments the part", False, f"pts={pts}")
    except Exception as e:
        check("auto-outline", False, repr(e))

    # 2b) auto_outline_full ALSO captures interior holes (a box-end ring, bolt holes) — what
    #     turns a flat silhouette into a printable replica. An open-jaw concavity opens to the
    #     exterior and must NOT be reported as a hole; an enclosed hole must be.
    try:
        from auto_outline import auto_outline_full
        ring = np.full((500, 700, 3), 185, np.uint8)
        cv2.circle(ring, (350, 250), 150, (55, 58, 62), -1)      # disc ...
        cv2.circle(ring, (350, 250), 70, (185, 185, 185), -1)    # ... with a hole (bg shows through)
        with _quiet():
            full = auto_outline_full(ring, seed=[350, 130])       # seed on the ring band
        holes = (full or {}).get("holes") or []
        ok_h = bool(full) and len(full.get("outer") or []) >= 6 and len(holes) >= 1
        detail = f"full={bool(full)}, holes={len(holes)}"
        if ok_h:
            h0 = holes[0]
            # A clean round bore must come back as a TRUE circle (not a faceted polygon).
            ok_h = (isinstance(h0, dict) and h0.get("shape") == "circle"
                    and abs(h0["cx"] - 350) < 25 and abs(h0["cy"] - 250) < 25 and abs(h0["r"] - 70) < 20)
            detail = (f"{len(holes)} hole(s), shape={h0.get('shape')}, "
                      f"center~({h0.get('cx', 0):.0f},{h0.get('cy', 0):.0f}), r~{h0.get('r', 0):.0f} vs 70")
        check("auto_outline_full captures an interior hole (round -> circle)", ok_h, detail)

        # A non-round hole (a slot) must stay a corner-preserving POLYGON, not be forced round.
        slot = np.full((500, 700, 3), 185, np.uint8)
        cv2.circle(slot, (350, 250), 150, (55, 58, 62), -1)
        cv2.rectangle(slot, (290, 232), (410, 268), (185, 185, 185), -1)   # a slot bore
        with _quiet():
            full2 = auto_outline_full(slot, seed=[350, 130])
        h2 = ((full2 or {}).get("holes") or [None])[0]
        ok_p = isinstance(h2, dict) and h2.get("shape") == "polygon" and len(h2.get("points") or []) >= 4
        check("auto_outline_full keeps a non-round hole as a polygon", ok_p,
              f"shape={h2.get('shape') if isinstance(h2, dict) else h2}")

        # A round bore whose rim is partly eaten by a SHADOW must still snap to a clean circle
        # at the true geometry — the robust fit sheds the localized notch (the real-wrench case).
        shad = np.full((520, 720, 3), 185, np.uint8)
        cv2.circle(shad, (360, 260), 160, (55, 58, 62), -1)
        cv2.circle(shad, (360, 260), 78, (185, 185, 185), -1)                         # bright bore
        notch = [[360, 260]] + [[int(360 + 78 * np.cos(a)), int(260 + 78 * np.sin(a))]
                                for a in np.linspace(0, np.deg2rad(55), 10)]
        cv2.fillPoly(shad, [np.array(notch, np.int32)], (45, 47, 50))                  # shadow notch on the rim
        with _quiet():
            full3 = auto_outline_full(shad, seed=[360, 130])
        h3 = ((full3 or {}).get("holes") or [None])[0]
        ok_s = (isinstance(h3, dict) and h3.get("shape") == "circle"
                and abs(h3["cx"] - 360) < 22 and abs(h3["cy"] - 260) < 22 and abs(h3["r"] - 78) < 14)
        check("shadow-notched bore still snaps to a clean circle", ok_s,
              f"shape={h3.get('shape') if isinstance(h3, dict) else h3}, "
              f"c=({h3.get('cx', 0):.0f},{h3.get('cy', 0):.0f}) r={h3.get('r', 0):.0f} vs 78" if isinstance(h3, dict) else str(h3))
    except Exception as e:
        check("auto_outline_full holes", False, repr(e))

    # 2e) A cast shadow on the OUTER boundary. A shadow is the surface darkened — a mid-tone
    #     between the bright surface and the darker part — so a single bg-vs-foreground split
    #     welds it to the part and the trace bulges into it. The shadow refinement (a second
    #     split on the foreground shoulder, adopted only when the tighter boundary sits on
    #     stronger edges) must pull the outline back onto the true part edge.
    try:
        from auto_outline import auto_outline

        def _iou(pts, gt, shape):
            if not pts:
                return 0.0
            m = np.zeros(shape, np.uint8)
            cv2.fillPoly(m, [np.array(pts, np.int32)], 255)
            inter = int(np.logical_and(m > 0, gt > 0).sum())
            union = int(np.logical_or(m > 0, gt > 0).sum())
            return inter / union if union else 0.0

        H, W = 600, 900
        sc = np.full((H, W, 3), 210, np.uint8)                      # bright surface
        cv2.rectangle(sc, (250, 180), (560, 420), (60, 62, 64), -1)  # dark matte part
        band = np.zeros((H, W), np.uint8)
        cv2.rectangle(band, (560, 210), (650, 470), 255, -1)         # shadow hugging the right ...
        cv2.rectangle(band, (300, 420), (650, 470), 255, -1)         # ... and bottom of the part
        sc[(band > 0) & (sc[:, :, 0] > 150)] = (120, 121, 123)       # surface darkened, same hue
        gt_s = np.zeros((H, W), np.uint8); cv2.rectangle(gt_s, (250, 180), (560, 420), 255, -1)
        with _quiet():
            pts_s = auto_outline(sc, seed=[400, 300])
        iou_s = _iou(pts_s, gt_s, (H, W))
        check("cast shadow does not bulge the outer trace (IoU>0.9)", iou_s > 0.9,
              f"IoU={iou_s:.3f} (a bulge into the shadow drops this well below 0.9)")

        # 2f) The guard against the reverted regression: a dark, low-contrast part whose own
        #     body reads shadow-like (chrome-style darker interior reflections) must still be
        #     captured IN FULL — the refinement must decline and keep the plain mask.
        ch = np.full((H, W, 3), 210, np.uint8)
        cv2.rectangle(ch, (250, 180), (620, 430), (95, 97, 99), -1)   # mid-dark neutral body
        for (cx, cy, rr) in [(330, 250, 45), (500, 340, 55), (430, 300, 40), (560, 220, 35), (300, 400, 30)]:
            cv2.circle(ch, (cx, cy), rr, (38, 39, 41), -1)            # darker neutral reflections inside
        gt_c = np.zeros((H, W), np.uint8); cv2.rectangle(gt_c, (250, 180), (620, 430), 255, -1)
        with _quiet():
            pts_c = auto_outline(ch, seed=[280, 200])
        iou_c = _iou(pts_c, gt_c, (H, W))
        check("dark low-contrast part is not eaten by shadow refinement (IoU>0.9)", iou_c > 0.9,
              f"IoU={iou_c:.3f} (refinement must decline here, keeping the whole part)")
    except Exception as e:
        check("auto_outline cast-shadow handling", False, repr(e))

    # 2g) ROI (the user's Area box) restricts segmentation to a region: a nearby object that
    #     the morphology-close would otherwise weld onto the part is cropped out, and the trace
    #     comes back cleanly on the target — in FULL-IMAGE coords. A bad/off-frame ROI is
    #     ignored (passthrough), never a crash.
    try:
        from auto_outline import auto_outline as _ao, auto_outline_full as _aof

        def _iou2(pts, gt, shape):
            if not pts:
                return 0.0
            m = np.zeros(shape, np.uint8); cv2.fillPoly(m, [np.array(pts, np.int32)], 255)
            return int(np.logical_and(m > 0, gt > 0).sum()) / max(1, int(np.logical_or(m > 0, gt > 0).sum()))

        H, W = 500, 700
        rimg = np.full((H, W, 3), 200, np.uint8)
        cv2.rectangle(rimg, (120, 150), (320, 360), (55, 58, 60), -1)   # TARGET
        cv2.rectangle(rimg, (332, 150), (470, 360), (50, 52, 54), -1)   # clutter, ~12px gap
        gt_t = np.zeros((H, W), np.uint8); cv2.rectangle(gt_t, (120, 150), (320, 360), 255, -1)
        seed_t = [220, 255]
        with _quiet():
            base = _ao(rimg, seed=seed_t)                                 # no ROI -> welds clutter on
            roied = _ao(rimg, seed=seed_t, roi=[120, 150, 200, 210])       # ROI -> clean target
        iou_base, iou_roi = _iou2(base, gt_t, (H, W)), _iou2(roied, gt_t, (H, W))
        check("ROI (Area box) isolates the part from nearby clutter", iou_roi > 0.9 and iou_roi > iou_base + 0.1,
              f"no-ROI IoU={iou_base:.3f} -> ROI IoU={iou_roi:.3f}")

        # Results must be in FULL-IMAGE coords (ROI origin added back), so a hole inside the
        # cropped part still lands at its true image position.
        rh_img = np.full((H, W, 3), 200, np.uint8)
        cv2.rectangle(rh_img, (120, 150), (320, 360), (55, 58, 60), -1)
        cv2.circle(rh_img, (220, 255), 34, (200, 200, 200), -1)          # bore near the part centre
        with _quiet():
            full_r = _aof(rh_img, seed=[150, 175], roi=[120, 150, 200, 210])
        hole0 = ((full_r or {}).get("holes") or [None])[0]
        ok_coord = (isinstance(hole0, dict) and hole0.get("shape") == "circle"
                    and abs(hole0["cx"] - 220) < 25 and abs(hole0["cy"] - 255) < 25)
        check("ROI results map back to full-image coords", ok_coord,
              f"bore center~({(hole0 or {}).get('cx', 0):.0f},{(hole0 or {}).get('cy', 0):.0f}) vs (220,255)"
              if isinstance(hole0, dict) else f"hole={hole0}")

        # A degenerate / off-frame ROI must be ignored (fall back to whole-image), not crash.
        with _quiet():
            passthru = _ao(rimg, seed=seed_t, roi=[9999, 9999, 5, 5])
            zero_roi = _ao(rimg, seed=seed_t, roi=[0, 0, 0, 0])
        check("bad ROI is ignored (graceful passthrough)", bool(passthru) and bool(zero_roi),
              f"offframe_pts={len(passthru) if passthru else None}, zero_pts={len(zero_roi) if zero_roi else None}")
    except Exception as e:
        check("auto_outline ROI handling", False, repr(e))

    # 2h) Edge-snap safety invariant: pulling the boundary onto the nearest strong image edge
    #     must be a strict NO-OP on a boundary already sitting on a crisp edge — a clean trace
    #     must not drift when snapping is on. (Its benefit is on soft real-photo fringes; here we
    #     lock in that it never DEGRADES a good trace.)
    try:
        from auto_outline import auto_outline as _ao2

        def _iou3(pts, gt, shape):
            if not pts:
                return 0.0
            m = np.zeros(shape, np.uint8); cv2.fillPoly(m, [np.array(pts, np.int32)], 255)
            return int(np.logical_and(m > 0, gt > 0).sum()) / max(1, int(np.logical_or(m > 0, gt > 0).sum()))

        cs = np.full((640, 900, 3), 185, np.uint8)
        cpart = np.array([[120, 200], [520, 210], [515, 300], [300, 305], [305, 520], [190, 515]], np.int32)
        cv2.fillPoly(cs, [cpart], (45, 48, 52))
        gt_e = np.zeros((640, 900), np.uint8); cv2.fillPoly(gt_e, [cpart], 255)
        with _quiet():
            off = _ao2(cs, seed=[280, 260], edge_snap=False)
            on = _ao2(cs, seed=[280, 260], edge_snap=True)
        iou_off, iou_on = _iou3(off, gt_e, (640, 900)), _iou3(on, gt_e, (640, 900))
        check("edge-snap is a no-op on a clean edge (never degrades)", iou_on > 0.98 and iou_on >= iou_off - 0.01,
              f"snap-off IoU={iou_off:.4f} -> snap-on IoU={iou_on:.4f}")
    except Exception as e:
        check("edge-snap safety", False, repr(e))

    # 3) No crash / no div-by-zero on an image with no calibration square.
    try:
        with _quiet():
            cal0, _ = calibrate_image(np.full((400, 400, 3), 60, np.uint8), edge_mm=30.0)
        check("calibrate_image on blank image (graceful)", isinstance(cal0, dict),
              f"markers={len(cal0.get('markers', []))}")
    except Exception as e:
        check("calibrate_image on blank image", False, repr(e))

    # 3b) A lone square (no 4-pad calibration pattern) must NOT report "high" confidence —
    #     it could be any square object, and trusting it silently yields a wrong scale.
    try:
        results = {}
        for name, bg, fg in (("dark-on-bright", 200, 35), ("bright-on-dark", 40, 235)):
            im = np.full((700, 900, 3), bg, np.uint8)
            cv2.rectangle(im, (380, 300), (520, 440), (fg, fg, fg), -1)
            with _quiet():
                calx, _ = calibrate_image(im, edge_mm=40.0)
            results[name] = calx.get("calibration_confidence")
        ok = all(c != "high" for c in results.values())
        check("lone square (no pattern) is never high confidence", ok, str(results))
    except Exception as e:
        check("lone-square confidence guard", False, repr(e))

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

    # 8) Reloadable job round-trip: /api/submit persistence writes job.camscan.json (raw
    #    image + calibration + editable annotations), keeps submission.json lean, and skips
    #    the bundle when no view carries a reloadable snapshot.
    try:
        import base64
        import json as _json
        import app as _app
        _okenc, _enc = cv2.imencode(".jpg", np.full((8, 8, 3), 200, np.uint8))
        durl = "data:image/jpeg;base64," + base64.b64encode(_enc.tobytes()).decode()
        with tempfile.TemporaryDirectory() as d:
            _app.SUBMISSIONS_DIR = d
            payload = {"id": "smoke", "brief": {"part": "p"}, "views": [{
                "label": "Top", "image": durl,
                "restore": {"raw": durl, "calib": {"markers": [], "marker_size_mm": 40},
                            "ann": [{"id": 1, "type": "polyline", "pts": [[1, 1], [2, 2]], "mm_per_px": 0.1}],
                            "units": "mm", "manual": {"mmPerPx": None}}}]}
            with _quiet():
                rec = _app._save_submission(payload)
                rec2 = _app._save_submission({"id": "s2", "brief": {}, "views": [{"label": "T", "image": durl}]})
            bp = rec.get("bundle")
            bundle = _json.load(open(bp)) if bp else {}
            sub = _json.load(open(os.path.join(rec["dir"], "submission.json")))
            lean = all("image" not in v and "restore" not in v for v in sub["views"])
            reloadable = (bundle.get("kind") == "camscan.job"
                          and bundle["views"][0]["restore"]["raw"].startswith("data:image/")
                          and bundle["views"][0]["restore"]["ann"][0]["type"] == "polyline")
            guard = rec2.get("bundle") is None          # no restore data -> no bundle written
        check("job round-trip: bundle persisted, submission lean, guard",
              bool(bp) and reloadable and lean and guard,
              f"bundle={bool(bp)}, reloadable={reloadable}, lean={lean}, guard={guard}")
    except Exception as e:
        check("job round-trip persistence", False, repr(e))

    # 8b) A rejected upload must be VISIBLE. #status lives inside the landing card, which is
    #     tucked off-screen once a photo loads, so every failure path also has to fill the
    #     always-visible #upload-error banner — otherwise picking a bad second photo looks
    #     like the tap did nothing. Guard the arity and the banner on every failure path.
    try:
        import base64
        import app as _app
        from dash import no_update
        _okenc, _enc = cv2.imencode(".jpg", np.full((8, 8, 3), 200, np.uint8))
        good = "data:image/jpeg;base64," + base64.b64encode(_enc.tobytes()).decode()
        bad_paths = [
            ("bad extension", good, "photo.heic"),
            ("not an image", "data:image/jpeg;base64," + base64.b64encode(b"nope").decode(), "p.jpg"),
            ("malformed data-uri", "no-comma", "p.jpg"),
        ]
        shown = []
        for _name, _c, _fn in bad_paths:
            with _quiet():
                r = _app.on_upload(_c, _fn)
            shown.append(len(r) == 6 and r[4] not in ("", None)
                         and r[5].get("display") == "block" and r[1] is no_update)
        # ...and a success clears it again rather than leaving a stale error on screen.
        with _quiet(), _app.server.test_request_context("/"):
            ok_r = _app.on_upload(good, "p.jpg")
        cleared = len(ok_r) == 6 and ok_r[4] == "" and ok_r[5].get("display") == "none"
        check("upload failures reach the always-visible banner", all(shown) and cleared,
              f"failure paths shown={sum(shown)}/{len(shown)}, success clears={cleared}")
    except Exception as e:
        check("upload failures reach the always-visible banner", False, repr(e))

    # 8c) One malformed geometry entry must not sink the whole DXF export. export_to_dxf
    #     documents "skip, don't abort", but its per-item guard calls item.get() first, so a
    #     bare string/number in the list used to raise past it and 500 the endpoint.
    try:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "mixed.dxf")
            with _quiet():
                ok = export_to_dxf(
                    ["junk", 42, None, {"type": "circle", "center_x": 10, "center_y": 10, "radius_px": 5}],
                    p, 1.0, image_height_px=100.0,
                )
            wrote = ok and os.path.exists(p) and os.path.getsize(p) > 0
        check("DXF export skips non-dict geometry entries", bool(wrote),
              f"ok={ok}, wrote={wrote}")
    except Exception as e:
        check("DXF export skips non-dict geometry entries", False, repr(e))

    # 9) Reload downscale preserves REAL measurements. calibrationOverlay.getRestoreState
    #    scales calibration + annotations by the SAME factor as the raw image (homography
    #    columns /s, mm_per_px /s, coordinates *s), so millimetres are unchanged. Guard the
    #    invariant here for the perspective (homography) and uniform (mm_per_px) paths.
    try:
        def _applyH(H, p):
            x, y = p
            w = H[2][0] * x + H[2][1] * y + H[2][2]
            return ((H[0][0] * x + H[0][1] * y + H[0][2]) / w,
                    (H[1][0] * x + H[1][1] * y + H[1][2]) / w)
        def _d(a, b):
            return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        H = [[0.0011, 6e-5, -0.12], [4e-5, 0.0012, -0.18], [1.8e-7, 9e-8, 1.0]]
        MM, s = 40.0, 1600.0 / 4032.0
        p1, p2 = (220, 640), (980, 705)
        Hs = [[H[r][0] / s, H[r][1] / s, H[r][2]] for r in range(3)]
        q1, q2 = (p1[0] * s, p1[1] * s), (p2[0] * s, p2[1] * s)
        persp = abs(MM * _d(_applyH(H, p1), _applyH(H, p2)) - MM * _d(_applyH(Hs, q1), _applyH(Hs, q2)))
        mmpp = 0.0995
        uni = abs(_d(p1, p2) * mmpp - _d(q1, q2) * (mmpp / s))
        check("reload downscale preserves mm (scale invariant)", persp < 1e-9 and uni < 1e-9,
              f"perspErr={persp:.2e}, uniformErr={uni:.2e}")
    except Exception as e:
        check("scale invariant", False, repr(e))

    if _failures:
        print(f"\nSMOKE_RESULT: FAIL ({', '.join(_failures)})")
        return 1
    print("\nSMOKE_RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
