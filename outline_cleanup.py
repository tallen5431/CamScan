"""Straighten a traced outline onto the part's real edges.

A traced outline follows the part's edge pixel by pixel, so a side that is straight on the
part comes back as a wobbly run of dots: shadows, scuffs and texture along the edge each nudge
a few of them. Fixing that by dragging every dot is the job this replaces.

    straighten_outline(img, points) -> {"points", "straight", "snapped"}

1. Find the outline's straight sides: walk it from its sharpest corner, growing each side
   while its dots stay in a band round one line and the line neither bows nor turns. The band
   and the allowances scale with the trace's own measured wobble (what flips from dot to dot;
   a curve's steady sag does not count), so a noisy side stays one side while an arc of a few
   clean dots is left alone (measured on discs the tracer outlined, 60-280 px radius: none
   changed but the smallest, which lost a dot or two — 0.15-0.35% of its area). Too short or
   too curved to be a side: left as traced.
2. Fit each side to the photo: at points along it, look across the side for the strongest
   edge, and fit one straight line through what is found (robustly, so a scuff that wins at
   one sample does not tilt the line). The edge must keep one polarity along the side — the
   part on the same side of it everywhere — so the fit cannot hop between the part's edge and
   a ridge or groove just inside it. A side where the photo shows no consistent edge is
   straightened through its own dots instead: still a clean line, just not snapped.
3. Two sides that meet become one sharp corner where their lines cross. A rounded corner —
   whatever lies between two sides — keeps its traced dots, joined to the sides either side.

Nothing is invented: a run is only ever replaced by the line that best fits it, and the
result is refused (the original returned) if it changes the outline's area by more than a
few percent, which only a fit gone wrong would.
"""
from typing import Any, Dict, List, Optional, Sequence

import math

import cv2
import numpy as np

_MAX_POINTS = 5000          # an outline from the app has a few hundred at most
_MAX_AREA_CHANGE = 0.08     # a straightened outline is the same part; more than this is a fit gone wrong


def _ring(points) -> Optional[np.ndarray]:
    try:
        p = np.asarray(points, dtype=float).reshape(-1, 2)
    except (TypeError, ValueError):
        return None
    if not (3 <= len(p) <= _MAX_POINTS) or not np.all(np.isfinite(p)):
        return None
    if np.allclose(p[0], p[-1]):
        p = p[:-1]
    return p if len(p) >= 3 else None


def _area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _bilinear(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = img.shape
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - x0, y - y0
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x0 + 1] * fx * (1 - fy)
            + img[y0 + 1, x0] * (1 - fx) * fy + img[y0 + 1, x0 + 1] * fx * fy)


def _fit_line(pts: np.ndarray):
    """(point on line, unit direction), Huber-robust."""
    vx, vy, x0, y0 = cv2.fitLine(pts.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    return np.array([x0, y0], float), np.array([vx, vy], float)


def _snap_run(gx, gy, a, b, r):
    """Edge points found across the run a->b, or None when the photo shows no consistent edge
    there. One polarity for the whole run (see the module notes)."""
    t = b - a
    L = float(np.hypot(*t))
    t = t / L
    n = np.array([-t[1], t[0]])
    k = int(min(80, max(8, L / 6)))
    s = np.linspace(0.12, 0.88, k)                       # stay clear of the corners
    base = a[None, :] + s[:, None] * (b - a)[None, :]
    offs = np.arange(-r, r + 0.25, 0.5)
    qx = base[:, 0, None] + offs[None, :] * n[0]
    qy = base[:, 1, None] + offs[None, :] * n[1]
    g = _bilinear(gx, qx, qy) * n[0] + _bilinear(gy, qx, qy) * n[1]    # signed, across the run
    near = np.exp(-0.5 * (offs / (0.75 * r)) ** 2)[None, :]          # prefer the nearest edge
    best = None
    for sign in (1.0, -1.0):
        w = sign * g * near
        j = np.argmax(w, axis=1)
        val = w[np.arange(k), j]
        if best is None or np.median(val) > np.median(best[1]):
            best = (j, val)
    j, val = best
    floor = max(12.0, 0.35 * float(np.median(val)))
    ok = val >= floor
    if ok.sum() < max(5, 0.5 * k):
        return None
    return base[ok] + offs[j[ok], None] * n[None, :]


def _sides(p: np.ndarray, min_run: float, sigma: float) -> List[tuple]:
    """Straight sides of the ring p as (first vertex, vertex count) — the side runs from p[first]
    through the next `count` vertices, wrapping. Walks the ring from its sharpest corner, growing
    each side while its dots stay in a band round one line — a band as wide as the trace's own
    wobble — and the line does not bow; then rejoins neighbours that are one side split by a
    stray dot. What is too short or too curved to be a side is left out."""
    n = len(p)
    band = max(2.5, 3.5 * sigma)                       # how far a dot may sit off its side
    v1 = p - np.roll(p, 1, axis=0)
    v2 = np.roll(p, -1, axis=0) - p
    cosang = np.einsum("ij,ij->i", v1, v2) / np.maximum(np.hypot(*v1.T) * np.hypot(*v2.T), 1e-9)
    start = int(np.argmin(cosang))                     # sharpest turn: never begin mid-side
    q = np.concatenate([p[start:], p[:start]] * 2 + [p[start:start + 1]])   # twice round, for wraps

    def fits(i, j):
        """Do vertices q[i..j] make one straight side?"""
        seg = q[i:j + 1]
        c, u = _fit_line(seg)
        nrm = np.array([-u[1], u[0]])
        res = (seg - c) @ nrm
        if np.max(np.abs(res)) > band:
            return False
        # Its two halves must point the same way, within what the wobble can explain. This is
        # what catches an arc traced as a few clean dots, which sit well inside the band
        # (measured: every disc the tracer outlined, 60-280 px radius, came back a polygon).
        mid = (i + j) // 2
        h1, h2 = q[mid] - q[i], q[j] - q[mid]
        l1, l2 = float(np.hypot(*h1)), float(np.hypot(*h2))
        if l1 > 1e-9 and l2 > 1e-9:
            turn = math.acos(max(-1.0, min(1.0, float(np.dot(h1, h2)) / (l1 * l2))))
            allow = max(math.radians(4), 3.0 * math.atan(1.5 * sigma / min(l1, l2)))
            if turn > allow:
                return False
        if j - i >= 5:
            t = (seg - c) @ u
            L = float(t.max() - t.min())
            # Long is not enough: every chord of a big circle is long. A side must not BOW —
            # by more than the trace's wobble, AND by more than chance: over a handful of
            # noisy dots the fitted bow is mostly noise, and stopping on that broke a side
            # into pieces.
            try:
                coef, cov = np.polyfit(t, res, 2, cov=True)
            except (ValueError, np.linalg.LinAlgError):
                return True
            c2, se = float(coef[0]), float(np.sqrt(max(cov[0, 0], 0.0)))
            if abs(c2) * (L / 2.0) ** 2 > max(1.0, sigma, L / 250.0) and abs(c2) > 2.5 * se:
                return False
        return True

    found = []
    i = 0
    while i < n:
        j = i + 1
        while j + 1 <= n and fits(i, j + 1):
            j += 1
        if float(np.hypot(*(q[j] - q[i]))) >= min_run and j - i >= 2:
            found.append([i, j])
            i = j
        else:
            i += 1
    # Rejoin a side split by one stray dot: neighbours sharing a vertex that fit as one.
    merged = True
    while merged and len(found) > 1:
        merged = False
        for k in range(len(found)):
            a, b = found[k], found[(k + 1) % len(found)]
            bj, bi = (b[1], b[0]) if k + 1 < len(found) else (b[1] + n, b[0] + n)
            if a[1] == bi and bj - a[0] < n and fits(a[0], bj):
                found[k] = [a[0], bj]
                del found[(k + 1) % len(found)]
                merged = True
                break
    return [((i + start) % n, j - i) for i, j in found]


def straighten_outline(img_bgr: Optional[np.ndarray], points: Sequence[Sequence[float]],
                       min_run_frac: float = 0.06) -> Optional[Dict[str, Any]]:
    """See the module notes. `points` are image pixel coords; so is the result. `img_bgr` may
    be None, in which case sides are straightened through their own dots (nothing snapped)."""
    p = _ring(points)
    if p is None:
        return None
    a0 = _area(p)
    if abs(a0) < 1.0:
        return None
    n = len(p)
    perim = float(np.hypot(*(np.roll(p, -1, axis=0) - p).T).sum())
    diag = float(np.hypot(*np.ptp(p, axis=0)))
    r = max(4.0, 0.012 * diag)                            # how far across a side to look for its edge
    min_run = max(20.0, min_run_frac * perim)

    # The trace's own wobble, measured from its dots: how far each sits off the line through
    # its two neighbours, SIGNED. On a curve that offset is the curve's sag — the same side
    # dot after dot — and must not count as wobble, or every arc reads as a noisy straight
    # line; so only what flips from dot to dot (the offset less its running median) counts.
    a_, b_ = np.roll(p, 1, axis=0), np.roll(p, -1, axis=0)
    ch = b_ - a_
    off = ((p - a_)[:, 0] * ch[:, 1] - (p - a_)[:, 1] * ch[:, 0]) / np.maximum(np.hypot(*ch.T), 1e-9)
    run_med = np.median(np.stack([np.roll(off, k) for k in (-2, -1, 0, 1, 2)]), axis=0)
    # For independent jitter of spread s this median is 0.647 s (simulated over 200 straight
    # runs of 200 dots), so dividing by that gives s back.
    sigma = float(np.median(np.abs(off - run_med))) / 0.647

    sides = _sides(p, min_run, sigma)
    if not sides:
        return None

    gx = gy = None
    if img_bgr is not None and getattr(img_bgr, "size", 0):
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gray = cv2.GaussianBlur(gray, (0, 0), 1.2)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3) / 8.0

    def verts(i, cnt):
        return p[(i + np.arange(cnt + 1)) % n]

    lines = []
    snapped = 0
    for (i, cnt) in sides:
        own = _fit_line(verts(i, cnt))
        line = own
        if gx is not None:
            # Search across the side along the line through its own dots, between its ends.
            c, u = own
            vv = verts(i, cnt)
            t = (vv - c) @ u
            a, b = c + u * float(t.min()), c + u * float(t.max())
            e = _snap_run(gx, gy, a, b, r)
            if e is not None:
                c2, u2 = _fit_line(e)
                mid = (a + b) / 2.0
                gap = abs((mid - c2)[0] * u2[1] - (mid - c2)[1] * u2[0])
                if abs(float(np.dot(u2, u))) > math.cos(math.radians(8)) and gap <= r:
                    line = (c2, u2)
                    snapped += 1
        lines.append((i, cnt, line))

    def proj(q, line):
        c, u = line
        return c + u * float(np.dot(q - c, u))

    def cross(l1, l2):
        (c1, u1), (c2, u2) = l1, l2
        den = u1[0] * u2[1] - u1[1] * u2[0]
        if abs(den) < math.sin(math.radians(10)):
            return None
        w = c2 - c1
        s_ = (w[0] * u2[1] - w[1] * u2[0]) / den
        return c1 + u1 * s_

    # Walk the ring: each side is its two ends on its line; what lies between two sides (a
    # rounded corner) keeps its traced dots; two sides that meet directly meet where their
    # lines cross.
    out: List[np.ndarray] = []
    k = len(lines)
    for s_i in range(k):
        i, cnt, line = lines[s_i]
        ni, ncnt, nline = lines[(s_i + 1) % k]
        end = (i + cnt) % n
        start_pt = proj(p[i], line)
        if not out:
            out.append(start_pt)
        between = (ni - end) % n
        if k == 1:
            between = (i - end) % n
        if between == 0:
            x = cross(line, nline)
            corner = p[end]
            if x is not None and float(np.hypot(*(x - corner))) <= 3 * r:
                out.append(x)
                if s_i == k - 1:
                    out[0] = x
            else:
                out.append(proj(corner, line))
                out.append(proj(corner, nline))
        else:
            out.append(proj(p[end], line))
            out.extend(p[(end + 1 + np.arange(between - 1)) % n])
            if s_i < k - 1:
                out.append(proj(p[ni], nline))
            else:
                out[0] = proj(p[ni], nline) if k > 1 else out[0]

    res_list = [out[0]]
    for v in out[1:]:
        if float(np.hypot(*(v - res_list[-1]))) > 0.5:
            res_list.append(v)
    if len(res_list) > 1 and float(np.hypot(*(res_list[0] - res_list[-1]))) <= 0.5:
        res_list.pop()
    res = np.asarray(res_list)
    if len(res) < 3:
        return None
    a1 = _area(res)
    if a1 * a0 <= 0 or abs(a1 - a0) > _MAX_AREA_CHANGE * abs(a0):
        return None
    return {"points": res.tolist(), "straight": len(lines), "snapped": snapped}
