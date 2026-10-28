"""Automatic part-outline extraction for CamScan's Outline tool.

Given a photo and (ideally) a seed point on the part, segment that object from the
background and return its outer boundary as a simplified polygon in image pixel
coordinates. The client turns that into an editable, closed outline — so a customer
gets a near-instant trace to nudge instead of clicking every point by hand.

Deterministic and fast: a border-ring background colour model (robust to a central
object) + Otsu on the colour-distance map + connected components. The seed disambiguates
which object to trace, which matters because the calibration card is itself "foreground"
against the surface — a seed on the part ignores the card.

The boundary is reduced with a corner-preserving, curvature-adaptive simplifier (not a
single global approxPolyDP epsilon): true corners are detected and kept crisp, the arcs
between them are smoothed to remove the pixel staircase, and vertices are spent where
curvature is high and saved on straight runs — so curves read smooth, sharp corners
aren't chorded across, and the result stays light enough to edit by hand.
"""
from typing import List, Optional, Sequence, Tuple
import math

import cv2
import numpy as np


# Defaults shared by auto_outline / auto_outline_full. `simplify` is much finer, and the
# working resolution higher, than the first cut — a round feature on a long part (a wrench's
# box end) needs the extra samples to read as a smooth curve rather than a coarse polygon.
# On a wrench this yields a smooth head (~27 pts) and captures the box-end hole; straight
# runs still collapse and corners are still preserved, so it stays editable.
_DEFAULT_SIMPLIFY = 0.0006
_DEFAULT_MAX_POINTS = 220   # ceiling only — the detail slider decides how many are actually used;
                            # a higher cap lets the finest setting capture more nuance on a complex
                            # outline (now editable thanks to the constant-size vertex handles).
_DEFAULT_DOWNSCALE = 1300


def auto_outline(
    img_bgr: np.ndarray,
    seed: Optional[Sequence[float]] = None,
    exclude_boxes: Sequence[Tuple[float, float, float, float]] = (),
    simplify: float = _DEFAULT_SIMPLIFY,
    max_points: int = _DEFAULT_MAX_POINTS,
    downscale_to: int = _DEFAULT_DOWNSCALE,
    roi: Optional[Sequence[float]] = None,
    edge_snap: bool = True,
) -> Optional[List[List[float]]]:
    """Return the part's OUTER boundary as [[x, y], ...] in IMAGE pixel coords, or None.
    (For interior holes too — what a printable replica needs — use auto_outline_full.)

    seed          : (x, y) on the part (image coords). Strongly recommended — without it
                    the largest non-excluded foreground blob is used, which may be the card.
    exclude_boxes : (x, y, w, h) regions to blank out (e.g. the calibration marker).
    simplify      : detail level — the simplification tolerance as a fraction of the
                    contour perimeter. Smaller = more vertices / smoother curves. Corners
                    are preserved regardless, so lowering this densifies curves, not corners.
    max_points    : cap on returned vertices (low-curvature points are dropped first, corners
                    last) — keeps the outline editable by hand.
    roi           : (x, y, w, h) region-of-interest in image coords (the user's Area box).
                    Segmentation is restricted to this region PLUS a background collar, which
                    (a) gives a clean, LOCAL background estimate, (b) drops distant clutter and
                    large cast shadows outside the box, and (c) stops a nearby object from being
                    welded on. Coordinates are still returned in full-image space.
    edge_snap     : pull the boundary onto the nearest strong image edge (a bounded, safe polish
                    of a soft-fringe boundary; a no-op on a boundary already on a crisp edge).
    """
    crop, seed2, excl2, ox, oy = _crop_to_roi(img_bgr, seed, exclude_boxes, roi)
    seg = _segment(crop, seed2, excl2, downscale_to, box=_box_in_crop(roi, ox, oy, crop, img_bgr))
    if seg is None:
        return None
    mask, scale, sh, sw, grad = seg
    poly = _outer_polygon(mask, scale, sh, sw, simplify, max_points, grad, edge_snap)
    if poly is None:
        return None
    return [[x + ox, y + oy] for (x, y) in poly] if (ox or oy) else poly


def auto_outline_full(
    img_bgr: np.ndarray,
    seed: Optional[Sequence[float]] = None,
    exclude_boxes: Sequence[Tuple[float, float, float, float]] = (),
    simplify: float = _DEFAULT_SIMPLIFY,
    max_points: int = _DEFAULT_MAX_POINTS,
    downscale_to: int = _DEFAULT_DOWNSCALE,
    want_holes: bool = True,
    min_hole_frac: float = 0.004,
    roi: Optional[Sequence[float]] = None,
    edge_snap: bool = True,
    fg_points: Optional[Sequence[Sequence[float]]] = None,
    bg_points: Optional[Sequence[Sequence[float]]] = None,
) -> Optional[dict]:
    """Segment the tapped part and return both its outer boundary AND its interior holes:

        {"outer": [[x, y], ...],
         "holes": [ {"shape": "circle",  "cx": .., "cy": .., "r": ..},      # a round bore
                    {"shape": "polygon", "points": [[x, y], ...]}, ... ]}   (image pixel coords)

    Holes are enclosed background inside the part — a box-end ring, bolt holes — which is what
    turns a flat outline into a printable replica; a round bore comes back as a true circle.
    A wrench's OPEN jaw is not a hole (it opens to the exterior) so it stays part of `outer`.
    If nothing could be segmented, "outer" is None and "hint" says what to try next.

    Also, when they apply: "hint" (the trace looks like the background, not a part — the
    caller should say so rather than report success), "warnings" (words for the user: the
    first trace took the background and the tapped region inside it was used instead; a long
    side steps out where a shadow lies beside it) and "jogs" (those steps, with where they
    are: {"side", "end", "px", "at": [x, y]}).

    fg_points / bg_points : taps saying "this IS the part" and "this is NOT". Supplying any
          background point switches segmentation to GrabCut, which models both sides as
          colour distributions instead of drawing one threshold. This is the only thing that
          separates a part from something the SAME COLOUR beside it — above all a dark part
          lying next to its own shadow, where the two differ by ~15 grey levels and the
          colour-distance split traces the shadow instead of the part.

    roi : (x, y, w, h) region-of-interest (the user's Area box) — see auto_outline. Restricts
          segmentation to that region plus a background collar; results stay in image coords.
    """
    crop, seed2, excl2, ox, oy = _crop_to_roi(img_bgr, seed, exclude_boxes, roi)
    # Hints are in full-image coords like everything else the caller passes; shift them into
    # the crop's frame so they still land on what the user pointed at.
    def _shift(pts):
        out = []
        for p in (pts or ()):
            try:
                out.append([float(p[0]) - ox, float(p[1]) - oy])
            except (TypeError, ValueError, IndexError):
                continue
        return out
    box = _box_in_crop(roi, ox, oy, crop, img_bgr)
    seg = _segment(crop, seed2, excl2, downscale_to, _shift(fg_points), _shift(bg_points), box=box)
    # Nothing found is still an answer the user can act on: what to do next, said by the
    # caller in place of a bare "no part found" — the advice that works is the Area box.
    failed = {"outer": None, "holes": [],
              "hint": _HINT_NO_OUTLINE_BOX if box is not None else _HINT_NO_OUTLINE}
    if seg is None:
        return failed
    mask, scale, sh, sw, grad = seg
    warnings: List[str] = []
    hint = None
    small = (cv2.resize(crop, (sw, sh), interpolation=cv2.INTER_AREA)
             if crop.shape[:2] != (sh, sw) else crop)
    inside = _background_as_part(mask, seed2, scale, sh, sw, box, small)
    if isinstance(inside, np.ndarray):
        mask = inside
        warnings.append(_WARN_BACKGROUND_SWAPPED)
    elif inside == "hint":
        hint = _HINT_BACKGROUND
    outer = _outer_polygon(mask, scale, sh, sw, simplify, max_points, grad, edge_snap)
    if outer is None:
        return failed
    holes = _holes(mask, scale, sh, sw, simplify, max_points, min_hole_frac) if want_holes else []
    if ox or oy:
        outer = [[x + ox, y + oy] for (x, y) in outer]
        holes = [_offset_hole(h, ox, oy) for h in holes]
    H, W = img_bgr.shape[:2]
    cov, border = trace_extent(outer, W, H)
    res = {"outer": outer, "holes": holes, "coverage": cov, "border": border,
           "runaway": is_runaway_trace(cov, border)}
    jogs = _jog_warnings(img_bgr, outer)
    if jogs:
        res["jogs"] = jogs
        warnings.extend(f"The {j['side']} side steps out {j['px']:.0f} px near the {j['end']} end "
                        f"— shadow? Tap ✖ Not the part there, or use Straighten." for j in jogs)
    if hint:
        res["hint"] = hint
    if warnings:
        res["warnings"] = warnings
    return res


# ---------------------------------------------------------------------------------------
# Advice for a trace that went wrong, carried as `hint` / `warnings` for the caller to show
# ---------------------------------------------------------------------------------------
_HINT_NO_OUTLINE = ("Couldn't separate the part from the background here. Draw a box round "
                    "the part (▭ Area), then tap the part again.")
_HINT_NO_OUTLINE_BOX = ("Couldn't find the part inside your Area box. Tap right on the part, "
                        "or tap ✖ Not the part on what isn't.")
_HINT_BACKGROUND = ("That traced the background — tap ✖ Not the part on the "
                    "background, or draw an Area box.")
_WARN_BACKGROUND_SWAPPED = ("The first trace took the background, so the region you tapped "
                            "inside it was used. Check the outline.")
_BG_MIN_PART_FRAC = 0.015    # of the frame: a hole smaller than this is a bore, not the part


def _background_as_part(mask, seed, scale, sh, sw, box, img=None):
    """When the trace took the BACKGROUND, the part the user tapped is a hole in it. Returns
    that hole as a filled mask to trace instead, the string "hint" when the trace looks like
    background but no hole can be taken for the part, or None when the trace looks like a part.

    Seen on the customer's top photo tapped near the cover's edge: the colour model called the
    cover background, and the "outline" was the concrete and the truck round it — 34% of the
    frame, along its top and bottom, with the cover a hole 21 px from the tap — reported as a
    success. Two signs, either enough:
      - the trace runs along 3 or more sides of the frame (of the Area box, when one is drawn)
        and the tap is in one of its holes, not on it;
      - it runs along 2 or more and the tap is on it but within 3% of the frame of a big
        hole: then the hole is used if the tapped pixels look like it, not like the trace
        (`img`, at mask size), and otherwise the user is told.
    A hole has to be 1.5% of the frame to be taken for the part: smaller is a bore."""
    if seed is None:
        return None
    try:
        sx = int(round(float(seed[0]) * scale)); sy = int(round(float(seed[1]) * scale))
    except (TypeError, ValueError, IndexError):
        return None
    if not (0 <= sx < sw and 0 <= sy < sh):
        return None
    if box is not None:
        x0, y0 = int(box[0] * scale), int(box[1] * scale)
        x1, y1 = int((box[0] + box[2]) * scale), int((box[1] + box[3]) * scale)
    else:
        x0, y0, x1, y1 = 0, 0, sw, sh
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(sw, x1), min(sh, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    t = max(2, int(0.01 * min(x1 - x0, y1 - y0)))
    on = mask > 0
    sides = sum(1 for v in (on[y0:y0 + t, x0:x1].any(axis=0).mean(), on[y1 - t:y1, x0:x1].any(axis=0).mean(),
                            on[y0:y1, x0:x0 + t].any(axis=1).mean(), on[y0:y1, x1 - t:x1].any(axis=1).mean())
                if v >= 0.2)
    tapped_on = bool(on[sy, sx])
    if sides < (2 if tapped_on else 3):
        return None
    min_area = _BG_MIN_PART_FRAC * (x1 - x0) * (y1 - y0)
    reach = 0.03 * min(x1 - x0, y1 - y0)
    cnts, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    best = None
    for i, cnt in enumerate(cnts if hier is not None else ()):
        if hier[0][i][3] < 0 or cv2.contourArea(cnt) < min_area:
            continue
        d = cv2.pointPolygonTest(cnt, (float(sx), float(sy)), True)    # > 0: inside the hole
        if d >= 0 or (tapped_on and -d <= reach):
            if best is None or d > best[0]:
                best = (d, cnt)
    if best is None:
        return None if tapped_on else "hint"
    part = np.zeros_like(mask)
    cv2.drawContours(part, [best[1]], -1, 255, -1)
    if tapped_on:
        # Less certain: say so, and swap only when the tapped pixels plainly match the hole.
        # On the customer's photo they did not — the tap was on the cover's dark lip, which
        # went with the shadow round it while the cover's lit face made the hole — so there
        # the user gets the hint, not a half cover.
        if img is None:
            return "hint"
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
        r = max(2, int(0.006 * min(sh, sw)))
        tap = np.median(lab[max(0, sy - r):sy + r + 1, max(0, sx - r):sx + r + 1].reshape(-1, 3), axis=0)
        d_hole = float(np.linalg.norm(tap - np.median(lab[part > 0], axis=0)))
        d_trace = float(np.linalg.norm(tap - np.median(lab[on], axis=0)))
        # Tapped pixels that plainly match the trace: either the trace IS the part — a plate
        # with a window, boxed tight or filling a close-up, tapped beside the window — or the
        # tap was on the background next to the part. Colour cannot tell those apart; the
        # frame can. A part runs along the Area box drawn round it, and a close-up's along 2
        # sides of the frame (both seen giving a false hint), while a background trace with
        # no box runs along all of them; only that one is told.
        if d_trace < 0.5 * d_hole:
            return "hint" if box is None and sides >= 3 else None
        if not d_hole < 0.5 * d_trace:
            return "hint"
    return part


def _jog_warnings(img_bgr, outer):
    """Steps a shadow put on the traced outline's long sides (see outline_cleanup._pull_jogs),
    found but not changed — the user decides — or [] when there are none or the check fails."""
    try:
        from outline_cleanup import straighten_outline
        st = straighten_outline(img_bgr, outer)
    except Exception:
        return []
    return list((st or {}).get("jogs") or [])


# ---------------------------------------------------------------------------------------
# Runaway guard
# ---------------------------------------------------------------------------------------
# Tapping a featureless patch — sky, a plain wall, an out-of-focus area, bare concrete —
# gives the segmenter nothing to find an edge against, and it returns the image rectangle.
# The caller then presents that to the user as "your part's outline", and it can be measured
# and exported like any other trace. Catching it needs a signal that is certain rather than
# merely suspicious, because a false alarm blocks a real trace.
#
# Measured through auto_outline_full over the six customer photos, synthetic parts and
# featureless frames, THROUGH A JPEG ROUND-TRIP — the client posts quality-85 JPEG, and
# compression moves these numbers enough to matter (one dead frame read 82% border raw and
# 64% after JPEG, which a threshold set on raw arrays would have missed):
#
#                                                 coverage        border
#     flat tone / out-of-focus (nothing there)    98.9-99.2%    88.1-99.2%
#     legitimate close-up, part fills the frame        93.2%          0.0%
#     real customer photos, plain centre tap       1.8-53.3%     0.0-19.3%
#
# Border does the separating — 41 points clear of the worst legitimate reading — and coverage
# is the sanity bar that keeps a merely edge-hugging trace from tripping it. Both are
# required: coverage alone fires on the 93% close-up.
#
# Border is measured by SAMPLING along the perimeter, not by testing segment endpoints. The
# endpoint test is brittle in exactly the case that matters: one vertex drifting a few pixels
# inward discards that whole segment, and on the same dead frames it swung 66.8-91.6% where
# sampling gave a steady 88.1-99.2%.
_RUNAWAY_COVERAGE = 0.90
_RUNAWAY_BORDER = 0.60
_BORDER_SAMPLES = 4000          # bounds the work on a long perimeter; ~2px steps at 1080p


def trace_extent(pts, frame_w, frame_h):
    """(coverage, border) for a traced outline: its area as a fraction of the frame, and the
    fraction of its PERIMETER LENGTH running along the frame's edge. Pure — no image, no
    OpenCV, so the rule can be tested directly."""
    n = len(pts or ())
    if n < 3 or frame_w <= 0 or frame_h <= 0:
        return 0.0, 0.0
    area = perim = 0.0
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i][:2], pts[(i + 1) % n][:2]
        area += x0 * y1 - x1 * y0
        perim += math.hypot(x1 - x0, y1 - y0)
    coverage = abs(area) * 0.5 / float(frame_w * frame_h)
    if perim <= 0:
        return coverage, 0.0

    tol = max(2.0, 0.004 * min(frame_w, frame_h))
    step = max(1.0, perim / _BORDER_SAMPLES)

    def _on_edge(x, y):
        return (x <= tol or y <= tol
                or x >= frame_w - 1 - tol or y >= frame_h - 1 - tol)

    total = on = 0.0
    for i in range(n):
        (px, py), (qx, qy) = pts[i][:2], pts[(i + 1) % n][:2]
        seg_len = math.hypot(qx - px, qy - py)
        if seg_len <= 0:
            continue
        k = max(1, int(seg_len / step))
        piece = seg_len / k
        for j in range(k):
            t = (j + 0.5) / k                      # midpoint of each piece
            total += piece
            if _on_edge(px + (qx - px) * t, py + (qy - py) * t):
                on += piece
    return coverage, (on / total if total > 0 else 0.0)


def is_runaway_trace(coverage, border):
    """True when the trace is the frame itself rather than an outline of anything.

    This deliberately catches only the certain case. A trace that merely looks too big is
    left alone: a false alarm blocks a real outline, and the user looking at the overlay
    judges 'is that my part?' far better than any threshold here can. In particular a trace
    that runs away INSIDE an Area box is not caught — it covers only the box, so coverage
    stays low — and that is accepted rather than guessed at.
    """
    return coverage >= _RUNAWAY_COVERAGE and border >= _RUNAWAY_BORDER


def _crop_to_roi(img_bgr, seed, exclude_boxes, roi, margin_frac=0.08, min_margin=10):
    """Crop the image to an ROI (x, y, w, h) plus a background COLLAR, translating the seed and
    exclude boxes into crop coordinates. Returns (crop, seed', exclude', ox, oy) where (ox, oy)
    is the crop origin to add back to results. With no/invalid ROI the inputs pass through
    unchanged (ox = oy = 0).

    The collar is essential: the segmenter builds its background model from the crop's border
    ring, so the crop must include real background AROUND the part — even when the user drew the
    box tight to the object. The margin scales with the box so it works at any zoom."""
    passthrough = (img_bgr, seed, tuple(exclude_boxes or ()), 0, 0)
    if roi is None:
        return passthrough
    try:
        rx, ry, rw, rh = (float(v) for v in roi)
    except (TypeError, ValueError):
        return passthrough
    if not (rw > 1 and rh > 1) or not all(np.isfinite(v) for v in (rx, ry, rw, rh)):
        return passthrough
    H, W = img_bgr.shape[:2]
    m = max(min_margin, margin_frac * max(rw, rh))
    x0 = int(max(0, np.floor(rx - m))); y0 = int(max(0, np.floor(ry - m)))
    x1 = int(min(W, np.ceil(rx + rw + m))); y1 = int(min(H, np.ceil(ry + rh + m)))
    if x1 - x0 < 8 or y1 - y0 < 8:                     # ROI off-frame / degenerate -> ignore it
        return passthrough
    crop = img_bgr[y0:y1, x0:x1]
    seed2 = None
    if seed is not None:
        try:
            seed2 = [float(seed[0]) - x0, float(seed[1]) - y0]
        except (TypeError, ValueError, IndexError):
            seed2 = None
    excl2 = []
    for box in (exclude_boxes or ()):
        try:
            bx, by, bw, bh = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        excl2.append((bx - x0, by - y0, bw, bh))
    return crop, seed2, tuple(excl2), x0, y0


def _box_in_crop(roi, ox, oy, crop, img_bgr):
    """The user's Area box in the crop's frame, or None when no box applies (no ROI, or one
    _crop_to_roi ignored and passed the whole image through)."""
    if roi is None or crop is img_bgr:
        return None
    try:
        rx, ry, rw, rh = (float(v) for v in roi)
    except (TypeError, ValueError):
        return None
    ch, cw = crop.shape[:2]
    x0, y0 = max(0.0, rx - ox), max(0.0, ry - oy)
    x1, y1 = min(float(cw), rx - ox + rw), min(float(ch), ry - oy + rh)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def _offset_hole(h, ox, oy):
    """Translate a typed hole (circle or polygon) from crop coords back to image coords."""
    if isinstance(h, dict) and h.get("shape") == "circle":
        return {**h, "cx": h["cx"] + ox, "cy": h["cy"] + oy}
    if isinstance(h, dict) and h.get("shape") == "polygon":
        return {"shape": "polygon", "points": [[x + ox, y + oy] for (x, y) in h["points"]]}
    return h


def _grabcut_mask(small, seed, fg_pts, bg_pts, scale, sw, sh, iters=4, border_bg=False,
                  work_max=None, box=None):
    """Foreground binary from user hints, via GrabCut.

    The Otsu split below separates the part from the BACKGROUND by colour distance. That
    fails completely when the part and the thing beside it are the same colour — which is
    the normal case for a dark part photographed in sunlight, because its own shadow is the
    same brightness as it is. Measured on a real customer photo of a black running board on
    concrete: part 71.4 mean grey, its shadow 56.7, bare concrete 169.3. The part and the
    shadow differ by 15 levels while the shadow and the concrete differ by 113, so no
    threshold can draw the line and the trace runs away into the shadow.

    Two taps fix what no threshold can: the user says "this is the part" and "this is not".
    GrabCut then models both as colour distributions rather than a single cut.

    border_bg : also take the photo's edge as certain background — see _segment for when.
    box       : (x, y, w, h) the user's Area box, unscaled: everything outside it is certain
                background, everything inside it may be the part — see _segment.
    work_max  : run on a copy no larger than this on its long side, for speed; the mask is
                scaled back up and the outline is snapped to image edges afterwards anyway.
    """
    f = 1.0
    img = small
    if work_max and max(sh, sw) > work_max:
        f = float(work_max) / max(sh, sw)
        img = cv2.resize(small, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    h2, w2 = img.shape[:2]
    gc = np.full((h2, w2), cv2.GC_PR_BGD, np.uint8)
    r = max(3 if f < 1.0 else 6, int(round(0.022 * min(h2, w2))))   # brush, resolution-independent
    if box is not None:
        bx0, by0 = int(box[0] * scale * f), int(box[1] * scale * f)
        bx1, by1 = int((box[0] + box[2]) * scale * f), int((box[1] + box[3]) * scale * f)
        gc[:] = cv2.GC_BGD
        gc[max(0, by0):min(h2, by1), max(0, bx0):min(w2, bx1)] = cv2.GC_PR_FGD

    def _paint(pts, value, rad):
        n = 0
        for p in (pts or ()):
            try:
                x, y = float(p[0]) * scale * f, float(p[1]) * scale * f
            except (TypeError, ValueError, IndexError):
                continue
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            if 0 <= x < w2 and 0 <= y < h2:
                cv2.circle(gc, (int(x), int(y)), rad, value, -1)
                n += 1
        return n

    # A generous probable-foreground halo round the taps keeps GrabCut from starving: seeded
    # with nothing but certain-FG discs in a sea of probable-background it tends to collapse.
    if box is None:
        _paint(list(fg_pts or ()) + ([seed] if seed is not None else []), cv2.GC_PR_FGD, r * 4)
    n_fg = _paint(list(fg_pts or ()) + ([seed] if seed is not None else []), cv2.GC_FGD, r)
    n_bg = _paint(bg_pts, cv2.GC_BGD, r) + (1 if box is not None else 0)
    if bg_pts:
        # One brush disc teaches GrabCut one patch of colour. A chrome step beside the part
        # is many — its reflections ramp along it — so one tap on it removed only the patch
        # under the tap, and the customer needed two taps and still kept a strip of chrome.
        # What the user tapped is the whole connected, smoothly-shaded region round the tap.
        lim = None
        if box is not None:
            lim = (int(box[0] * scale * f), int(box[1] * scale * f),
                   int((box[0] + box[2]) * scale * f), int((box[1] + box[3]) * scale * f))
        keep = [(float(p[0]) * scale * f, float(p[1]) * scale * f)
                for p in list(fg_pts or ()) + ([seed] if seed is not None else [])]
        for p in bg_pts:
            try:
                x, y = float(p[0]) * scale * f, float(p[1]) * scale * f
            except (TypeError, ValueError, IndexError):
                continue
            region = _flood_bg(img, x, y, lim, keep)
            if region is not None:
                gc[region & (gc != cv2.GC_FGD)] = cv2.GC_BGD
    if border_bg:
        b = max(2, int(0.03 * min(h2, w2)))
        gc[:b, :] = gc[-b:, :] = cv2.GC_BGD
        gc[:, :b] = gc[:, -b:] = cv2.GC_BGD
        n_bg += 1
    if n_fg == 0 or n_bg == 0:
        return None                                       # needs both sides to be worth running

    try:
        gc = _grabcut_coarse_fine(img, gc, int(iters))
    except cv2.error:
        return None
    fg = np.where((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    if not fg.any():
        return None
    if f < 1.0:
        fg = cv2.resize(fg, (sw, sh), interpolation=cv2.INTER_LINEAR)
        fg = np.where(fg >= 128, 255, 0).astype(np.uint8)
    # Same tidy-up the threshold path gets, so downstream sees a comparable mask.
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k)
    return fg


_FLOOD_STEP = 4          # Lab units between neighbouring pixels a background flood may cross
# Log brightness per px that walls a flood in, loosest first (8%, 5%, 3% contrast per px).
# No one wall suits every photo: on the customer's cover only the loosest let a tap in the
# black gap beside it fill the gap (the trace's 30 px spill there went: right edge 906 -> 878
# at y=1300, the true edge ~879), while a dark part beside its own shadow (71 vs 57 grey)
# needed the strictest, the looser ones leaking into the part and reaching the tap on it.
_FLOOD_WALLS = (0.08, 0.05, 0.03)
_FLOOD_MAX_FRAC = 0.5    # of the box (or the photo): a flood bigger than this has leaked


def _flood_bg(img, x, y, lim, keep):
    """The region a background tap at (x, y) stands for, as a bool mask, or None.

    Flood from the tap across neighbours that differ by a few Lab units — so it follows the
    slow shading of a chrome step but not a step in colour — and never across an edge, which
    is walled off first. Kept inside the Area box `lim` (x0, y0, x1, y1) when given. A flood
    that reaches a tap on the part (`keep`, the seed and the "this IS the part" taps) or
    covers half the box has leaked into the part; it is tried again with stricter walls, and
    when even the strictest leaks, None: the plain brush disc is all that is safe."""
    h, w = img.shape[:2]
    if not (math.isfinite(x) and math.isfinite(y) and 0 <= x < w and 0 <= y < h):
        return None
    lab = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2LAB), (0, 0), 1.5)
    # Edges are judged in log brightness, as contrast: in deep shadow the cover's end (grey
    # ~15) and the gap beside it (~4) differ by a handful of levels, which a wall in grey
    # levels did not stop — a flood from a tap in that gap ran up the shadowed end and cut
    # 100 px off the cover — but by a factor of two, as plain an edge as any in sunlight.
    L = np.log(lab[:, :, 0].astype(np.float32) + 8.0)
    g = cv2.magnitude(cv2.Sobel(L, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(L, cv2.CV_32F, 0, 1, ksize=3)) / 8.0
    area = float(h * w)
    out = None
    if lim is not None:
        x0, y0, x1, y1 = max(0, lim[0]), max(0, lim[1]), min(w, lim[2]), min(h, lim[3])
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        out = np.ones((h, w), bool)
        out[y0:y1, x0:x1] = False
        area = float((x1 - x0) * (y1 - y0))
    xi, yi = int(x), int(y)
    d = (_FLOOD_STEP,) * 3
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (2 << 8)
    for wall in _FLOOD_WALLS:
        m = np.zeros((h + 2, w + 2), np.uint8)
        m[1:-1, 1:-1][g > wall] = 1
        if out is not None:
            m[1:-1, 1:-1][out] = 1
        m[yi + 1, xi + 1] = 0
        try:
            cv2.floodFill(lab, m, (xi, yi), 0, d, d, flags)
        except cv2.error:
            return None
        region = m[1:-1, 1:-1] == 2
        if region.sum() > _FLOOD_MAX_FRAC * area:
            continue
        if any(0 <= kx < w and 0 <= ky < h and region[int(ky), int(kx)] for (kx, ky) in keep):
            continue
        # Off the region's own rim by a pixel: those pixels are as much the part's edge.
        region = cv2.erode(region.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        return region if region.any() else None
    return None


_GC_COARSE_PX = 560      # long side of the first, whole-picture GrabCut pass
_GC_FINE_ITERS = 3


def _grabcut_coarse_fine(img, gc, iters):
    """GrabCut, coarse to fine. Returns the labelled mask, same size as `gc`.

    GrabCut's time grows with the pixel count: a whole 1300 px photo took 6-8 s. Most of
    those pixels are nowhere near the part's edge. So: run it on a small copy to decide
    roughly where the part is, then run it again at full size where only a thin band round
    that rough edge is still open — inside the band is certainly the part, outside it is
    certainly not — cropped to the part. Measured on the customer's step-pad photos: 3.5x
    faster overall (a 21 s trace took 1 s), and the same overlap with the true outline to
    within 0.01. The full-size pass still decides every pixel near the edge; only pixels
    well inside or well outside are taken from the small copy.
    """
    h, w = gc.shape[:2]
    # GrabCut starts its colour models from k-means, seeded from OpenCV's global random state,
    # so the same tap could trace differently depending on what ran before it. Fix the seed:
    # the same photo and taps always give the same outline.
    cv2.setRNGSeed(0x5EED)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    if max(h, w) <= _GC_COARSE_PX * 1.5:
        cv2.grabCut(img, gc, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
        return gc
    f = float(_GC_COARSE_PX) / max(h, w)
    small = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    # Shrink the hints with a min filter on the label codes' certainty, so a thin certain
    # stroke survives: certain labels (0 BGD, 1 FGD) are painted back on after resizing.
    g2 = cv2.resize(gc, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    for code in (cv2.GC_BGD, cv2.GC_FGD):
        hit = cv2.resize((gc == code).astype(np.uint8), (small.shape[1], small.shape[0]),
                         interpolation=cv2.INTER_AREA) > 0
        g2[hit] = code
    if not ((g2 == cv2.GC_FGD) | (g2 == cv2.GC_PR_FGD)).any() or not \
            ((g2 == cv2.GC_BGD) | (g2 == cv2.GC_PR_BGD)).any():
        cv2.grabCut(img, gc, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
        return gc
    cv2.grabCut(small, g2, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
    rough = np.where((g2 == cv2.GC_FGD) | (g2 == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    rough = cv2.resize(rough, (w, h), interpolation=cv2.INTER_LINEAR)
    rough = np.where(rough >= 128, 255, 0).astype(np.uint8)
    if not rough.any():
        return np.where(gc == cv2.GC_FGD, cv2.GC_FGD, cv2.GC_BGD).astype(np.uint8)
    k = max(3, int(round(5.0 / f)))                       # 5 small-copy px either side
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1, 2 * k + 1))
    inner, outer = cv2.erode(rough, ker), cv2.dilate(rough, ker)
    fine = np.full((h, w), cv2.GC_BGD, np.uint8)
    fine[outer > 0] = cv2.GC_PR_BGD
    fine[rough > 0] = cv2.GC_PR_FGD
    fine[inner > 0] = cv2.GC_FGD
    # The user's own certain hints win over the rough pass.
    fine[gc == cv2.GC_FGD] = cv2.GC_FGD
    fine[gc == cv2.GC_BGD] = cv2.GC_BGD
    ys, xs = np.nonzero(outer)
    pad = 8
    y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad + 1)
    sub = np.ascontiguousarray(fine[y0:y1, x0:x1])
    open_ = (sub == cv2.GC_PR_FGD) | (sub == cv2.GC_PR_BGD)
    if open_.any() and ((sub == cv2.GC_FGD) | (sub == cv2.GC_PR_FGD)).any() and \
            ((sub == cv2.GC_BGD) | (sub == cv2.GC_PR_BGD)).any():
        bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        cv2.grabCut(np.ascontiguousarray(img[y0:y1, x0:x1]), sub, None, bgd, fgd,
                    _GC_FINE_ITERS, cv2.GC_INIT_WITH_MASK)
        fine[y0:y1, x0:x1] = sub
    return fine


def _seed_in(fg, seed, scale, sw, sh) -> bool:
    """Is the tapped point on the foreground side of a binary split? Majority of a 3x3
    neighbourhood, so a single noisy pixel under the tap cannot decide it."""
    try:
        sx = int(round(float(seed[0]) * scale)); sy = int(round(float(seed[1]) * scale))
    except (TypeError, ValueError, IndexError):
        return True                       # no usable seed: nothing to contradict
    if not (0 <= sx < sw and 0 <= sy < sh):
        return True
    win = fg[max(0, sy - 1):sy + 2, max(0, sx - 1):sx + 2]
    return bool((win > 0).mean() >= 0.5)


def _segment(img_bgr, seed, exclude_boxes, downscale_to, fg_points=None, bg_points=None,
             box=None):
    """Segment the tapped part. Returns (mask, scale, sh, sw, grad) at the downscaled
    resolution, or None. `mask` is the filled part (255) with interior holes preserved as 0, so
    callers can read both its outer boundary and its holes; `grad` is the image gradient
    magnitude (reused for shadow arbitration and the outer-boundary edge-snap)."""
    if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
        return None
    H, W = img_bgr.shape[:2]
    scale = min(1.0, float(downscale_to) / float(max(H, W)))
    small = (cv2.resize(img_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
             if scale < 1.0 else img_bgr.copy())
    sh, sw = small.shape[:2]

    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)

    # Background colour model from a border ring — robust when the part sits centrally.
    b = max(2, int(round(0.03 * min(sh, sw))))
    ring = np.ones((sh, sw), np.uint8)
    ring[b:sh - b, b:sw - b] = 0
    ring_px = lab[ring.astype(bool)]
    if ring_px.size == 0:
        return None
    bg = np.median(ring_px, axis=0)
    dist = np.linalg.norm(lab - bg, axis=2)
    dist_u8 = cv2.normalize(dist, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    # Plain split: background (near bg) vs everything else. Otsu draws ONE line, so a cast
    # shadow — a mid-tone between the bright surface and the darker/more-saturated part — lands
    # on the part side and the outline bulges into it.
    _, fg = cv2.threshold(dist_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # When the user has pointed at the background, trust that over the colour-distance split:
    # it is the only thing that can separate a part from something the same colour as it.
    used_grabcut = False
    if box is not None and seed is not None:
        # An Area box drawn round the part says more than any colour model can: everything
        # outside it is NOT the part. GrabCut takes that literally — outside is certain
        # background, inside is "maybe the part", the tap is certainly the part — and learns
        # both sides' colours from the photo itself. A part half in sun and half in shadow is
        # still one object to it, and the trace cannot leave the box. Measured on the
        # customer's top-down step-pad photo, the bottom third in the photographer's shadow:
        # the colour-distance trace in the same box missed the shadowed end and a strip down
        # one side (IoU 0.60); this kept the whole cover (IoU 0.91 with a loose box, 0.92 with
        # a tight one), in under a second (see _grabcut_coarse_fine).
        gc_fg = _grabcut_mask(small, seed, fg_points, bg_points, scale, sw, sh, iters=5, box=box)
        if gc_fg is not None and _component(gc_fg, exclude_boxes, scale, sw, sh, seed) is not None:
            fg = gc_fg
            used_grabcut = True
    if used_grabcut:
        pass
    elif bg_points:
        gc_fg = _grabcut_mask(small, seed, fg_points, bg_points, scale, sw, sh)
        if gc_fg is not None and _component(gc_fg, exclude_boxes, scale, sw, sh, seed) is not None:
            fg = gc_fg
            used_grabcut = True
    elif seed is not None and not _seed_in(fg, seed, scale, sw, sh):
        # The colour model calls the pixel the user TAPPED background — so the model is wrong
        # for this photo, and the plain trace can only return nothing. It assumes the photo's
        # border is one background colour; a real photo's border is often several. Measured
        # on the customer's step-pad photo: the border is concrete, white truck and shadow,
        # their median lands between the concrete and the dark cover, and the cover (distance
        # 95) and the concrete (103) both fall on the background side of the split, so a tap
        # squarely on the cover traced nothing at all.
        #
        # What IS known is enough for GrabCut: the tap is the part, and the photo's edge is
        # not. Five iterations: it finds the cover's edges. Fewer iterations, or a single
        # pass on a small copy, lost a narrow dark lip along one side — 5 to 16 mm of the
        # part. The coarse-to-fine pass keeps it (decided at full size, near the edge) in
        # about 1.3 s instead of 6-7 s.
        gc_fg = _grabcut_mask(small, seed, fg_points, None, scale, sw, sh, iters=5,
                              border_bg=True)
        if gc_fg is not None and _component(gc_fg, exclude_boxes, scale, sw, sh, seed) is not None:
            fg = gc_fg
            used_grabcut = True

    mask = _component(fg, exclude_boxes, scale, sw, sh, seed)
    if mask is None:
        return None

    grad = _gradient_mag(small)          # edge-strength field, reused below and for edge-snap

    # Shadow refinement (purely additive). A SECOND Otsu on the foreground shoulder separates
    # the shadow (mid distance from bg) from the part (far distance). We adopt that tighter mask
    # only when its boundary demonstrably sits on stronger image edges than the plain one — a
    # true part edge is a sharp gradient, a shadow's penumbra is soft — AND it still keeps the
    # bulk of the tapped object. So a dark, low-contrast part whose own body reads shadow-like
    # (chrome reflections, a matte-dark part) is never eaten: its refined mask either collapses
    # or fails the edge test, and the plain mask stands.
    # Shadow refinement is a guess about which side of a colour split the shadow fell on.
    # When the user has TOLD us where the background is, that guess can only do harm, so it
    # is skipped — GrabCut already used better information.
    if not used_grabcut:
        refined = _shadow_refine(dist_u8, fg, grad, exclude_boxes, scale, sw, sh, seed, mask)
        if refined is not None:
            mask = refined

    return mask, scale, sh, sw, grad


def _component(fg, exclude_boxes, scale, sw, sh, seed):
    """Clean a foreground binary and return the tapped/largest component as a filled mask
    (255), interior holes preserved as 0, or None. Shared by the plain and shadow-refined
    segmentations so both go through identical morphology, exclusion and component picking."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, k, iterations=2)
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, k, iterations=1)

    # Blank out excluded regions (the calibration marker), padded a little.
    for box in (exclude_boxes or ()):
        try:
            bx, by, bw, bh = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        x0 = int(bx * scale) - 4; y0 = int(by * scale) - 4
        x1 = int((bx + bw) * scale) + 4; y1 = int((by + bh) * scale) + 4
        cv2.rectangle(fg, (max(0, x0), max(0, y0)), (min(sw, x1), min(sh, y1)), 0, -1)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    if n <= 1:
        return None
    target = _pick_component(labels, stats, n, seed, scale, sw, sh)
    if target is None:
        return None
    return (labels == target).astype(np.uint8) * 255


def _shadow_refine(dist_u8, fg_plain, grad, exclude_boxes, scale, sw, sh, seed, mask_plain,
                   min_keep_frac=0.45, edge_ratio=1.15, edge_floor=20.0):
    """Try to shed a cast shadow welded onto the part; return the tighter mask or None.

    The plain mask splits bg from foreground once, so a shadow (mid distance from the
    background) sits with the part (far distance). A second Otsu ON THE FOREGROUND SHOULDER
    finds the bg->shadow->part step and keeps only the far side (the part). That tighter mask
    is adopted ONLY when both hold: (1) it retains >= min_keep_frac of the plain area — so it
    hasn't eaten a genuine dark part whose body reads shadow-like — and (2) its boundary sits on
    clearly stronger image gradients than the plain boundary — the real part edge is sharp,
    a shadow's penumbra is soft. Otherwise the plain mask stands. This is why it fixes a real
    cast shadow without repeating the reverted per-pixel rule that erased dark-chrome parts."""
    fgvals = dist_u8[fg_plain > 0]
    if fgvals.size < 50 or int(fgvals.max()) <= int(fgvals.min()):
        return None                                  # foreground is one mode -> no shadow shoulder
    t2, _ = cv2.threshold(fgvals, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    fg2 = ((dist_u8 > t2).astype(np.uint8)) * 255
    mask_alt = _component(fg2, exclude_boxes, scale, sw, sh, seed)
    if mask_alt is None:
        return None

    a_plain = int((mask_plain > 0).sum())
    a_alt = int((mask_alt > 0).sum())
    if a_plain == 0 or a_alt < min_keep_frac * a_plain:
        return None                                  # collapsed / ate the part

    s_plain = _boundary_edge_score(mask_plain, grad)
    s_alt = _boundary_edge_score(mask_alt, grad)
    if s_alt > s_plain * edge_ratio + edge_floor:
        return mask_alt
    return None


def _gradient_mag(small):
    """Scharr gradient magnitude of the grayscale image — the edge-strength field."""
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Scharr(gray, cv2.CV_32F, 1, 0)
    gy = cv2.Scharr(gray, cv2.CV_32F, 0, 1)
    return cv2.magnitude(gx, gy)


def _boundary_edge_score(mask, grad):
    """Median gradient magnitude sampled along the mask's outer boundary — how well the mask
    edge coincides with a real image edge. A boundary hugging the true part edge scores high;
    one bulged onto a shadow's soft penumbra scores low."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return 0.0
    c = max(cnts, key=cv2.contourArea).reshape(-1, 2)
    xs = np.clip(c[:, 0], 0, grad.shape[1] - 1)
    ys = np.clip(c[:, 1], 0, grad.shape[0] - 1)
    return float(np.median(grad[ys, xs]))


def _sample_grad(grad, pts, sw, sh):
    """Nearest-pixel gradient magnitude at each (x, y), clipped to the image."""
    xs = np.clip(np.round(pts[:, 0]).astype(int), 0, sw - 1)
    ys = np.clip(np.round(pts[:, 1]).astype(int), 0, sh - 1)
    return grad[ys, xs]


def _smooth_1d_cyclic(v, r):
    """Box-smooth a per-vertex scalar around a closed contour (wrap-around)."""
    if r < 1:
        return v
    n = len(v)
    if n <= 2:
        return v
    r = min(r, n - 1)
    ker = np.ones(2 * r + 1) / (2 * r + 1)
    vp = np.concatenate([v[-r:], v, v[:r]])
    return np.convolve(vp, ker, mode="same")[r:r + n]


def _edge_snap(contour, grad, sh, sw, max_shift_frac=0.006, min_shift=3):
    """Pull the dense outer boundary onto the true image edge — a bounded, self-calibrating
    active-contour step.

    For each boundary point we look a few px inward/outward along the local normal and find the
    strongest gradient in that window (a mild distance penalty prefers the NEAREST strong edge).
    A point is moved ONLY when it is genuinely off an edge — its own gradient is well below the
    boundary's typical strength — AND a clearly stronger, above-typical edge sits within reach.
    So a boundary already on a crisp edge (a clean trace) does not move at all, while a boundary
    drifted onto a soft shadow/penumbra fringe is tightened onto the real edge. The per-vertex
    shift is smoothed along the contour (neighbours move coherently, no jaggies/self-crossing)
    and the whole step is vetoed if it would change the enclosed area by more than 25% — so this
    can only REFINE a boundary, never relocate it. Bounded reach (a few px) by design: a large
    weld/shadow is the region-split's / ROI's job, not this polish."""
    P = contour.reshape(-1, 2).astype(np.float64)
    n = len(P)
    max_shift = max(min_shift, int(round(max_shift_frac * max(sh, sw))))
    if n < 12 or max_shift < 1:
        return contour

    i = np.arange(n)
    k = max(1, n // 200)                                   # tangent window (resolution-independent)
    tang = P[(i + k) % n] - P[(i - k) % n]
    nrm = np.stack([-tang[:, 1], tang[:, 0]], axis=1)
    ln = np.hypot(nrm[:, 0], nrm[:, 1]); ln[ln == 0] = 1.0
    nrm /= ln[:, None]
    centroid = P.mean(axis=0)
    nrm[((centroid - P) * nrm).sum(1) < 0] *= -1          # orient normals inward

    cur = _sample_grad(grad, P, sw, sh)
    edge_ref = float(np.median(cur))                       # this boundary's typical edge strength
    if not (edge_ref > 0):
        return contour
    penalty = 0.02 * edge_ref                              # prefer the nearest strong edge
    best_g = np.full(n, -np.inf)
    best_t = np.zeros(n)
    for t in range(-max_shift, max_shift + 1):
        g = _sample_grad(grad, P + t * nrm, sw, sh) - penalty * abs(t)
        upd = g > best_g
        best_g[upd] = g[upd]; best_t[upd] = t

    # Move only weak, off-edge points toward a clearly-stronger-than-typical edge.
    move = (cur < 0.8 * edge_ref) & (best_g > 1.3 * cur) & (best_g > edge_ref)
    shift = np.where(move, best_t, 0.0)
    shift = _smooth_1d_cyclic(shift, max(1, max_shift // 2))
    Q = P + shift[:, None] * nrm

    a0 = abs(cv2.contourArea(P.astype(np.float32)))
    a1 = abs(cv2.contourArea(Q.astype(np.float32)))
    if a0 > 0 and abs(a1 - a0) / a0 > 0.25:                # too large a change -> refuse the snap
        return contour
    return Q.reshape(-1, 1, 2).astype(np.float32)


def _outer_polygon(mask, scale, sh, sw, simplify, max_points, grad=None, snap=True):
    """The part's outer boundary as a simplified polygon in IMAGE pixel coords, or None.

    When `snap` and a gradient field are given, the dense boundary is first edge-snapped: weak
    (off-edge) points are pulled a few px onto the nearest strong gradient ridge, tightening a
    soft-fringe boundary onto the true part edge. It is a strict no-op on a boundary already
    sitting on a strong edge, so a clean trace is unchanged."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.003 * sh * sw:
        return None
    if snap and grad is not None:
        c = _edge_snap(c, grad, sh, sw)
    poly = _simplify(c, simplify, max_points)
    if poly is None or len(poly) < 3:
        return None
    return [[float(x) / scale, float(y) / scale] for (x, y) in poly]


def _holes(mask, scale, sh, sw, simplify, max_points, min_hole_frac=0.004, max_holes=6):
    """Interior holes of the part, largest first, as TYPED shapes in IMAGE pixel coords:

        {"shape": "circle",  "cx": .., "cy": .., "r": ..}      -- a round bore
        {"shape": "polygon", "points": [[x, y], ...]}          -- a hex socket, slot, etc.

    A near-circular hole is returned as a true circle, so the bore is perfectly round in the
    DXF instead of a faceted loop; anything else keeps its corner-preserving polygon. Holes
    are the enclosed background INSIDE the part (a box-end ring, bolt holes); an open jaw
    opens to the exterior and is not one. Specks below min_hole_frac of the part area — glare,
    texture — are ignored so a hole means a real feature."""
    # RETR_CCOMP gives a 2-level hierarchy: outer boundaries (no parent) and, one level down,
    # their holes (parent index >= 0). hierarchy entry = [next, prev, first_child, parent].
    cnts, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is None or len(cnts) == 0:
        return []
    hier = hier[0]
    part_area = float((mask > 0).sum()) or 1.0
    min_area = max(30.0, min_hole_frac * part_area)
    holes = []
    for i, cnt in enumerate(cnts):
        if hier[i][3] < 0:                      # no parent -> outer boundary, not a hole
            continue
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        shape = _classify_hole(cnt, scale, simplify, max_points)
        if shape is not None:
            holes.append((area, shape))
    holes.sort(key=lambda h: h[0], reverse=True)
    return [h[1] for h in holes[:max_holes]]


def _fit_circle_ls(pts):
    """Algebraic (Kåsa) least-squares circle fit. Returns (cx, cy, r) or None."""
    x = pts[:, 0]; y = pts[:, 1]
    A = np.column_stack([2.0 * x, 2.0 * y, np.ones(len(x))])
    b = x * x + y * y
    try:
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    except Exception:
        return None
    cx, cy, c = sol
    v = c + cx * cx + cy * cy
    if not np.isfinite(v) or v <= 0:
        return None
    return float(cx), float(cy), float(np.sqrt(v))


def _largest_inlier_arc_frac(pts, cx, cy, inliers, bins=None):
    """Fraction of the full circle spanned by the LARGEST CONTIGUOUS run of on-circle sectors.
    This separates a round bore with ONE localized defect (a shadow notch — its outliers sit
    in a single arc, so the rest is one long contiguous inlier run) from a polygon (whose
    off-circle corners are spread all the way round, so the longest contiguous run is short).

    ``bins`` scales with the number of boundary points instead of being a fixed 48. A sector
    with NO points in it is counted as off-circle (it has to be — an arc that simply stops,
    like a semicircular notch, must not read as a full bore), so at a fixed 48 a small bore
    broke its own run on empty sectors: a perfectly round hole of radius <= ~10 px in the
    working image has too few boundary points to land in all 48, and came back a polygon
    rather than a circle with a diameter. Roughly three points per sector keeps genuine
    circles unbroken while still resolving a real gap.
    """
    if len(pts) == 0:
        return 0.0
    if bins is None:
        bins = int(min(48, max(8, len(pts) // 3)))
    ang = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
    idx = (((ang + np.pi) / (2.0 * np.pi)) * bins).astype(int) % bins
    good = np.zeros(bins, dtype=bool)
    for b in range(bins):
        m = idx == b
        good[b] = bool(m.any()) and float(inliers[m].mean()) >= 0.5   # sector mostly on-circle
    if good.all():
        return 1.0
    if not good.any():
        return 0.0
    best = run = 0
    for v in np.concatenate([good, good]):   # doubled for cyclic wraparound
        run = run + 1 if v else 0
        best = max(best, run)
    return min(best, bins) / float(bins)


def _classify_hole(cnt, scale, simplify, max_points):
    """A hole contour -> a TRUE circle when it is a round bore — even a 12-point socket, or a
    bore whose rim is partly eaten by shadow/glare — else a corner-preserving polygon.

    Seeded from a MEDIAN radius (robust to a notch's minority of points) and refined by
    iterated least-squares fits on the inliers, so a localized shadow defect — even a deep one
    that adds many contour points — is shed and the bore snaps to a clean circle. The decisive
    gate is ANGULAR (the largest contiguous on-circle arc), not a point count: a shadow notch
    leaves one long contiguous arc, while a polygon's off-circle corners are spread all the way
    round (short longest run). A weak point-fraction floor rejects total non-circles.
    """
    pts = cnt.reshape(-1, 2).astype(np.float64)
    n = len(pts)
    if n >= 8:
        cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
        r = float(np.median(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)))   # robust seed radius
        inliers = None
        for _ in range(4):
            if not (r > 3.0):
                break
            inliers = (np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r) / r) < 0.06
            if int(inliers.sum()) < 5:
                break
            fit = _fit_circle_ls(pts[inliers])
            if fit is None:
                break
            cx, cy, r = fit
        if inliers is not None and r > 3.0:
            inliers = (np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r) / r) < 0.06  # vs final fit
            if inliers.mean() >= 0.45 and _largest_inlier_arc_frac(pts, cx, cy, inliers) >= 0.75:
                return {"shape": "circle", "cx": cx / scale, "cy": cy / scale, "r": r / scale}
    poly = _simplify(cnt, simplify, max_points)
    if poly is None or len(poly) < 3:
        return None
    return {"shape": "polygon", "points": [[float(x) / scale, float(y) / scale] for (x, y) in poly]}


def _pick_component(labels, stats, n, seed, scale, sw, sh):
    """Choose the component to trace: the seed's object, else the largest non-trivial one.

    When a seed is given we trace ONLY what the user tapped (or a blob just beside it) and
    return None if they tapped empty space — tapping nothing must not grab a random object.
    """
    if seed is not None:
        try:
            sx = int(round(float(seed[0]) * scale)); sy = int(round(float(seed[1]) * scale))
        except (TypeError, ValueError, IndexError):
            return None
        if not (0 <= sx < sw and 0 <= sy < sh):
            return None
        lbl = int(labels[sy, sx])
        if lbl != 0:
            return lbl
        # Seed landed just off the object (a highlight/hole edge): take the nearest blob.
        r = max(3, int(0.02 * min(sw, sh)))
        win = labels[max(0, sy - r):sy + r, max(0, sx - r):sx + r]
        vals, counts = np.unique(win[win != 0], return_counts=True)
        return int(vals[counts.argmax()]) if vals.size else None

    best, best_area = None, 0
    total = float(sh * sw)
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < 0.005 * total or area > 0.9 * total:   # skip speckle and background inversions
            continue
        if area > best_area:
            best, best_area = i, area
    if best is not None:
        return best
    # Nothing in (0.5%, 90%]: a close-up part can legitimately fill >90% of the frame. Rather
    # than give up, take the largest non-background blob below a looser ceiling so a full-frame
    # part still traces (the >90% cap only exists to avoid grabbing a background inversion).
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if 0.005 * total < area <= 0.98 * total and area > best_area:
            best, best_area = i, area
    return best


def _simplify(contour, simplify, max_points):
    """Corner-preserving, curvature-adaptive reduction of a dense closed contour.

    A single global approxPolyDP epsilon has to trade off corners against curves: coarse
    enough to keep the vertex count editable, it chords straight across sharp corners and
    can't follow a curve. Instead we find the true corners, simplify each arc *between*
    corners on its own (so a corner is never a casualty of smoothing the run beside it),
    smooth those arcs to kill the pixel staircase, and thin low-curvature points last if
    we're over budget. Falls back to the original global approxPolyDP on any error so a
    trace is never lost to a simplifier edge case.
    """
    peri = cv2.arcLength(contour, True)
    if peri <= 0:
        return None
    try:
        approx = _simplify_adaptive(contour, peri, simplify, max_points)
        if approx is not None and len(approx) >= 3:
            return approx
    except Exception:
        pass
    return _simplify_dp(contour, simplify, max_points)


def _simplify_dp(contour, simplify, max_points):
    """Original global-epsilon simplification — kept as a robust fallback."""
    peri = cv2.arcLength(contour, True)
    if peri <= 0:
        return None
    eps = max(1.0, simplify * peri)
    approx = cv2.approxPolyDP(contour, eps, True).reshape(-1, 2)
    guard = 0
    while len(approx) > max_points and guard < 20:
        eps *= 1.3
        approx = cv2.approxPolyDP(contour, eps, True).reshape(-1, 2)
        guard += 1
    if len(approx) > max_points:                 # still over after the guard loop: hard-cap
        idx = np.linspace(0, len(approx) - 1, max_points).round().astype(int)
        approx = approx[np.unique(idx)]           # uniform decimation, honour max_points
    return approx


def _simplify_adaptive(contour, peri, simplify, max_points):
    C = contour.reshape(-1, 2).astype(np.float64)
    n = len(C)
    if n < 4:
        return C

    # Windows/tolerances scale with the contour so behaviour is resolution-independent.
    # The contour is ~1px-spaced (CHAIN_APPROX_NONE), so an index window ≈ an arc-length
    # window in pixels.
    k = int(max(2, round(0.01 * n)))                 # half-window the corner turn is measured over
    eps = max(0.8, simplify * peri)                  # per-arc approxPolyDP tolerance (px)
    sigma = min(6.0, max(0.6, 0.003 * peri))         # boundary-smoothing strength (px)
    corner_thresh = np.deg2rad(32.0)                 # turn sharper than this is a real corner

    ang = np.abs(_turn_angles(C, k))
    # Only trust corner detection when the contour is long enough that the +/-k window stays
    # well short of antipodal; a tiny contour would otherwise read a false corner everywhere.
    corners = _nms_corners(ang, corner_thresh, min_sep=max(3, k), n=n) if n > 4 * k else []

    # Guard: a noisy boundary could nominate more "corners" than the vertex budget. Keep
    # only the sharpest, so max_points still bounds the result.
    if len(corners) > max_points:
        corners = sorted(corners, key=lambda i: ang[i], reverse=True)[:max_points]
        corners = sorted(corners)

    if len(corners) >= 2:
        parts = []
        for a_i, b_i in _arc_pairs(corners, n):
            arc = _slice_cyclic(C, a_i, b_i)         # includes both corner endpoints
            arc = _smooth_open(arc, sigma)           # smooth interior; endpoints pinned
            red = cv2.approxPolyDP(arc.astype(np.float32).reshape(-1, 1, 2), eps, False).reshape(-1, 2)
            parts.append(red[:-1] if len(red) > 1 else red)  # drop shared corner (next arc supplies it)
        P = np.concatenate(parts, axis=0) if parts else C
        prot = _mark_protected(P, C[np.asarray(corners, int)])
    else:
        Cs = _smooth_closed(C, sigma)
        P = cv2.approxPolyDP(Cs.astype(np.float32).reshape(-1, 1, 2), eps, True).reshape(-1, 2)
        prot = np.zeros(len(P), dtype=bool)

    if len(P) > max_points:
        P = _thin_to(P, prot, max_points)
    return P if len(P) >= 3 else None


def _turn_angles(P, k):
    """Signed turning angle (radians) at each vertex, measured over a ±k window so the
    pixel staircase doesn't register as a corner. |angle| is the sharpness of the turn."""
    n = len(P)
    i = np.arange(n)
    v1 = P - P[(i - k) % n]
    v2 = P[(i + k) % n] - P
    cross = v1[:, 0] * v2[:, 1] - v1[:, 1] * v2[:, 0]
    dot = (v1 * v2).sum(1)
    return np.arctan2(cross, dot)


def _nms_corners(mag, thresh, min_sep, n):
    """Indices of turn-magnitude peaks above `thresh`, thinned so no two kept corners sit
    within `min_sep` (cyclically) — strongest wins, so a corner is one vertex, not a cluster."""
    cand = np.where(mag > thresh)[0]
    if cand.size == 0:
        return []
    kept = []
    for idx in cand[np.argsort(mag[cand])[::-1]]:
        idx = int(idx)
        if all(min(abs(idx - j), n - abs(idx - j)) >= min_sep for j in kept):
            kept.append(idx)
    return sorted(kept)


def _arc_pairs(corners, n):
    """Consecutive (start, end) corner-index pairs around the closed contour, wrapping the
    last corner back to the first."""
    m = len(corners)
    return [(corners[i], corners[(i + 1) % m]) for i in range(m)]


def _slice_cyclic(P, a, b):
    """Points from index a to b inclusive, walking forward and wrapping past the end."""
    n = len(P)
    idx = np.arange(a, b + 1) if b >= a else np.concatenate([np.arange(a, n), np.arange(0, b + 1)])
    return P[idx]


def _smooth_closed(P, sigma):
    """Gaussian-smooth a closed contour (wrap-around) to remove boundary noise."""
    n = len(P)
    r = int(max(1, round(3 * sigma)))
    if sigma <= 0 or r >= n:
        return P
    ker = _gauss_kernel(r, sigma)
    xp = np.concatenate([P[-r:], P, P[:r]], axis=0)
    sx = np.convolve(xp[:, 0], ker, mode="same")[r:r + n]
    sy = np.convolve(xp[:, 1], ker, mode="same")[r:r + n]
    return np.stack([sx, sy], axis=1)


def _smooth_open(P, sigma):
    """Gaussian-smooth an open arc with its endpoints (the corners) pinned exactly."""
    n = len(P)
    if n <= 2 or sigma <= 0:
        return P
    r = min(int(max(1, round(3 * sigma))), n - 1)
    ker = _gauss_kernel(r, sigma)
    xp = np.concatenate([np.repeat(P[:1], r, axis=0), P, np.repeat(P[-1:], r, axis=0)], axis=0)
    sx = np.convolve(xp[:, 0], ker, mode="same")[r:r + n]
    sy = np.convolve(xp[:, 1], ker, mode="same")[r:r + n]
    out = np.stack([sx, sy], axis=1)
    out[0], out[-1] = P[0], P[-1]        # keep the corner vertices crisp
    return out


def _gauss_kernel(r, sigma):
    xs = np.arange(-r, r + 1)
    ker = np.exp(-(xs * xs) / (2.0 * sigma * sigma))
    return ker / ker.sum()


def _mark_protected(P, corner_pts):
    """Mark the vertex nearest each detected corner as non-removable during thinning."""
    prot = np.zeros(len(P), dtype=bool)
    for cp in corner_pts:
        prot[int(np.argmin(np.hypot(P[:, 0] - cp[0], P[:, 1] - cp[1])))] = True
    return prot


def _thin_to(P, prot, max_points):
    """Visvalingam-style thinning: repeatedly drop the non-corner vertex whose triangle
    with its neighbours has the least area (the least visually significant point), so
    straight runs collapse first and curves/corners survive."""
    pts = [row for row in P]
    keep = list(prot)
    while len(pts) > max_points:
        m = len(pts)
        best, best_area = -1, None
        for i in range(m):
            if keep[i]:
                continue
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % m]
            area = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) * 0.5
            if best_area is None or area < best_area:
                best, best_area = i, area
        if best < 0:
            break                         # everything left is a protected corner
        pts.pop(best)
        keep.pop(best)
    return np.array(pts, dtype=np.float64)
