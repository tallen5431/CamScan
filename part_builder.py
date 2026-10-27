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

Each view is traced in its own photo, at its own scale and orientation. Each is normalised to
its bounding box (origin at the box's minimum), with the long axis laid along X by default
for top and side views, and the user's rotate/flip applied on top. The views are then
registered by extent: the top view sets length and width, the side view (else the end view)
sets height. A view that disagrees with the one setting an axis by up to FIT_TOLERANCE is
stretched to match and the stretch is reported; beyond that the build is refused and the
message names both views — a disagreement that large is a wrong scale, not a tracing wobble.

The booleans are done by manifold3d, which keeps every result a closed, consistently wound
solid. Every mesh is still checked with solid_export.mesh_report before it is returned.
"""
from typing import Any, Dict, List, Optional
import math

import numpy as np

try:
    import manifold3d as mf
except ImportError:  # pragma: no cover - reported to the caller as "unavailable"
    mf = None

from solid_export import _print_orientation, build_shell, mesh_report, signed_area

ROLES = ("top", "side", "end")
# (u axis, v axis, normal axis) of each view in X=0, Y=1, Z=2.
AXES = {"top": (0, 1, 2), "side": (0, 2, 1), "end": (1, 2, 0)}
AXIS_NAMES = ("length", "width", "height")
# Who sets each axis, in order of preference. The top view is usually the best-measured
# photo (the reference lies flat on the face being traced), so it leads where it can.
AXIS_SOURCES = {0: ("top", "side"), 1: ("top", "end"), 2: ("side", "end")}

FIT_TOLERANCE = 0.08          # views may disagree by this much before the build is refused
MAX_CLEARANCE_MM = 10.0
MAX_POINTS = 20000
THIN_WALL_MM = 0.8            # two nozzle widths: thinner walls print unreliably
_MARGIN = 1.0                 # prisms overshoot the part box so faces never coincide


class PartError(ValueError):
    """A reason the part cannot be built, worded for the person building it."""


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


class View:
    """One traced outline, oriented into its (u, v) plane with the bounding box at 0."""

    def __init__(self, role: str, pts, rot: int = 0, flip_h: bool = False,
                 flip_v: bool = False, auto: bool = True):
        self.role = role
        raw = _ring(pts, f"{role} view")
        w, h = np.ptp(raw, axis=0)
        # A top or side view of a long part is laid along X unless told otherwise; the user's
        # quarter-turns are applied on top of this, so the preview shows the combined result.
        self.auto_rot = 1 if (auto and role in ("top", "side") and h > w) else 0
        self.turns = (self.auto_rot + int(rot)) % 4
        self.flip_h, self.flip_v = bool(flip_h), bool(flip_v)
        base = self._orient(raw)
        self.lo = base.min(axis=0)
        self.pts = base - self.lo
        self.scale = np.ones(2)

    def _orient(self, q: np.ndarray) -> np.ndarray:
        q = _quarter_turns(np.asarray(q, float), self.turns)
        if self.flip_h:
            q = q * [-1.0, 1.0]
        if self.flip_v:
            q = q * [1.0, -1.0]
        return q

    def place(self, q: np.ndarray) -> np.ndarray:
        """Another outline from the SAME photo, put through this view's exact transform."""
        return (self._orient(q) - self.lo) * self.scale

    @property
    def extent(self) -> np.ndarray:
        return np.ptp(self.pts, axis=0)

    def stretch(self, axis_index: int, factor: float):
        self.pts = self.pts.copy()
        self.pts[:, axis_index] *= factor
        self.scale[axis_index] *= factor


def _ccw(p: np.ndarray) -> np.ndarray:
    return p if signed_area(p) > 0 else p[::-1]


def _cross_section(p: np.ndarray):
    return mf.CrossSection([_ccw(np.asarray(p, float))])


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
    mesh = man.to_mesh()
    v = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    t = np.asarray(mesh.tri_verts, dtype=np.int64)
    return v[t]


# ---------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------
def build_part(views: Dict[str, Dict[str, Any]], depth_mm: Optional[float] = None,
               cavity: Optional[Dict[str, Any]] = None, clearance_mm: float = 0.0,
               wall_mm: Optional[float] = None, mirror: bool = False,
               upside_down: bool = False, preview: bool = False) -> Dict[str, Any]:
    """Build the part. Raises PartError with a user-facing reason when it cannot.

    views   : {"top"|"side"|"end": {"points": [[u, v], ...] in mm, "rot": quarter turns,
               "flip_h": bool, "flip_v": bool}} — any combination that sets all three axes,
               with depth_mm standing in for one missing axis.
    cavity  : {"points", "view", "rot", "flip_h", "flip_v", "same_photo": bool,
               "align": "bottom"|"center"|"top", "open": "through"|"min"|"max",
               "depth_mm"} — material removed, traced in `view`, grown by clearance_mm.
    wall_mm : uniform wall instead of a traced cavity; only for a lone top view + depth.
    mirror  : the opposite-hand part (the twin from the other side of the vehicle).
    Returns {"tris" (absent in preview), "info", "preview"}.
    """
    if mf is None:
        raise PartError("3D part building needs the manifold3d package on the server.")
    info: Dict[str, Any] = {"fits": [], "warnings": [], "kind": None}

    vs: Dict[str, View] = {}
    for role in ROLES:
        spec = (views or {}).get(role)
        if not spec:
            continue
        vs[role] = View(role, spec.get("points"), int(spec.get("rot") or 0),
                        bool(spec.get("flip_h")), bool(spec.get("flip_v")))
    if not vs:
        raise PartError("Assign at least one outline to a view (Top, Side or End).")

    # Who sets each axis, and how long it is.
    size = [None, None, None]
    setter = [None, None, None]
    for axis in range(3):
        for role in AXIS_SOURCES[axis]:
            if role in vs:
                k = AXES[role].index(axis)
                size[axis] = float(vs[role].extent[k])
                setter[axis] = role
                break
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

    # Register: every view must agree with whichever view sets each of its axes.
    for role, view in vs.items():
        for k, axis in enumerate(AXES[role][:2]):
            if setter[axis] == role:
                continue
            have = float(view.extent[k])
            want = float(size[axis])
            if have <= 0:
                raise PartError(f"The {role} view has no {AXIS_NAMES[axis]}.")
            off = have / want - 1.0
            if abs(off) > FIT_TOLERANCE:
                raise PartError(
                    "The {} view's {} is {:.0f}% {} than the {} view's ({:.1f} vs {:.1f} mm). "
                    "One of those photos is scaled wrong — check its reference.".format(
                        role, AXIS_NAMES[axis], abs(off) * 100,
                        "longer" if off > 0 else "shorter", setter[axis], have, want))
            view.stretch(k, want / have)
            info["fits"].append({"view": role, "axis": AXIS_NAMES[axis],
                                 "pct": round(off * 100, 2)})

    X, Y, Z = size
    info["size_mm"] = [round(X, 3), round(Y, 3), round(Z, 3)]

    # Uniform wall: the existing, measured shell builder, for the case it was built for.
    if wall_mm is not None and float(wall_mm) > 0:
        if cavity:
            raise PartError("Use either a uniform wall or a traced cavity, not both.")
        if set(vs) != {"top"}:
            raise PartError("A uniform wall works on a Top outline alone. For a shaped part, "
                            "trace the cavity as its own outline instead.")
        tris, sinfo = build_shell(vs["top"].pts.tolist(), [], Z, float(wall_mm))
        if tris is None:
            raise PartError(sinfo.get("reason") or "Could not build the cover.")
        info.update(kind="shell", note=sinfo.get("note"), wall_mm=float(wall_mm))
        out = {"info": info}
        if preview:
            # build_shell delivers it plate-down; that turn is its own inverse, so undo it to
            # draw the cover as traced, on top of its outline.
            out["preview"] = _preview_from_tris(_print_orientation(tris, Z), vs, None)
        if mirror:
            tris = _mirror_tris(tris)
        info["mirrored"] = bool(mirror)
        rep = mesh_report(tris)
        info["volume_mm3"] = rep["volume_mm3"]
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
                      bool(cavity.get("flip_v")), auto=False)
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

    # The preview draws the part as traced — before mirroring or turning — so its silhouettes
    # sit on the outlines they came from, which is what the preview is for: checking that
    # the views line up. The dialog says separately that the download is mirrored/turned.
    shape = solid
    if upside_down:
        # A rotation, never a flip: turning a part over must not change its hand.
        solid = solid.rotate([180.0, 0.0, 0.0]).translate([0.0, Y, Z])
    if mirror:
        solid = solid.mirror([1.0, 0.0, 0.0]).translate([X, 0.0, 0.0])
    info["mirrored"] = bool(mirror)
    info["upside_down"] = bool(upside_down)

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
            "cavity": cav, "size_mm": None}
