"""Turn a measured 2D profile into a watertight 3D solid, ready to slice and print.

WHY THIS EXISTS
---------------
CamScan could measure a part and export PNG, JSON, CSV, DXF and SVG — all of them flat. A
customer photographing a broken running board got a drawing, and somebody still had to open
CAD, extrude the profile by hand and hope they picked the right depth. For the large class of
replacement parts that ARE an extrusion — running boards, mouldings, sills, trim, brackets,
gaskets, spacers — the traced outline plus one depth is the whole part, so the last step can
be done here instead.

This module takes a closed outer profile and any interior holes, both in millimetres and
already perspective-corrected by the calibration, plus an extrusion depth, and produces a
closed triangle mesh: two caps and a wall for every ring. Output is binary STL because every
slicer reads it. build_shell() makes the hollow version — a cover — instead; see "Shells".

WHAT "WATERTIGHT" MEANS HERE, AND WHY IT IS CHECKED
--------------------------------------------------
A slicer needs a closed surface; a mesh with a gap or a flipped triangle either fails to load
or silently prints wrong. Two properties are therefore verified rather than assumed:

  * every undirected edge is shared by exactly two triangles (no holes, no duplicate faces)
  * the signed volume is positive and equals profile_area * depth (normals point outward,
    and the caps really do span the profile)

`mesh_report()` returns both so callers — and the tests — can assert on them.

CAP TRIANGULATION
-----------------
Triangulating a polygon WITH holes is the hard part. Rather than hand-roll ear clipping with
hole bridging, each ring is densified and run through OpenCV's Delaunay (cv2.Subdiv2D), then
triangles are kept only when their centroid lies inside the outer ring and outside every
hole. Densifying is what makes this exact: with vertices spaced finely along the boundary,
Delaunay edges follow the true outline instead of cutting across a concavity. Measured
against closed-form areas it is exact (0.00% error) on a plain square, a square with a hole,
an L-shape, a deep U-channel, and a board with three slots.

It also needs no new dependency: OpenCV and numpy are already here.
"""
from typing import List, Sequence, Tuple, Dict, Any, Optional
import struct
import math

import numpy as np
import cv2

# Boundary vertex spacing for the cap triangulation, as a fraction of the profile's larger
# bounding-box side. Fine enough that Delaunay follows a concave boundary, coarse enough that
# a long part does not explode into millions of triangles.
DENSIFY_FRAC: float = 0.01
DENSIFY_MIN_MM: float = 0.5
MAX_RING_POINTS: int = 20000         # per ring; the spacing widens to respect it, never truncates

MIN_DEPTH_MM: float = 0.01
MAX_DEPTH_MM: float = 10000.0        # 10 m; past this it is a typo, not a part


def _ring_array(ring: Sequence[Sequence[float]]) -> Optional[np.ndarray]:
    """A clean (N,2) float array, or None when the ring cannot be a polygon."""
    try:
        a = np.asarray(ring, dtype=float).reshape(-1, 2)
    except (ValueError, TypeError):
        return None
    if a.shape[0] < 3 or not np.all(np.isfinite(a)):
        return None
    # Drop a repeated closing vertex; the rings here are implicitly closed.
    if np.allclose(a[0], a[-1]):
        a = a[:-1]
    return a if a.shape[0] >= 3 else None


def signed_area(ring: np.ndarray) -> float:
    """Shoelace. Positive = counter-clockwise in a standard right-handed XY frame."""
    x, y = ring[:, 0], ring[:, 1]
    return float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) / 2.0)


def _orient(ring: np.ndarray, ccw: bool) -> np.ndarray:
    """Force a winding. Outer rings run CCW and holes CW, so one wall-building rule gives
    every wall an outward-facing normal — outward from the MATERIAL, which on a hole means
    facing into the void."""
    return ring if (signed_area(ring) >= 0) == ccw else ring[::-1]


def _ring_step(ring: np.ndarray, step: float) -> float:
    """`step`, widened if needed so this ring stays inside the point budget.

    Widening rather than truncating matters: a ring cut off part-way is no longer closed, and
    the mesh built from it cannot be watertight. A 1500 mm board densified at the 0.5 mm step
    its own 50 mm clip slots demanded hit the budget mid-outline and silently produced half a
    part. Each ring now gets a spacing suited to its own size.
    """
    per = float(np.sum(np.linalg.norm(np.roll(ring, -1, axis=0) - ring, axis=1)))
    if per <= 0:
        return max(step, DENSIFY_MIN_MM)
    return max(step, per / float(max(3, MAX_RING_POINTS)))


def _densify(ring: np.ndarray, step: float) -> np.ndarray:
    """Insert vertices so no edge is longer than `step`. This is what lets a Delaunay
    triangulation follow a concave boundary instead of cutting the corner off it. Never
    truncates — see _ring_step."""
    step = _ring_step(ring, step)
    out = []
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        d = float(np.linalg.norm(b - a))
        k = 1 if (step <= 0 or d <= step) else int(math.ceil(d / step))
        for t in range(k):
            out.append(a + (b - a) * (t / k))
    return np.asarray(out, dtype=float)


# Bucket size for snapping Delaunay output back onto the exact ring vertices. cv2.Subdiv2D
# stores points as float32, so what it returns is NOT bit-identical to what was inserted —
# measured up to 2e-6 mm off, with only 1 of 186 vertices matching exactly on a circle. Cap
# vertices that miss their wall counterparts by a micro-millimetre leave every boundary edge
# unpaired, which reads as a shredded mesh. A micron bucket is far above that error and far
# below any real feature.
_SNAP_MM: float = 1e-3


_KEY_Y_OFFSET = 1 << 30
_KEY_X_STRIDE = 1 << 31


def _snap_keys(p: np.ndarray) -> np.ndarray:
    """One int64 per point, from its micron-quantised (x, y). Distinct buckets never collide
    for parts up to about 1 km, far past MAX_DEPTH_MM's sanity bound on any dimension."""
    q = np.round(np.asarray(p, float) / _SNAP_MM).astype(np.int64)
    return q[:, 0] * _KEY_X_STRIDE + (q[:, 1] + _KEY_Y_OFFSET)


def _snap_all(v: np.ndarray, ring_pts: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """For each point in `v`, the EXACT ring vertex it came from. Returns (snapped, found).

    Vectorised: per-vertex dictionary lookups in Python were most of the export time on a
    real traced part (127k lookups for one six-hole cover)."""
    keys = _snap_keys(ring_pts)
    order = np.argsort(keys, kind="stable")
    sk = keys[order]
    q = np.round(v / _SNAP_MM).astype(np.int64)
    out = np.zeros_like(v, dtype=float)
    found = np.zeros(len(v), bool)
    # Exact bucket first, then the eight neighbours: the float32 drift can push a point just
    # across a bucket edge.
    for dx, dy in ((0, 0), (-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
        todo = ~found
        if not todo.any():
            break
        k = (q[todo, 0] + dx) * _KEY_X_STRIDE + (q[todo, 1] + dy + _KEY_Y_OFFSET)
        pos = np.clip(np.searchsorted(sk, k), 0, len(sk) - 1)
        hit = sk[pos] == k
        idx = np.nonzero(todo)[0][hit]
        out[idx] = ring_pts[order[pos[hit]]]
        found[idx] = True
    return out, found


def _inside(pts: np.ndarray, ring: np.ndarray) -> np.ndarray:
    """Even-odd ray cast: True where a point is inside `ring`.

    An edge can only be crossed by a ray from a point whose y lies in that edge's own y-span,
    so points are sorted by y and each edge is tested only against the points in its span.
    Rings here are densified to a few mm per edge, so that is a few dozen points per edge
    instead of all of them — testing every point against every edge was 98M pairs per cap
    on a real six-hole cover, and most of a 5.5 s export."""
    M = len(pts)
    out = np.zeros(M, bool)
    if M == 0 or len(ring) < 3:
        return out
    x1, y1 = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x1, -1), np.roll(y1, -1)
    order = np.argsort(pts[:, 1], kind="stable")
    ys = pts[order, 1]
    # Half-open [ymin, ymax): exactly the (y1 > py) != (y2 > py) rule, and a horizontal edge
    # gets an empty span.
    lo = np.searchsorted(ys, np.minimum(y1, y2), "left")
    hi = np.searchsorted(ys, np.maximum(y1, y2), "left")
    cnt = hi - lo
    hits = np.zeros(M, np.int64)
    ends = np.cumsum(cnt)
    budget = 4_000_000                         # (edge, point) pairs per batch
    e0 = 0
    E = len(ring)
    while e0 < E:
        base = ends[e0 - 1] if e0 else 0
        e1 = int(np.searchsorted(ends, base + budget, "right"))
        e1 = min(E, max(e0 + 1, e1))
        c = cnt[e0:e1]
        total = int(c.sum())
        if total:
            e = np.repeat(np.arange(e0, e1), c)
            first = np.repeat(np.cumsum(c) - c, c)
            p = order[np.repeat(lo[e0:e1], c) + (np.arange(total) - first)]
            py = pts[p, 1]
            xint = x1[e] + (py - y1[e]) * (x2[e] - x1[e]) / (y2[e] - y1[e])
            hit = pts[p, 0] < xint
            hits += np.bincount(p[hit], minlength=M)
        e0 = e1
    return (hits % 2) == 1


def _cap_triangles(rings: List[np.ndarray]) -> np.ndarray:
    """Triangulate the region inside rings[0] and outside rings[1:]. Returns (K,3,2)."""
    empty = np.zeros((0, 3, 2), float)
    pts = np.vstack(rings)
    lo = pts.min(axis=0) - 10.0
    hi = pts.max(axis=0) + 10.0
    span = hi - lo
    if not np.all(np.isfinite(span)) or np.any(span <= 0):
        return empty
    sub = cv2.Subdiv2D((float(lo[0]), float(lo[1]), float(span[0]), float(span[1])))
    for p in pts:
        try:
            sub.insert((float(p[0]), float(p[1])))
        except cv2.error:
            continue                       # a duplicate/degenerate point is simply skipped
    raw = np.asarray(sub.getTriangleList(), dtype=float).reshape(-1, 3, 2)
    if len(raw) == 0:
        return empty
    # Subdiv2D returns triangles touching its virtual super-triangle; they fall outside the
    # inserted bounds and must go.
    inb = np.all((raw >= lo) & (raw <= hi), axis=(1, 2))
    raw = raw[inb]
    if len(raw) == 0:
        return empty
    # Put the vertices back on the exact ring coordinates the walls will use.
    snapped, found = _snap_all(raw.reshape(-1, 2), pts)
    ok = found.reshape(-1, 3).all(axis=1)
    tris = snapped.reshape(-1, 3, 2)[ok]
    if len(tris) == 0:
        return empty
    c = tris.mean(axis=1)
    keep = _inside(c, rings[0])
    for h in rings[1:]:
        keep &= ~_inside(c, h)
    return tris[keep]


def _cap_3d(tri2: np.ndarray, z: float, up: bool) -> np.ndarray:
    """Lift 2D cap triangles to height z with the winding forced: +Z normal when `up`, else
    -Z. Delaunay hands back arbitrary winding, and getting this wrong is the classic 'slicer
    prints it inside out' bug."""
    e1, e2 = tri2[:, 1] - tri2[:, 0], tri2[:, 2] - tri2[:, 0]
    ccw = (e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) > 0
    t = tri2.copy()
    flip = ccw != up
    t[flip] = t[flip][:, ::-1]
    return np.concatenate([t, np.full(t.shape[:2] + (1,), float(z))], axis=2)


def _wall_3d(r: np.ndarray, z0: float, z1: float) -> np.ndarray:
    """The two triangles per ring edge between heights z0 and z1. With outer rings CCW and
    holes CW, this one rule faces every wall away from the material."""
    a, b = r, np.roll(r, -1, axis=0)
    n = len(r)
    a0 = np.column_stack([a, np.full(n, z0)]); b0 = np.column_stack([b, np.full(n, z0)])
    a1 = np.column_stack([a, np.full(n, z1)]); b1 = np.column_stack([b, np.full(n, z1)])
    return np.concatenate([np.stack([a0, b0, b1], axis=1), np.stack([a0, b1, a1], axis=1)])


def build_extrusion(outer: Sequence[Sequence[float]],
                    holes: Sequence[Sequence[Sequence[float]]],
                    depth_mm: float) -> Optional[np.ndarray]:
    """Closed triangle mesh (T,3,3) in mm for `outer` extruded `depth_mm` along +Z, with
    `holes` as through-holes. Returns None when the input cannot make a solid.

    The profile is taken as-is: by the time it reaches here the calibration has already put
    it in real millimetres on the measured plane.
    """
    try:
        depth = float(depth_mm)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(depth) or not (MIN_DEPTH_MM <= abs(depth) <= MAX_DEPTH_MM):
        return None
    depth = abs(depth)

    o = _ring_array(outer)
    if o is None or abs(signed_area(o)) <= 0:
        return None
    o = _orient(o, ccw=True)

    hs: List[np.ndarray] = []
    for h in (holes or []):
        hr = _ring_array(h)
        if hr is None or abs(signed_area(hr)) <= 0:
            continue
        # Only a ring genuinely inside the outer profile is a hole. One that is not would
        # otherwise punch a void through nothing and tear the mesh open.
        if cv2.pointPolygonTest(o.astype(np.float32),
                                (float(hr[:, 0].mean()), float(hr[:, 1].mean())), False) < 0:
            continue
        hs.append(_orient(hr, ccw=False))

    # Step size comes from the SMALLEST ring, not the largest. A 300 mm board with 20 mm
    # slots sized off the board alone gets a step too coarse for the slots, and the Delaunay
    # then cuts across their corners.
    scale = 1.0

    # Delaunay is UNCONSTRAINED: nothing guarantees it contains the ring edges the walls are
    # built from, and a cap that cuts a corner leaves the mesh open even though its area is
    # still right (measured: a board with three slots came out with the exact volume and 120
    # unpaired edges). Densifying makes it hold in practice, so build, CHECK, and refine —
    # and if it still will not close, return nothing rather than hand a slicer a leaky mesh.
    tris = None
    for _attempt in range(4):
        candidate = _assemble(o, hs, scale, depth)
        if candidate is not None and _closed(candidate):
            tris = candidate
            break
        scale /= 2.0                      # refine every ring together and try again
    if tris is None:
        # The same prism as a boolean — notably for an outline that crosses itself where a
        # dot was dragged over an edge, which the capping above cannot fill. A lobe wound
        # against the rest (the small loop such a drag leaves) is dropped, as a slicer would.
        tris = _manifold_shell(o, [], [], hs, depth, depth)
        if tris is not None and not _closed(tris):
            tris = None
    return tris


def _assemble(o: np.ndarray, hs: List[np.ndarray], scale: float,
              depth: float) -> Optional[np.ndarray]:
    """One attempt at the mesh. Each ring is densified relative to its OWN size, so a long
    outline is not forced to the fine spacing a small slot needs (which is what blew the
    point budget), while a small slot still gets enough vertices to resolve its corners."""
    def _step_for(r):
        bb = r.max(axis=0) - r.min(axis=0)
        return max(DENSIFY_MIN_MM, DENSIFY_FRAC * float(max(bb)) * scale)
    rings = [_densify(o, _step_for(o))] + [_densify(h, _step_for(h)) for h in hs]
    # Densifying must not collapse a ring below a triangle.
    if any(len(r) < 3 for r in rings):
        return None

    caps2d = _cap_triangles(rings)
    if len(caps2d) == 0:
        return None

    # Caps: +Z normal on top, -Z on the bottom. Walls: outer rings run CCW and holes CW, so
    # one rule faces every wall away from the material.
    parts = [_cap_3d(caps2d, depth, up=True), _cap_3d(caps2d, 0.0, up=False)]
    parts += [_wall_3d(r, 0.0, depth) for r in rings]
    return np.concatenate(parts).astype(np.float64)


def mesh_report(tris: np.ndarray) -> Dict[str, Any]:
    """Facts a slicer cares about, so they can be asserted rather than hoped for.

    watertight : every undirected edge is shared by exactly two triangles
    oriented   : watertight AND every triangle wound consistently with its neighbours
    volume_mm3 : signed volume via the divergence theorem — positive means outward normals
    open_edges : how many edges fail the pairing (0 on a closed mesh)
    """
    if tris is None or len(tris) == 0:
        return {"triangles": 0, "watertight": False, "volume_mm3": 0.0, "open_edges": 0}

    # Quantise before pairing edges: cap and wall vertices are built from the same densified
    # rings so they should coincide exactly, but float arithmetic can leave the last bit off
    # and a 1-ulp gap would read as a hole that is not there.
    q = np.round(np.asarray(tris, dtype=float) * 1e6).astype(np.int64)
    a = q.reshape(-1, 3)                                  # each triangle's edges: v0v1, v1v2, v2v0
    b = np.roll(q, -1, axis=1).reshape(-1, 3)
    # Order each edge's endpoints lexicographically, so (a, b) and (b, a) are one key.
    d = a - b
    swap = (d[:, 0] > 0) | ((d[:, 0] == 0) & (d[:, 1] > 0)) | ((d[:, 0] == 0) & (d[:, 1] == 0) & (d[:, 2] > 0))
    lo = np.where(swap[:, None], b, a)
    hi = np.where(swap[:, None], a, b)
    _keys, counts = np.unique(np.concatenate([lo, hi], axis=1), axis=0, return_counts=True)
    open_edges = int(np.count_nonzero(counts != 2))
    # Counting undirected edges cannot see a single triangle wound the wrong way — it still
    # shares each edge with exactly two neighbours (measured: a flipped triangle reads 0 open
    # edges). On a closed, consistently wound mesh every DIRECTED edge occurs exactly once,
    # its neighbour running the same edge the other way.
    _dk, dcounts = np.unique(np.concatenate([a, b], axis=1), axis=0, return_counts=True)
    misoriented = int(np.count_nonzero(dcounts != 1))

    a, b, c = tris[:, 0, :], tris[:, 1, :], tris[:, 2, :]
    vol = float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)
    return {"triangles": int(len(tris)), "watertight": open_edges == 0,
            "oriented": open_edges == 0 and misoriented == 0,
            "volume_mm3": vol, "open_edges": int(open_edges)}


def _closed(tris: np.ndarray) -> bool:
    """Fit to hand a slicer: closed, consistently wound, and enclosing positive volume."""
    rep = mesh_report(tris)
    return bool(rep["watertight"] and rep["oriented"] and rep["volume_mm3"] > 0)


def write_binary_stl(tris: np.ndarray, path: str, header: str = "CamScan") -> bool:
    """Binary STL — the format every slicer reads. Returns False rather than raising."""
    try:
        t = np.asarray(tris, dtype=np.float32)
        if t.ndim != 3 or t.shape[1:] != (3, 3) or len(t) == 0:
            return False
        n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        # A degenerate triangle has no normal; STL allows (0,0,0) and slicers recompute it.
        n = np.divide(n, ln, out=np.zeros_like(n), where=ln > 0)
        with open(path, "wb") as f:
            f.write(header.encode("ascii", "replace")[:80].ljust(80, b"\0"))
            f.write(struct.pack("<I", len(t)))
            for i in range(len(t)):
                f.write(struct.pack("<12fH", *n[i], *t[i, 0], *t[i, 1], *t[i, 2], 0))
        return True
    except (OSError, ValueError, TypeError):
        return False


def profile_to_stl(outer, holes, depth_mm: float, path: str,
                   header: str = "CamScan extruded profile") -> Optional[Dict[str, Any]]:
    """Build the solid and write it. Returns mesh_report() on success, else None."""
    tris = build_extrusion(outer, holes, depth_mm)
    if tris is None:
        return None
    if not write_binary_stl(tris, path, header):
        return None
    return mesh_report(tris)


# ---------------------------------------------------------------------------------------
# Shells: a cover, not a block
# ---------------------------------------------------------------------------------------
# A running-board step pad, a trim cap, an end cover — most replacement covers are a thin
# plate with a wall round its edge, open underneath. Extruding the traced outline makes a
# solid block instead: for a 900 x 180 mm pad 25 mm deep that is 4.05 L of plastic (about
# 5 kg of PLA), against 0.63 L for the same pad with a 3 mm wall. The block would not even
# fit over the board it is meant to cover.
#
# THE CAVITY: offsetting a polygon inward is easy on paper and fragile in practice — a traced
# outline has hundreds of small jogs, and a per-vertex offset turns every one of them into a
# spike or a self-intersecting loop. So the offset is taken on a raster instead: fill the
# profile, take the exact Euclidean distance to its edge (cv2.distanceTransform, precise
# mask), keep everything at least one wall-thickness in, and trace that back to a ring. The
# distance field handles every case that breaks a vector offset for free — a neck narrower
# than two walls simply closes off, a concave corner rounds to the true uniform-wall arc, a
# convex one stays sharp.
#
# Measured against closed forms over 55 random shapes (rectangles of random size and angle,
# discs of random radius, walls 1-6 mm) at the default resolution: wall thickness comes out
# within +0.12% on average, 0.71% standard deviation, 1.78% worst — 0.05 mm on a 3 mm wall,
# well under one nozzle width. The outer surface is not rasterised at all; it is the traced
# profile itself, so the dimension that has to fit is exact.
#
# ORIENTATION: the solid comes out plate-down, open side up, which is how a cover prints
# without supports (plate-up would hang the whole plate over the cavity). Turning it over is
# a 180-degree ROTATION — y and z both negate — never a z flip alone: a flip mirrors the part,
# and a mirrored step pad is the one for the other side of the truck.
SHELL_PX_PER_WALL: float = 12.0       # raster cells across one wall
SHELL_MAX_SIDE: int = 6000            # cells on the long side, so a 1.5 m board stays cheap
SHELL_MAX_PIXELS: int = 16_000_000
_SHELL_BIAS_PX: float = 0.125         # centre of the measured zero-bias plateau (0.1-0.15)
MIN_WALL_MM: float = 0.4              # one nozzle; thinner cannot print as a wall
_HOLE_CLEARANCE_PX: float = 1.0


def _shell_grid(o: np.ndarray, wall: float):
    """Raster frame for the cavity: (px_mm, origin_mm, (H, W))."""
    lo, hi = o.min(axis=0), o.max(axis=0)
    size = hi - lo
    px = max(wall / SHELL_PX_PER_WALL,
             float(max(size)) / SHELL_MAX_SIDE,
             math.sqrt(float(size[0] * size[1]) / SHELL_MAX_PIXELS))
    pad = 3
    W = int(math.ceil(size[0] / px)) + 2 * pad + 1
    H = int(math.ceil(size[1] / px)) + 2 * pad + 1
    return px, lo - pad * px, (H, W)


def _fill(ring: np.ndarray, px: float, org: np.ndarray, shape) -> np.ndarray:
    shift = 8                                  # fixed-point bits: sub-pixel vertex placement
    pts = np.round((ring - org) / px * (1 << shift)).astype(np.int32)
    m = np.zeros(shape, np.uint8)
    cv2.fillPoly(m, [pts], 255, lineType=cv2.LINE_8, shift=shift)
    return m


def _cavity(o: np.ndarray, wall: float, holes: Sequence[np.ndarray] = ()):
    """Inward offset of the outer ring by `wall`, via the distance field, with every hole
    placed. Returns (cavity_rings_mm, plate_holes, through_holes, bossed_count):

      * a hole comfortably inside the cavity pierces the plate only — a bolt hole in the top
      * any other hole runs the full depth inside a solid boss: the cavity is carved back one
        wall-thickness from it, so the wall wraps round the hole. That is what a designer does
        with a hole near an edge, and it always closes — where refusing the export, which an
        earlier version did, threw away a whole part over one hole (two of seven real traces
        from the customer photos hit exactly that).

    Cavity rings smaller than one wall-square are dropped — that spot is simply solid."""
    px, org, shape = _shell_grid(o, wall)
    d = cv2.distanceTransform(_fill(o, px, org, shape), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    thr = wall / px + _SHELL_BIAS_PX
    cav = (d >= thr).astype(np.uint8)

    masks = [_fill(h, px, org, shape) > 0 for h in holes]
    bossed = [False] * len(holes)
    plate_idx: List[int] = []
    carve_cache: Dict[int, Tuple[slice, slice, np.ndarray]] = {}

    def _carve(i):
        """Cells within one wall of hole i — computed on a window round the hole, since the
        carve reaches no further, and once (a full-frame transform per hole per pass was
        most of a 6 s export on a real six-hole trace)."""
        if i not in carve_cache:
            ys, xs = np.nonzero(masks[i])
            if len(xs) == 0:
                cx, cy = (holes[i].mean(axis=0) - org) / px
                xs = np.array([int(round(cx))]); ys = np.array([int(round(cy))])
            m = int(math.ceil(thr)) + 2
            y0, y1 = max(0, ys.min() - m), min(shape[0], ys.max() + m + 1)
            x0, x1 = max(0, xs.min() - m), min(shape[1], xs.max() + m + 1)
            win = np.ones((y1 - y0, x1 - x0), np.uint8)
            sub = masks[i][y0:y1, x0:x1]
            if sub.any():
                win[sub] = 0
            else:
                win[min(win.shape[0] - 1, max(0, int(ys[0]) - y0)),
                    min(win.shape[1] - 1, max(0, int(xs[0]) - x0))] = 0
            away = cv2.distanceTransform(win, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
            carve_cache[i] = (slice(y0, y1), slice(x0, x1), away < thr)
        return carve_cache[i]

    # Carving a boss can take cavity away from a neighbouring plate hole, so settle the
    # classification until nothing changes. Each pass can only move holes INTO the bossed
    # set, so this ends within len(holes) passes.
    for _ in range(len(holes) + 1):
        work = cav.copy()
        for i in range(len(holes)):
            if bossed[i]:
                sy, sx, near = _carve(i)
                work[sy, sx][near] = 0
        # Where a cavity tapers into a neck it can end in a one-cell-wide spur, and the
        # contour traces that out and straight back — a zero-width spike whose edge the walls
        # then use twice, so the mesh cannot close (measured on a dumbbell whose neck is under
        # two walls). A feature one cell wide is below what the raster resolves; open it away.
        work = cv2.morphologyEx(work, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        # Clearance of each hole from the cavity's edge, in cells.
        inside = cv2.distanceTransform(work, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        changed = False
        plate_idx = []
        for i, m in enumerate(masks):
            if bossed[i]:
                continue
            if m.any():
                clear = float(inside[m].min())
            else:                                             # smaller than one cell
                cx, cy = (holes[i].mean(axis=0) - org) / px
                clear = float(inside[min(shape[0] - 1, max(0, int(round(cy)))),
                                     min(shape[1] - 1, max(0, int(round(cx))))])
            if clear >= 1.0 + _HOLE_CLEARANCE_PX:
                plate_idx.append(i)
            else:
                bossed[i] = changed = True
        if not changed:
            break
    cav = work

    contours, _ = cv2.findContours(cav, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    rings: List[np.ndarray] = []
    for c in contours:
        a = cv2.approxPolyDP(c.astype(np.float32), 0.3, True).reshape(-1, 2).astype(float)
        a = _despike(a)
        if a is None:
            continue
        r = a * px + org
        if abs(signed_area(r)) < wall * wall:
            continue
        rings.append(_orient(r, ccw=False))                   # material lies outside them

    plate: List[Tuple[np.ndarray, int]] = []
    through: List[np.ndarray] = [holes[i] for i in range(len(holes)) if bossed[i]]
    for i in plate_idx:
        c = (float(holes[i][:, 0].mean()), float(holes[i][:, 1].mean()))
        k = next((j for j, cr in enumerate(rings)
                  if cv2.pointPolygonTest(cr.astype(np.float32), c, False) > 0), None)
        if k is None:                    # its cavity was too small to keep: solid round it
            through.append(holes[i])
        else:
            plate.append((holes[i], k))
    return rings, plate, through, sum(bossed)


def _despike(r: np.ndarray) -> Optional[np.ndarray]:
    """Drop repeated vertices and zero-width spikes (out and straight back along the same
    line), which the opening above should already have removed — kept as a guarantee, since
    one surviving spike is enough to leave the mesh open."""
    pts = [p for p in r]
    changed = True
    while changed and len(pts) >= 3:
        changed = False
        out = []
        n = len(pts)
        for i in range(n):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
            if np.allclose(a, b):
                changed = True
                continue
            ab, cb = a - b, c - b
            cross = ab[0] * cb[1] - ab[1] * cb[0]
            if abs(cross) < 1e-9 and float(np.dot(ab, cb)) > 0:   # reverses along a line
                changed = True
                continue
            out.append(b)
        pts = out
    return np.asarray(pts, float) if len(pts) >= 3 else None


def build_shell(outer: Sequence[Sequence[float]],
                holes: Sequence[Sequence[Sequence[float]]],
                depth_mm: float, wall_mm: float) -> Tuple[Optional[np.ndarray], Dict[str, Any]]:
    """A cover: plate of `wall_mm` with a `wall_mm` wall round its edge, `depth_mm` overall,
    open underneath. Returned plate-down (print orientation). Returns (tris, info); tris is
    None when it cannot be built, and info["reason"] says why in words a user can act on.

    Holes are placed against the cavity (see _cavity): one comfortably over the cavity
    pierces the plate only; any other runs the full depth inside a solid boss.
    When the wall fills the whole profile (a part narrower than two walls) or is at least the
    depth, there is no cavity and the plain solid is returned, saying so.
    """
    info: Dict[str, Any] = {"kind": None, "wall_mm": None, "cavities": 0, "plate_holes": 0,
                            "through_holes": 0, "bossed_holes": 0, "note": None, "reason": None}
    try:
        depth, wall = abs(float(depth_mm)), float(wall_mm)
    except (TypeError, ValueError):
        info["reason"] = "Depth and wall thickness must be numbers."
        return None, info
    if not (math.isfinite(depth) and math.isfinite(wall)):
        info["reason"] = "Depth and wall thickness must be finite."
        return None, info
    if not (MIN_DEPTH_MM <= depth <= MAX_DEPTH_MM):
        info["reason"] = "Depth is out of range."
        return None, info
    if wall < MIN_WALL_MM:
        info["reason"] = ("A wall under {:.1f} mm cannot print — that is thinner than one "
                          "nozzle width.".format(MIN_WALL_MM))
        return None, info
    info["wall_mm"] = wall

    def _solid(note):
        info.update(kind="solid", note=note)
        tris = build_extrusion(outer, holes, depth)
        if tris is None:
            info["reason"] = "Could not build a closed solid from this profile."
        return tris, info

    if wall >= depth:
        return _solid("The wall is as thick as the part is deep, so it is solid.")

    o = _ring_array(outer)
    if o is None or abs(signed_area(o)) <= 0:
        info["reason"] = "The profile is not a usable polygon."
        return None, info
    o = _orient(o, ccw=True)

    hole_rings: List[np.ndarray] = []
    for h in (holes or []):
        hr = _ring_array(h)
        if hr is None or abs(signed_area(hr)) <= 0:
            continue
        if cv2.pointPolygonTest(o.astype(np.float32),
                                (float(hr[:, 0].mean()), float(hr[:, 1].mean())), False) < 0:
            continue                                          # same rule as build_extrusion
        hole_rings.append(_orient(hr, ccw=False))

    cav_rings, plate, through, bossed = _cavity(o, wall, hole_rings)
    if not cav_rings:
        return _solid("The part is narrower than two walls everywhere, so it is solid.")

    info.update(kind="shell", cavities=len(cav_rings), plate_holes=len(plate),
                through_holes=len(through), bossed_holes=int(bossed),
                cavity_area_mm2=float(sum(abs(signed_area(c)) for c in cav_rings)),
                expected_volume_mm3=shell_volume_mm3(o, cav_rings, [h for h, _ in plate],
                                                     through, depth, wall))

    scale = 1.0
    for _attempt in range(4):
        tris = _assemble_shell(o, cav_rings, plate, through, depth, wall, scale)
        if tris is not None and _closed(tris):
            return _print_orientation(tris, depth), info
        scale /= 2.0
    # The cup above is built by hand, matching caps to walls by position, and on about 2% of
    # outlines traced on a customer's photo it still would not close. The same cup as a
    # boolean of prisms cannot fail that way; it is only a fallback so every cup that already
    # built keeps exactly the mesh it had.
    tris = _manifold_shell(o, cav_rings, plate, through, depth, wall)
    if tris is not None and _closed(tris):
        exp = info["expected_volume_mm3"]
        if abs(mesh_report(tris)["volume_mm3"] - exp) <= 0.01 * exp:
            return _print_orientation(tris, depth), info
    info["reason"] = "Could not close the shell's mesh for this profile."
    return None, info


def manifold_tris(man) -> np.ndarray:
    """A manifold3d solid as triangles, the way an STL holds it: by position, not by index.

    Manifold's mesh is closed by vertex INDEX, and may keep two distinct vertices at the same
    position with zero-area triangles between them (measured: 3 of 4 outlines traced on a
    customer's photo, from near-coincident points at a corner). An STL has no indices — a
    slicer, like mesh_report, joins vertices by position — so those slivers read as holes.
    Welding by position and dropping the triangles that collapse is exactly what the slicer
    would see, minus the slivers; the volume is unchanged.
    """
    mesh = man.to_mesh()
    v = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    t = np.asarray(mesh.tri_verts, dtype=np.int64)
    key = np.round(v * 1e6).astype(np.int64)            # the same quantum mesh_report pairs by
    _u, first, weld = np.unique(key, axis=0, return_index=True, return_inverse=True)
    w = weld.reshape(-1)[t]
    keep = (w[:, 0] != w[:, 1]) & (w[:, 1] != w[:, 2]) & (w[:, 2] != w[:, 0])
    V = v[first]
    return V[unsliver(V, w[keep])]


def unsliver(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    """Remove zero-area triangles — three corners on one line — without opening the surface.

    Manifold can leave such a sliver where two near-parallel faces meet (measured: opening a
    rounded cover underneath, where the opening's top sits 0.01 mm above the inside's floor,
    and where a rounded inner offset meets a chamfer). It is closed by vertex index, but by
    position its long edge p-q is met on the other side by the two short edges p-m and m-q:
    a slicer, mesh_report and a CAD face all see a hole. The sliver says the edge is really
    p-m-q, so: drop it, and split the triangle across p-q at m. The surface stays closed.

    One pass over a directed-edge index, so the cost grows with the number of triangles, not
    with slivers x triangles (a 12k-triangle cover with 45 slivers took 28 s the slow way).
    F is (T, 3) vertex indices into V; returns the new (T', 3)."""
    F = np.asarray(F, dtype=np.int64).reshape(-1, 3)
    if len(F) == 0:
        return F
    P = V[F]
    cr = np.linalg.norm(np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]), axis=1)
    span = np.max(np.stack([np.linalg.norm(P[:, 1] - P[:, 0], axis=1),
                            np.linalg.norm(P[:, 2] - P[:, 0], axis=1),
                            np.linalg.norm(P[:, 2] - P[:, 1], axis=1)]), axis=0)

    def flat(cross, sp):
        return cross <= 1e-9 * max(sp, 1e-12) ** 2
    bad = np.nonzero(cr <= 1e-9 * np.maximum(span, 1e-12) ** 2)[0]
    if len(bad) == 0:
        return F
    tris = [list(map(int, f)) for f in F]
    alive = [True] * len(tris)
    edge = {}                                   # directed edge (u, v) -> triangle index
    for ti, (a, b, c) in enumerate(tris):
        edge[(a, b)] = ti; edge[(b, c)] = ti; edge[(c, a)] = ti

    def is_flat(tri):
        Q = V[tri]
        c_ = float(np.linalg.norm(np.cross(Q[1] - Q[0], Q[2] - Q[0])))
        sp = max(float(np.linalg.norm(Q[1] - Q[0])), float(np.linalg.norm(Q[2] - Q[0])),
                 float(np.linalg.norm(Q[2] - Q[1])))
        return flat(c_, sp)

    def drop(ti):
        a, b, c = tris[ti]
        alive[ti] = False
        for e in ((a, b), (b, c), (c, a)):
            if edge.get(e) == ti:
                del edge[e]

    def add(tri):
        ti = len(tris)
        tris.append(tri); alive.append(True)
        a, b, c = tri
        edge[(a, b)] = ti; edge[(b, c)] = ti; edge[(c, a)] = ti
        return ti

    queue = list(map(int, bad))
    guard = 4 * len(tris) + 16
    while queue and guard > 0:
        guard -= 1
        ti = queue.pop()
        if not alive[ti] or not is_flat(tris[ti]):
            continue
        a, b, c = tris[ti]
        # The middle vertex m and the long edge p->q as this sliver runs it.
        best = None
        for m, (p_, q_) in ((a, (b, c)), (b, (c, a)), (c, (a, b))):
            d = float(np.linalg.norm(V[q_] - V[p_]))
            if best is None or d > best[0]:
                best = (d, m, p_, q_)
        _d, m, p_, q_ = best
        drop(ti)
        # The neighbour runs that edge the other way: q->p. Split it at m.
        tj = edge.get((q_, p_))
        if tj is None or not alive[tj]:
            continue
        tri = tris[tj]
        k = next(i for i in range(3) if tri[i] == q_ and tri[(i + 1) % 3] == p_)
        r = tri[(k + 2) % 3]
        drop(tj)
        for new in ([q_, m, r], [m, p_, r]):
            nt = add(new)
            if is_flat(new):
                queue.append(nt)              # the neighbour was a sliver too: keep going
    return np.asarray([t_ for t_, ok in zip(tris, alive) if ok], dtype=np.int64).reshape(-1, 3)


def _manifold_shell(o, cavs, plate, through, depth, wall) -> Optional[np.ndarray]:
    """The cup of _assemble_shell as booleans: the outer prism, less each cavity over
    [0, depth-wall], each plate hole over [depth-wall, depth], each through-hole over the
    full depth (with wall == depth and no cavities, that is a plain extrusion with holes).
    None when manifold3d is unavailable or the result is empty."""
    try:
        import manifold3d as mf
    except Exception:
        return None
    eps = 0.01                                        # cut tools overshoot the faces they open

    def prism(ring, z0, z1):
        r = np.asarray(ring, float)
        cs = mf.CrossSection([r if signed_area(r) > 0 else r[::-1]])
        return mf.Manifold.extrude(cs, z1 - z0).translate([0.0, 0.0, z0])

    try:
        floor = depth - wall
        solid = prism(o, 0.0, depth)
        for c in cavs:
            solid = solid - prism(c, -eps, floor)
        for h, _k in plate:
            solid = solid - prism(h, floor - eps, depth + eps)
        for h in through:
            solid = solid - prism(h, -eps, depth + eps)
        if solid.is_empty() or solid.volume() <= 0:
            return None
        return manifold_tris(solid)
    except Exception:
        return None


def _print_orientation(tris: np.ndarray, depth: float) -> np.ndarray:
    """Turn it plate-down: a 180-degree rotation about X (y and z both negate), shifted back
    into place. A rotation keeps every triangle's winding, so normals stay outward and the
    part keeps its hand."""
    out = np.array(tris, dtype=np.float64, copy=True)
    ys = out[:, :, 1]
    out[:, :, 1] = (float(ys.min()) + float(ys.max())) - ys
    out[:, :, 2] = depth - out[:, :, 2]
    return out


def _assemble_shell(o, cavs, plate, through, depth, wall, scale) -> Optional[np.ndarray]:
    """One attempt at the cup, built the way up it sits on the vehicle — plate on top
    (z in [depth-wall, depth]), wall band below it, open at z = 0:

        top    z=depth        outer, minus every hole                  facing +Z
        bottom z=0            outer, minus cavities and through-holes  facing -Z
        ceiling z=depth-wall  each cavity, minus its plate holes       facing -Z
        walls: outer and through-holes over the full depth, cavities over [0, depth-wall],
               plate holes over [depth-wall, depth]

    Every edge pairs: each ring's top and bottom edges land on exactly one face each.
    """
    floor = depth - wall

    # The wall band can be one wall thick and a metre long. Unconstrained Delaunay only
    # keeps a boundary edge when nothing across the band falls inside its circle, so the
    # boundary spacing must stay under the band's width — not just under the part's size.
    def _step_for(r):
        bb = r.max(axis=0) - r.min(axis=0)
        base = max(DENSIFY_MIN_MM, DENSIFY_FRAC * float(max(bb)) * scale)
        return min(base, max(DENSIFY_MIN_MM * scale, 0.9 * wall * scale))

    od = _densify(o, _step_for(o))
    cd = [_densify(c, _step_for(c)) for c in cavs]
    pd = [(_densify(h, _step_for(h)), k) for (h, k) in plate]
    td = [_densify(h, _step_for(h)) for h in through]
    if any(len(r) < 3 for r in [od] + cd + [p for p, _ in pd] + td):
        return None

    parts: List[np.ndarray] = []
    for rings, z, up in ([[od] + [p for p, _ in pd] + td, depth, True],
                         [[od] + cd + td, 0.0, False],
                         *[[[c] + [p for p, kk in pd if kk == k], floor, False]
                           for k, c in enumerate(cd)]):
        tri2 = _cap_triangles(rings)
        if len(tri2) == 0:
            return None
        parts.append(_cap_3d(tri2, z, up))

    parts.append(_wall_3d(od, 0.0, depth))
    parts += [_wall_3d(r, 0.0, depth) for r in td]
    parts += [_wall_3d(c, 0.0, floor) for c in cd]
    parts += [_wall_3d(p, floor, depth) for p, _k in pd]
    return np.concatenate(parts).astype(np.float64)


def shell_volume_mm3(outer, cavities, plate_holes, through_holes, depth, wall) -> float:
    """Closed-form volume of the cup, for checking the mesh against: the plate is the outer
    profile less every hole, `wall` thick; the band below it is the outer profile less the
    cavities and through-holes, `depth - wall` tall."""
    A = lambda r: abs(signed_area(np.asarray(r, float)))
    a_o = A(outer)
    a_t = sum(A(h) for h in through_holes)
    a_p = sum(A(h) for h in plate_holes)
    a_c = sum(A(c) for c in cavities)
    return (a_o - a_t - a_p) * wall + (a_o - a_t - a_c) * (depth - wall)
