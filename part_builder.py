"""Build a printable part from several traced views — not just one extruded outline.

WHY THIS EXISTS
---------------
solid_export makes a part from ONE outline pushed straight through a depth. That is the whole
part only when the shape is the same all the way along. The running-board step pad that drove
this work is not: its toe is chamfered and rounded (visible only from the side and the end),
and the inside has to fit over the board it caps — a shape set by the BOARD, not by the
cover's outside minus a wall.

The standard way to get such a part from photographs is the draughtsman's: take the outline
from each orthographic view, push each one straight through the part, and keep only what all
of them agree is material. A top outline intersected with a side profile gives the tapered
toe; intersecting the end profile as well gives the rounded cross-section. Then cut the
cavity — traced from its own view, grown by a fit clearance — out of that.

FRAME
-----
    X = length, Y = width, Z = height (up)
    top  view is X-Y      side view is X-Z      end view is Y-Z

Each view is traced in its own photo, at its own scale and orientation. Each is first turned
by the small tilt it was photographed at (a degree or two is normal for a hand-held shot, and
on a 700 mm part each degree adds 12 mm to the width a bounding box reads), then normalised
to its bounding box (origin at the box's minimum), with the long axis laid along X by default
for top and side views, and the user's rotate/flip applied on top. The views are then
registered by extent: the top view sets length and width, the side view (else the end view)
sets height, unless the request says which view to trust for an axis. A view's width or
height is read across its length, ignoring the widest tenth, so one blob of shadow in a
trace does not set the size of the whole part. A view that disagrees with the one setting an
axis by up to FIT_TOLERANCE is fitted to it and the fit is reported. A photo has one scale,
so when its other axis is its own the whole view is rescaled, not just the shared axis —
except on an axis the user chose a view for, which moves only that size; beyond
FIT_TOLERANCE the build is refused and the message names both views.

The booleans are done by manifold3d, which keeps every result a closed, consistently wound
solid. Every mesh is still checked with solid_export.mesh_report before it is returned.
"""
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional
import math

import numpy as np

try:
    import manifold3d as mf
except ImportError:  # pragma: no cover - reported to the caller as "unavailable"
    mf = None

from solid_export import MIN_WALL_MM, _print_orientation, build_shell, manifold_tris, mesh_report, signed_area

ROLES = ("top", "side", "end")
# (u axis, v axis, normal axis) of each view in X=0, Y=1, Z=2.
AXES = {"top": (0, 1, 2), "side": (0, 2, 1), "end": (1, 2, 0)}
AXIS_NAMES = ("length", "width", "height")
# Who sets each axis, in order of preference. The top view is usually the best-measured
# photo (the reference lies flat on the face being traced), so it leads where it can.
AXIS_SOURCES = {0: ("top", "side"), 1: ("top", "end"), 2: ("side", "end")}

FIT_TOLERANCE = 0.08          # views may disagree by this much before the build is refused
FIT_WARN = 0.03               # a fit past this is a warning: 3% of 700 mm is 21 mm of plastic
LARGE_MISMATCH = 0.25         # past this, views disagreeing is not one photo's scale slip
KNOWN_TOLERANCE = 0.30        # a typed size this far off every photo is a typo, not a fix
MAX_CLEARANCE_MM = 10.0
MAX_POINTS = 20000
THIN_WALL_MM = 0.8            # two nozzle widths: thinner walls print unreliably
_MARGIN = 1.0                 # prisms overshoot the part box so faces never coincide
# A tilt is removed only in this range. Under 0.3 degrees it changes a 175 mm width by less
# than 1 mm; over 15 it is no longer a hand-held wobble but a view that is turned on purpose
# or an outline whose long side is not its edge (a wedge, a round).
TILT_MIN_DEG, TILT_MAX_DEG = 0.3, 15.0
BULGE_WARN = 0.05             # a box this much over the outline's usual width is a blob
_BAND_PCT = 90                # the widest tenth of an outline's length is taken as stray
FLAT_TILT_DEG = 3.0           # a top edge leaning less than this is a level deck with noise
BED_CONTACT_MIN = 0.5         # less of the deck than this on the bed is a print that fails
_LAYER_MM = 0.2              # a typical first/last layer height
_RIM_SCAN_MM = 3.0            # a ragged rim closes within this: 0.5 mm of trace noise needed 1 mm


class PartError(ValueError):
    """A reason the part cannot be built, worded for the person building it. `detail`, when
    set, is the same reason as data ({view, axis, have_mm, want_mm, setter}), so the dialog
    can offer to fix it without reading the sentence. `info`, set by build_part once the views
    are read, holds what it measured ({deskew, measured_mm}): a refusal quoting 185.2 mm for an
    End whose row says 181.4 mm has to be able to show where the other number came from."""

    def __init__(self, message: str, detail: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.detail = detail
        self.info: Optional[Dict[str, Any]] = None


def available() -> bool:
    return mf is not None


# ---------------------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------------------
def _ring(pts, what: str) -> np.ndarray:
    try:
        a = np.asarray(pts, dtype=float).reshape(-1, 2)
    except (TypeError, ValueError):
        raise PartError(f"The {what} outline is not a list of points.")
    if len(a) > MAX_POINTS:
        raise PartError(f"The {what} outline has too many points ({len(a)}).")
    if len(a) < 3 or not np.all(np.isfinite(a)):
        raise PartError(f"The {what} outline needs at least three finite points.")
    if np.allclose(a[0], a[-1]):
        a = a[:-1]
    if len(a) < 3 or abs(signed_area(a)) < 1e-6:
        raise PartError(f"The {what} outline encloses no area.")
    return a


def _quarter_turns(q: np.ndarray, n: int) -> np.ndarray:
    for _ in range(n % 4):
        q = np.column_stack([-q[:, 1], q[:, 0]])      # +90 degrees, counter-clockwise
    return q


def _rotate(q: np.ndarray, deg: float, c) -> np.ndarray:
    """Points turned `deg` counter-clockwise about c."""
    t = math.radians(deg)
    co, si = math.cos(t), math.sin(t)
    d = np.asarray(q, float) - c
    return np.column_stack([d[:, 0] * co - d[:, 1] * si, d[:, 0] * si + d[:, 1] * co]) + c


def _hull(q: np.ndarray) -> np.ndarray:
    """Convex hull, counter-clockwise (Andrew's monotone chain)."""
    p = np.unique(np.asarray(q, float), axis=0)
    if len(p) < 3:
        return p

    def half(seq):
        out: List[np.ndarray] = []
        for x in seq:
            while len(out) >= 2:
                a, b = out[-2], out[-1]
                if (b[0] - a[0]) * (x[1] - a[1]) - (b[1] - a[1]) * (x[0] - a[0]) > 0:
                    break
                out.pop()
            out.append(x)
        return out

    lower, upper = half(p), half(p[::-1])
    return np.array(lower[:-1] + upper[:-1])


def _tilt_deg(q: np.ndarray) -> float:
    """How far an outline is turned off square in its photo, in degrees, or 0 to leave it.

    The minimum-area rectangle (one side always lies on a hull edge) gives the angle, folded
    into (-45, 45]. It is used only when it is a small tilt AND turning by it shrinks the
    bounding box by at least 1%: a round or irregular outline has no edge to square, and
    on those the 'best' angle is noise. A 700 x 175 outline at 0.3 degrees already reads 2%
    larger in area, so the threshold does not hide a tilt that matters."""
    h = _hull(q)
    if len(h) < 3:
        return 0.0
    e = np.roll(h, -1, axis=0) - h
    ang = np.degrees(np.arctan2(e[:, 1], e[:, 0]))
    ang = -((-ang + 45.0) % 90.0 - 45.0)
    w0, h0 = np.ptp(h, axis=0)
    a0 = best_a = float(w0 * h0)
    best = 0.0
    for a in np.unique(np.round(ang, 6)):
        w, hh = np.ptp(_rotate(h, -float(a), np.zeros(2)), axis=0)
        if w * hh < best_a:
            best, best_a = float(a), float(w * hh)
    if TILT_MIN_DEG <= abs(best) <= TILT_MAX_DEG and best_a <= 0.99 * a0:
        return best
    return 0.0


def _view_number(value, default: float, what: str) -> float:
    if value is None or value == "":
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise PartError(f"The {what} must be a number.")
    if not math.isfinite(f):
        raise PartError(f"The {what} must be a number.")
    return f


class View:
    """One traced outline, oriented into its (u, v) plane with the bounding box at 0."""

    def __init__(self, role: str, pts, rot: int = 0, flip_h: bool = False,
                 flip_v: bool = False, auto: bool = True, scale=1.0, rot_deg=0.0,
                 keep_tilt: bool = False):
        self.role = role
        raw = _ring(pts, f"{role} view")
        # The dialog's 'Match' buttons rescale one photo to agree with another: the factor
        # goes in before anything else, as if the photo had been scaled that way.
        self.factor = _view_number(scale, 1.0, f"{role} view's scale")
        if not (0.2 <= self.factor <= 5.0):
            raise PartError(f"The {role} view's scale factor must be between 0.2 and 5.")
        self.rot_deg = _view_number(rot_deg, 0.0, f"{role} view's fine turn")
        if abs(self.rot_deg) > 45.0:
            raise PartError(f"The {role} view's fine turn must be within 45 degrees; use the "
                            f"quarter-turn button for more.")
        raw_s = raw * self.factor
        self.center = raw_s.mean(axis=0)
        self.tilt = _tilt_deg(raw_s) if (auto and not keep_tilt) else 0.0
        w, h = np.ptp(_rotate(raw_s, -self.tilt, self.center), axis=0)
        # A top or side view of a long part is laid along X unless told otherwise; the user's
        # quarter-turns are applied on top of this, so the preview shows the combined result.
        self.auto_rot = 1 if (auto and role in ("top", "side") and h > w) else 0
        self.turns = (self.auto_rot + int(rot)) % 4
        self.flip_h, self.flip_v = bool(flip_h), bool(flip_v)
        # The fine turn is about the outline's centre as the dialog shows it (after the
        # quarter-turns and flips), so + always turns the drawing the same way.
        self.center_shown = self._turn_flip(self.center[None, :])[0]
        base = self._orient(raw)
        self.lo = base.min(axis=0)
        self.pts = base - self.lo
        self.scale = np.ones(2)
        self.shift = np.zeros(2)          # where _extend_side or a robust fit moved it

    def _turn_flip(self, q: np.ndarray) -> np.ndarray:
        q = _quarter_turns(np.asarray(q, float), self.turns)
        if self.flip_h:
            q = q * [-1.0, 1.0]
        if self.flip_v:
            q = q * [1.0, -1.0]
        return q

    def _orient(self, q: np.ndarray) -> np.ndarray:
        q = np.asarray(q, float) * self.factor
        if self.tilt:
            q = _rotate(q, -self.tilt, self.center)
        q = self._turn_flip(q)
        if self.rot_deg:
            q = _rotate(q, self.rot_deg, self.center_shown)
        return q

    def place(self, q: np.ndarray) -> np.ndarray:
        """Another outline from the SAME photo, put through this view's exact transform."""
        return (self._orient(q) - self.lo) * self.scale + self.shift

    @property
    def extent(self) -> np.ndarray:
        return np.ptp(self.pts, axis=0)

    def stretch(self, axis_index: int, factor: float):
        self.pts = self.pts.copy()
        self.pts[:, axis_index] *= factor
        self.scale[axis_index] *= factor

    def rescale(self, factor: float):
        self.stretch(0, factor)
        self.stretch(1, factor)

    def move(self, axis_index: int, by: float):
        self.pts = self.pts.copy()
        self.pts[:, axis_index] += by
        self.shift[axis_index] += by


def _stations(p: np.ndarray, n: int = 256):
    """The outline's lowest and highest v at n stations along u: (xs, lo_s, hi_s, ok, u0,
    span), or None for an outline with no length. `ok` marks the stations it crosses."""
    a, b = p, np.roll(p, -1, axis=0)
    ua, ub, va, vb = a[:, 0], b[:, 0], a[:, 1], b[:, 1]
    u0, u1 = float(p[:, 0].min()), float(p[:, 0].max())
    span = u1 - u0
    if span <= 0:
        return None
    du = np.where(ub != ua, ub - ua, 1.0)
    xs = u0 + (np.arange(n) + 0.5) / n * span
    lo_s = np.full(n, np.nan)
    hi_s = np.full(n, np.nan)
    for i, x in enumerate(xs):
        hit = ((ua <= x) & (x < ub)) | ((ub <= x) & (x < ua))
        if hit.any():
            vv = va[hit] + (x - ua[hit]) * (vb[hit] - va[hit]) / du[hit]
            lo_s[i], hi_s[i] = vv.min(), vv.max()
    return xs, lo_s, hi_s, np.isfinite(lo_s), u0, span


# A stretch of an outline wider than its median by BULGE_WARN, over no more than this much of
# its length, is a blob, not the part's shape: a shadow 18 mm proud of a 155 mm top over 19% of
# its length passed the 90th-percentile band unseen, while a part that is simply wider at one
# end (a taper) is wide over half its length or more.
_BULGE_SPAN_MAX = 0.35


def _lone_bulge(pts: np.ndarray):
    """For a view that sets its cross size with nothing to check it against: (box, median,
    where) when the outline is a blob wider than its usual width, else None. `median` is the
    median of the per-station widths; `where` the fraction of the length at the widest."""
    p = np.asarray(pts, float)
    st = _stations(p)
    if st is None:
        return None
    xs, lo_s, hi_s, ok, u0, span = st
    if ok.sum() < 8:
        return None
    w = (hi_s - lo_s)[ok]
    med = float(np.median(w))
    box = float(np.ptp(p[:, 1]))
    if med <= 0 or box - med <= BULGE_WARN * med:
        return None
    wide = w > med * (1.0 + BULGE_WARN / 2.0)
    if not wide.any() or float(np.mean(wide)) > _BULGE_SPAN_MAX:     # a spike between stations
        return None
    # A blob stands proud as a plateau: most of the wide stations reach near the box. A part
    # that tapers 155 -> 175 mm passes through every width on the way (42% of them in the top
    # quarter of the excess, against all of them for a shadow step).
    if float(np.mean(w[wide] >= box - 0.25 * (box - med))) < 0.5:
        return None
    i = int(np.argmax(np.where(ok, hi_s - lo_s, -np.inf)))
    return box, med, float((xs[i] - u0) / span)


def _band(pts: np.ndarray, n: int = 256):
    """How far an outline usually reaches across, read at n stations along it.

    Returns (lo, hi, where, at): lo is the 10th percentile of the lowest edge over the
    stations and hi the 90th percentile of the highest, so a stray blob over less than a
    tenth of the length moves neither; `where` is the fraction of the length at which the
    outline sticks out furthest past [lo, hi], and `at` is that station's u."""
    p = np.asarray(pts, float)
    st = _stations(p, n)
    if st is None:
        v = p[:, 1]
        return float(v.min()), float(v.max()), 0.0, float(p[:, 0].min())
    xs, lo_s, hi_s, ok, u0, span = st
    if not ok.any():
        v = p[:, 1]
        return float(v.min()), float(v.max()), 0.0, u0
    lo = float(np.percentile(lo_s[ok], 100 - _BAND_PCT))
    hi = float(np.percentile(hi_s[ok], _BAND_PCT))
    out = np.where(ok, np.maximum(hi_s - hi, lo - lo_s), -np.inf)
    i = int(np.argmax(out))
    return lo, hi, float((xs[i] - u0) / span), float(xs[i])


def _hollow(solid, vs, size, wall: float, info: Dict[str, Any]):
    """A cover of a shaped part: `wall` thick everywhere, open underneath.

    The part is the box cut by each view's prism, so the inside — the part shrunk by the
    wall — is the same intersection with every view's outline shrunk inward by the wall
    (shrinking distributes over intersection, and shrinking a prism along its length leaves
    it a prism). That inside is then carried straight down out of the bottom, so the cover
    opens underneath, as a cover over a board does."""
    try:
        w = float(wall)
    except (TypeError, ValueError):
        raise PartError("The wall thickness must be a number.")
    if not (math.isfinite(w) and w >= MIN_WALL_MM):
        raise PartError(f"A wall under {MIN_WALL_MM:.1f} mm cannot print.")
    X, Y, Z = size
    if 2 * w >= min(X, Y) or w >= Z:
        raise PartError("The wall is as thick as the part, so it would be solid. Use a thinner "
                        "wall, or leave the wall empty for a solid part.")
    # The inside, carried straight down out of the bottom so the cover opens underneath: in
    # each view that looks at the part from the side, the space under the inside's floors is
    # inside too (see _open_below). This used to extend the inside's lowest slice — taken
    # 0.01 mm above its floor — downward. That thin layer left edges where four faces met, so
    # round covers often failed to close, and a piece of the inside with a higher floor (a
    # stepped underside) stayed sealed inside the print.
    inner = mf.Manifold.cube([X - 2 * w, Y - 2 * w, Z - w + 2 * _MARGIN]).translate([w, w, -2 * _MARGIN])
    for role, view in vs.items():
        cs = _cross_section(view.pts).offset(-w, mf.JoinType.Round)
        if cs.is_empty():
            raise PartError(f"The {role} view is thinner than two walls, so there is no inside.")
        if AXES[role][1] == 2:                         # this view's up is the part's height
            cs = _open_below(cs, -3 * _MARGIN)
        n_ax = AXES[role][2]
        inner = inner ^ _prism(cs, role, -_MARGIN, size[n_ax] + _MARGIN)
    above = inner ^ mf.Manifold.cube([X, Y, Z]).translate([0.0, 0.0, 0.0])
    if inner.is_empty() or above.is_empty() or above.volume() <= 0:
        raise PartError("The views leave no inside for that wall — try a thinner wall.")
    shell = solid - inner
    if shell.is_empty() or shell.volume() <= 0:
        raise PartError("Hollowing left nothing — try a thinner wall.")
    # Which way up it downloads is said once the final turn is known (see _orientation): this
    # used to say "turned plate-down" here, before the user's flag could turn it back.
    info.update(kind="shell", wall_mm=w)
    return shell


def _shell_note(wall: float) -> str:
    return "Hollow, {:.1f} mm walls, open underneath".format(wall)


def _orientation(shell: bool, turned: bool, for_print: bool, flat_ok: bool = True) -> str:
    """Which way up the file is, in words — the same words whatever made it that way.

    `flat_ok` False: the deck does not sit on the bed (see the bed-contact check). The status
    said "ready to print (no supports)" right under "Only 2% of the deck lies on the bed",
    and a 700 mm print started on that comes loose."""
    if shell:
        if turned:
            if for_print and not flat_ok:
                return "plate-down, open side up — but the deck is not flat on the bed, not ready to print"
            return ("plate-down, open side up — ready to print (no supports)" if for_print
                    else "plate-down, open side up")
        if for_print:
            return "open side down — the deck needs supports"
        return "as traced (Z up), open side down — for CAD"
    return "upside down" if turned else "as traced"


def _runs(mask: np.ndarray) -> List[np.ndarray]:
    """Index runs of True in a ring's mask, in ring order (a run may wrap past the end)."""
    n = len(mask)
    if mask.all():
        return [np.arange(n)]
    start = int(np.argmin(mask))                     # a False: no run straddles it
    runs, cur = [], []
    for i in (np.arange(n) + start) % n:
        if mask[i]:
            cur.append(int(i))
        elif cur:
            runs.append(np.array(cur))
            cur = []
    if cur:
        runs.append(np.array(cur))
    return runs


def _flatten_top(view: "View", level: Optional[float] = None):
    """Level the near-level top edge of a side or end profile. Returns (level, moved_mm), or
    (None, 0) when it has no such edge.

    A hollow cover prints deck-down, and the deck is the top edge of the side and end traces.
    Ordinary trace noise (0.35 mm, under a pixel) left only a few specks of it on the bed:
    measured 18 of 122,500 mm^2 within 0.2 mm. The band is the points within a tolerance of
    the edge's middle (1% of the height, or three times the edge's own noise, kept within
    0.5-1.5 mm); a run of them along the edge that leans under FLAT_TILT_DEG is set to one
    level, the 90th percentile of the band, so one high dot does not set the height. Between
    the first and last dots in the upper half of the band everything is raised to it; outside
    them dots are only lowered, so a rounded or sloped shoulder keeps its shape (a 14 mm round
    loses 0.25 mm over its first 2.6 mm, no more)."""
    p = view.pts
    v = p[:, 1]
    H = float(np.ptp(v))
    if H <= 0:
        return None, 0.0
    _lo, top, _w, _at = _band(p)
    near = v >= top - 1.5
    if not near.any():
        return None, 0.0
    mid = float(np.median(v[near]))
    noise = 1.4826 * float(np.median(np.abs(v[near] - mid)))
    tol = float(np.clip(max(0.01 * H, 3.0 * noise), 0.5, 1.5))
    # Half as wide again around the edge's middle, so a dot that strays past 3 sigma does not
    # cut the edge into pieces (one in 370 does, and an edge has hundreds of dots).
    band = np.abs(v - mid) <= 1.5 * tol
    if band.sum() < 2:
        return None, 0.0
    L = float(np.percentile(v[band], 90)) if level is None else float(level)
    span_min = max(5.0, 0.02 * float(np.ptp(p[:, 0])))
    lean = math.tan(math.radians(FLAT_TILT_DEG))
    new = v.copy()
    found = False
    for idx in _runs(band):
        x = p[idx, 0]
        if len(idx) < 2 or float(np.ptp(x)) < span_min:
            continue
        if abs(float(np.polyfit(x - x.mean(), v[idx], 1)[0])) > lean:
            continue
        found = True
        at = np.nonzero(v[idx] >= mid - tol / 2.0)[0]
        if len(at):
            new[idx[at[0]:at[-1] + 1]] = L
        new[idx] = np.minimum(new[idx], L)
    if not found:
        return None, 0.0
    moved = float(np.max(np.abs(new - v)))
    view.pts = p.copy()
    view.pts[:, 1] = new
    return L, moved


def _thin_rim(solid) -> Optional[float]:
    """The open edge of a cover, one print layer above where its rim closes: the thickness it
    thins to there when that is under THIN_WALL_MM, else None. A rounded outer bottom corner
    feathers the wall at the rim, because the inside is carried straight down while the
    outside curves in under it: r = 6 under a 2 mm wall leaves 0.9 mm of rim under 0.8 mm.

    Read a layer up, not at the rim itself: a noisy bottom edge leaves the rim ragged — at a
    fixed 0.05 mm only specks of a square 3 mm wall were cut (0-12 of 5,250 mm^2 at 0.35 mm of
    noise) and opening removed them all — so the slice is taken where it has first closed into
    the same loops as the wall higher up. Thinning that ends within a layer (r = 3 under a 3 mm
    wall: 0.11 mm) is a sliver the slicer does not print, and is not reported."""
    bb = solid.bounding_box()
    z0, z1 = bb[2], bb[5]
    if z1 - z0 <= 2 * _LAYER_MM:
        return None

    def shape(cs):
        return len(cs.decompose()), len(cs.to_polygons())

    want = shape(solid.slice(z0 + (z1 - z0) / 2.0))
    z = z0 + 0.05
    stop = z0 + min(_RIM_SCAN_MM, (z1 - z0) / 2.0)
    while z < stop:
        cs = solid.slice(z)
        if cs.area() > 0 and shape(cs) == want:
            break
        z += 0.05
    else:
        return None
    cs = solid.slice(z + _LAYER_MM)
    a0 = cs.area()
    if a0 <= 0:
        return None
    corners = max(1, cs.num_vert())
    for t in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, THIN_WALL_MM):
        r = t / 2.0
        opened = cs.offset(-r, mf.JoinType.Round).offset(r, mf.JoinType.Round)
        if a0 - opened.area() > corners * (1 - math.pi / 4) * r * r * 1.5 + 1e-6:
            return t
    return None


def _fillet(poly: np.ndarray, i: int, r: float, n: int = 12) -> np.ndarray:
    """The arc that rounds corner i of a polygon with radius r (smaller if the edges are
    too short for it), as points from the incoming edge to the outgoing one."""
    P, A, B = poly[i], poly[i - 1], poly[(i + 1) % len(poly)]
    la, lb = float(np.linalg.norm(A - P)), float(np.linalg.norm(B - P))
    u1, u2 = (A - P) / la, (B - P) / lb
    half = math.acos(float(np.clip(np.dot(u1, u2), -1.0, 1.0))) / 2.0
    if half <= 1e-6 or half >= math.pi / 2 - 1e-6:
        return P[None, :]
    t = min(r / math.tan(half), 0.49 * la, 0.49 * lb)
    re = t * math.tan(half)
    bis = (u1 + u2) / np.linalg.norm(u1 + u2)
    C = P + bis * (re / math.sin(half))
    p1, p2 = P + u1 * t, P + u2 * t
    a1 = math.atan2(p1[1] - C[1], p1[0] - C[0])
    a2 = math.atan2(p2[1] - C[1], p2[0] - C[0])
    d = (a2 - a1 + math.pi) % (2 * math.pi) - math.pi
    k = np.linspace(0.0, 1.0, n + 1)
    return np.column_stack([C[0] + re * np.cos(a1 + d * k), C[1] + re * np.sin(a1 + d * k)])


def _ends_profile(X: float, Z: float, slope_deg: float, radius_mm: float) -> np.ndarray:
    """A side profile (X-Z) standing in for a missing Side view: the part's length and height,
    each top corner cut back by Z*tan(slope) and rounded by the radius."""
    d = Z * math.tan(math.radians(slope_deg))
    if 2 * d >= X - 1.0:
        raise PartError("That end slope leaves no top face on a part this long and tall — use "
                        "a smaller slope.")
    poly = np.array([[0.0, 0.0], [X, 0.0], [X - d, Z], [d, Z]])
    if radius_mm <= 0:
        return poly
    return np.vstack([poly[0], poly[1], _fillet(poly, 2, radius_mm), _fillet(poly, 3, radius_mm)])


_FLOOR_SLOPE = math.tan(math.radians(30.0))


def _open_below(cs, floor: float):
    """A view's shrunk outline (the inside, seen from the side), with the space under each of
    its floors added, down to `floor`.

    A floor is an edge with the inside above it that is nearly level (under 30 degrees). A
    steeper edge with the inside above it is a wall leaning out over the bottom — the sloped
    toe of a step-pad cover — and what is under it is that wall, which must stay."""
    pieces = [cs]
    for ring in cs.to_polygons():
        r = np.asarray(ring, float)
        if signed_area(r) < 0:
            r = r[::-1]            # outer rings counter-clockwise: the inside is left of each edge
            hole = True
        else:
            hole = False
        for (x0, y0), (x1, y1) in zip(r, np.roll(r, -1, axis=0)):
            dx = (x0 - x1) if hole else (x1 - x0)       # > 0: the inside is above this edge
            if dx <= 1e-9 or abs(y1 - y0) > _FLOOR_SLOPE * dx:
                continue
            quad = np.array([[x0, y0], [x1, y1], [x1, floor], [x0, floor]])
            pieces.append(mf.CrossSection([_ccw(quad)]))
    return mf.CrossSection.batch_boolean(pieces, mf.OpType.Add)


def _extend_side(view: "View", length: float, covers: str) -> None:
    """Make a side profile of one end into a profile of the whole length: place it at its
    end, and carry its cut edge's height across the rest. The height there is read off the
    profile itself, a sliver in from the cut, so it is whatever the trace says it is."""
    pts = view.pts
    lp = float(np.ptp(pts[:, 0]))
    view.covered = min(lp, length)
    if covers == "end":
        # Its far end is the part's far end — also when it reads a little longer than the
        # part (within the fit tolerance): then its near end is what overhangs and is cut
        # off, not the shaped toe.
        pts = pts + [length - lp, 0.0]
        view.shift = view.shift + [length - lp, 0.0]
    if lp >= length:
        view.pts = pts
        return
    cs = _cross_section(pts)
    sliver = min(2.0, 0.02 * lp)
    cut = lp if covers == "start" else length - lp
    x0 = cut - sliver if covers == "start" else cut
    big = float(np.ptp(pts[:, 1])) + 10.0
    strip = mf.CrossSection.square([sliver, big]).translate([x0, float(pts[:, 1].min()) - 5.0])
    edge = (cs ^ strip).bounds()                       # (xmin, ymin, xmax, ymax)
    v0, v1 = float(edge[1]), float(edge[3])
    if not (v1 > v0):
        raise PartError("The side view of one end has no height at its cut edge.")
    if covers == "start":
        rest = mf.CrossSection.square([length - x0, v1 - v0]).translate([x0, v0])
    else:
        rest = mf.CrossSection.square([x0 + sliver, v1 - v0]).translate([0.0, v0])
    whole = cs + rest
    polys = whole.to_polygons()
    if not polys:
        raise PartError("Could not extend the side view along the part.")
    view.pts = np.asarray(max(polys, key=lambda q: abs(signed_area(np.asarray(q)))), float)


def _ccw(p: np.ndarray) -> np.ndarray:
    return p if signed_area(p) > 0 else p[::-1]


# Points closer than this are one point. A traced outline often has two dots almost on top of
# each other at a corner; kept apart, they leave the solid with slivers a few nanometres wide.
# 0.01 mm is far below anything a photo measures or a printer prints.
_WELD_MM = 0.01


def _cross_section(p: np.ndarray):
    return mf.CrossSection([_ccw(np.asarray(p, float))]).simplify(_WELD_MM)


def _prism(cs, role: str, lo: float, hi: float):
    """A cross-section in a view's (u, v), extruded along that view's normal over [lo, hi]."""
    man = mf.Manifold.extrude(cs, hi - lo).translate([0.0, 0.0, lo])
    u, v, n = AXES[role]
    M = np.zeros((3, 4))
    M[u, 0] = M[v, 1] = M[n, 2] = 1.0
    return man.transform(M)                 # manifold rewinds when the map is a reflection


def _to_view_plane(man, role: str):
    """Rotate/permute a solid so the view's (u, v, normal) become (X, Y, Z)."""
    u, v, n = AXES[role]
    M = np.zeros((3, 4))
    M[0, u] = M[1, v] = M[2, n] = 1.0
    return man.transform(M)


def _silhouette(man, role: str) -> List[List[List[float]]]:
    cs = _to_view_plane(man, role).project()
    return [np.round(np.asarray(p, float), 3).tolist() for p in cs.to_polygons()]


def _tris(man) -> np.ndarray:
    return manifold_tris(man)


# ---------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------
def _fmt1(x: float) -> str:
    """x to one decimal exactly as JavaScript's toFixed(1) gives it (half up on the binary
    value), so a number the server writes and the dialog writes reads the same."""
    return str(Decimal(float(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _known(known_mm) -> Dict[int, float]:
    """{axis: mm} from the sizes the user typed as already measured on the part."""
    out: Dict[int, float] = {}
    if not known_mm:
        return out
    if not isinstance(known_mm, dict):
        raise PartError("Known sizes must be given as length, width and height.")
    for i, nm in enumerate(AXIS_NAMES):
        val = known_mm.get(nm)
        if val is None or val == "":
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            raise PartError(f"The known {nm} must be a number.")
        if not (math.isfinite(f) and f > 0):
            raise PartError(f"The known {nm} must be more than zero.")
        out[i] = f
    return out


def _axis_order(axis_source, vs: Dict[str, "View"], info: Dict[str, Any]):
    """AXIS_SOURCES, with the views the user chose to trust put first, and the set of axes
    the user chose that way. A choice is for that one size: 'Use End' on a width line
    rescaled the whole Top under one-scale-per-photo, so its ruler-measured length jumped
    700 -> 742 mm when the user had asked only about the width."""
    order = {a: tuple(r) for a, r in AXIS_SOURCES.items()}
    chosen = set()
    if not axis_source:
        return order, chosen
    if not isinstance(axis_source, dict):
        raise PartError("Which view sets each size must be given as {length|width|height: view}.")
    for nm, role in axis_source.items():
        if nm not in AXIS_NAMES:
            raise PartError(f"'{str(nm)[:20]}' is not length, width or height.")
        if role is None or role == "":
            continue
        a = AXIS_NAMES.index(nm)
        if role not in ROLES or a not in AXES[role][:2]:
            raise PartError(f"The {str(role)[:20]} view does not show the part's {nm}.")
        if role not in vs:
            info["warnings"].append(f"The {nm} was to come from the {role} view, but there is "
                                    f"no {role} view; it comes from the usual one.")
            continue
        order[a] = (role,) + tuple(r for r in order[a] if r != role)
        chosen.add(a)
    return order, chosen


def _mismatch(role: str, axis: int, have: float, want: float, setter: str, off: float,
              tilt: Dict[str, float], pinned_by: Optional[List[int]] = None) -> PartError:
    """`tilt`: {role: degrees} each view was turned to square it. `pinned_by`: the typed axes
    that fix the setter's scale, when they do — then the setter cannot be matched to this view
    (the typed number rescales it straight back), and the message names the typed number."""
    if pinned_by and axis in pinned_by:
        src = "the {} you typed".format(AXIS_NAMES[axis])
    else:
        src = "the {} view's".format(setter)
    head = "The {} view's {} is {:.0f}% {} than {} ({:.1f} vs {:.1f} mm). ".format(
        role, AXIS_NAMES[axis], abs(off) * 100, "longer" if off > 0 else "shorter", src, have, want)
    # The row in the dialog shows the photo's bounding box; this number is after the deskew, so
    # an End on the row at 181.4 mm was refused at 185.2 mm with nothing to say why.
    turned = [(r, tilt[r]) for r in (role, setter) if tilt.get(r)]
    if turned:
        head += " ".join("The {} view was turned {:.1f}° to square it before it was measured.".format(r, abs(d))
                         for r, d in turned) + " "
    if pinned_by and axis not in pinned_by:
        head += "The {} view's size is set by the {} you typed. ".format(
            setter, " and ".join(AXIS_NAMES[a] for a in pinned_by))
    if abs(off) > LARGE_MISMATCH:
        # A 76% gap was reported as "scaled wrong" when the photo was a top view assigned as
        # Side; at this size a scale slip is the least likely cause.
        tail = ("That is too far apart for a scale slip: the {0} photo may not be a straight-on "
                "{0} view (taken at an angle, or of a different face), a trace may have a stray "
                "blob, or one photo's scale is wrong.".format(role))
    elif pinned_by:
        tail = ("Check the {0} photo's reference (or its trace for a stray blob), or match the "
                "{0} view to that size.".format(role))
    else:
        tail = ("One of those photos is scaled wrong, or a trace has a stray blob — check its "
                "reference, or match one view to the other.")
    detail = {"view": role, "axis": AXIS_NAMES[axis], "have_mm": round(have, 2),
              "want_mm": round(want, 2), "setter": setter}
    if pinned_by:
        # The dialog must not offer "Scale Top to match End" here: pressing it stored x0.888 on
        # the Top, the typed size cancelled it, and the same refusal came straight back.
        detail.update(setter_pinned=True, setter_view=setter)
        if axis in pinned_by:
            detail["setter"] = "known"
    if tilt.get(role):
        detail["deskew_deg"] = round(tilt[role], 2)
    return PartError(head + tail, detail=detail)


def build_part(views: Dict[str, Dict[str, Any]], depth_mm: Optional[float] = None,
               cavity: Optional[Dict[str, Any]] = None, clearance_mm: float = 0.0,
               wall_mm: Optional[float] = None, mirror: bool = False,
               upside_down: bool = False, preview: bool = False,
               known_mm: Optional[Dict[str, Any]] = None,
               axis_source: Optional[Dict[str, str]] = None,
               ends: Optional[Dict[str, Any]] = None, for_print: bool = True) -> Dict[str, Any]:
    """Build the part. Raises PartError with a user-facing reason when it cannot.

    views   : {"top"|"side"|"end": {"points": [[u, v], ...] in mm, "rot": quarter turns,
               "flip_h": bool, "flip_v": bool, "scale": factor, "rot_deg": fine turn,
               "keep_tilt": bool}} — any combination that sets all three axes, with depth_mm
               (or a known size) standing in for one missing axis.
    cavity  : {"points", "view", "rot", "flip_h", "flip_v", "same_photo": bool,
               "align": "bottom"|"center"|"top", "open": "through"|"min"|"max",
               "depth_mm"} — material removed, traced in `view`, grown by clearance_mm.
    wall_mm : uniform wall instead of a traced cavity: a cover, open underneath.
    mirror  : the opposite-hand part (the twin from the other side of the vehicle).
    known_mm: {length, width, height} already measured on the part: the view that sets that
              size is scaled to it, and every view's error against it is reported.
    axis_source: {length|width|height: view} — trust this view for that size.
    ends    : {slope_deg, radius_mm} for both ends when there is no Side view.
    for_print: a cover is turned plate-down, ready to print (STL). False delivers it as
              traced, Z up, for CAD; upside_down then turns it literally.
    Returns {"tris" (absent in preview), "info", "preview"}.
    """
    if mf is None:
        raise PartError("3D part building needs the manifold3d package on the server.")
    info: Dict[str, Any] = {"fits": [], "warnings": [], "kind": None, "deskew": []}
    try:
        return _build_part(info, views, depth_mm, cavity, clearance_mm, wall_mm, mirror,
                           upside_down, preview, known_mm, axis_source, ends, for_print)
    except PartError as e:
        # What was measured goes back with a refusal too, so the dialog can show it.
        if e.info is None:
            e.info = {"deskew": info["deskew"], "measured_mm": info.get("measured_mm") or {}}
        raise


def _build_part(info, views, depth_mm, cavity, clearance_mm, wall_mm, mirror, upside_down,
                preview, known_mm, axis_source, ends, for_print) -> Dict[str, Any]:
    notes: List[str] = []

    vs: Dict[str, View] = {}
    for role in ROLES:
        spec = (views or {}).get(role)
        if not spec:
            continue
        vs[role] = View(role, spec.get("points"), int(spec.get("rot") or 0),
                        bool(spec.get("flip_h")), bool(spec.get("flip_v")),
                        scale=spec.get("scale"), rot_deg=spec.get("rot_deg"),
                        keep_tilt=bool(spec.get("keep_tilt")))
        if vs[role].tilt:
            info["deskew"].append({"view": role, "deg": round(vs[role].tilt, 2)})
    # Each view's size as the builder reads it — after the deskew, before any fit — which is
    # not the photo's bounding box the dialog's rows show when a view was turned.
    info["measured_mm"] = {r: [round(float(x), 2) for x in v.extent] for r, v in vs.items()}
    tilt = {r: v.tilt for r, v in vs.items() if v.tilt}
    if not vs:
        raise PartError("Assign at least one outline to a view (Top, Side or End).")

    # A side profile of only ONE END of a long part — the toe of a step pad, say, photographed
    # close enough to trace — covers part of the length. It sets the height and shapes that
    # end; the rest of the length carries on at the height of the profile's cut edge.
    covers = ((views.get("side") or {}).get("covers") or "all") if "side" in vs else "all"
    if covers not in ("all", "start", "end"):
        raise PartError("A side view covers the whole length, its start, or its end.")
    partial_side = covers != "all"
    if partial_side and "top" not in vs:
        raise PartError("A side view of one end needs a Top view for the part's full length.")

    known = _known(known_mm)
    order, chosen = _axis_order(axis_source, vs, info)

    # Who sets each axis, and how long it is.
    size = [None, None, None]
    setter = [None, None, None]
    for axis in range(3):
        for role in order[axis]:
            if role in vs and not (partial_side and role == "side" and axis == 0):
                k = AXES[role].index(axis)
                size[axis] = float(vs[role].extent[k])
                setter[axis] = role
                break
        if setter[axis] is None and axis in known:
            size[axis], setter[axis] = known[axis], "known"
    missing = [a for a in range(3) if size[a] is None]
    if len(missing) > 1:
        raise PartError("Not enough views: add a Side or End view, or give a Top view a depth.")
    if missing:
        try:
            d = float(depth_mm)
        except (TypeError, ValueError):
            d = float("nan")
        if not (math.isfinite(d) and d > 0):
            raise PartError(f"Give the part's {AXIS_NAMES[missing[0]]} (depth), or add a view "
                            f"that shows it.")
        size[missing[0]] = d
        setter[missing[0]] = "depth"

    def _fit_entry(role, axis, have, want, by, kind, off):
        entry = {"view": role, "axis": AXIS_NAMES[axis], "pct": round(off * 100, 2) + 0.0,
                 "have_mm": round(have, 2), "want_mm": round(want, 2), "setter": by, "kind": kind}
        info["fits"].append(entry)
        if abs(off) > FIT_WARN:
            what = ("the whole {} view was rescaled to match, so its other size moved with it"
                    .format(role) if kind == "rescale" else
                    "the {} view was stretched in {} to match".format(role, AXIS_NAMES[axis]))
            src = "the {} you typed".format(AXIS_NAMES[axis]) if by == "known" else "the {} view's".format(by)
            # From the entry's own rounded numbers, rounded as the dialog's toFixed(1) does: the
            # same fit read "170.7 mm, 7.7%" on its fit line and "170.6 mm, 7.6%" here.
            text = ("The {} view's {} is {}% off {} ({} vs {} mm); {}. Check that photo's "
                    "reference.".format(role, AXIS_NAMES[axis], _fmt1(abs(entry["pct"])), src,
                                        _fmt1(entry["have_mm"]), _fmt1(entry["want_mm"]), what))
            info["warnings"].append(text)
            entry["warning"] = text            # the dialog shows the fit line once, not both

    # Sizes typed as already measured on the part come first: the view that sets each is
    # scaled to it — the whole view, as a photo has one scale, unless it sets two of them.
    pinned = set()
    bulged = set()

    def _bulge_note(s, axis, box, usual, where):
        if s in bulged:
            return
        bulged.add(s)
        info["warnings"].append(
            "The {} outline is {:.0f} mm {} near {:.0f}% of its {} than along the rest "
            "— the trace may include shadow; use Tidy/Refine. The other views were "
            "fitted to its usual {:.1f} mm.".format(
                s, box - usual, "taller" if axis == 2 else "wider", where * 100,
                AXIS_NAMES[AXES[s][0]], usual))

    governs: Dict[str, List[int]] = {}
    for axis in known:
        if setter[axis] in vs:
            governs.setdefault(setter[axis], []).append(axis)
    for role, axes in governs.items():
        view = vs[role]
        rows = []
        # A typed size sets this view's scale outright, so a Match factor on it is cancelled.
        # The fit is reported against the photo's own reading: a stale x0.888 left from a
        # Match button inflated a 13% stretch to 28%, near KNOWN_TOLERANCE, for no reason.
        f0 = view.factor
        for axis in axes:
            kk = AXES[role].index(axis)
            have, want = float(view.extent[kk]), known[axis]
            if kk == 1:
                # Read across the length, as target() does: a shadow blob 18 mm proud of a
                # 155 mm top otherwise rescaled the whole view by 11.6%, length and all.
                lo, hi, where, _at = _band(view.pts)
                usual = hi - lo
                if usual > 0 and have - usual > BULGE_WARN * usual and abs(want - usual) < abs(want - have):
                    _bulge_note(role, axis, have, usual, where)
                    have = usual
            photo = have / f0
            off = photo / want - 1.0
            if abs(off) > KNOWN_TOLERANCE:
                raise PartError(
                    "The {} view's {} is {:.0f}% off the {:.1f} mm you typed ({:.1f} mm). Check "
                    "the number, or that photo's scale.".format(
                        role, AXIS_NAMES[axis], abs(off) * 100, want, photo),
                    detail={"view": role, "axis": AXIS_NAMES[axis], "have_mm": round(photo, 2),
                            "want_mm": round(want, 2), "setter": "known"})
            rows.append((kk, axis, have, want, off, photo))
        if len(rows) == 1:
            view.rescale(rows[0][3] / rows[0][2])
            kind = "rescale"
        else:
            for kk, axis, have, want, off, photo in rows:
                view.stretch(kk, want / have)
            kind = "stretch"
        for kk, axis, have, want, off, photo in rows:
            _fit_entry(role, axis, photo, want, "known", kind, off)
        if abs(f0 - 1.0) > 1e-6:
            info["warnings"].append(
                "The {} view's Match ×{:.3f} has no effect: the {} you typed {} its size. Undo "
                "it (↶ on its row) — to fit another view, match that view instead.".format(
                    role, f0, " and ".join(AXIS_NAMES[a] for a in axes), "sets" if len(axes) == 1 else "set"))
        for kk, axis in enumerate(AXES[role][:2]):
            if setter[axis] == role:
                size[axis] = float(view.extent[kk])
        pinned.update(axes)
    for axis, k in known.items():
        if setter[axis] == "depth":
            size[axis] = k

    if partial_side:
        have, want = float(vs["side"].extent[0]), float(size[0])
        if have > want * (1.0 + FIT_TOLERANCE):
            raise PartError(
                "The side view of one end is longer than the whole part ({:.1f} vs "
                "{:.1f} mm). Set it to cover the whole length, or check its scale."
                .format(have, want))

    def fitted(role):
        # A side profile of one end is placed at its end below, not fitted.
        return [(k, a) for k, a in enumerate(AXES[role][:2])
                if setter[a] != role and not (partial_side and role == "side" and a == 0)]

    def target(axis: int, have: float):
        """What a view's `axis` must measure to agree with the view setting it, and where along
        that axis it starts. A width (or height) is read across the setter's length, so a blob
        of shadow at one end does not widen the whole part — unless this view agrees with the
        blob, which makes it real."""
        s = setter[axis]
        sv = vs[s]
        ks = AXES[s].index(axis)
        box = float(sv.extent[ks])
        if ks == 1:
            lo, hi, where, _at = _band(sv.pts)
            usual = hi - lo
            if usual > 0 and box - usual > BULGE_WARN * usual and abs(have - usual) < abs(have - box):
                _bulge_note(s, axis, box, usual, where)
                return usual, lo
        return box, 0.0

    def fit(role: str, rescale_ok: bool):
        view = vs[role]
        rows = []
        for k, axis in fitted(role):
            have = float(view.extent[k])
            if have <= 0:
                raise PartError(f"The {role} view has no {AXIS_NAMES[axis]}.")
            want, start = target(axis, have)
            off = have / want - 1.0
            if abs(off) > FIT_TOLERANCE:
                raise _mismatch(role, axis, have, want, setter[axis], off, tilt,
                                governs.get(setter[axis]))
            rows.append((k, axis, have, want, start, off))
        own = [(k, a) for k, a in enumerate(AXES[role][:2]) if setter[a] == role]
        # One photo has one scale: when a view meets another on one axis and its other axis
        # is its own, the gap on the shared axis is that photo's scale error on both. Fitting
        # only the shared axis left the other with the whole error (End at x1.05: 47.25 mm
        # tall for a 45 mm part, reported only as a width stretch). Not when the user chose
        # the other view for that axis: that choice is about the one size (see _axis_order).
        uniform = (rescale_ok and len(rows) == 1 and len(own) == 1 and own[0][1] not in pinned
                   and rows[0][1] not in chosen)
        if uniform:
            k, axis, have, want, start, off = rows[0]
            view.rescale(want / have)
            ko, ao = own[0]
            size[ao] = float(view.extent[ko])
        else:
            for k, axis, have, want, start, off in rows:
                view.stretch(k, want / have)
        for k, axis, have, want, start, off in rows:
            if start:
                view.move(k, start)
            _fit_entry(role, axis, have, want, setter[axis], "rescale" if uniform else "stretch", off)

    # Register: every view must agree with whichever view sets each of its axes. A view is
    # fitted once the views it is fitted to are final, as a rescale moves its own axis too.
    done = {r for r in vs if not fitted(r)}
    todo = [r for r in ROLES if r in vs and r not in done]
    while todo:
        ready = [r for r in todo if all(setter[a] in done for _k, a in fitted(r))]
        if not ready:                     # views fitted to each other in a ring: stretch only
            for r in todo:
                fit(r, False)
            break
        fit(ready[0], True)
        done.add(ready[0])
        todo.remove(ready[0])

    # A view that sets its cross size (a Top's width, a Side's or End's height) with no other
    # view and no typed number to check it is never read across its length above, so a shadow
    # bulge set the size unseen: a 700 x 155 Top with an 18 mm bulge over 8-19% of its length,
    # built with a typed height, came out 173 mm wide with no warning. Warn only — nothing
    # corroborates the bulge either way, and a part that really is wider must not be trimmed.
    for axis in range(3):
        s = setter[axis]
        if s not in vs or s in bulged or axis in known or AXES[s].index(axis) != 1:
            continue
        if any(r != s and axis in AXES[r][:2] for r in vs):
            continue
        bump = _lone_bulge(vs[s].pts)
        if bump:
            box, usual, where = bump
            bulged.add(s)
            info["warnings"].append(
                "The {} outline is {:.0f} mm {} near {:.0f}% of its {} than along the rest "
                "({:.1f} vs a usual {:.1f} mm) — the trace may include shadow, and nothing else "
                "measures the {}. The part was built at the full {:.1f} mm; if that stretch is "
                "shadow, use Tidy → 📏 Straighten or ✖ Not the part on it, or type the known {}."
                .format(s, box - usual, "taller" if axis == 2 else "wider", where * 100,
                        AXIS_NAMES[AXES[s][0]], box, usual, AXIS_NAMES[axis], box, AXIS_NAMES[axis]))

    shell_wall = None
    if wall_mm is not None and float(wall_mm) > 0:
        if cavity:
            raise PartError("Use either a uniform wall or a traced cavity, not both.")
        shell_wall = float(wall_mm)

    # Typed end shape, standing in for the Side view the user has no square-on photo for.
    end_shape = None
    if ends:
        if not isinstance(ends, dict):
            raise PartError("The end shape must be given as a slope and a radius.")
        slope = _view_number(ends.get("slope_deg"), 0.0, "end slope")
        radius = _view_number(ends.get("radius_mm"), 0.0, "end radius")
        if not (0.0 <= slope <= 75.0):
            raise PartError("The end slope must be between 0 and 75 degrees from upright.")
        if radius < 0:
            raise PartError("The end radius cannot be negative.")
        if slope > 0 or radius > 0:
            if "side" in vs:
                notes.append("The Side view shapes the ends, so the typed end slope is not used.")
            else:
                end_shape = (slope, radius)
    shaped = set(vs) != {"top"} or end_shape is not None

    # A cover prints deck-down, so the top edges of its side and end traces are what sits on
    # the bed: level them before anything is built from them.
    if shell_wall is not None and shaped:
        prof = [r for r in ("side", "end") if r in vs]
        if prof:
            lead = setter[2] if setter[2] in prof else None
            level = None if lead else size[2]
            moved = 0.0
            for r in ([lead] if lead else []) + [r for r in prof if r != lead]:
                lv, m = _flatten_top(vs[r], level)
                if lv is not None:
                    level = lv if level is None else level
                    moved = max(moved, m)
            if level is not None and level > 0:
                size[2] = float(level)
                if moved >= 0.005:
                    notes.append("Flattened the face that prints on the bed (moved up to {:.1f} mm)."
                                 .format(moved))
                info["flattened_mm"] = round(moved, 3)

    if end_shape is not None:
        vs["side"] = View("side", _ends_profile(size[0], size[2], *end_shape), auto=False)
        info["ends"] = {"slope_deg": end_shape[0], "radius_mm": end_shape[1]}
        notes.append("Ends: {} (typed — there is no Side view).".format(", ".join(
            t for t in ("{:.0f}° slope".format(end_shape[0]) if end_shape[0] > 0 else "",
                        "{:.0f} mm edge radius".format(end_shape[1]) if end_shape[1] > 0 else "") if t)))

    X, Y, Z = size
    info["size_mm"] = [round(X, 3), round(Y, 3), round(Z, 3)]
    if partial_side:
        _extend_side(vs["side"], X, covers)
        info["side_covers"] = covers
        notes.append("The side view shapes the {} {:.0f} mm; the rest of the length continues "
                     "at the height of its cut edge.".format(
                         "first" if covers == "start" else "last", vs["side"].covered))

    info["upside_down"] = bool(upside_down)
    info["for_print"] = bool(for_print)
    # Uniform wall on a Top outline alone: the existing, measured shell builder (bosses round
    # holes and all), for the case it was built for.
    if shell_wall is not None and not shaped:
        tris, sinfo = build_shell(vs["top"].pts.tolist(), [], Z, shell_wall)
        if tris is None:
            raise PartError(sinfo.get("reason") or "Could not build the cover.")
        is_shell = sinfo.get("kind") == "shell"
        # build_shell delivers a cover plate-down and its fallback solid as traced; that turn
        # is its own inverse, so this is the part as it sits on the vehicle.
        traced = _print_orientation(tris, Z) if is_shell else tris
        turned = (is_shell and bool(for_print)) != bool(upside_down)
        tris = _print_orientation(traced, Z) if turned else traced
        orient = _orientation(is_shell, turned, for_print)
        if is_shell:
            notes.insert(0, "{}; downloads {}.".format(_shell_note(shell_wall), orient))
        elif sinfo.get("note"):
            notes.insert(0, sinfo["note"])
        info.update(kind="shell" if is_shell else "solid", wall_mm=shell_wall,
                    plate_down=is_shell and turned, orientation=orient)
        if is_shell and for_print and not turned:
            info["warnings"].append("This cover downloads open side down, so its deck spans the "
                                    "opening and needs supports. Turn it back over to print it "
                                    "plate-down.")
        out = {"info": info}
        if preview:
            out["preview"] = _preview_from_tris(traced, vs, None)
        if mirror:
            tris = _mirror_tris(tris)
        info["mirrored"] = bool(mirror)
        rep = mesh_report(tris)
        info["volume_mm3"] = rep["volume_mm3"]
        if notes:
            info["note"] = " ".join(notes)
        if not preview:
            out["tris"] = tris
        return out

    box = mf.Manifold.cube([X, Y, Z])
    solid = box
    for role, view in vs.items():
        n = AXES[role][2]
        solid = solid ^ _prism(_cross_section(view.pts), role, -_MARGIN, size[n] + _MARGIN)
    if solid.is_empty() or solid.volume() <= 0:
        raise PartError("The views do not overlap — one may be flipped. Use the rotate/flip "
                        "buttons until the preview outlines line up.")
    info["kind"] = "solid"
    cav_poly = None

    if shell_wall is not None:
        solid = _hollow(solid, vs, size, shell_wall, info)
        rim = _thin_rim(solid)
        if rim is not None:
            info["warnings"].append(
                "The open edge thins to under {:.1f} mm where the outer bottom corner is rounded "
                "— thinner than {} mm prints unreliably. Square off the bottom corners of the "
                "Side/End outline, or use a thicker wall.".format(rim, THIN_WALL_MM))

    if cavity:
        role = cavity.get("view")
        if role not in ROLES:
            raise PartError("Say which view the cavity was traced in (Top, Side or End).")
        u_ax, v_ax, n_ax = AXES[role]
        raw = _ring(cavity.get("points"), "cavity")
        if cavity.get("same_photo") and role in vs:
            # Traced on the same photo as that view's outline: keep exactly where it sat.
            cav = vs[role].place(raw)
            info["cavity_placement"] = "as traced"
        else:
            cv = View(role, raw, int(cavity.get("rot") or 0), bool(cavity.get("flip_h")),
                      bool(cavity.get("flip_v")), auto=False, scale=cavity.get("scale"))
            cav = cv.pts.copy()
            ext_u, ext_v = size[u_ax], size[v_ax]
            cav[:, 0] += (ext_u - cv.extent[0]) / 2.0
            align = cavity.get("align") or "bottom"
            if align == "center":
                cav[:, 1] += (ext_v - cv.extent[1]) / 2.0
            elif align == "top":
                cav[:, 1] += ext_v - cv.extent[1]
            elif align != "bottom":
                raise PartError("Cavity alignment must be bottom, center or top.")
            info["cavity_placement"] = f"centred, {align}-aligned"
        try:
            c = float(clearance_mm or 0.0)
        except (TypeError, ValueError):
            raise PartError("Clearance must be a number.")
        if not (math.isfinite(c) and 0.0 <= c <= MAX_CLEARANCE_MM):
            raise PartError(f"Clearance must be between 0 and {MAX_CLEARANCE_MM:.0f} mm.")
        cs = _cross_section(cav)
        if c > 0:
            # Rounded joins hold the SAME gap all the way round a sharp corner of whatever it
            # fits over; a mitred offset would push the corner further out than asked.
            cs = cs.offset(c, mf.JoinType.Round)
        cav_poly = [np.round(np.asarray(p, float), 3).tolist() for p in cs.to_polygons()]

        span = size[n_ax]
        how = cavity.get("open") or "through"
        dep = cavity.get("depth_mm")
        if how == "through" or dep in (None, ""):
            lo, hi = -_MARGIN, span + _MARGIN
            how = "through"
        else:
            try:
                d = float(dep)
            except (TypeError, ValueError):
                raise PartError("Cavity depth must be a number.")
            if not (math.isfinite(d) and 0 < d):
                raise PartError("Cavity depth must be greater than zero.")
            if how == "min":
                lo, hi = -_MARGIN, min(d, span + _MARGIN)
            elif how == "max":
                lo, hi = max(span - d, -_MARGIN), span + _MARGIN
            else:
                raise PartError("Cavity opening must be through, min or max.")
        hole = _prism(cs, role, lo, hi)
        solid = solid - hole
        if solid.is_empty() or solid.volume() <= 0:
            raise PartError("The cavity removes the whole part — it is bigger than the outline.")
        pieces = len(solid.decompose())
        if pieces > 1:
            raise PartError(f"The cavity cuts the part into {pieces} separate pieces. Check its "
                            f"size and alignment in the preview.")
        info.update(kind="cavity", clearance_mm=c, cavity_view=role, cavity_open=how)

        # Thinnest wall, from a slice through the middle of the cavity's run. Opening the
        # wall's cross-section by a disc removes exactly the parts thinner than the disc;
        # anything beyond the rounding of convex corners is a wall too thin to print.
        mid = (max(lo, 0.0) + min(hi, span)) / 2.0
        wall_cs = _to_view_plane(solid, role).slice(mid)
        r = THIN_WALL_MM / 2.0
        a0 = wall_cs.area()
        if a0 > 0:
            opened = wall_cs.offset(-r, mf.JoinType.Round).offset(r, mf.JoinType.Round)
            corners = max(1, wall_cs.num_vert())
            if a0 - opened.area() > corners * (1 - math.pi / 4) * r * r * 1.5 + 1e-6:
                info["warnings"].append(
                    f"Some of the wall is thinner than {THIN_WALL_MM} mm — too thin to print "
                    f"reliably. Reduce the clearance or check the cavity's size.")

    # The size is the part's, not the box's: a blob the other views cut away is not in it.
    bb = solid.bounding_box()
    info["size_mm"] = [round(bb[3] - bb[0], 3), round(bb[4] - bb[1], 3), round(bb[5] - bb[2], 3)]
    # A typed size is applied to the view that sets it, before the views are intersected; the
    # intersection can still trim it. 30° ends on real traces gave 698.7 mm for a typed 700
    # while the fit line said "using your number". Said, not rescaled away: rescaling after
    # the fact would change the typed slope and every view's fit.
    for axis, k in sorted(known.items()):
        final = float(bb[3 + axis] - bb[axis])
        if abs(final - k) <= 0.3:
            continue
        for f in info["fits"]:
            if f["setter"] == "known" and f["axis"] == AXIS_NAMES[axis]:
                f["final_mm"] = round(final, 2)
        why = (" (here the {:.0f}° ends)".format(end_shape[0])
               if end_shape is not None and end_shape[0] > 0 and axis in (0, 2) else "")
        notes.append("The built part's {0} is {1:.1f} mm, not the {2:.1f} mm you typed — where the "
                     "views meet they trim it{3}. If {2:.1f} mm must hold, add the {4:.1f} mm to the "
                     "typed {0}.".format(AXIS_NAMES[axis], final, k, why, k - final)
                     if final < k else
                     "The built part's {} is {:.1f} mm, not the {:.1f} mm you typed — check the "
                     "views line up in the preview.".format(AXIS_NAMES[axis], final, k))

    # The preview draws the part as traced — before mirroring or turning — so its silhouettes
    # sit on the outlines they came from, which is what the preview is for: checking that
    # the views line up. The dialog says separately that the download is mirrored/turned.
    shape = solid
    is_shell = shell_wall is not None
    # A cover prints plate-down, open side up, with no support — for the printer only: a STEP
    # or OBJ opened in CAD to add lettering came in upside down. The user's flag turns it over
    # from there, and is reported as given, apart from the automatic turn.
    turned = (is_shell and bool(for_print)) != bool(upside_down)
    if turned:
        # A rotation, never a flip: turning a part over must not change its hand. Whatever is
        # highest — the deck — lands at z = 0: a noisy bottom edge leaves the part's lowest point
        # 0.35-0.4 mm up, and keeping its z range floated the deck that far off the bed.
        solid = solid.rotate([180.0, 0.0, 0.0]).translate([0.0, bb[1] + bb[4], bb[5]])
    if mirror:
        solid = solid.mirror([1.0, 0.0, 0.0]).translate([X, 0.0, 0.0])
    info["mirrored"] = bool(mirror)
    info["plate_down"] = bool(is_shell and turned)
    # The bed check comes before the orientation is put into words, so the two cannot disagree.
    flat_ok = True
    if info["plate_down"]:
        # What a slicer's first layer gets: the part within 0.2 mm of the bed, against the deck.
        deck = abs(signed_area(vs["top"].pts)) if "top" in vs else shape.project().area()
        on_bed = solid.slice(0.2).area()
        if deck > 0:
            info["bed_contact"] = round(on_bed / deck, 3)
        if deck > 0 and on_bed < BED_CONTACT_MIN * deck:
            flat_ok = False
            # The traced profile views, not a typed end shape (that one is flat by construction).
            prof = [r for r in ("side", "end") if r in vs and not (r == "side" and end_shape is not None)]
            nm = " / ".join(r.capitalize() for r in prof) or "Side/End"
            # Straighten did not help the case that found this: an End photo taken at an angle
            # has the far side of the deck as its top edge — a crown, not a noisy straight line.
            info["warnings"].append(
                "Only {:.0f}% of the deck lies on the bed — the top edge of the {} trace is curved "
                "or sloped, so the print may come loose. That usually means the {} photo was taken "
                "at an angle (its top edge is then the far side of the deck). Retake it square-on, "
                "or remove that view and type the height instead.".format(100.0 * on_bed / deck, nm, nm))
    info["orientation"] = _orientation(is_shell, turned, for_print, flat_ok)
    if is_shell:
        notes.insert(0, "{}; downloads {}.".format(_shell_note(shell_wall), info["orientation"]))
        if for_print and not turned:
            info["warnings"].append("This cover downloads open side down, so its deck spans the "
                                    "opening and needs supports. Turn it back over to print it "
                                    "plate-down.")
    if notes:
        info["note"] = " ".join(notes)

    tris = _tris(solid)
    rep = mesh_report(tris)
    if not (rep["watertight"] and rep["oriented"] and rep["volume_mm3"] > 0):
        raise PartError("The part's mesh did not close — try simplifying the outlines.")
    info["volume_mm3"] = rep["volume_mm3"]
    info["triangles"] = rep["triangles"]

    out: Dict[str, Any] = {"info": info}
    if preview:
        out["preview"] = {
            "views": {role: {"outline": np.round(v.pts, 3).tolist(),
                             "result": _silhouette(shape, role)} for role, v in vs.items()},
            "cavity": {"view": cavity.get("view"), "outline": cav_poly} if cav_poly else None,
            "size_mm": info["size_mm"],
            "mesh": _mesh_json(manifold_tris(shape)),
        }
        for role in ROLES:                       # silhouettes of views nobody traced, too
            if role not in out["preview"]["views"]:
                out["preview"]["views"][role] = {"outline": None, "result": _silhouette(shape, role)}
    else:
        out["tris"] = tris
    return out


def _mirror_tris(tris: np.ndarray) -> np.ndarray:
    """Mirror across X and reverse each triangle, so the normals stay outward."""
    t = np.array(tris, dtype=np.float64, copy=True)
    x = t[:, :, 0]
    t[:, :, 0] = (float(x.min()) + float(x.max())) - x
    return t[:, ::-1, :]


def _preview_from_tris(tris: np.ndarray, vs: Dict[str, View], cav) -> Dict[str, Any]:
    mesh = mf.Mesh(vert_properties=np.asarray(tris, np.float32).reshape(-1, 3),
                   tri_verts=np.arange(len(tris) * 3, dtype=np.uint32).reshape(-1, 3))
    mesh.merge()
    man = mf.Manifold(mesh)
    return {"views": {role: {"outline": (np.round(vs[role].pts, 3).tolist() if role in vs else None),
                             "result": _silhouette(man, role)} for role in ROLES},
            "cavity": cav, "size_mm": None, "mesh": _mesh_json(tris)}


MAX_PREVIEW_TRIS = 60000


def _mesh_json(tris) -> Dict[str, Any]:
    """The part as shared vertices + faces for the dialog's 3D view: as built (before any
    mirror or turn for printing), 0.01 mm grid, capped in size. Larger meshes are thinned
    for the preview only — the download is never touched."""
    t = np.asarray(tris, dtype=float).reshape(-1, 3, 3)
    if len(t) > MAX_PREVIEW_TRIS:
        t = t[np.linspace(0, len(t) - 1, MAX_PREVIEW_TRIS).astype(int)]
    key = np.round(t.reshape(-1, 3) * 100).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    return {"v": (uniq / 100.0).round(2).tolist(), "f": inv.reshape(-1, 3).tolist()}
