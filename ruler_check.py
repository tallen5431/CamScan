"""Check a Square up against the ruler itself.

A ruler is a rectangle: its two long edges are parallel and it is the same width all the
way along. A photo taken at an angle breaks both — the edges converge and the far end looks
narrower. So once the photo is squared up, the ruler should come back to a true rectangle.
How close it comes is a measurement of how well the correction worked, made from something
already in the photo, with no calipers and no part needed. Its width, read in mm, can also
be compared with the real ruler's.

    find_ruler_edges(img_bgr, a, b) -> [[p, q], [p, q]] or None   (the two long edges, image px)
    ruler_report(edges, H) -> {"angle_deg", "width_mm": [at one end, at the other], ...}
"""
from typing import Sequence
import math

import cv2
import numpy as np


def _robust_line(t, s, iters=4):
    """Fit s = c0 + c1 * t, dropping outliers (a tick mark, a letter, a finger). Returns
    (c0, c1, rms of the kept points, fraction kept) or None."""
    t, s = np.asarray(t, float), np.asarray(s, float)
    keep = np.isfinite(s)
    if keep.sum() < 8:
        return None
    for _ in range(iters):
        A = np.vstack([np.ones(keep.sum()), t[keep]]).T
        c, *_ = np.linalg.lstsq(A, s[keep], rcond=None)
        r = s - (c[0] + c[1] * t)
        mad = np.median(np.abs(r[keep])) / 0.6745 if keep.any() else 0.0
        new = np.isfinite(r) & (np.abs(r) <= max(0.6, 3.0 * mad))
        if new.sum() < 8:
            break
        keep = new
    A = np.vstack([np.ones(keep.sum()), t[keep]]).T
    c, *_ = np.linalg.lstsq(A, s[keep], rcond=None)
    r = s[keep] - (c[0] + c[1] * t[keep])
    rms = float(np.sqrt(np.mean(r * r)))
    tk = t[keep]
    # Standard error of the slope. Neighbouring samples are not independent (blur, JPEG
    # blocks, a run of ticks), so count one sample per few px of edge, not every sample.
    spacing = float(np.median(np.diff(tk))) if tk.size > 1 else 1.0
    n_eff = max(3.0, tk.size * min(1.0, spacing / 4.0))
    se = rms / max(1e-9, float(np.sqrt(np.sum((tk - tk.mean()) ** 2) * n_eff / tk.size)))
    return float(c[0]), float(c[1]), rms, float(keep.mean()), se


def find_ruler_edges(img_bgr, a: Sequence, b: Sequence, with_error: bool = False):
    """The ruler's two long edges, near the scale line drawn along it from `a` to `b`.

    Samples the photo square to the line all along it, finds the strip the line lies on — a
    band brighter (or darker) than both sides of it, with a sharp edge each side — and fits
    each edge as a straight line to sub-pixel. Returns [[p0, p1], [q0, q1]] in image px,
    each spanning the measured part of the ruler, or None when no clear strip is there.
    with_error: return (edges, sigma) instead, sigma the standard error of the angle between
    the two edges in radians — what the tilt found from them is uncertain by.
    """
    none = (None, None) if with_error else None
    if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
        return none
    try:
        a = np.array([float(a[0]), float(a[1])])
        b = np.array([float(b[0]), float(b[1])])
    except (TypeError, ValueError, IndexError):
        return none
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        return none
    L = float(np.hypot(*(b - a)))
    if L < 60:
        return none
    # A line longer than the photo's diagonal is not on it; and the sample grid below grows
    # with the line, so an absurd one would be an absurd allocation.
    if L > 1.5 * max(img_bgr.shape[:2]):
        return none
    u = (b - a) / L
    n = np.array([-u[1], u[0]])
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    R = max(20.0, 0.22 * L)                       # look this far either side of the line
    q = 4                                         # samples per px across the line
    ns = int(round(2 * R * q)) + 1
    off = np.linspace(-R, R, ns)
    nt = int(min(1600, max(40, L)))                # a sample per px along the line
    ts = np.linspace(0.04, 0.96, nt) * L
    X = (a[0] + np.outer(ts, np.ones(ns)) * u[0] + np.outer(np.ones(nt), off) * n[0]).astype(np.float32)
    Y = (a[1] + np.outer(ts, np.ones(ns)) * u[1] + np.outer(np.ones(nt), off) * n[1]).astype(np.float32)
    strip = cv2.remap(gray, X, Y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=np.nan)
    strip = cv2.GaussianBlur(strip, (0, 0), sigmaX=0.8 * q, sigmaY=0.8)
    g = np.gradient(strip, axis=1)                 # along the offset: + where it gets brighter

    # Coarse: the average profile. The ruler need not lie exactly along the drawn line, so
    # average in a few chunks and take the edge pair every chunk agrees on.
    prof = np.nanmedian(g, axis=0)
    if not np.all(np.isfinite(prof)):
        prof = np.nan_to_num(prof)
    best = None
    i0 = int(np.argmin(np.abs(off)))
    peak = float(np.max(np.abs(prof))) or 1.0
    for sign in (1.0, -1.0):                       # 1: ruler brighter than what is round it
        lo = sign * prof[: i0 + 3]                 # rising edge at or left of the line
        hi = -sign * prof[max(0, i0 - 2):]         # falling edge at or right of it
        j1 = int(np.argmax(lo))
        j2 = int(np.argmax(hi)) + max(0, i0 - 2)
        sc = min(lo[j1], hi[j2 - max(0, i0 - 2)])
        if j2 - j1 >= 5 * q and sc > 0.25 * peak and (best is None or sc > best[0]):
            best = (sc, sign, j1, j2)
    if best is None:
        return none
    _, sign, j1, j2 = best

    # Fine: each edge per sample, sub-pixel, within a few px of the coarse place; then a
    # straight-line fit that throws out the samples a tick or a shadow pulled away.
    #
    # Tick marks run in from the edges, close together, and a sample that lands on one sees
    # the edge where the tick ends — further in. Too many samples land on ticks for the fit to
    # throw them all out, and in a tilted photo the ticks crowd together toward the far end,
    # so the pull grows along the ruler and tilts the fitted edge: measured on rendered
    # rulers, 2 mm of false taper across a 700 mm part. A tick can only pull the edge IN, so
    # each few px of edge keeps its outermost sample — the gap between two ticks.
    out, ses = [], []
    win = max(3 * q, int(round(0.25 * (j2 - j1))))
    # Sub-pixel: the centroid of the gradient peak's upper half, on samples a quarter-px
    # apart. (A parabola through three whole-px samples is pulled toward the nearest whole
    # px, by an amount that changes as the edge drifts across the pixel grid along its
    # length — enough, on rendered rulers, to tilt the fitted edge by a tenth of a px and
    # the squared-up photo by a degree.)
    hw = int(1.5 * q)
    for j, pol in ((j1, sign), (j2, -sign)):
        s = np.full(nt, np.nan)
        for i in range(nt):
            lo_, hi_ = max(1, j - win), min(ns - 1, j + win + 1)
            seg = pol * g[i, lo_:hi_]
            if seg.size < 3 or not np.all(np.isfinite(seg)):
                continue
            k = int(np.argmax(seg))
            if seg[k] < 0.3 * pol * prof[j] or k < hw or k > seg.size - 1 - hw:
                continue
            w = seg[k - hw:k + hw + 1] - 0.5 * seg[k]
            w[w < 0] = 0.0
            s[i] = float(np.dot(w, off[lo_ + k - hw:lo_ + k + hw + 1]) / max(float(w.sum()), 1e-12))
        step = float(ts[1] - ts[0]) if nt > 1 else 1.0
        B = max(2, int(round(6.0 / max(step, 1e-6))))
        nb = nt // B
        sb = s[:nb * B].reshape(nb, B)
        tb = ts[:nb * B].reshape(nb, B).mean(axis=1)
        have = np.isfinite(sb).any(axis=1)
        out_s = np.full(nb, np.nan)
        if have.any():
            # Outward: away from the line for each edge (j1 lies at lower offsets than j2).
            out_s[have] = (np.nanmin(sb[have], axis=1) if j == j1 else np.nanmax(sb[have], axis=1))
        fit = _robust_line(tb, out_s)
        if fit is None or fit[3] < 0.5 or fit[2] > 1.5:
            return none
        c0, c1 = fit[0], fit[1]
        ses.append(fit[4])
        p0 = a + ts[0] * u + (c0 + c1 * ts[0]) * n
        p1 = a + ts[-1] * u + (c0 + c1 * ts[-1]) * n
        out.append([p0.tolist(), p1.tolist()])
    width_px = abs(float(np.dot(np.array(out[1][0]) - np.array(out[0][0]), n)))
    if width_px < 0.02 * L or width_px > 0.3 * L:          # rulers and tapes: 3-12% of length
        return none
    if with_error:
        return out, float(math.hypot(ses[0], ses[1]))
    return out


def _plane(H, p):
    q = np.asarray(H, float) @ np.array([p[0], p[1], 1.0])
    return q[:2] / q[2]


def ruler_report(edges, H) -> dict:
    """How rectangular the ruler is through the image -> mm map H: the angle between its
    two edges (0 for a true rectangle) and its width at each end, in mm."""
    e1 = [_plane(H, p) for p in edges[0]]
    e2 = [_plane(H, p) for p in edges[1]]
    d1, d2 = e1[1] - e1[0], e2[1] - e2[0]
    ang = math.degrees(abs(math.atan2(d1[0] * d2[1] - d1[1] * d2[0], float(np.dot(d1, d2)))))
    ang = min(ang, 180.0 - ang)
    n1 = np.array([-d1[1], d1[0]]) / max(np.hypot(*d1), 1e-12)
    w0 = abs(float(np.dot(e2[0] - e1[0], n1)))
    w1 = abs(float(np.dot(e2[1] - e1[0], n1)))
    return {"angle_deg": round(ang, 3), "width_mm": [round(w0, 2), round(w1, 2)],
            "width_change_pct": round(100.0 * abs(w1 - w0) / max(min(w0, w1), 1e-9), 2)}
