"""Check a Square up against the ruler itself.

A ruler is a rectangle: its two long edges are parallel and it is the same width all the
way along. A photo taken at an angle breaks both — the edges converge and the far end looks
narrower. So once the photo is squared up, the ruler should come back to a true rectangle.
How close it comes is a measurement of how well the correction worked, made from something
already in the photo, with no calipers and no part needed. Its width, read in mm, can also
be compared with the real ruler's.

    find_ruler_edges(img_bgr, a, b) -> [[p, q], [p, q]] or None   (the two long edges, image px)
    find_ruler_edges(img_bgr, a, b, detail=True) -> {edges, sigma, partial, reason}
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


def find_ruler_edges(img_bgr, a: Sequence, b: Sequence, with_error: bool = False, detail: bool = False):
    """The ruler's two long edges, near the scale line drawn along it from `a` to `b`.

    Samples the photo square to the line all along it, finds the strip the line lies on — a
    band brighter (or darker) than both sides of it, with a sharp edge each side — and fits
    each edge as a straight line to sub-pixel. Returns [[p0, p1], [q0, q1]] in image px,
    each spanning the measured part of the ruler, or None when no clear strip is there.
    with_error: return (edges, sigma) instead, sigma the standard error of the angle between
    the two edges in radians — what the tilt found from them is uncertain by.
    detail: return a dict {edges, sigma, partial, reason} instead. `partial` is True when
    only one edge was found clearly and the other is assumed parallel to it, one coarse
    ruler width away — enough to line the photo up, not to measure a tilt along the ruler.
    `reason` says, in words, why no edges (or only one) were found.
    """
    def result(edges=None, sigma=None, partial=False, reason=None):
        if detail:
            return {"edges": edges, "sigma": sigma, "partial": bool(partial and edges), "reason": reason}
        if with_error:
            return (edges, sigma) if edges and not partial else (None, None)
        return edges if edges and not partial else None

    no_strip = "no ruler strip along the scale line"
    if img_bgr is None or getattr(img_bgr, "size", 0) == 0:
        return result(reason=no_strip)
    try:
        a = np.array([float(a[0]), float(a[1])])
        b = np.array([float(b[0]), float(b[1])])
    except (TypeError, ValueError, IndexError):
        return result(reason=no_strip)
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        return result(reason=no_strip)
    L = float(np.hypot(*(b - a)))
    if L < 60:
        return result(reason="the scale line is too short to find the ruler's edges along")
    # A line longer than the photo's diagonal is not on it; and the sample grid below grows
    # with the line, so an absurd one would be an absurd allocation.
    if L > 1.5 * max(img_bgr.shape[:2]):
        return result(reason=no_strip)
    u = (b - a) / L
    n = np.array([-u[1], u[0]])
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    R = max(20.0, 0.22 * L)                       # look this far either side of the line
    q = 4                                         # samples per px across the line
    ns = int(round(2 * R * q)) + 1
    off = np.linspace(-R, R, ns)
    nt = int(min(1600, max(40, L)))                # a sample per px along the line
    ts = np.linspace(0.04, 0.96, nt) * L

    def sample(d0, d1):
        # The strip square to the line, each row slid along by d0 + d1 * t px.
        sh = np.outer(ts, np.ones(ns)) * d1 + d0 + np.outer(np.ones(nt), off)
        X = (a[0] + np.outer(ts, np.ones(ns)) * u[0] + sh * n[0]).astype(np.float32)
        Y = (a[1] + np.outer(ts, np.ones(ns)) * u[1] + sh * n[1]).astype(np.float32)
        st = cv2.remap(gray, X, Y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=np.nan)
        st = cv2.GaussianBlur(st, (0, 0), sigmaX=0.8 * q, sigmaY=0.8)
        return st, np.gradient(st, axis=1)         # along the offset: + where it gets brighter
    strip, g = sample(0.0, 0.0)

    # The ruler need not lie exactly along the drawn line: on a customer's photo it drifted
    # 10 px across a 500 px line, which smeared each edge in the average profile below until a
    # straight feature beside the ruler outscored it. Measure that drift — how far each stretch
    # of the strip's profile is slid from the middle stretch's — and sample the strip again
    # along the ruler rather than along the line.
    drift = (0.0, 0.0)
    nc = 8
    cr = np.array_split(np.arange(nt), nc)
    pc = [np.nan_to_num(np.nanmedian(g[r_], axis=0)) if r_.size else np.zeros(ns) for r_ in cr]
    ml = int(max(3.0, 0.04 * L) * q)
    ref = pc[nc // 2]
    tc, lag = [], []
    for r_, p_ in zip(cr, pc):
        if not r_.size or not np.any(p_) or not np.any(ref):
            continue
        cc = np.correlate(p_, ref, "full")[ns - 1 - ml: ns + ml]
        k_ = int(np.argmax(cc))
        if 0 < k_ < cc.size - 1:
            den = cc[k_ - 1] - 2 * cc[k_] + cc[k_ + 1]
            k_ = k_ + (0.5 * (cc[k_ - 1] - cc[k_ + 1]) / den if den < 0 else 0.0)
        tc.append(float(ts[r_].mean()))
        lag.append((k_ - ml) / q)
    if len(tc) >= 5:
        A_ = np.vstack([np.ones(len(tc)), tc]).T
        dfit, *_ = np.linalg.lstsq(A_, np.array(lag), rcond=None)
        res_ = np.array(lag) - A_ @ dfit
        # Slid by more than a px end to end, and the stretches agree on it.
        if abs(dfit[1]) * L > 1.0 and float(np.sqrt(np.mean(res_ ** 2))) < 2.0:
            drift = (float(dfit[0]), float(dfit[1]))
            strip, g = sample(*drift)

    # Coarse: the average profile, along the ruler.
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
        return result(reason=no_strip)
    _, sign, j1, j2 = best

    # Which edge is which in the photo, for the person reading why one was not found: the
    # strip's j1 side lies toward -n. "Left/right" for a ruler running up and down the
    # photo, "top/bottom" for one running across it.
    if abs(n[0]) >= abs(n[1]):
        side_name = {j1: "left" if n[0] > 0 else "right", j2: "right" if n[0] > 0 else "left"}
    else:
        side_name = {j1: "top" if n[1] > 0 else "bottom", j2: "bottom" if n[1] > 0 else "top"}

    # Fine: each edge per sample, sub-pixel, within a few px of the coarse place; then a
    # straight-line fit that throws out the samples a tick or a shadow pulled away.
    #
    # Tick marks run in from the edges, close together, and a sample that lands on one sees
    # the edge where the tick ends — further in. Too many samples land on ticks for the fit to
    # throw them all out, and in a tilted photo the ticks crowd together toward the far end,
    # so the pull grows along the ruler and tilts the fitted edge: measured on rendered
    # rulers, 2 mm of false taper across a 700 mm part. A tick can only pull the edge IN, so
    # each few px of edge keeps its outermost sample — the gap between two ticks.
    #
    # Sub-pixel: the centroid of the gradient peak's upper half, on samples a quarter-px
    # apart. (A parabola through three whole-px samples is pulled toward the nearest whole
    # px, by an amount that changes as the edge drifts across the pixel grid along its
    # length — enough, on rendered rulers, to tilt the fitted edge by a tenth of a px and
    # the squared-up photo by a degree.)
    hw = int(1.5 * q)
    step = float(ts[1] - ts[0]) if nt > 1 else 1.0
    B = max(2, int(round(6.0 / max(step, 1e-6))))
    nb = nt // B
    tb = ts[:nb * B].reshape(nb, B).mean(axis=1)

    def edge_samples(j, pol, centre, win, gate=None):
        """Per-sample edge offsets near `centre` (one per row: a coarse index, or an index per
        row), then the outermost of each block. With `gate(i, k, outward)`, the strongest
        candidate it lets through within `win` of the centre, rather than the strongest."""
        outward = -1.0 if j == j1 else 1.0
        s = np.full(nt, np.nan)
        pad = hw if gate is not None else 0
        for i in range(nt):
            c = int(round(centre[i])) if np.ndim(centre) else j
            lo_, hi_ = max(1, c - win - pad), min(ns - 1, c + win + pad + 1)
            seg = pol * g[i, lo_:hi_]
            if seg.size < 3 or not np.all(np.isfinite(seg)):
                continue
            if gate is None:
                k = int(np.argmax(seg))
            else:
                cand = [kk for kk in range(hw, seg.size - hw) if gate(i, lo_ + kk, outward)]
                if not cand:
                    continue
                k = max(cand, key=lambda kk: seg[kk])
            if seg[k] < 0.3 * pol * prof[j] or k < hw or k > seg.size - 1 - hw:
                continue
            w = seg[k - hw:k + hw + 1] - 0.5 * seg[k]
            w[w < 0] = 0.0
            s[i] = float(np.dot(w, off[lo_ + k - hw:lo_ + k + hw + 1]) / max(float(w.sum()), 1e-12))
        sb = s[:nb * B].reshape(nb, B)
        have = np.isfinite(sb).any(axis=1)
        out_s = np.full(nb, np.nan)
        if have.any():
            out_s[have] = (np.nanmin(sb[have], axis=1) if outward < 0 else np.nanmax(sb[have], axis=1))
        return out_s

    def good(fit):
        return fit is not None and fit[3] >= 0.5 and fit[2] <= 1.5

    win = max(3 * q, int(round(0.25 * (j2 - j1))))
    fits = {}
    for j, pol in ((j1, sign), (j2, -sign)):
        fits[j] = _robust_line(tb, edge_samples(j, pol, None, win))

    dq = float(off[1] - off[0])
    rows = np.arange(nt)

    def rebuild(jg, jb):
        """The edge on the jb side, found again from the clean edge on the jg side: (fit,
        whether it is only assumed — parallel to the clean edge, a ruler's width away)."""
        polb = sign if jb == j1 else -sign
        c0, c1 = fits[jg][0], fits[jg][1]

        def assume(width):
            return (c0 + width * dq, c1, 0.0, 1.0, fits[jg][4]), True

        base = (c0 + c1 * ts - off[0]) / dq              # the clean edge, as a strip index per row
        dirb = 1.0 if jb > jg else -1.0
        # The strip lined up on the clean edge, ruler-bright up: the ruler is a plateau from
        # that edge across the scale line, and its other edge is where the plateau ends. The
        # median over the rows rides over the tick marks (dark on fewer than half the rows),
        # and anything that does not run exactly along the ruler is spread out by the lining up.
        D = int(min(ns, 1.6 * abs(jb - jg) + 8 * q))
        prof_a = np.full(D, np.nan)
        for d in range(D):
            idx = np.round(base + dirb * d).astype(int)
            okr = (idx >= 0) & (idx < ns)
            if okr.sum() >= 0.5 * nt:
                prof_a[d] = np.nanmedian(sign * strip[rows[okr], idx[okr]])
        dl = int(round(float(np.nanmedian((i0 - base) * dirb))))   # the scale line, inside the ruler
        # No plateau to walk across: the coarse width is all there is to go on.
        if not (2 * q < dl < D - 2 * q) or not np.isfinite(prof_a[2 * q:dl + 1]).any():
            return assume(jb - jg)
        ruler_level = float(np.nanmedian(prof_a[2 * q:dl + 1]))
        beyond = prof_a[dl:]
        if not np.isfinite(beyond).any():
            return assume(jb - jg)
        floor = float(np.nanmin(beyond))
        if ruler_level - floor < 0.25 * peak * 2 * q:     # no step to speak of past the line
            return assume(jb - jg)
        thr = 0.5 * (ruler_level + floor)
        below = np.nonzero(np.nan_to_num(beyond, nan=np.inf) < thr)[0]
        if not below.size:
            return assume(jb - jg)
        wb = dl + int(below[0])                           # first drop below half-way: the edge
        reach = 2 * q

        def ruler_to_background(i, k, outward):
            # Bright to dark (for a bright ruler) across the candidate: ruler-coloured just
            # inside it and background-coloured just outside. A groove's near edge steps from
            # background to darker, and fails the first half.
            ki, ko = int(k - outward * reach), int(k + outward * reach)
            if not (0 <= ki < ns and 0 <= ko < ns):
                return False
            vi, vo = sign * strip[i, ki], sign * strip[i, ko]
            lv, bv = ruler_level, floor
            return bool(abs(vi - lv) < abs(vi - bv) and abs(vo - bv) < abs(vo - lv))

        refit = _robust_line(tb, edge_samples(jb, polb, base + dirb * wb, 2 * q, gate=ruler_to_background))
        # Every sample here has passed the ruler-to-background test, so a third of the
        # ruler's length showing its edge is enough (a ruler lying partly on something as
        # bright as itself shows none along the rest).
        if refit is not None and refit[2] <= 1.5 and refit[3] >= 0.35 and abs(refit[1] - c1) < 0.05:
            return refit, False
        return assume(dirb * wb)

    partial = False
    reason = None
    ok1, ok2 = good(fits[j1]), good(fits[j2])
    if ok1 != ok2:
        # One edge is clean and the other is not: on a customer's top photo the ruler lay
        # beside a moulded rib whose highlight, a few px out, pulled that side's fit off (rms
        # 3.45 px against 0.27 for the clean side), and the whole check was thrown away for
        # it. A ruler's edges are near-parallel, so the other one is looked for again from the
        # clean one: the width from the strip lined up on the clean edge, then each sample
        # only within 2 px of that parallel and only where it steps from ruler to background.
        jg, jb = (j1, j2) if ok1 else (j2, j1)
        fits[jb], partial = rebuild(jg, jb)
        if partial:
            reason = side_name[jb] + " edge unclear"
    elif not ok1:
        return result(reason="neither of the ruler's edges is clear along the scale line")
    else:
        # Both fit cleanly — but a straight feature beside the ruler fits cleanly too: on
        # that same photo, with a shorter scale line, the left fit took the rib's highlight
        # 16 px out instead of the ruler's edge (a 2.4° convergence the ruler does not have),
        # and a dark groove 4 px off a rendered ruler's edge pulled that edge 4-5 px out all
        # along. Find each edge again from the other one. The ruler's true edge comes back
        # where it is from either side; a feature taken for an edge does not come back, so
        # the edge found again replaces it — when only one of the two moves.
        moved = {}
        for jg, jb in ((j1, j2), (j2, j1)):
            fit, assumed = rebuild(jg, jb)
            if assumed:
                continue
            shift = max(abs((fit[0] - fits[jb][0]) + (fit[1] - fits[jb][1]) * t_) for t_ in (ts[0], ts[-1]))
            if shift > 1.5:
                moved[jb] = fit
        if len(moved) == 1:
            fits.update(moved)

    out, ses = [], []
    for j in (j1, j2):
        c0, c1 = fits[j][0] + drift[0], fits[j][1] + drift[1]
        ses.append(fits[j][4])
        p0 = a + ts[0] * u + (c0 + c1 * ts[0]) * n
        p1 = a + ts[-1] * u + (c0 + c1 * ts[-1]) * n
        out.append([p0.tolist(), p1.tolist()])
    width_px = abs(float(np.dot(np.array(out[1][0]) - np.array(out[0][0]), n)))
    if width_px < 0.02 * L or width_px > 0.3 * L:          # rulers and tapes: 3-12% of length
        return result(reason="the strip along the scale line is not ruler-shaped")
    return result(out, float(math.hypot(ses[0], ses[1])), partial, reason)


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
