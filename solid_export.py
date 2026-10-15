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
slicer reads it.

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


def _snap_index(rings: List[np.ndarray]) -> Dict[Tuple[int, int], np.ndarray]:
    """Lookup from a micron-quantised key to the EXACT ring vertex at that spot."""
    idx: Dict[Tuple[int, int], np.ndarray] = {}
    for r in rings:
        for p in r:
            idx[(int(round(p[0] / _SNAP_MM)), int(round(p[1] / _SNAP_MM)))] = p
    return idx


def _snap(p: np.ndarray, idx: Dict[Tuple[int, int], np.ndarray]) -> Optional[np.ndarray]:
    """The exact ring vertex `p` came from, or None if it is not one."""
    kx, ky = int(round(p[0] / _SNAP_MM)), int(round(p[1] / _SNAP_MM))
    hit = idx.get((kx, ky))
    if hit is not None:
        return hit
    # The float32 drift can push a point just across a bucket edge.
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            hit = idx.get((kx + dx, ky + dy))
            if hit is not None:
                return hit
    return None


def _cap_triangles(rings: List[np.ndarray]) -> List[np.ndarray]:
    """Triangulate the region inside rings[0] and outside rings[1:]."""
    pts = np.vstack(rings)
    lo = pts.min(axis=0) - 10.0
    hi = pts.max(axis=0) + 10.0
    span = hi - lo
    if not np.all(np.isfinite(span)) or np.any(span <= 0):
        return []
    sub = cv2.Subdiv2D((float(lo[0]), float(lo[1]), float(span[0]), float(span[1])))
    for p in pts:
        try:
            sub.insert((float(p[0]), float(p[1])))
        except cv2.error:
            continue                       # a duplicate/degenerate point is simply skipped
    outer = rings[0].astype(np.float32)
    holes = [r.astype(np.float32) for r in rings[1:]]
    idx = _snap_index(rings)
    keep: List[np.ndarray] = []
    for t in sub.getTriangleList():
        tri = np.asarray(t, dtype=float).reshape(3, 2)
        # Subdiv2D returns triangles touching its virtual super-triangle; they fall outside
        # the inserted bounds and must go.
        if np.any(tri < lo) or np.any(tri > hi):
            continue
        # Put the vertices back on the exact ring coordinates the walls will use.
        snapped = [_snap(v, idx) for v in tri]
        if any(v is None for v in snapped):
            continue
        tri = np.asarray(snapped, dtype=float)
        c = tri.mean(axis=0)
        cx, cy = float(c[0]), float(c[1])
        if cv2.pointPolygonTest(outer, (cx, cy), False) < 0:
            continue
        if any(cv2.pointPolygonTest(h, (cx, cy), False) > 0 for h in holes):
            continue
        keep.append(tri)
    return keep


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
        if candidate is not None and mesh_report(candidate)["watertight"]:
            tris = candidate
            break
        scale /= 2.0                      # refine every ring together and try again
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
    if not caps2d:
        return None

    tris: List[np.ndarray] = []

    # Caps. Delaunay hands back arbitrary winding, so force it: +Z normal on top, -Z on the
    # bottom. Getting this wrong is the classic "slicer prints it inside out" bug.
    for tri in caps2d:
        ccw = np.cross(tri[1] - tri[0], tri[2] - tri[0]) > 0
        top = tri if ccw else tri[::-1]
        tris.append(np.column_stack([top, np.full(3, depth)]))
        bot = tri[::-1] if ccw else tri
        tris.append(np.column_stack([bot, np.zeros(3)]))

    # Walls. Outer rings run CCW and holes CW, so this one rule faces every wall away from
    # the material.
    for r in rings:
        n = len(r)
        for i in range(n):
            a, b = r[i], r[(i + 1) % n]
            a0 = np.array([a[0], a[1], 0.0]); b0 = np.array([b[0], b[1], 0.0])
            a1 = np.array([a[0], a[1], depth]); b1 = np.array([b[0], b[1], depth])
            tris.append(np.array([a0, b0, b1]))
            tris.append(np.array([a0, b1, a1]))

    return np.asarray(tris, dtype=np.float64)


def mesh_report(tris: np.ndarray) -> Dict[str, Any]:
    """Facts a slicer cares about, so they can be asserted rather than hoped for.

    watertight : every undirected edge is shared by exactly two triangles
    volume_mm3 : signed volume via the divergence theorem — positive means outward normals
    open_edges : how many edges fail the pairing (0 on a closed mesh)
    """
    if tris is None or len(tris) == 0:
        return {"triangles": 0, "watertight": False, "volume_mm3": 0.0, "open_edges": 0}

    # Quantise before pairing edges: cap and wall vertices are built from the same densified
    # rings so they should coincide exactly, but float arithmetic can leave the last bit off
    # and a 1-ulp gap would read as a hole that is not there.
    q = np.round(np.asarray(tris, dtype=float) * 1e6).astype(np.int64)
    edges: Dict[Tuple[Tuple[int, int, int], Tuple[int, int, int]], int] = {}
    for t in q:
        for i in range(3):
            a = tuple(int(v) for v in t[i])
            b = tuple(int(v) for v in t[(i + 1) % 3])
            edges[(a, b) if a <= b else (b, a)] = edges.get((a, b) if a <= b else (b, a), 0) + 1
    open_edges = sum(1 for n in edges.values() if n != 2)

    a, b, c = tris[:, 0, :], tris[:, 1, :], tris[:, 2, :]
    vol = float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)
    return {"triangles": int(len(tris)), "watertight": open_edges == 0,
            "volume_mm3": vol, "open_edges": int(open_edges)}


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
