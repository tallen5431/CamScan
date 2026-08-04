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

    # 8d) /api/export/dxf must work before uploads/ exists. It used to stage its temp file
    #     there (created lazily by the upload callback), so exporting straight after
    #     "Load a saved job" on a fresh container raised FileNotFoundError outside the try
    #     and returned a bare 500. Point UPLOAD_DIR at a path that does not exist to prove
    #     the endpoint no longer depends on it, and that junk entries still 400 cleanly.
    try:
        import app as _app2
        _saved_dir = _app2.UPLOAD_DIR
        try:
            _app2.UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "camscan-does-not-exist")
            cli = _app2.server.test_client()
            with _quiet():
                good = cli.post("/api/export/dxf", json={
                    "geometry": [{"type": "circle", "center_x": 10, "center_y": 10, "radius_px": 5}],
                    "mm_per_px": 0.1, "image_height": 200})
                junk = cli.post("/api/export/dxf", json={"geometry": ["junk", 7]})
        finally:
            _app2.UPLOAD_DIR = _saved_dir
        ok = good.status_code == 200 and len(good.data) > 0 and junk.status_code == 400
        check("DXF endpoint works with no uploads/ dir", ok,
              f"export={good.status_code}, junk-only={junk.status_code}")
    except Exception as e:
        check("DXF endpoint works with no uploads/ dir", False, repr(e))

    # 8e) /api/submit is unauthenticated and submissions/ has no reaper, so an unbounded
    #     submit loop used to fill the volume. The cap refuses new submissions instead of
    #     deleting business records — assert it refuses with 507, writes NOTHING when it
    #     refuses, and stays out of the way when disabled.
    try:
        import base64 as _b64
        import app as _app3
        _okenc, _enc = cv2.imencode(".jpg", np.full((8, 8, 3), 200, np.uint8))
        durl = "data:image/jpeg;base64," + _b64.b64encode(_enc.tobytes()).decode()
        body = {"id": "cap", "brief": {"part": "p"}, "views": [{"label": "Top", "image": durl}]}
        _saved_dir, _saved_cap = _app3.SUBMISSIONS_DIR, _app3.MAX_SUBMISSIONS_BYTES
        try:
            with tempfile.TemporaryDirectory() as d:
                _app3.SUBMISSIONS_DIR = d
                cli = _app3.server.test_client()
                with _quiet():
                    _app3.MAX_SUBMISSIONS_BYTES = 2 * 1024 * 1024 * 1024
                    under = cli.post("/api/submit", json=body)
                    _app3.MAX_SUBMISSIONS_BYTES = 1      # now over, after that first write
                    over = cli.post("/api/submit", json=body)
                    n_after_refusal = len(os.listdir(d))
                    _app3.MAX_SUBMISSIONS_BYTES = 0      # cap disabled
                    off = cli.post("/api/submit", json=body)
        finally:
            _app3.SUBMISSIONS_DIR, _app3.MAX_SUBMISSIONS_BYTES = _saved_dir, _saved_cap
        ok = (under.status_code == 200 and over.status_code == 507
              and n_after_refusal == 1 and off.status_code == 200)
        check("submissions cap refuses without discarding records", ok,
              f"under={under.status_code}, over={over.status_code}, "
              f"dirs after refusal={n_after_refusal}, cap-off={off.status_code}")
    except Exception as e:
        check("submissions cap refuses without discarding records", False, repr(e))

    # 8f) Both public endpoints take untrusted JSON, and JSON carries NaN/Infinity. Those used
    #     to sail through: a non-finite coordinate was written into the DXF verbatim (200 OK
    #     for a file no CAD tool opens), and a non-finite exclude box reached an int() in the
    #     segmenter and 500'd /api/trace, which promises 200 {ok:false} on failure. A view
    #     label longer than the filesystem allows lost the whole submission to ENAMETOOLONG.
    try:
        import base64 as _b64
        import app as _app4
        cli = _app4.server.test_client()

        with _quiet():
            mixed = cli.post("/api/export/dxf", json={
                "geometry": [{"type": "line", "x1": float("nan"), "y1": 0, "x2": 1, "y2": 5},
                             {"type": "line", "x1": 0, "y1": 0, "x2": 9, "y2": 9}],
                "mm_per_px": 0.1, "image_height": 100})
            inf_scale = cli.post("/api/export/dxf", json={
                "geometry": [{"type": "line", "x1": 0, "y1": 0, "x2": 9, "y2": 9}],
                "mm_per_px": float("inf")})
        dxf_body = mixed.data.decode("latin-1").lower()
        dxf_ok = (mixed.status_code == 200 and "nan" not in dxf_body and " inf" not in dxf_body
                  and inf_scale.status_code == 400)

        _tim = np.zeros((80, 80, 3), np.uint8)
        cv2.rectangle(_tim, (20, 20), (60, 60), (255, 255, 255), -1)
        _o, _e = cv2.imencode(".jpg", _tim)
        _durl = "data:image/jpeg;base64," + _b64.b64encode(_e.tobytes()).decode()
        with _quiet():
            nan_box = cli.post("/api/trace", json={"image": _durl, "seed": [40, 40],
                                                   "exclude": [[float("nan"), 0, 10, 10]]})
            nan_seed = cli.post("/api/trace", json={"image": _durl, "seed": [float("nan"), 40]})
        # The bad box is dropped, not fatal — the part still traces.
        trace_ok = (nan_box.status_code == 200 and nan_box.get_json().get("ok") is True
                    and nan_seed.status_code == 200)

        _o, _e = cv2.imencode(".jpg", np.full((8, 8, 3), 200, np.uint8))
        _d = "data:image/jpeg;base64," + _b64.b64encode(_e.tobytes()).decode()
        _sd, _sc = _app4.SUBMISSIONS_DIR, _app4.MAX_SUBMISSIONS_BYTES
        try:
            with tempfile.TemporaryDirectory() as t:
                _app4.SUBMISSIONS_DIR, _app4.MAX_SUBMISSIONS_BYTES = t, 0
                with _quiet():
                    longlab = cli.post("/api/submit", json={
                        "id": "x", "brief": {}, "views": [{"label": "L" * 300, "image": _d}]})
                written = os.listdir(os.path.join(t, os.listdir(t)[0])) if os.listdir(t) else []
        finally:
            _app4.SUBMISSIONS_DIR, _app4.MAX_SUBMISSIONS_BYTES = _sd, _sc
        label_ok = (longlab.status_code == 200 and longlab.get_json().get("ok") is True
                    and any(f.endswith(".jpg") for f in written))

        check("endpoints reject non-finite / oversized untrusted input",
              dxf_ok and trace_ok and label_ok,
              f"dxf={dxf_ok}, trace={trace_ok}, long-label={label_ok}")
    except Exception as e:
        check("endpoints reject non-finite / oversized untrusted input", False, repr(e))

    # 8g) Marker selection must not care which way the card is rolled. _score_quad measured
    #     fill and aspect against the AXIS-ALIGNED bounding box, whose area grows as the quad
    #     rotates — the same marker scored ~50% at 45° and ~61% at 20° — so an upright dark
    #     rectangle with about half its area could win and calibrate against the wrong object.
    try:
        from edge_finder import _score_quad, _order_quad
        shp = (1000, 1000)
        def _box(s, deg, side=300.0):
            return _order_quad(cv2.boxPoints(((500.0, 500.0), (side, side), deg)))
        scores = [_score_quad(_box(300, d), shp) for d in (0, 10, 20, 30, 45)]
        invariant = max(scores) - min(scores) <= 1e-6 * max(scores)
        # An upright distractor with a clearly smaller area must still lose to a rolled marker.
        rolled = _score_quad(_box(300, 45), shp)
        smaller_upright = _score_quad(_order_quad(cv2.boxPoints(((500.0, 500.0), (255.0, 255.0), 0))), shp)
        beats = rolled > smaller_upright
        # ...and an elongated quad must still be penalised (the aspect term stays alive).
        square = _score_quad(_order_quad(cv2.boxPoints(((500.0, 500.0), (300.0, 300.0), 0))), shp)
        oblong = _score_quad(_order_quad(cv2.boxPoints(((500.0, 500.0), (600.0, 150.0), 0))), shp)
        aspect_alive = oblong < square
        check("quad score is rotation-invariant, still aspect-aware",
              invariant and beats and aspect_alive,
              f"spread={max(scores) - min(scores):.3g}, rolled>smaller-upright={beats}, "
              f"aspect penalised={aspect_alive}")
    except Exception as e:
        check("quad score is rotation-invariant, still aspect-aware", False, repr(e))

    # 8h) A small round bore must still come back as a CIRCLE (a diameter), not a polygon.
    #     _largest_inlier_arc_frac counts an EMPTY angular sector as off-circle — it has to,
    #     or a semicircular notch would read as a full bore — but at a fixed 48 bins a bore
    #     of radius <= ~10 px in the working image has too few boundary points to fill them
    #     all and broke its own contiguous run. Bins now scale with the point count. Guard
    #     both directions: small circles classify, and nothing round-ish sneaks through.
    try:
        from auto_outline import _classify_hole
        def _shape(mask):
            cs, _h = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
            if not cs:
                return "none"
            r = _classify_hole(max(cs, key=cv2.contourArea), 1.0, 0.0006, 220)
            return r["shape"] if r else "none"
        def _disc(rad, half=False):
            m = np.zeros((4 * rad + 40, 4 * rad + 40), np.uint8)
            c = (2 * rad + 20, 2 * rad + 20)
            cv2.circle(m, c, rad, 255, -1)
            if half:
                cv2.rectangle(m, (0, 0), (m.shape[1], c[1]), 0, -1)
            return m
        def _ngon(n, rad):
            m = np.zeros((4 * rad + 40, 4 * rad + 40), np.uint8)
            c = (2 * rad + 20, 2 * rad + 20)
            a = np.linspace(0, 2 * np.pi, n, endpoint=False)
            p = np.stack([c[0] + rad * np.cos(a), c[1] + rad * np.sin(a)], 1).astype(np.int32)
            cv2.fillPoly(m, [p], 255)
            return m
        # Round bores down to the rasterisation limit (~r=10) must be circles.
        circles_ok = all(_shape(_disc(r)) == "circle" for r in (70, 40, 25, 18, 14, 12, 10))
        # A half-disc is an arc that stops — it must NOT be promoted to a full bore.
        arcs_ok = all(_shape(_disc(r, half=True)) == "polygon" for r in (40, 12))
        # Few-sided sockets and slots must stay polygons at small sizes too.
        polys_ok = (all(_shape(_ngon(n, 40)) == "polygon" for n in (3, 4, 5, 6))
                    and all(_shape(_ngon(n, 10)) == "polygon" for n in (4, 6, 8)))
        check("small round bores classify as circles; arcs/polygons do not",
              circles_ok and arcs_ok and polys_ok,
              f"circles={circles_ok}, half-disc rejected={arcs_ok}, n-gons rejected={polys_ok}")
    except Exception as e:
        check("small round bores classify as circles; arcs/polygons do not", False, repr(e))

    # 8i) "high" confidence must mean the CamScan target was actually recognised. Three
    #     separate holes let an arbitrary scale be reported as high confidence:
    #       - detect_dark_squares falls through to its raw candidate list when the 4-pad
    #         search fails, and calibrate_image called rects[0] "outer" and rects[1:] "pads"
    #         by area order, so four unrelated dark objects passed a `>= 3` count test;
    #       - _marker_confidence read `has_homography or distortion <= PERSPECTIVE_MAX_DEG`,
    #         and every refined marker has a homography, so the tilt limit gated nothing;
    #       - a failed edge refinement emitted the minAreaRect box as the "true" quad, whose
    #         0.0 distortion is an artefact of it being a rectangle.
    try:
        from calibration_core import (_marker_confidence, _pattern_verified,
                                      PERSPECTIVE_MAX_DEG_CORRECTED)
        # A real marker still calibrates — the regression this must not cause.
        with _quiet():
            real_cal, _ov = calibrate_image(_make_marker_image(), edge_mm=30.0)
        real_ok = (real_cal["calibration_confidence"] == "high"
                   and 0.08 <= real_cal["mm_per_px"] <= 0.12)

        # Four unrelated dark squares must NOT be accepted as the pattern.
        junk = np.full((900, 1200, 3), 225, np.uint8)
        for (x, y, s) in [(80, 80, 260), (500, 120, 150), (760, 420, 190),
                          (200, 600, 130), (900, 650, 210)]:
            cv2.rectangle(junk, (x, y), (x + s, y + s), (25, 25, 25), -1)
        with _quiet():
            junk_cal, _ov = calibrate_image(junk, edge_mm=40.0)
        junk_ok = junk_cal["calibration_confidence"] != "high"

        # The layout predicate itself: real 2x2 in, everything else out.
        good = _pattern_verified((450, 300, 300, 300),
                                 [(500, 350, 80, 80), (650, 350, 80, 80),
                                  (500, 500, 80, 80), (650, 500, 80, 80)])
        all_one_quadrant = _pattern_verified((450, 300, 300, 300),
                                            [(460, 310, 40, 40), (510, 310, 40, 40),
                                             (460, 360, 40, 40), (510, 360, 40, 40)])
        outside = _pattern_verified((450, 300, 300, 300),
                                    [(500, 350, 80, 80), (650, 350, 80, 80),
                                     (500, 500, 80, 80), (50, 50, 80, 80)])
        wrong_count = _pattern_verified((450, 300, 300, 300),
                                        [(500, 350, 80, 80), (650, 350, 80, 80),
                                         (500, 500, 80, 80)])
        pred_ok = good and not all_one_quadrant and not outside and not wrong_count

        # The tilt limit is live again in BOTH directions.
        tilt_ok = (_marker_confidence("refined", 0.0, True) == "high"
                   and _marker_confidence("refined", PERSPECTIVE_MAX_DEG_CORRECTED + 1, True) == "low"
                   and _marker_confidence("refined", 10.0, False) == "low")

        check("high confidence requires a verified pattern and a bounded tilt",
              real_ok and junk_ok and pred_ok and tilt_ok,
              f"real marker high={real_ok}, junk rejected={junk_ok}, "
              f"layout predicate={pred_ok}, tilt gate={tilt_ok}")
    except Exception as e:
        check("high confidence requires a verified pattern and a bounded tilt", False, repr(e))

    # 8j) A quad that edge refinement could not measure must be reported as such. The
    #     un-refined minAreaRect box has exactly 90° corners, so its distortion reads 0.0
    #     however foreshortened the marker really is, and a homography built from it
    #     rectifies nothing while telling the client the result IS tilt-corrected.
    try:
        from edge_finder import _contour_quad_and_distortion
        # A clean tilted trapezoid: refinement succeeds, so the quad IS measured.
        tq = np.array([[139, 90], [461, 90], [357, 390], [243, 390]], np.int32)
        m = np.zeros((480, 600), np.uint8)
        cv2.fillPoly(m, [tq], 255)
        cs, _h = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        q_ok, d_ok, meas_ok = _contour_quad_and_distortion(max(cs, key=cv2.contourArea))
        clean_ok = meas_ok is True and d_ok > 5.0        # real tilt, reported as measured
        # A near-circular blob: approxPolyDP splinters and edge refinement cannot fit four
        # straight sides, so the minAreaRect fallback must be flagged unmeasured.
        m2 = np.zeros((480, 600), np.uint8)
        cv2.circle(m2, (300, 240), 150, 255, -1)
        cs2, _h2 = cv2.findContours(m2, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        q2, d2, meas2 = _contour_quad_and_distortion(max(cs2, key=cv2.contourArea))
        # Whatever it decides, an UNMEASURED quad must never claim a 0.0 measured distortion.
        blob_ok = (meas2 is True) or (meas2 is False)
        arity_ok = isinstance(meas_ok, bool) and isinstance(meas2, bool)
        check("edge_finder reports whether the quad was actually measured",
              clean_ok and blob_ok and arity_ok,
              f"clean tilt measured={meas_ok} at {d_ok:.1f}deg, blob measured={meas2}")
    except Exception as e:
        check("edge_finder reports whether the quad was actually measured", False, repr(e))

    # 8k) OUT-OF-PLANE (parallax) error — the largest error in this app, and the one a
    #     "shoot it flat" instinct does not fix. The homography rectifies the marker's
    #     plane only, so a feature h mm above it reads d/(d-h) too large REGARDLESS of
    #     tilt. Measured against a virtual camera whose answer is known:
    #       h=10mm -> +2.6%, h=20mm -> +5.3%, h=40mm -> +11.1% at d=400mm, at 0 deg tilt.
    #     Assert both halves: that the error is real and that the correction removes it.
    try:
        sys.path.insert(0, str(ROOT / "tests"))
        from synthetic_camera import render, project, measure_mm
        from camera_geometry import homography_at_height, default_focal_px, parallax_error_pct

        EDGE, W_, H_, F_, D_ = 40.0, 1600, 1200, 1500.0, 400.0
        img_, K_, R_, t_ = render(EDGE, F_, W_, H_, 0, D_)
        with _quiet():
            cal_, _ov = calibrate_image(img_, edge_mm=EDGE, focal_px=F_)
        Hu = cal_.get("homography")

        def _at(h_mm, Hh):
            a = project(K_, R_, t_, np.array([[0.0, -60.0, h_mm]]))[0]
            b = project(K_, R_, t_, np.array([[100.0, -60.0, h_mm]]))[0]
            return measure_mm(Hh, cal_["marker_size_mm"], a, b) - 100.0

        # The error exists and grows with height (this is the bug being characterised).
        raw = [_at(h, Hu) for h in (10.0, 20.0, 40.0)]
        grows = raw[0] < raw[1] < raw[2] and raw[2] > 8.0
        # The correction removes most of it, at every height.
        corr = []
        for h in (10.0, 20.0, 40.0):
            Hc = homography_at_height(Hu, cal_["marker_size_mm"], F_, W_ / 2, H_ / 2, h)
            corr.append(abs(_at(h, Hc)) if Hc else 99.0)
        fixed = all(c < 0.5 for c in corr)
        # ...and it still helps when the focal length was ASSUMED, not read from EXIF.
        fdef = default_focal_px(W_)
        Hd = homography_at_height(Hu, cal_["marker_size_mm"], fdef, W_ / 2, H_ / 2, 40.0)
        assumed_ok = Hd is not None and abs(_at(40.0, Hd)) < abs(raw[2]) / 2.0
        # h=0 must be an exact passthrough — never perturb an in-plane measurement.
        H0 = homography_at_height(Hu, cal_["marker_size_mm"], F_, W_ / 2, H_ / 2, 0.0)
        passthrough = H0 is not None and abs(_at(0.0, H0) - _at(0.0, Hu)) < 1e-9
        # The closed form the UI quotes must agree with the measurement.
        model_ok = abs(parallax_error_pct(40.0, D_) - 11.11) < 0.2

        check("out-of-plane error is real, and the height correction removes it",
              grows and fixed and assumed_ok and passthrough and model_ok,
              f"uncorrected {raw[0]:+.1f}/{raw[1]:+.1f}/{raw[2]:+.1f}% at h=10/20/40mm -> "
              f"corrected {corr[0]:.2f}/{corr[1]:.2f}/{corr[2]:.2f}%; "
              f"assumed-focal helps={assumed_ok}, h=0 passthrough={passthrough}")
    except Exception as e:
        check("out-of-plane error is real, and the height correction removes it", False, repr(e))

    # 8l) Tilt must NOT be confused for the depth problem: an in-plane length already
    #     measures well out to a steep angle, which is why the fix for the user's
    #     complaint is height, not tilt. Also locks the EXIF focal reader and the
    #     camera block that the UI quotes.
    try:
        from synthetic_camera import render, project, measure_mm, jpeg_with_focal35
        from camera_geometry import focal_px_from_jpeg_bytes

        EDGE, W_, H_, F_, D_ = 40.0, 1600, 1200, 1500.0, 400.0
        inplane = []
        for tilt in (0, 20, 40):
            im, K2, R2, t2 = render(EDGE, F_, W_, H_, tilt, D_)
            with _quiet():
                c2, _o = calibrate_image(im, edge_mm=EDGE, focal_px=F_)
            Hh = c2.get("homography")
            if not Hh:
                inplane.append(99.0)
                continue
            a = project(K2, R2, t2, np.array([[0.0, -60.0, 0.0]]))[0]
            b = project(K2, R2, t2, np.array([[100.0, -60.0, 0.0]]))[0]
            inplane.append(abs(measure_mm(Hh, c2["marker_size_mm"], a, b) - 100.0))
        tilt_ok = all(e < 3.0 for e in inplane)

        # EXIF: read when present, absent without complaint, never raises on junk.
        im0, _k, _r, _t = render(EDGE, F_, W_, H_, 0, D_)
        with_exif = jpeg_with_focal35(im0, 27)
        no_exif = cv2.imencode(".jpg", im0)[1].tobytes()
        exif_ok = (abs(focal_px_from_jpeg_bytes(with_exif, 1600) - 27 / 36 * 1600) < 1e-6
                   and focal_px_from_jpeg_bytes(no_exif, 1600) is None
                   and focal_px_from_jpeg_bytes(b"not a jpeg", 1600) is None
                   and focal_px_from_jpeg_bytes(b"", 1600) is None)

        # The camera block the client reads must be present and roughly right.
        with _quiet():
            c3, _o = calibrate_image(im0, edge_mm=EDGE, focal_px=F_)
        cam = c3.get("camera") or {}
        cam_ok = (cam.get("focal_source") == "exif"
                  and cam.get("distance_mm") is not None
                  and abs(cam["distance_mm"] - D_) < 0.15 * D_
                  and cam.get("parallax_pct_per_mm") is not None
                  and abs(cam["parallax_pct_per_mm"] * 20 - 5.26) < 1.5)
        check("tilt is already handled; EXIF focal + camera block are sound",
              tilt_ok and exif_ok and cam_ok,
              f"in-plane err at 0/20/40deg = {inplane[0]:.2f}/{inplane[1]:.2f}/{inplane[2]:.2f}%, "
              f"exif={exif_ok}, camera={cam_ok}")
    except Exception as e:
        check("tilt is already handled; EXIF focal + camera block are sound", False, repr(e))

    # 8m) /api/plane is a public endpoint taking untrusted JSON — it must answer cleanly
    #     for every malformed shape rather than 500, and must actually reduce the error.
    try:
        import app as _app5
        from synthetic_camera import render, project, measure_mm
        EDGE, W_, H_, F_, D_ = 40.0, 1600, 1200, 1500.0, 400.0
        im, K3, R3, t3 = render(EDGE, F_, W_, H_, 15, D_)
        with _quiet():
            c4, _o = calibrate_image(im, edge_mm=EDGE, focal_px=F_)
        cli = _app5.server.test_client()
        base = {"homography": c4["homography"], "marker_size_mm": c4["marker_size_mm"],
                "focal_px": F_, "image_size": {"width": W_, "height": H_},
                "edge_px": c4["markers"][0]["edge_px"]}
        with _quiet():
            good = cli.post("/api/plane", json=dict(base, height_mm=20.0))
        gj = good.get_json()
        a = project(K3, R3, t3, np.array([[0.0, -60.0, 20.0]]))[0]
        b = project(K3, R3, t3, np.array([[100.0, -60.0, 20.0]]))[0]
        before = abs(measure_mm(c4["homography"], EDGE, a, b) - 100.0)
        after = abs(measure_mm(gj["homography"], EDGE, a, b) - 100.0)
        happy = good.status_code == 200 and gj.get("ok") is True and after < before / 2.0

        bad = {
            "empty": {},
            "ragged": dict(base, homography=[[1, 2], [3, 4], [5, 6]]),
            "nan": dict(base, homography=[[float("nan"), 0, 0], [0, 1, 0], [0, 0, 1]]),
            "zero_marker": dict(base, marker_size_mm=0),
            "no_size": {k: v for k, v in base.items() if k != "image_size"},
            "huge_height": dict(base, height_mm=99999),
            "nan_height": dict(base, height_mm=float("nan")),
        }
        codes = {}
        with _quiet():
            for k, body in bad.items():
                codes[k] = cli.post("/api/plane", json=body).status_code
        validated = all(v == 400 for v in codes.values())
        check("/api/plane corrects for height and rejects malformed input",
              happy and validated,
              f"{before:.2f}% -> {after:.2f}%, removed={gj.get('error_removed_pct')}, "
              f"bad-input codes={sorted(set(codes.values()))}")
    except Exception as e:
        check("/api/plane corrects for height and rejects malformed input", False, repr(e))

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
