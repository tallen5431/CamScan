"""Working distance, out-of-plane (parallax) error, and height-corrected homographies.

WHY THIS EXISTS
---------------
A homography rectifies exactly one plane: the marker's. Any feature that is NOT in that
plane is measured wrong, and the error is pure perspective magnification:

    measured / true  =  d / (d - h)

for a feature h mm above the marker plane seen from working distance d. Measured against
the real pipeline on synthetic renders with a known camera pose, at d = 400 mm:

    feature height h :    0 mm    5 mm   10 mm   20 mm   40 mm
    measurement error:   0.0%   +1.3%   +2.6%   +5.3%  +11.1%

Two things about that table matter:

  * It does not go away in a square-on shot. The same +11.1% appears at 0 deg of tilt, so
    "hold the phone flat" is not the fix; the homography already handles tilt well (an
    in-plane length measures within ~2% out to 40 deg of tilt). Tilt does still matter
    here, but only through d: the PERPENDICULAR distance to the plane shrinks as the
    camera swings over, so the same feature height costs more the more tilted the shot.
  * It dwarfs every other error source in the pipeline. Corner-localisation noise costs
    ~1-2%; a part merely 20 mm thick costs +5.3% — a 5 mm error on a 100 mm dimension.

So the dominant error in this app is not detection quality. It is that the customer lays
the card on the table and measures the top face of a part that has thickness.

The exact fix costs nothing and needs no maths: put the card ON the face being measured,
so h = 0. That is what `parallax_advice()` tells the user. When the photo has already been
taken and the part thickness is known, `homography_at_height()` corrects for it instead.

FOCAL LENGTH
------------
Both the error estimate and the correction need the working distance d, and d needs the
focal length in pixels. Two sources, in order of trust:

  * EXIF. Phone cameras report FocalLengthIn35mmFilm, which converts directly and
    accurately. `focal_px_from_jpeg_bytes()` reads it without any new dependency.
  * A typical-phone default (DEFAULT_HFOV_DEG). Good to roughly +/-25%, which is fine for
    "your top face reads about 5% large" but NOT good enough to silently correct with.

Recovering f from the marker homography itself was tried and rejected: a small, nearly
fronto-parallel square carries almost no focal information. Measured against a known
f = 1500 px, the estimate was undefined below ~10 deg of tilt and off by 15-26% between
10 and 30 deg — worse than the default assumption, and worse than not correcting at all.
`focal_px_from_marker()` implements it anyway, clearly labelled, for diagnostics only.
"""
from typing import Optional, Dict, Any
import struct
import math

import numpy as np

# Horizontal field of view assumed when EXIF carries no focal length. Most phone main
# cameras sit around 65-70 deg; 68 puts the error band at roughly +/-25% on the working
# distance, which is a usable warning and an unusable correction.
DEFAULT_HFOV_DEG: float = 68.0

# Below this the correction is not worth applying: a feature this close to the marker
# plane is already within the pipeline's own noise.
MIN_CORRECTABLE_HEIGHT_MM: float = 1.0


# ----------------------------------------------------------------------------------
# EXIF
# ----------------------------------------------------------------------------------
# Minimal reader for the two focal-length tags, so no imaging library is needed just to
# read a number. Walks the JPEG segments to APP1/Exif, then the TIFF header and IFD0 +
# the Exif sub-IFD. Anything unexpected returns None rather than raising: a missing or
# malformed focal length must never fail an upload.
_TAG_FOCAL_35MM = 0xA405     # FocalLengthIn35mmFilm (short), in mm
_TAG_FOCAL = 0x920A          # FocalLength (rational), in mm — needs a sensor size we lack
_TAG_EXIF_IFD = 0x8769


def _exif_ifd_entries(buf: bytes, tiff_off: int, ifd_off: int, le: bool, depth: int = 0):
    """Yield (tag, value) for one IFD, following the Exif sub-IFD pointer once."""
    if depth > 1:
        return
    o = tiff_off + ifd_off
    if o + 2 > len(buf):
        return
    fmt = "<" if le else ">"
    (count,) = struct.unpack_from(fmt + "H", buf, o)
    o += 2
    for _ in range(count):
        if o + 12 > len(buf):
            return
        tag, typ, n = struct.unpack_from(fmt + "HHI", buf, o)
        raw = buf[o + 8:o + 12]
        val = None
        if typ == 3 and n >= 1:                       # SHORT
            (val,) = struct.unpack_from(fmt + "H", raw, 0)
        elif typ == 4 and n >= 1:                     # LONG
            (val,) = struct.unpack_from(fmt + "I", raw, 0)
        elif typ == 5 and n >= 1:                     # RATIONAL (value is an offset)
            (ptr,) = struct.unpack_from(fmt + "I", raw, 0)
            p = tiff_off + ptr
            if p + 8 <= len(buf):
                num, den = struct.unpack_from(fmt + "II", buf, p)
                val = (num / den) if den else None
        if val is not None:
            yield tag, val
        if tag == _TAG_EXIF_IFD and typ == 4:
            (sub,) = struct.unpack_from(fmt + "I", raw, 0)
            for t, v in _exif_ifd_entries(buf, tiff_off, sub, le, depth + 1):
                yield t, v
        o += 12


def exif_focal_35mm(data: bytes) -> Optional[float]:
    """FocalLengthIn35mmFilm (mm) from JPEG bytes, or None. Never raises."""
    try:
        if not data or len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
            return None                                  # not a JPEG
        i = 2
        while i + 4 <= len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0xD9):
                i += 2
                continue
            if marker == 0xDA:                           # start of scan — no more metadata
                return None
            (seglen,) = struct.unpack_from(">H", data, i + 2)
            seg = data[i + 4:i + 2 + seglen]
            if marker == 0xE1 and seg[:6] == b"Exif\x00\x00":
                tiff = seg[6:]
                if len(tiff) < 8:
                    return None
                le = tiff[:2] == b"II"
                fmt = "<" if le else ">"
                (ifd0,) = struct.unpack_from(fmt + "I", tiff, 4)
                base = i + 4 + 6
                for tag, val in _exif_ifd_entries(data, base, ifd0, le):
                    if tag == _TAG_FOCAL_35MM and val and val > 0:
                        return float(val)
                return None
            i += 2 + seglen
    except Exception:
        return None
    return None


def focal_px_from_jpeg_bytes(data: bytes, image_width_px: int) -> Optional[float]:
    """Focal length in PIXELS from a JPEG's EXIF, or None when it carries none.

    FocalLengthIn35mmFilm is the focal length that would give this frame's horizontal
    angle of view on 35 mm film, whose frame is 36 mm wide. So the pixel focal length is
    f_px = f35 / 36 * width_px, independent of the real sensor size — which is exactly
    why this tag is the reliable one to use.
    """
    f35 = exif_focal_35mm(data)
    if not f35 or not image_width_px or image_width_px <= 0:
        return None
    f_px = f35 / 36.0 * float(image_width_px)
    # Sanity band: a plausible phone/DSLR frame is roughly 0.4x to 6x the frame width.
    if not (0.4 * image_width_px <= f_px <= 6.0 * image_width_px):
        return None
    return float(f_px)


def default_focal_px(image_width_px: int) -> float:
    """Focal length in pixels implied by DEFAULT_HFOV_DEG. A fallback, not a measurement."""
    return float(image_width_px) / (2.0 * math.tan(math.radians(DEFAULT_HFOV_DEG) / 2.0))


# ----------------------------------------------------------------------------------
# Working distance and parallax
# ----------------------------------------------------------------------------------
def working_distance_mm(edge_px: float, edge_mm: float, focal_px: float) -> Optional[float]:
    """Camera-to-marker distance from the marker's apparent size: d = f * edge_mm / edge_px."""
    try:
        if edge_px <= 0 or edge_mm <= 0 or focal_px <= 0:
            return None
        d = float(focal_px) * float(edge_mm) / float(edge_px)
        return d if math.isfinite(d) and d > 0 else None
    except (TypeError, ValueError):
        return None


def parallax_scale(height_mm: float, distance_mm: float) -> Optional[float]:
    """measured/true for a feature `height_mm` above the marker plane: d / (d - h).

    Positive height means toward the camera (a part's top face), which reads LARGE.
    Returns None when the geometry is degenerate (feature at or past the camera).
    """
    try:
        h, d = float(height_mm), float(distance_mm)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(h) and math.isfinite(d)) or d <= 0 or d - h <= 1e-6:
        return None
    return d / (d - h)


def parallax_error_pct(height_mm: float, distance_mm: float) -> Optional[float]:
    """How much too large a feature at `height_mm` reads, in percent."""
    s = parallax_scale(height_mm, distance_mm)
    return None if s is None else (s - 1.0) * 100.0


def parallax_advice(height_mm: float, distance_mm: float) -> Optional[str]:
    """One plain sentence for a customer about a feature `height_mm` off the card's plane.

    Deliberately leads with the free, exact fix (move the card) rather than with the
    correction, because moving the card removes the error instead of estimating it.
    """
    pct = parallax_error_pct(height_mm, distance_mm)
    if pct is None or abs(pct) < 0.5:
        return None
    return (f"A face {height_mm:g} mm above the card reads about {pct:.0f}% large from this "
            f"distance (~{100 * pct / 100:.1f} mm on a 100 mm dimension). For an exact "
            f"measurement, rest the card on that face and take the photo again.")


# ----------------------------------------------------------------------------------
# Pose and height-corrected homography
# ----------------------------------------------------------------------------------
def _K(focal_px: float, cx: float, cy: float) -> np.ndarray:
    return np.array([[focal_px, 0.0, cx], [0.0, focal_px, cy], [0.0, 0.0, 1.0]], float)


def pose_from_homography(H_unit, edge_mm: float, focal_px: float,
                         cx: float, cy: float) -> Optional[Dict[str, Any]]:
    """Camera pose relative to the marker plane, from the stored image->unit-square homography.

    Returns {"R": 3x3, "t": 3, "tilt_deg": float, "distance_mm": float} or None.
    `tilt_deg` is the angle between the camera's optical axis and the marker's normal —
    the real quantity, not edge_finder's max-corner-deviation proxy.
    """
    try:
        Hu = np.asarray(H_unit, float).reshape(3, 3)
        if not np.all(np.isfinite(Hu)) or focal_px <= 0 or edge_mm <= 0:
            return None
        # image px -> plane mm, then invert to get plane mm -> image px
        H_mm = np.diag([edge_mm, edge_mm, 1.0]) @ Hu
        G = np.linalg.inv(H_mm)
        Kn = np.linalg.inv(_K(focal_px, cx, cy))
        h1, h2, h3 = G[:, 0], G[:, 1], G[:, 2]
        r1, r2, tv = Kn @ h1, Kn @ h2, Kn @ h3
        n1, n2 = np.linalg.norm(r1), np.linalg.norm(r2)
        if n1 <= 1e-12 or n2 <= 1e-12:
            return None
        lam = 2.0 / (n1 + n2)                 # one scale for both columns
        r1, r2, tv = r1 * lam, r2 * lam, tv * lam
        # The homography's overall sign is ambiguous; resolve it by requiring the marker to
        # sit IN FRONT of the camera. This has to happen BEFORE r3 is formed: negating the
        # assembled 3x3 instead flips r3 as well, and det(-R) = -det(R) for a 3x3, so R
        # silently stops being a rotation and becomes a reflection.
        if tv[2] < 0:
            r1, r2, tv = -r1, -r2, -tv
        r3 = np.cross(r1, r2)
        # Nearest true rotation (the two recovered columns are not exactly orthonormal).
        U, _s, Vt = np.linalg.svd(np.stack([r1, r2, r3], axis=1))
        R = U @ Vt
        if np.linalg.det(R) < 0:
            R = U @ np.diag([1.0, 1.0, -1.0]) @ Vt
        normal_cam = R[:, 2]                  # plane normal expressed in camera axes
        cosang = abs(float(normal_cam[2]))
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
        # PERPENDICULAR distance from the camera to the marker's plane — the quantity
        # parallax actually depends on, since a feature rises along the plane NORMAL.
        # The line-of-sight distance (f * edge_mm / edge_px) is a different number and moves
        # the wrong way under tilt: measured on synthetic renders at a true perpendicular
        # distance falling 400 -> 257 mm between 0 and 50 deg of tilt, line-of-sight ROSE
        # 400 -> 512 mm, so a parallax warning built on it under-reported the real error by
        # about half at 50 deg. That is exactly the "off AND has depth" case.
        perp = abs(float(np.dot(R[:, 2], tv)))
        dist = float(abs(tv[2]))
        if not (math.isfinite(tilt) and math.isfinite(dist)) or dist <= 0:
            return None
        return {"R": R, "t": tv, "tilt_deg": tilt, "distance_mm": dist,
                "plane_distance_mm": perp if (math.isfinite(perp) and perp > 0) else dist}
    except (np.linalg.LinAlgError, ValueError, TypeError):
        return None


def homography_at_height(H_unit, edge_mm: float, focal_px: float, cx: float, cy: float,
                         height_mm: float):
    """The image->unit-square homography for a plane PARALLEL to the marker's, `height_mm`
    above it (toward the camera). Feed this to the same measurement code and a raised face
    measures true.

    The marker plane maps to image via G0 = K[r1 r2 t]; a plane offset by h along the plane
    normal maps via Gh = K[r1 r2 (t + h*r3)].

    The correction is applied RELATIVE to the measured homography rather than by rebuilding
    one from the pose:

        H_corrected = H_measured @ G0 @ inv(Gh)

    G0 @ inv(Gh) carries a point's image position at height h back to where the same plane
    coordinates sit at height 0, and H_measured then does the accurate part. This matters a
    lot in practice. Rebuilding from the pose instead throws away the measured homography
    and inherits the pose's own error as a CONSTANT scale bias: measured on synthetic
    renders it left -6.2% to -6.9% at 30 deg of tilt, turning a +11.6% overshoot into a
    -6.9% undershoot. Composed this way the correction is exactly identity at h = 0 by
    construction, so pose error perturbs only the (small) correction term.

    Returns a nested list (JSON-ready), or None when the pose is unusable.
    """
    H_arr = np.asarray(H_unit, float).reshape(3, 3)
    if abs(float(height_mm or 0.0)) < MIN_CORRECTABLE_HEIGHT_MM:
        return [[float(v) for v in row] for row in H_arr]
    pose = pose_from_homography(H_unit, edge_mm, focal_px, cx, cy)
    if pose is None:
        return None
    try:
        R, tv = pose["R"], pose["t"]
        Kf = _K(focal_px, cx, cy)
        # Plane-normal direction pointing from the marker TOWARD the camera.
        r3 = R[:, 2]
        if float(np.dot(r3, tv)) > 0:
            r3 = -r3
        G0 = Kf @ np.stack([R[:, 0], R[:, 1], tv], axis=1)
        Gh = Kf @ np.stack([R[:, 0], R[:, 1], tv + float(height_mm) * r3], axis=1)
        H_unit_h = H_arr @ G0 @ np.linalg.inv(Gh)
        if not np.all(np.isfinite(H_unit_h)) or abs(H_unit_h[2, 2]) < 1e-12:
            return None
        H_unit_h = H_unit_h / H_unit_h[2, 2]
        return [[float(v) for v in row] for row in H_unit_h]
    except (np.linalg.LinAlgError, ValueError, TypeError):
        return None


def focal_px_from_marker(H_unit, edge_mm: float, cx: float, cy: float) -> Optional[float]:
    """Focal length from the marker homography alone, via the two square constraints.

    DIAGNOSTIC ONLY — do not calibrate with this. A small, nearly fronto-parallel square
    carries almost no focal information: against a known f = 1500 px this returned nothing
    at all below ~10 deg of tilt and was 15-26% out between 10 and 30 deg. That is worse
    than assuming a typical phone field of view, and far worse than leaving h uncorrected.
    """
    try:
        Hu = np.asarray(H_unit, float).reshape(3, 3)
        G = np.linalg.inv(np.diag([edge_mm, edge_mm, 1.0]) @ Hu)
        g1, g2 = G[:, 0], G[:, 1]
        u1, v1, w1 = g1[0] - cx * g1[2], g1[1] - cy * g1[2], g1[2]
        u2, v2, w2 = g2[0] - cx * g2[2], g2[1] - cy * g2[2], g2[2]
        cands = []
        if abs(w1 * w2) > 1e-12:                       # r1 . r2 = 0
            f2 = -(u1 * u2 + v1 * v2) / (w1 * w2)
            if f2 > 0:
                cands.append(math.sqrt(f2))
        if abs(w2 * w2 - w1 * w1) > 1e-12:             # |r1| = |r2|
            f2 = ((u1 * u1 + v1 * v1) - (u2 * u2 + v2 * v2)) / (w2 * w2 - w1 * w1)
            if f2 > 0:
                cands.append(math.sqrt(f2))
        return float(np.median(cands)) if cands else None
    except (np.linalg.LinAlgError, ValueError, TypeError):
        return None
