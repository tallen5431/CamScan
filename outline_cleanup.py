"""Straighten a traced outline onto the part's real edges.

A traced outline follows the part's edge pixel by pixel, so a side that is straight on the
part comes back as a wobbly run of dots: shadows, scuffs and texture along the edge each nudge
a few of them. Fixing that by dragging every dot is the job this replaces.

    straighten_outline(img, points) -> {"points", "straight", "snapped", ...}

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
3. Two sides that meet become one sharp corner where their lines cross — unless that point
   lies out beyond the trace (the sides swallowed a gently rounded corner's dots), when the
   dots round the corner stay. A rounded corner — whatever lies between two sides — keeps its
   traced dots, joined to the sides either side.

Before that, a short step out at the end of a long side — a shadow traced as part of the
part — is pulled back onto the side's line where the photo shows the edge carrying on
(reported in "jogs" and "notes"). For a Side or End profile (role=), the near-level top edge
is made one straight side.

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
        if float(np.hypot(*(q[j] - q[i]))) >= min_run:
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
                       min_run_frac: float = 0.06, fillets: bool = False,
                       snap: bool = True, exact: bool = False, jogs: bool = True,
                       role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """See the module notes. `points` are image pixel coords; so is the result. `img_bgr` may
    be None, in which case sides are straightened through their own dots (nothing snapped).

    jogs : pull a short outward step at the end of a long side (a shadow traced as part)
           back onto the side's line — see _pull_jogs. Reported in "jogs" and "notes".
    role : "side" or "end" for a Side/End profile photo: its long, near-level top edge (the
           cover's deck) becomes one straight side — see _merge_top_run."""
    res = _straighten(img_bgr, points, min_run_frac, fillets, snap, exact, jogs)
    if role not in ("side", "end"):
        return res
    # The deck of a cover is the top edge of its Side and End profiles, and every wobble left
    # in it becomes a ridge in the 3D part and a face in the STEP file: Refine left 58 dots on
    # the customer's End trace, and the deck printed touching the bed over 0.4% of its area.
    # Straightening alone keeps that edge in pieces wherever the wobble broke it, so the near-
    # level run along the top is made one side, whatever straightening made of it.
    p0 = _ring(points)
    if p0 is None:
        return res
    base = np.asarray(res["points"], float) if res else p0
    merged = _merge_top_run(base)
    if merged is None:
        return res
    a0, a1 = _area(p0), _area(merged)
    if a1 * a0 <= 0 or abs(a1 - a0) > _MAX_AREA_CHANGE * abs(a0):
        return res
    out = dict(res) if res else {"straight": 0, "snapped": 0, "rounded": 0, "kept_round": 0}
    out["points"] = merged.tolist()
    out["top_merged"] = True
    out["notes"] = list(out.get("notes") or []) + ["made the top edge one straight line"]
    return out


def _merge_top_run(p: np.ndarray) -> Optional[np.ndarray]:
    """The ring with its near-level top run (image y is down: the part lies below it) replaced
    by one straight line, or None when there is no such run.

    The run is the widest stretch of consecutive dots that stays within a band round one line,
    keeps going one way across, and has the part below it. Grown from every dot, so a corner
    dot at one end cannot spoil it, and not tied to the topmost dot: on the customer's End
    trace the highest point was the top of a sloped end, with the deck lower and 6 degrees off
    level. The band is as wide as a step pad's measured wobble (~1% of the profile's size); the
    line must stay within 15 degrees of level (that End photo, not squared up, had its deck at
    6-11; the 3D build levels it), and the run span 30% of the profile's width to count — a
    short flat at the top of a rounded end is not the deck."""
    n = len(p)
    if n < 4:
        return None
    w = float(np.ptp(p[:, 0]))
    if w <= 0:
        return None
    tol = max(3.0, 0.012 * float(np.hypot(*np.ptp(p, axis=0))))
    max_slope = math.tan(math.radians(_TOP_MAX_TILT))
    ring32 = p.astype(np.float32).reshape(-1, 1, 2)
    # The search grows a run from every dot, so its cost goes with the cube of the dots: a
    # 1132-dot trace took 16 s, a 2800-dot one minutes. It runs on dots picked evenly along
    # the outline instead — at 240 they are at most about one band apart, so a run's ends move
    # by a dot at most, and the dots between stay as they were — and the line is fitted to all
    # the dots of the run it finds.
    sub = np.arange(n)
    if n > _TOP_SEARCH_DOTS:
        arc = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(p, axis=0).T))])
        sub = np.unique(np.searchsorted(arc, np.linspace(0.0, arc[-1], _TOP_SEARCH_DOTS, endpoint=False)))
        sub = sub[sub < n]
    q = p[sub]
    k = len(q)
    if k < 4:
        return None

    best = None
    for i in range(k):
        # The line through the run so far, from running sums: a fit per added dot.
        x, y = q[i]
        sx, sy, sxx, sxy, cnt = x, y, x * x, x * y, 1
        lo, hi = x, x
        fwd = bwd = True                    # every step so far within tol of going right / left
        last = None
        j = 1
        while j < k - 2:
            xa, ya = q[(i + j) % k]
            dx = xa - q[(i + j - 1) % k][0]
            fwd, bwd = fwd and dx > -tol, bwd and dx < tol
            if not (fwd or bwd):
                break
            sx, sy, sxx, sxy, cnt = sx + xa, sy + ya, sxx + xa * xa, sxy + xa * ya, cnt + 1
            lo, hi = min(lo, xa), max(hi, xa)
            span = hi - lo
            den = cnt * sxx - sx * sx
            if span < 1e-6 or den <= 1e-9:
                break                       # straight down: a side, not a top
            m = (cnt * sxy - sx * sy) / den
            c = (sy - m * sx) / cnt
            # Over a few dots the slope is all wobble; it is judged once the run has some length.
            if span >= 0.1 * w and abs(m) > max_slope:
                break
            idx = (i + np.arange(j + 1)) % k
            if float(np.max(np.abs(q[idx, 1] - (m * q[idx, 0] + c)))) > tol:
                break
            last = (j, m, c, span)
            j += 1
        if last is None:
            continue
        j, m, c, span = last
        if span < 0.3 * w or (best is not None and span <= best[2]):
            continue
        xm = 0.5 * (q[i][0] + q[(i + j) % k][0])
        if cv2.pointPolygonTest(ring32, (float(xm), float(m * xm + c + tol)), False) <= 0:
            continue                                   # the part is above it: a bottom edge
        best = (i, j, span)
    if best is None:
        return None
    i, j, _ = best
    a, b = int(sub[i]), int(sub[(i + j) % k])
    J = (b - a) % n                                    # the run, in the full ring's dots
    run = p[(a + np.arange(J + 1)) % n]
    m, c = np.polyfit(run[:, 0], run[:, 1], 1)
    x0, x1 = p[a][0], p[b][0]
    ends = [np.array([x0, m * x0 + c]), np.array([x1, m * x1 + c])]
    keep = [p[(a + J + 1 + t) % n] for t in range(n - J - 1)]
    return np.asarray(ends + keep, float)


def _straighten(img_bgr, points, min_run_frac, fillets, snap, exact, jogs):
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
    if img_bgr is not None:
        r = min(r, 4.0 + 0.02 * max(img_bgr.shape[:2]))   # never further than the photo allows
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
    # Only at dots that are not corners: a sparse outline (one already straightened, or
    # clicked by hand) is all corners, and its "wobble" would read as the size of the part.
    v1, v2 = p - a_, b_ - p
    straightish = np.einsum("ij,ij->i", v1, v2) > np.cos(np.radians(45)) * np.hypot(*v1.T) * np.hypot(*v2.T)
    sigma = (float(np.median(np.abs(off - run_med)[straightish])) / 0.647
             if straightish.sum() >= 8 and not exact else 0.0)

    sides = _sides(p, min_run, sigma)
    if not sides:
        return None

    gx = gy = None
    if img_bgr is not None and getattr(img_bgr, "size", 0):
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gray = cv2.GaussianBlur(gray, (0, 0), 1.2)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3) / 8.0

    jog_list: List[Dict[str, Any]] = []
    if gx is not None and jogs:
        # A shadow beside the end of a side gets traced as part of it: the side runs straight,
        # steps out, and runs on — on the customer's step pad 25-30 px (13-19 mm) over its last
        # 100 mm. Splitting the side at the step keeps it; so the long side's line is carried
        # on over the step, and the step pulled back onto it where the photo shows the edge.
        p2, jog_list = _pull_jogs(p, gx, gy, sides, float(max(np.ptp(p, axis=0))))
        if jog_list:
            p = p2
            n = len(p)
            sides = _sides(p, min_run, sigma)
            if not sides:
                return None

    def verts(i, cnt):
        return p[(i + np.arange(cnt + 1)) % n]

    lines = []
    snapped = 0
    for (i, cnt) in sides:
        own = _fit_line(verts(i, cnt))
        line = own
        if gx is not None and snap:
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

    if fillets and len(lines) > 3:
        # A large, gentle rounding — the end of a step pad — is often traced as a short
        # straight piece with a corner at each end, which no single corner's fillet can
        # follow. A side much shorter than both neighbours that turns the same way at each
        # end is treated as part of one corner instead: its dots join the stretch between the
        # long sides, where an arc tangent to those two is tried against them.
        def span(t):
            i_, c_, ln_ = t
            a_, b_ = p[i_], p[(i_ + c_) % n]
            return abs(float(np.dot(b_ - a_, ln_[1])))
        lens = [span(t) for t in lines]
        k_ = len(lines)
        drop = set()
        for j in range(k_):
            jp, jn = (j - 1) % k_, (j + 1) % k_
            if jp in drop or jn in drop:
                continue
            if lens[j] >= 0.35 * min(lens[jp], lens[jn]):
                continue
            # Same turn at both ends: the short side bends from one long side to the next.
            d = [lines[q][2][1] for q in (jp, j, jn)]                        # each side's direction
            o = [p[(lines[q][0] + lines[q][1]) % n] - p[lines[q][0]] for q in (jp, j, jn)]
            d = [dd * (1.0 if float(np.dot(dd, oo)) > 0 else -1.0) for dd, oo in zip(d, o)]
            t1 = d[0][0] * d[1][1] - d[0][1] * d[1][0]
            t2 = d[1][0] * d[2][1] - d[1][1] * d[2][0]
            if t1 * t2 > 0:
                drop.add(j)
        if drop:
            lines = [t for q, t in enumerate(lines) if q not in drop]

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
    rounded = 0
    kept_round = 0
    ring32 = p.astype(np.float32).reshape(-1, 1, 2)
    tol_on = max(1.0, 1.5 * sigma)                 # a dot this near its side's line is on it
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
            if x is not None and _outside(ring32, x) > r:
                # The two sides swallowed a gently rounded corner's dots, so their lines meet
                # out beyond it: on a trace of the customer's step pad that put a sharp point
                # 41 px out on the concrete (+10 mm of length). The traced dots near the corner
                # stay instead, as a short stretch between the sides — what a corner the sides
                # did not swallow gets, and what Refine can then fit a fillet to.
                piece = _corner_dots(p, i, cnt, ni, ncnt, 0, line, nline, proj, tol_on)
                kept_round += 1
                out.extend(piece[:-1] if s_i == k - 1 and k > 1 else piece)
                if s_i == k - 1 and k > 1:
                    out[0] = piece[-1]
            elif x is not None and float(np.hypot(*(x - corner))) <= 3 * r:
                arc = None
                if fillets and gx is not None and k > 1:
                    # Each side may give at most 45% of itself to a corner, so a rounded end
                    # never eats a side.
                    u_in = line[1] * (1.0 if float(np.dot(line[1], x - start_pt)) > 0 else -1.0)
                    far = proj(p[(ni + ncnt) % n], nline)
                    u_out = nline[1] * (1.0 if float(np.dot(nline[1], far - x)) > 0 else -1.0)
                    max_t = 0.45 * min(float(np.hypot(*(x - start_pt))), float(np.hypot(*(far - x))))
                    arc = _fillet(gx, gy, x, u_in, u_out, min(max_t, 6.0 * r))
                if arc:
                    rounded += 1
                    if s_i == k - 1:
                        out[0] = arc[-1]
                        out.extend(arc[:-1])
                    else:
                        out.extend(arc)
                else:
                    out.append(x)
                    if s_i == k - 1:
                        out[0] = x
            else:
                out.append(proj(corner, line))
                out.append(proj(corner, nline))
        else:
            e1, s2_ = proj(p[end], line), proj(p[ni], nline)
            dots = [e1] + [p[(end + 1 + j) % n] for j in range(between - 1)] + [s2_]
            if max(_outside(ring32, e1), _outside(ring32, s2_)) > r:
                # A side that took in a corner dot well off its line would put that dot out on
                # its line, beyond the corner: give such dots back to the corner instead.
                dots = _corner_dots(p, i, cnt, ni, ncnt, between, line, nline, proj, tol_on)
                e1, s2_ = dots[0], dots[-1]
                kept_round += 1
            arc = None
            x = cross(line, nline) if (fillets and gx is not None and k > 1) else None
            if x is not None:
                # A short traced stretch between two sides — a corner the first trace rounded
                # or cut — may be a fillet of the two sides; it is one if the photo's edge runs
                # along the fillet better than along the dots.
                u_in = line[1] * (1.0 if float(np.dot(line[1], x - start_pt)) > 0 else -1.0)
                far = proj(p[(ni + ncnt) % n], nline)
                u_out = nline[1] * (1.0 if float(np.dot(nline[1], far - x)) > 0 else -1.0)
                gap = max(float(np.hypot(*(x - e1))), float(np.hypot(*(x - s2_))))
                if gap <= 6.0 * r:
                    max_t = 0.45 * min(float(np.hypot(*(x - start_pt))), float(np.hypot(*(far - x))))
                    arc = _fillet(gx, gy, x, u_in, u_out, min(max_t, 6.0 * r),
                                  versus=_path_edge(gx, gy, np.asarray(dots)))
            if arc:
                # A refinement of the dots, never a relocation: every point of the arc must lie
                # near the traced path it replaces (else a strong edge beside the part — the
                # gap at a truck's door — could pull the corner out to it).
                # Measured from the outline as it was: inward, a fillet may cut well into a
                # traced chamfer (3x the reach); outward, only as far as a side's own snap.
                for q_ in np.asarray(arc):
                    dmin = min(_seg_dist(q_, p[j], p[(j + 1) % n]) for j in range(n))
                    inside_ = cv2.pointPolygonTest(ring32, (float(q_[0]), float(q_[1])), False) >= 0
                    if dmin > (3.0 * r if inside_ else r):
                        arc = None
                        break
            sharp_ok = False
            if not arc and x is not None and len(dots) > 2:
                # No fillet, but the photo may show the corner SHARP and the first trace
                # simply bevelled it: then the two sides meet where their lines cross.
                if (_path_edge(gx, gy, np.asarray([e1, x, s2_])) > 1.2 * _path_edge(gx, gy, np.asarray(dots))
                        and min(_seg_dist(x, p[j], p[(j + 1) % n]) for j in range(n)) <= r):
                    sharp_ok = True
            if arc:
                rounded += 1
                piece = arc
            elif sharp_ok:
                piece = [e1, x, s2_]
            else:
                piece = dots
            out.extend(piece[:-1] if s_i == k - 1 and k > 1 else piece)
            if s_i == k - 1 and k > 1:
                out[0] = piece[-1]

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
    out_d = {"points": res.tolist(), "straight": len(lines), "snapped": snapped, "rounded": rounded,
             "kept_round": kept_round}
    if jog_list:
        out_d["jogs"] = jog_list
        out_d["notes"] = [f"pulled in a {j['px']:.0f} px bulge on the {j['side']} side near the "
                          f"{j['end']} end (likely shadow)" for j in jog_list]
    return out_d


_TOP_SEARCH_DOTS = 240     # dots the top-run search looks at (see _merge_top_run)
_TOP_MAX_TILT = 15.0      # degrees: a Side/End photo's deck further off level than this is not its top
_JOG_MAX_STRETCH = 0.20   # of the side's length (step included): a longer step is a real change of width
_JOG_MIN_STEP = 0.01      # of the part's length: below this a step is trace wobble
_JOG_MAX_STEP = 0.08      # of the part's length: above this it is a real feature, not a shadow


def _edge_along(gx, gy, a, b, nrm, reach=2.0, inward=None):
    """Median edge strength across the segment a->b (looking up to `reach` px either side of
    it, or `inward` px to the -nrm side), in the one polarity that is stronger along it —
    grey levels per px."""
    L = float(np.hypot(*(b - a)))
    k = int(min(60, max(6, L / 4)))
    base = a[None, :] + np.linspace(0.1, 0.9, k)[:, None] * (b - a)[None, :]
    offs = np.arange(-(reach if inward is None else inward), reach + 0.25, 0.5)
    qx = base[:, 0, None] + offs[None, :] * nrm[0]
    qy = base[:, 1, None] + offs[None, :] * nrm[1]
    g = _bilinear(gx, qx, qy) * nrm[0] + _bilinear(gy, qx, qy) * nrm[1]
    return max(float(np.median(np.max(sg * g, axis=1))) for sg in (1.0, -1.0))


def _side_name(v) -> str:
    return ("right" if v[0] > 0 else "left") if abs(v[0]) >= abs(v[1]) else ("bottom" if v[1] > 0 else "top")


def _pull_jogs(p, gx, gy, sides, part_len):
    """Pull a shadow's step off the end of each long side: (new ring, [jog, ...]).

    From each end of a long side, the dots that carry on past it OUTSIDE its line — by more
    than 1% of the part's length (trace wobble is less) and less than 8% (a flange is more),
    over less than 20% of the side's length — are a step out. It is pulled back onto the
    side's line (a Huber fit over the whole side, so the step cannot tilt it) only where the
    photo shows an edge along that line through the step: there the part's own edge carries
    on and the step is shadow beside it (the customer's cover: grey 68 -> 4 at the line).
    A real widening has no edge across it, a taper no step, and both are left alone."""
    n = len(p)
    ring32 = p.astype(np.float32).reshape(-1, 1, 2)
    step_min, step_max = _JOG_MIN_STEP * part_len, _JOG_MAX_STEP * part_len
    taken = set()
    edits = []                                   # (ring-ordered dots that go, what replaces them)
    found = []
    for (i, cnt) in sides:
        vv = p[(i + np.arange(cnt + 1)) % n]
        c, u = _fit_line(vv)
        if float(np.dot(u, vv[-1] - vv[0])) < 0:
            u = -u                               # along the ring's direction
        t = (vv - c) @ u
        side_len = float(t.max() - t.min())
        if side_len < 0.25 * part_len:
            continue
        nrm = np.array([-u[1], u[0]])
        mid = c + u * float(t.mean())
        if cv2.pointPolygonTest(ring32, (float(mid[0] + 3 * nrm[0]), float(mid[1] + 3 * nrm[1])), False) > 0:
            nrm = -nrm                           # point it outward
        side_edge = _edge_along(gx, gy, c + u * float(t.min() + 0.1 * side_len),
                                c + u * float(t.max() - 0.1 * side_len), nrm)

        def off(q):
            return float((q - c) @ nrm)
        for d in (1, -1):
            idx0 = (i + cnt) % n if d > 0 else i
            t_side = float((p[idx0] - c) @ u)
            ext = []
            # Dots still within a step of the line, past the side's end, are the side going on:
            # a sparse trace puts the step's own corner dot there, and on the customer's cover
            # the trace dipped 11 px in just before it stepped out.
            for _ in range(n - cnt - 2):
                j = (idx0 + d) % n
                if abs(off(p[j])) > step_min or d * float((p[j] - p[idx0]) @ u) <= 0:
                    break
                idx0 = j
                ext.append(j)
            t_end = float((p[idx0] - c) @ u)
            run_len = side_len + d * (t_end - t_side)
            k = 1
            ok = True
            while k < n - cnt - 1:
                j = (idx0 + d * k) % n
                if off(p[j]) <= 0.5 * step_min:
                    break
                # Measured against the whole side, step included.
                if d * (float((p[j] - c) @ u) - t_end) > _JOG_MAX_STRETCH / (1.0 - _JOG_MAX_STRETCH) * run_len:
                    ok = False
                    break
                k += 1
            if not ok or k == 1 or k >= n - cnt - 1:
                continue
            bulge = [(idx0 + d * m) % n for m in range(1, k)]
            j_back = (idx0 + d * k) % n
            if any(b in taken for b in bulge + ext):
                continue
            o = np.array([off(p[b]) for b in bulge])
            if not (step_min <= float(o.max()) <= step_max):
                continue
            # Where the step comes back: the segment from its last dot to the first dot back
            # inside crosses the line there (or that dot, put on the line, if it is not inside).
            q_out, q_in = p[bulge[-1]], p[j_back]
            oi, oo = off(q_in), off(q_out)
            x_out = (q_in + (q_out - q_in) * (oi / (oi - oo)) if oi < 0 < oo
                     else c + u * float(np.dot(q_in - c, u)))
            x_in = c + u * t_side
            if float(np.hypot(*(x_out - x_in))) < 4.0:
                continue
            # The edge may sit a little inside the line there: the customer's cover, darker in
            # the shadow, showed its edge 8-15 px in from the side's line over the last 100 mm.
            e = _edge_along(gx, gy, x_in, x_out, nrm, inward=step_min)
            if e < max(4.0, 0.15 * side_edge):
                continue
            # The side's run on past its end and the step both go; the side now ends where
            # the step came back.
            gone = ext + bulge
            if d < 0:
                gone = gone[::-1]
            taken.update(gone)
            edits.append((gone, [x_out]))
            at = p[bulge].mean(axis=0)
            found.append({"side": _side_name(nrm), "end": _side_name(u * d), "px": round(float(o.max()), 1),
                          "at": [round(float(at[0]), 1), round(float(at[1]), 1)]})
    if not edits:
        return p, []
    first = {e[0][0]: e[1] for e in edits}
    out = []
    for j in range(n):
        if j in first:
            out.extend(first[j])
        if j not in taken:
            out.append(p[j])
    return np.asarray(out, float), found


def _outside(ring32, q) -> float:
    """How far q lies OUTSIDE the ring (px; 0 or negative when inside)."""
    return -float(cv2.pointPolygonTest(ring32, (float(q[0]), float(q[1])), True))


def _corner_dots(p, i, cnt, ni, ncnt, between, line, nline, proj, tol):
    """The traced dots round the corner where side (i, cnt) meets the side starting at ni
    (`between` dots after the first side ends): from the last dot of the first side that still
    sits on its line (within tol) to the first dot of the second that does, the ends put on
    the lines. At most 45% of either side is given up, as a fillet may take, so a corner never
    eats a side."""
    n = len(p)
    end = (i + cnt) % n

    def off(q, ln):
        c, u = ln
        return abs(float((q - c)[0] * u[1] - (q - c)[1] * u[0]))
    a = 0
    while a < int(0.45 * cnt) and off(p[(end - a) % n], line) > tol:
        a += 1
    b = 0
    while b < int(0.45 * ncnt) and off(p[(ni + b) % n], nline) > tol:
        b += 1
    ia, ib = (end - a) % n, (ni + b) % n
    mid = [p[(ia + 1 + j) % n] for j in range(a + between + b - 1)]
    return [proj(p[ia], line)] + mid + [proj(p[ib], nline)]


# ---------------------------------------------------------------------------------------
# Refine: round the corners the photo shows are round
# ---------------------------------------------------------------------------------------
def _seg_dist(q, a, b) -> float:
    """Distance from point q to segment ab."""
    ab = b - a
    L2 = float(np.dot(ab, ab))
    t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, float(np.dot(q - a, ab)) / L2))
    return float(np.hypot(*(q - (a + ab * t))))


def _path_edge(gx, gy, pts):
    """Mean edge strength across a polyline, sampled every ~2 px."""
    tot, cnt = 0.0, 0
    for a, b in zip(pts[:-1], pts[1:]):
        L = float(np.hypot(*(b - a)))
        if L < 1e-6:
            continue
        u = (b - a) / L
        nrm = np.array([-u[1], u[0]])
        k = max(2, int(math.ceil(L / 2.0)))
        q = a + (b - a) * np.linspace(0, 1, k)[:, None]
        tot += float(np.sum(np.abs(_bilinear(gx, q[:, 0], q[:, 1]) * nrm[0] + _bilinear(gy, q[:, 0], q[:, 1]) * nrm[1])))
        cnt += k
    return tot / cnt if cnt else 0.0


def _fillet(gx, gy, X, u_in, u_out, max_t, versus=None):
    """The rounded corner the photo supports where two straight sides meet at X, as a list of
    points from the first side's tangent point to the second's — or None to keep it sharp
    (or, given `versus` — the edge strength along the traced dots now joining the two sides —
    to keep those dots).

    Every radius whose arc fits the two sides is tried (one number per corner, so the answer
    can only ever be a true fillet of THESE sides: it cannot wander onto a ridge inside the
    part or an edge beside it). An arc is kept only where the photo's edge runs along it more
    strongly than along the sharp corner it would replace."""
    cos_t = float(np.dot(-u_in, u_out))
    theta = math.acos(max(-1.0, min(1.0, cos_t)))      # interior angle at the corner
    if not (math.radians(20) < theta < math.radians(160)):
        return None
    bis = -u_in + u_out
    bis /= max(float(np.hypot(*bis)), 1e-9)
    half = theta / 2.0
    sweep = math.pi - theta

    def edge(pts, nrm):
        return float(np.mean(np.abs(_bilinear(gx, pts[:, 0], pts[:, 1]) * nrm[:, 0]
                                    + _bilinear(gy, pts[:, 0], pts[:, 1]) * nrm[:, 1])))

    n_in, n_out = np.array([-u_in[1], u_in[0]]), np.array([-u_out[1], u_out[0]])
    best = None
    R_max = max_t * math.tan(half)
    # From 8 px: below that a photo cannot tell a fillet from a sharp corner — the corner's
    # own blur reads as a small radius (sharp rectangles came back with 3-4 px "fillets").
    for R in np.arange(8.0, R_max + 0.5, 1.0):
        t = R / math.tan(half)
        C = X + bis * (R / math.sin(half))
        T1, T2 = X - u_in * t, X + u_out * t
        a1 = math.atan2(*(T1 - C)[::-1])
        a2 = math.atan2(*(T2 - C)[::-1])
        d = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi          # the short way round
        if abs(abs(d) - sweep) > 1e-3:
            continue
        m = max(3, int(math.ceil(sweep / (2.0 * math.acos(max(-1.0, 1.0 - 0.5 / R))))))   # <= 0.5 px off the true arc
        ang = a1 + d * np.linspace(0.0, 1.0, m + 1)
        arc = C + R * np.stack([np.cos(ang), np.sin(ang)], 1)
        e_arc = edge(arc, (arc - C) / R)
        k = max(3, int(math.ceil(t / 2.0)))
        s1 = T1 + (X - T1) * np.linspace(0, 1, k)[:, None]
        s2 = X + (T2 - X) * np.linspace(0, 1, k)[:, None]
        e_sharp = 0.5 * (edge(s1, np.repeat(n_in[None], k, 0)) + edge(s2, np.repeat(n_out[None], k, 0)))
        if (e_arc > 1.2 * e_sharp and e_arc >= 6.0 and (versus is None or e_arc > 1.1 * versus)
                and (best is None or e_arc > best[0])):
            best = (e_arc, arc)
    return None if best is None else [q for q in best[1]]


def refine_outline(img_bgr: np.ndarray, points: Sequence[Sequence[float]],
                   role: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Straighten, then give each corner where two sides meet the radius the photo shows.

    Straightening meets sides at a sharp point, which is wrong for a part whose corners are
    rounded. Following the photo's edge point by point to fix that was tried and rejected: on
    the customer's step pad the strongest edge near a corner was as often the ridge just
    inside the cover, the gap beside it, or the chrome below it as the cover's own edge, and
    every guard against one of those let another through. A fillet is one number per corner
    and can only ever be tangent to the two sides already fitted, so it is either the corner
    the photo shows or no change at all.

    role: "side" or "end" also makes the profile's top edge one straight side (see straighten_outline).

    Returns {"points", "straight", "snapped", "rounded"} in image pixel coords (plus "jogs",
    "notes" and "top_merged" when those apply), or None.
    """
    if img_bgr is None or not getattr(img_bgr, "size", 0):
        return None
    st = straighten_outline(img_bgr, points, role=role)
    if st is None:
        return None
    # The sides are now on the photo's edges. Fitting them to it again, from two dots each,
    # only nudged them off (measured on rounded rectangles); so the second pass keeps every
    # side's line exactly (and, the outline being straight already, allows its sides no
    # wobble — or a corner dot joins a side and tilts it) and only decides the corners.
    out = straighten_outline(img_bgr, st["points"], fillets=True, snap=False, exact=True, role=role)
    if out is None:
        return dict(st, rounded=0)
    # The second pass checks its corners against the first pass's outline, which may already
    # sit a side's snap outside the trace; held to the trace itself, a fillet there could end
    # up further out than any side may move (a 60 px corner on a noisy trace: 8.7 px with a
    # 7.7 px reach). Then the first pass's corners stand.
    p0 = _ring(points)
    if p0 is not None and st.get("points") is not None:
        ring32 = p0.astype(np.float32).reshape(-1, 1, 2)
        reach = max(4.0, 0.012 * float(np.hypot(*np.ptp(p0, axis=0))))
        reach = min(reach, 4.0 + 0.02 * max(img_bgr.shape[:2]))
        far_o = max(_outside(ring32, q) for q in np.asarray(out["points"], float))
        far_s = max(_outside(ring32, q) for q in np.asarray(st["points"], float))
        if far_o > reach and far_o > far_s:
            return dict(st, rounded=0)
    out["snapped"] = st["snapped"]
    for key in ("jogs", "notes", "top_merged"):
        if key in st:
            out[key] = st[key]
    return out


# ---------------------------------------------------------------------------------------
# Symmetric: average an outline with its own mirror image
# ---------------------------------------------------------------------------------------
_SYM_MAX_MISMATCH = 0.25      # halves this different are a different shape, not a sloppy trace


def _mirror_matrix(c, ang):
    """2x3 affine reflecting about the line through c at angle ang (radians)."""
    ca, sa = math.cos(2 * ang), math.sin(2 * ang)
    R = np.array([[ca, sa], [sa, -ca]])
    t = np.asarray(c, float) - R @ np.asarray(c, float)
    return np.hstack([R, t[:, None]])


def symmetrize_outline(points: Sequence[Sequence[float]], axis: str = "auto") -> Optional[Dict[str, Any]]:
    """Make an outline symmetric about its long or short axis.

    A part that is symmetric comes back from a photo slightly lopsided — light falls on one
    side, a shadow on the other, the trace wanders. Averaging the outline with its own mirror
    image (their signed distance fields, so straight sides stay straight and corners stay put)
    gives the shape halfway between the two halves, which is exactly symmetric.

    The axis runs through the outline's centre along its long or short principal direction,
    then is nudged (a degree or two, a percent or two) to where the halves agree best.
    axis: "long", "short" or "auto" (whichever the halves already agree about more).

    Returns {"points", "axis", "mismatch_pct", "other": {"axis", "mismatch_pct"}} in image
    pixel coords, or None. Refused (None) when the halves differ by more than 25%, which is a
    different shape, not a lopsided trace of the same one.
    """
    p = _ring(points)
    if p is None or abs(_area(p)) < 1.0:
        return None
    lo, hi = p.min(axis=0), p.max(axis=0)
    span = float(max(hi - lo))
    k = 1000.0 / max(span, 1e-9)                       # raster the long side to ~1000 px
    pad = 60
    W = int(math.ceil((hi[0] - lo[0]) * k)) + 2 * pad
    H = int(math.ceil((hi[1] - lo[1]) * k)) + 2 * pad
    q = (p - lo) * k + pad
    mask = np.zeros((H, W), np.uint8)
    cv2.fillPoly(mask, [np.round(q).astype(np.int32)], 255)
    m = cv2.moments(mask, binaryImage=True)
    if m["m00"] <= 0:
        return None
    c = np.array([m["m10"] / m["m00"], m["m01"] / m["m00"]])
    th = 0.5 * math.atan2(2 * m["mu11"], m["mu20"] - m["mu02"])   # the long principal axis

    def mismatch(ang, off):
        cc = c + off * np.array([-math.sin(ang), math.cos(ang)])
        mir = cv2.warpAffine(mask, _mirror_matrix(cc, ang), (W, H), flags=cv2.INTER_NEAREST)
        inter = np.count_nonzero(mask & mir)
        union = np.count_nonzero(mask | mir)
        return (1.0 - inter / union) if union else 1.0, cc, mir

    best = {}
    for name, base in (("long", th), ("short", th + math.pi / 2)):
        b = None
        for da in np.radians(np.arange(-3.0, 3.01, 0.5)):
            for off in np.linspace(-0.02, 0.02, 9) * 1000.0:
                mm_, cc, mir = mismatch(base + da, off)
                if b is None or mm_ < b[0]:
                    b = (mm_, cc, base + da, mir)
        best[name] = b
    if axis not in ("long", "short"):
        axis = "long" if best["long"][0] <= best["short"][0] else "short"
    other = "short" if axis == "long" else "long"
    mm_, cc, ang, mir = best[axis]
    if mm_ > _SYM_MAX_MISMATCH:
        return None

    def sdf(bm):
        inside = cv2.distanceTransform(bm, cv2.DIST_L2, 5)
        outside = cv2.distanceTransform(255 - bm, cv2.DIST_L2, 5)
        return inside - outside
    avg = sdf(mask) + sdf(mir)
    sym = np.where(avg > 0, 255, 0).astype(np.uint8)
    cnts, _ = cv2.findContours(sym, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    cnt = max(cnts, key=cv2.contourArea).astype(np.float32)
    eps = max(0.6, 0.0006 * cv2.arcLength(cnt, True))
    poly = cv2.approxPolyDP(cnt, eps, True).reshape(-1, 2).astype(float)
    if len(poly) < 3:
        return None
    out = (poly - pad) / k + lo
    return {"points": out.tolist(), "axis": axis, "mismatch_pct": round(100.0 * float(mm_), 2),
            "other": {"axis": other, "mismatch_pct": round(100.0 * float(best[other][0]), 2)}}
