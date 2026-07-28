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

import cv2
import numpy as np


# Defaults shared by auto_outline / auto_outline_full. `simplify` is much finer, and the
# working resolution higher, than the first cut — a round feature on a long part (a wrench's
# box end) needs the extra samples to read as a smooth curve rather than a coarse polygon.
# On a wrench this yields a smooth head (~27 pts) and captures the box-end hole; straight
# runs still collapse and corners are still preserved, so it stays editable.
_DEFAULT_SIMPLIFY = 0.0006
_DEFAULT_MAX_POINTS = 160
_DEFAULT_DOWNSCALE = 1300


def auto_outline(
    img_bgr: np.ndarray,
    seed: Optional[Sequence[float]] = None,
    exclude_boxes: Sequence[Tuple[float, float, float, float]] = (),
    simplify: float = _DEFAULT_SIMPLIFY,
    max_points: int = _DEFAULT_MAX_POINTS,
    downscale_to: int = _DEFAULT_DOWNSCALE,
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
    """
    seg = _segment(img_bgr, seed, exclude_boxes, downscale_to)
    if seg is None:
        return None
    mask, scale, sh, sw = seg
    return _outer_polygon(mask, scale, sh, sw, simplify, max_points)


def auto_outline_full(
    img_bgr: np.ndarray,
    seed: Optional[Sequence[float]] = None,
    exclude_boxes: Sequence[Tuple[float, float, float, float]] = (),
    simplify: float = _DEFAULT_SIMPLIFY,
    max_points: int = _DEFAULT_MAX_POINTS,
    downscale_to: int = _DEFAULT_DOWNSCALE,
    want_holes: bool = True,
    min_hole_frac: float = 0.004,
) -> Optional[dict]:
    """Segment the tapped part and return both its outer boundary AND its interior holes:

        {"outer": [[x, y], ...],
         "holes": [ {"shape": "circle",  "cx": .., "cy": .., "r": ..},      # a round bore
                    {"shape": "polygon", "points": [[x, y], ...]}, ... ]}   (image pixel coords)

    Holes are enclosed background inside the part — a box-end ring, bolt holes — which is what
    turns a flat outline into a printable replica; a round bore comes back as a true circle.
    A wrench's OPEN jaw is not a hole (it opens to the exterior) so it stays part of `outer`.
    Returns None if nothing could be segmented.
    """
    seg = _segment(img_bgr, seed, exclude_boxes, downscale_to)
    if seg is None:
        return None
    mask, scale, sh, sw = seg
    outer = _outer_polygon(mask, scale, sh, sw, simplify, max_points)
    if outer is None:
        return None
    holes = _holes(mask, scale, sh, sw, simplify, max_points, min_hole_frac) if want_holes else []
    return {"outer": outer, "holes": holes}


def _segment(img_bgr, seed, exclude_boxes, downscale_to):
    """Segment the tapped part. Returns (mask, scale, sh, sw) at the downscaled resolution, or
    None. `mask` is the filled part (255) with interior holes preserved as 0, so callers can
    read both its outer boundary and its holes."""
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
    _, fg = cv2.threshold(dist_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

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

    mask = (labels == target).astype(np.uint8) * 255
    return mask, scale, sh, sw


def _outer_polygon(mask, scale, sh, sw, simplify, max_points):
    """The part's outer boundary as a simplified polygon in IMAGE pixel coords, or None."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < 0.003 * sh * sw:
        return None
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


def _classify_hole(cnt, scale, simplify, max_points):
    """A hole contour -> a circle when it is a genuinely round bore, else a simplified
    polygon. Corner count is the discriminator: a plain round hole has no sharp corners,
    while a hex / 12-point socket or a slot has several and must keep its true geometry —
    a radius-variance test alone can't tell a hexagon from a circle (both barely vary)."""
    pts = cnt.reshape(-1, 2).astype(np.float64)
    n = len(pts)
    if n < 3:
        return None
    m = cv2.moments(cnt)
    if m["m00"] != 0:                           # centroid (sign of m00 cancels for a hole)
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
    else:
        cx, cy = pts.mean(axis=0)
    d = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    r = float(d.mean())
    k = int(max(2, round(0.02 * n)))
    corners = _nms_corners(np.abs(_turn_angles(pts, k)), np.deg2rad(30.0), max(3, k), n)
    if r > 3.0 and len(corners) <= 1 and float(d.std()) / r < 0.08:
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
    corners = _nms_corners(ang, corner_thresh, min_sep=max(3, k), n=n)

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
