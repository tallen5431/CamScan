"""Square up a photo from two edges that are parallel on the part.

A photo taken at an angle shrinks the far end of the part: its two long sides, parallel on
the part, converge in the picture. A scale from a ruler then holds only near the ruler —
measured on a customer's top-down photo of a step pad, the cover was 289 px wide at one end
and 306 px at the other, so the width read about 3% small at one end and 3% large at the
other, and the length was distorted with it.

The cure needs no extra object. Where the two sides meet in the image (their vanishing
point) is the direction of the part's length in 3D, given the camera's focal length. If the
camera tilted along the part — the usual case, standing at one end of a long part and aiming
down at it — that also fixes the plane: its other direction lies square to the length and to
the line of sight. Every pixel then maps onto that plane, and the ruler sets the scale.

    square_up(side1, side2, ruler, ruler_mm, image_w, image_h, focal_px=None)
        -> {"H": 3x3 image px -> plane mm, "tilt_deg", "width_change_pct", "focal_px", ...}

Exact when the camera tilted only along the part; it ignores the much smaller part of the
error from tilting sideways as well. A rectangle of known size laid on the face (Set scale ->
rectangle) is exact in every case and needs no focal length.
"""
from typing import Any, Dict, Optional, Sequence
import math

import numpy as np

_MIN_CONVERGE_DEG = 0.15      # sides closer to parallel than this: the photo is already square


class SquareUpError(ValueError):
    """Why the photo cannot be squared up, in words for the person doing it."""


def _pt(p, what):
    try:
        x, y = float(p[0]), float(p[1])
    except (TypeError, ValueError, IndexError):
        raise SquareUpError(f"{what} is not a point.")
    if not (math.isfinite(x) and math.isfinite(y)):
        raise SquareUpError(f"{what} is not a finite point.")
    return np.array([x, y, 1.0])


def _seg(s, what):
    if not isinstance(s, (list, tuple)) or len(s) != 2:
        raise SquareUpError(f"{what} needs two end points.")
    a, b = _pt(s[0], what), _pt(s[1], what)
    if np.hypot(*(b - a)[:2]) < 5.0:
        raise SquareUpError(f"{what} is too short to use.")
    return a, b


def square_up(side1: Sequence, side2: Sequence, ruler: Sequence, ruler_mm: float,
              image_w: float, image_h: float, focal_px: Optional[float] = None) -> Dict[str, Any]:
    a1, b1 = _seg(side1, "The first side")
    a2, b2 = _seg(side2, "The second side")
    ra, rb = _seg(ruler, "The scale line")
    try:
        L_mm = float(ruler_mm)
        W, Hh = float(image_w), float(image_h)
    except (TypeError, ValueError):
        raise SquareUpError("The scale line's length and the image size must be numbers.")
    if not (math.isfinite(L_mm) and L_mm > 0 and W > 0 and Hh > 0):
        raise SquareUpError("The scale line's length and the image size must be positive.")
    from camera_geometry import default_focal_px
    f_src = "exif"
    try:
        f = float(focal_px) if focal_px else 0.0
    except (TypeError, ValueError):
        f = 0.0
    if not (math.isfinite(f) and f > 0):
        f, f_src = default_focal_px(W), "assumed"

    d1_img = (b1 - a1)[:2] / np.hypot(*(b1 - a1)[:2])
    d2_img = (b2 - a2)[:2] / np.hypot(*(b2 - a2)[:2])
    if np.dot(d1_img, d2_img) < 0:
        d2_img = -d2_img
    angle = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(d1_img, d2_img))))))
    if angle > 25.0:
        raise SquareUpError("Those two edges are not near-parallel in the photo — pick the "
                            "part's two long sides.")
    # The two must be different edges, not two pieces of one.
    n_img = np.array([-d1_img[1], d1_img[0]])
    sep = abs(float(np.dot((a2 - a1)[:2], n_img)))
    if sep < 10.0:
        raise SquareUpError("Those two edges lie on one line — pick the part's two opposite sides.")

    K = np.array([[f, 0.0, W / 2.0], [0.0, f, Hh / 2.0], [0.0, 0.0, 1.0]])
    Ki = np.linalg.inv(K)
    if angle < _MIN_CONVERGE_DEG:
        # Already parallel in the photo: the plane is square to the camera along the part.
        d1 = np.array([d1_img[0], d1_img[1], 0.0])
    else:
        v = np.cross(np.cross(a1, b1), np.cross(a2, b2))     # vanishing point (homogeneous)
        d1 = Ki @ v
        # Point d1 the way the sides run in the image, toward the vanishing point.
        if np.dot(d1[:2] / max(np.linalg.norm(d1[:2]), 1e-12), d1_img) < 0:
            d1 = -d1
    d1 = d1 / np.linalg.norm(d1)
    z = np.array([0.0, 0.0, 1.0])
    d2 = np.cross(d1, z)
    if np.linalg.norm(d2) < 1e-6:
        raise SquareUpError("The camera was looking straight along the part — retake it from "
                            "further above.")
    d2 /= np.linalg.norm(d2)
    nrm = np.cross(d1, d2)
    nrm /= np.linalg.norm(nrm)
    tilt = math.degrees(math.acos(min(1.0, abs(float(nrm[2])))))
    if tilt > 70.0:
        raise SquareUpError("The photo is too steeply angled to square up reliably ({:.0f}°). "
                            "Retake it from further above.".format(tilt))

    # Image -> plane: the ray through the pixel meets the plane n.X = 1; read it in (d1, d2).
    H0 = np.vstack([d1, d2, nrm]) @ Ki

    def to_plane(H, p):
        q = H @ p
        if abs(q[2]) < 1e-12:
            raise SquareUpError("A point lies on the horizon of that plane — retake the photo.")
        return q[:2] / q[2]

    # Keep the photo's own orientation and handedness, so the squared-up part is not turned
    # or mirrored: map the image's x direction (at the ruler) onto +x of the plane.
    mid = (ra + rb) / 2.0
    p0 = to_plane(H0, mid)
    px_ = to_plane(H0, mid + np.array([10.0, 0.0, 0.0])) - p0
    py_ = to_plane(H0, mid + np.array([0.0, 10.0, 0.0])) - p0
    if px_[0] * py_[1] - px_[1] * py_[0] < 0:                 # mirrored: flip the second axis
        H0 = np.diag([1.0, -1.0, 1.0]) @ H0
        p0 = to_plane(H0, mid)
        px_ = to_plane(H0, mid + np.array([10.0, 0.0, 0.0])) - p0
    th = -math.atan2(px_[1], px_[0])
    Rz = np.array([[math.cos(th), -math.sin(th), 0.0], [math.sin(th), math.cos(th), 0.0], [0.0, 0.0, 1.0]])
    H0 = Rz @ H0

    L0 = float(np.hypot(*(to_plane(H0, rb) - to_plane(H0, ra))))
    if not (L0 > 0):
        raise SquareUpError("The scale line has no length on the squared-up plane.")
    s = L_mm / L0
    H = np.diag([s, s, 1.0]) @ H0
    # Anchor the plane's origin at the image's top-left, so coordinates stay near where a
    # uniform scale would put them (only shapes and sizes matter, but text and exports
    # read better).
    o = to_plane(H, np.array([0.0, 0.0, 1.0]))
    H = np.array([[1.0, 0.0, -o[0]], [0.0, 1.0, -o[1]], [0.0, 0.0, 1.0]]) @ H
    H = H / H[2, 2] if abs(H[2, 2]) > 1e-12 else H

    # How much the sides' apparent width changed along the part, for the person to see why.
    def width_at(t):
        p = a1 + (b1 - a1) * t
        q = np.cross(np.cross(a2, b2), np.cross(p, p + np.array([n_img[0], n_img[1], 0.0])))
        q = q / q[2]
        return float(np.hypot(*(q - p)[:2]))
    w0, w1 = width_at(0.0), width_at(1.0)
    return {"H": H.tolist(), "tilt_deg": round(tilt, 2), "focal_px": round(f, 1),
            "focal_source": f_src, "converge_deg": round(angle, 3),
            "width_change_pct": round(100.0 * (max(w0, w1) / max(min(w0, w1), 1e-9) - 1.0), 2)}


def long_sides(points: Sequence) -> Sequence:
    """The two long sides of an outline that are parallel on the part: straighten it, then
    take the longest side and the longest other side running within 25 degrees of it that is
    not on the same line. Returns [[a, b], [c, d]] in image px."""
    from outline_cleanup import straighten_outline
    st = straighten_outline(None, points)
    P = np.asarray(st["points"] if st else points, dtype=float).reshape(-1, 2)
    n = len(P)
    if n < 4:
        raise SquareUpError("Trace the part's outline first — it needs two long sides.")
    segs = sorted(((float(np.hypot(*(P[(i + 1) % n] - P[i]))), i) for i in range(n)), reverse=True)
    L0, i0 = segs[0]
    a, b = P[i0], P[(i0 + 1) % n]
    u = (b - a) / L0
    nrm = np.array([-u[1], u[0]])
    for L1, i1 in segs[1:]:
        c, d = P[i1], P[(i1 + 1) % n]
        v = (d - c) / L1
        if abs(float(np.dot(u, v))) < math.cos(math.radians(25)):
            continue
        if abs(float(np.dot(c - a, nrm))) < 0.1 * L0:
            continue
        if L1 < 0.25 * L0:
            break
        return [[a.tolist(), b.tolist()], [c.tolist(), d.tolist()]]
    raise SquareUpError("This outline has no two long parallel sides to square up from.")
