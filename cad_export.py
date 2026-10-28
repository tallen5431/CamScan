"""Write a part as something a CAD program can EDIT, not just display.

An STL is a bag of triangles. Fusion 360 (and every other CAD program) opens it as a mesh,
and turning a mesh into a body makes one face per triangle — a flat side of the part comes
in as dozens of slivers, and press-pull or fillet has nothing sensible to grab.

The parts CamScan builds are polyhedra: every face is flat. So the triangles are merged back
into those flat faces here, and written two ways:

    write_step(tris, path)  ISO 10303-21 (STEP, AP214): a solid B-rep — one planar face per
                            flat face of the part, holes and all. Fusion opens it as a body
                            directly; nothing to convert.
    write_obj(tris, path)   Wavefront OBJ with one polygon per flat face. Converting it to a
                            body in Fusion gives one face per polygon. A face with a hole in it
                            (an OBJ polygon cannot have one) is written as its triangles.

Both take the closed, outward-wound triangle soup the STL writer takes (T, 3, 3) in mm.
"""
from typing import Dict, List, Tuple
import datetime

import numpy as np

_Q = 1e6            # positions are joined at 1e-6 mm, the quantum mesh_report pairs edges by


# ---------------------------------------------------------------------------------------
# Triangles -> flat faces with boundary loops
# ---------------------------------------------------------------------------------------
def _weld(tris: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Unique vertices (V, 3) and triangles as vertex indices (T, 3); collapsed ones dropped,
    and zero-area slivers (three points on one line) resolved — see _unsliver."""
    t = np.asarray(tris, dtype=float).reshape(-1, 3, 3)
    key = np.round(t.reshape(-1, 3) * _Q).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    idx = inv.reshape(-1, 3)
    ok = (idx[:, 0] != idx[:, 1]) & (idx[:, 1] != idx[:, 2]) & (idx[:, 2] != idx[:, 0])
    V = uniq.astype(float) / _Q
    return V, _unsliver(V, idx[ok])


def _unsliver(V: np.ndarray, F: np.ndarray) -> np.ndarray:
    """Remove zero-area triangles without opening the surface.

    The solid library can leave a sliver whose three corners lie on one line (seen where a
    rounded inner offset meets a chamfer: a 0.09 mm edge). It is closed by vertex index and
    harmless in an STL, but a CAD face needs area. Such a sliver says the long edge p-q is
    really p-m-q: so drop it and split the triangle across p-q at m — the surface stays closed.
    """
    tris = [list(map(int, f)) for f in F]
    for _ in range(len(tris)):
        bad = None
        for ti, (a, b, c) in enumerate(tris):
            P = V[[a, b, c]]
            cr = np.linalg.norm(np.cross(P[1] - P[0], P[2] - P[0]))
            span = max(np.linalg.norm(P[1] - P[0]), np.linalg.norm(P[2] - P[0]), np.linalg.norm(P[2] - P[1]))
            if cr <= 1e-9 * max(span, 1e-12) ** 2:
                bad = ti
                break
        if bad is None:
            break
        a, b, c = tris.pop(bad)
        # The middle vertex m and the long edge p->q as this sliver runs it.
        best = None
        for m, (p_, q_) in ((a, (b, c)), (b, (c, a)), (c, (a, b))):
            d = np.linalg.norm(V[q_] - V[p_])
            if best is None or d > best[0]:
                best = (d, m, p_, q_)
        _d, m, p_, q_ = best
        # The neighbour runs that edge the other way: q->p. Split it at m.
        for tj, tri in enumerate(tris):
            for k in range(3):
                if tri[k] == q_ and tri[(k + 1) % 3] == p_:
                    r = tri[(k + 2) % 3]
                    tris[tj] = [q_, m, r]
                    tris.append([m, p_, r])
                    break
            else:
                continue
            break
    return np.asarray(tris, dtype=np.int64).reshape(-1, 3)


def _faces(V: np.ndarray, F: np.ndarray, angle_tol: float = 1e-6):
    """Group triangles into flat faces: neighbours (sharing an edge) on the same plane.

    Returns a list of (normal, [loops], [triangle indices]) — loops of vertex indices, the
    first the outer one (counter-clockwise seen from outside), the rest holes (clockwise)."""
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    n = np.cross(b - a, c - a)
    area2 = np.linalg.norm(n, axis=1)
    n = n / np.maximum(area2, 1e-300)[:, None]
    d = np.einsum("ij,ij->i", n, a)

    # Triangle adjacency through shared undirected edges.
    edge_tris: Dict[Tuple[int, int], List[int]] = {}
    for ti, (i, j, k) in enumerate(F):
        for u, v in ((i, j), (j, k), (k, i)):
            edge_tris.setdefault((min(u, v), max(u, v)), []).append(ti)

    scale = float(np.ptp(V, axis=0).max()) or 1.0
    group = -np.ones(len(F), dtype=int)
    groups: List[List[int]] = []
    for seed in range(len(F)):
        if group[seed] >= 0:
            continue
        gid = len(groups)
        group[seed] = gid
        stack, members = [seed], [seed]
        while stack:
            ti = stack.pop()
            i, j, k = F[ti]
            for u, v in ((i, j), (j, k), (k, i)):
                for tj in edge_tris[(min(u, v), max(u, v))]:
                    if group[tj] >= 0:
                        continue
                    if (np.dot(n[ti], n[tj]) > 1.0 - angle_tol
                            and abs(d[ti] - d[tj]) <= 1e-6 * scale):
                        group[tj] = gid
                        stack.append(tj)
                        members.append(tj)
        groups.append(members)

    faces = []
    for members in groups:
        # Boundary: directed edges of the group whose reverse is not in it.
        directed = set()
        for ti in members:
            i, j, k = F[ti]
            directed.update(((i, j), (j, k), (k, i)))
        nxt: Dict[int, List[int]] = {}
        for (u, v) in directed:
            if (v, u) not in directed:
                nxt.setdefault(u, []).append(v)
        loops = []
        used = set()
        for start in list(nxt):
            for first in nxt[start]:
                if (start, first) in used:
                    continue
                loop, u, v = [start], start, first
                used.add((u, v))
                guard = 0
                while v != start and guard < 1_000_000:
                    loop.append(v)
                    cand = [w for w in nxt.get(v, []) if (v, w) not in used]
                    if not cand:
                        break
                    u, v = v, cand[0]
                    used.add((u, v))
                    guard += 1
                if v == start and len(loop) >= 3:
                    loops.append(loop)
        if not loops:
            continue
        # Normal of the face: area-weighted mean of its triangles'.
        nf = np.sum(n[members] * area2[members, None], axis=0)
        nf /= max(float(np.linalg.norm(nf)), 1e-300)

        def loop_area(lp):
            P = V[lp]
            return float(np.dot(np.sum(np.cross(P, np.roll(P, -1, axis=0)), axis=0), nf)) / 2.0
        loops.sort(key=lambda lp: -abs(loop_area(lp)))
        faces.append((nf, loops, members))
    return faces


def _drop_collinear(V: np.ndarray, faces):
    """Remove loop vertices that sit on a straight run AND belong to no other edge — what a
    triangulation leaves along a long side. Shared consistently by both faces of the edge,
    because a vertex is only removed when every loop it appears in sees it as straight."""
    count: Dict[int, int] = {}
    straight: Dict[int, bool] = {}
    for _nf, loops, _m in faces:
        for lp in loops:
            m = len(lp)
            for t in range(m):
                p, q, r = V[lp[t - 1]], V[lp[t]], V[lp[(t + 1) % m]]
                u, w = q - p, r - q
                lu, lw = np.linalg.norm(u), np.linalg.norm(w)
                s = (lu > 0 and lw > 0
                     and np.linalg.norm(np.cross(u, w)) <= 1e-9 * lu * lw
                     and np.dot(u, w) > 0)
                v_ = lp[t]
                count[v_] = count.get(v_, 0) + 1
                straight[v_] = straight.get(v_, True) and bool(s)
    # On a closed solid a vertex in the middle of an edge appears in exactly the two loops
    # either side of it.
    drop = {v_ for v_, ok in straight.items() if ok and count[v_] == 2}
    out = []
    for nf, loops, members in faces:
        nl = [[v_ for v_ in lp if v_ not in drop] for lp in loops]
        out.append((nf, [lp for lp in nl if len(lp) >= 3], members))
    return out


def flat_faces(tris: np.ndarray):
    """(vertices, faces) — see _faces — with straight-run vertices removed."""
    V, F = _weld(tris)
    if len(F) == 0:
        raise ValueError("no triangles")
    return V, _drop_collinear(V, _faces(V, F))


# ---------------------------------------------------------------------------------------
# OBJ
# ---------------------------------------------------------------------------------------
def write_obj(tris: np.ndarray, path: str, name: str = "part") -> Dict[str, int]:
    """One polygon per flat face; a face with a hole in it (an OBJ polygon cannot have one) as
    its triangles. Vertices on straight runs are kept here, so polygons and triangles always
    meet vertex to vertex."""
    V, F = _weld(tris)
    if len(F) == 0:
        raise ValueError("no triangles")
    lines = ["# CamScan part: one polygon per flat face, millimetres", f"o {name}"]
    lines += [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in V]
    polys = tri_faces = 0
    for _nf, loops, members in _faces(V, F):
        if len(loops) == 1:
            lines.append("f " + " ".join(str(i + 1) for i in loops[0]))
            polys += 1
        else:
            for ti in members:
                lines.append("f " + " ".join(str(i + 1) for i in F[ti]))
                tri_faces += 1
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return {"polygons": polys, "triangles": tri_faces}


# ---------------------------------------------------------------------------------------
# STEP (AP214) — a solid B-rep of planar faces
# ---------------------------------------------------------------------------------------
def _fmt(x: float) -> str:
    s = repr(float(x))
    if "e" in s or "E" in s:
        m, e = s.lower().split("e")
        if "." not in m:
            m += "."
        return f"{m}E{int(e)}"
    return s if "." in s else s + "."


def write_step(tris: np.ndarray, path: str, name: str = "part") -> Dict[str, int]:
    V, faces = flat_faces(tris)
    ents: List[str] = []

    def add(s: str) -> int:
        ents.append(s)
        return len(ents)

    def pt(p):
        return add("CARTESIAN_POINT('',({},{},{}))".format(*(_fmt(c) for c in p)))

    def direction(v):
        v = np.asarray(v, float)
        v = v / max(float(np.linalg.norm(v)), 1e-300)
        return add("DIRECTION('',({},{},{}))".format(*(_fmt(c) for c in v)))

    app = add("APPLICATION_CONTEXT('automotive design')")
    add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{app})")
    pctx = add(f"PRODUCT_CONTEXT('',#{app},'mechanical')")
    prod = add(f"PRODUCT('{name}','{name}','',(#{pctx}))")
    pdf = add(f"PRODUCT_DEFINITION_FORMATION('','',#{prod})")
    pdc = add(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app},'design')")
    pd = add(f"PRODUCT_DEFINITION('design','',#{pdf},#{pdc})")
    pds = add(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")
    mm = add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
    rad = add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    sr = add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
    unc = add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-06),#{mm},'distance_accuracy_value','confusion accuracy')")
    ctx = add("(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{}))"
              "GLOBAL_UNIT_ASSIGNED_CONTEXT((#{},#{},#{}))REPRESENTATION_CONTEXT('',''))".format(unc, mm, rad, sr))

    vert = {}
    for i in sorted({v_ for _nf, loops, _m in faces for lp in loops for v_ in lp}):
        vert[i] = add(f"VERTEX_POINT('',#{pt(V[i])})")
    edge = {}

    def edge_of(u, v):
        key = (min(u, v), max(u, v))
        if key not in edge:
            a, b = V[key[0]], V[key[1]]
            L = float(np.linalg.norm(b - a))
            vec = add(f"VECTOR('',#{direction(b - a)},{_fmt(L)})")
            line = add(f"LINE('',#{pt(a)},#{vec})")
            edge[key] = add(f"EDGE_CURVE('',#{vert[key[0]]},#{vert[key[1]]},#{line},.T.)")
        return edge[key], (u, v) == (min(u, v), max(u, v))

    face_ids = []
    for nf, loops, _m in faces:
        bounds = []
        for li, lp in enumerate(loops):
            oes = []
            for t in range(len(lp)):
                e, fwd = edge_of(lp[t], lp[(t + 1) % len(lp)])
                oes.append(add(f"ORIENTED_EDGE('',*,*,#{e},{'.T.' if fwd else '.F.'})"))
            loop = add("EDGE_LOOP('',({}))".format(",".join(f"#{o}" for o in oes)))
            kind = "FACE_OUTER_BOUND" if li == 0 else "FACE_BOUND"
            bounds.append(add(f"{kind}('',#{loop},.T.)"))
        o = V[loops[0][0]]
        ref = V[loops[0][1]] - o
        ref = ref - nf * float(np.dot(ref, nf))
        if np.linalg.norm(ref) < 1e-12:
            ref = np.cross(nf, [1.0, 0.0, 0.0] if abs(nf[0]) < 0.9 else [0.0, 1.0, 0.0])
        ax = add(f"AXIS2_PLACEMENT_3D('',#{pt(o)},#{direction(nf)},#{direction(ref)})")
        plane = add(f"PLANE('',#{ax})")
        face_ids.append(add("ADVANCED_FACE('',({}),#{},.T.)".format(",".join(f"#{b}" for b in bounds), plane)))

    shell = add("CLOSED_SHELL('',({}))".format(",".join(f"#{f}" for f in face_ids)))
    brep = add(f"MANIFOLD_SOLID_BREP('{name}',#{shell})")
    origin = add(f"AXIS2_PLACEMENT_3D('',#{pt([0, 0, 0])},#{direction([0, 0, 1])},#{direction([1, 0, 0])})")
    rep = add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('{name}',(#{origin},#{brep}),#{ctx})")
    add(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{rep})")

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    out = ["ISO-10303-21;", "HEADER;",
           "FILE_DESCRIPTION(('CamScan part'),'2;1');",
           f"FILE_NAME('{name}.step','{stamp}',(''),(''),'CamScan','CamScan','');",
           "FILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));",
           "ENDSEC;", "DATA;"]
    out += [f"#{i + 1}={e};" for i, e in enumerate(ents)]
    out += ["ENDSEC;", "END-ISO-10303-21;"]
    with open(path, "w", encoding="ascii") as f:
        f.write("\n".join(out) + "\n")
    return {"faces": len(face_ids), "edges": len(edge), "vertices": len(vert)}
