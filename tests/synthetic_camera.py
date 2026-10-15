"""A virtual camera and marker, so calibration accuracy can be checked against ground truth.

The whole point of this module is that the ANSWER IS KNOWN. It renders the real CamScan
marker under a chosen camera pose, so a test can measure a feature of known length at a
known height above the marker plane and compare what the app reports against what is true.
That is the only way to make claims about out-of-plane error mean anything.

World frame is millimetres: x right, y forward, z up out of the marker plane. The marker
lies on z = 0. Measurement mirrors assets/calib.measure.js exactly, so the number a test
sees is the number a user would see.
"""
import numpy as np
import cv2

# Marker proportions, straight from tools/make_marker.py — pads are 26.667% of the edge,
# with their top-left corners at 16.667% and 66.667% of it.
PAD, P0, P1 = 0.26667, 0.16667, 0.66667


def marker_world(edge_mm, ox=0.0, oy=0.0):
    """The marker's 20 physical corners on z=0: outer square first, then each pad."""
    e = edge_mm
    pts = [(ox, oy), (ox + e, oy), (ox + e, oy + e), (ox, oy + e)]
    for (fx, fy) in [(P0, P0), (P1, P0), (P0, P1), (P1, P1)]:
        x0, y0, s = ox + fx * e, oy + fy * e, PAD * e
        pts += [(x0, y0), (x0 + s, y0), (x0 + s, y0 + s), (x0, y0 + s)]
    return np.array(pts, float)


def camera(f_px, w, h, tilt_deg, dist_mm, look_at=(0.0, 0.0)):
    """(K, R, t) for a camera `dist_mm` from the plane, `tilt_deg` off perpendicular."""
    K = np.array([[f_px, 0, w / 2.0], [0, f_px, h / 2.0], [0, 0, 1]], float)
    th = np.deg2rad(tilt_deg)
    C = np.array([look_at[0], look_at[1] - dist_mm * np.sin(th), dist_mm * np.cos(th)])
    fwd = np.array([look_at[0], look_at[1], 0.0]) - C
    fwd /= np.linalg.norm(fwd)
    # Any hint not parallel to fwd; straight down (tilt=0) makes the z hint degenerate.
    up_hint = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(fwd, up_hint))) > 0.999:
        up_hint = np.array([0.0, 1.0, 0.0])
    right = np.cross(fwd, up_hint); right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd])            # world -> camera
    return K, R, -R @ C


def project(K, R, t, P):
    """World points (N,2 on z=0, or N,3) -> image pixels (N,2)."""
    P = np.asarray(P, float)
    if P.ndim == 1:
        P = P.reshape(1, -1)
    if P.shape[1] == 2:
        P = np.hstack([P, np.zeros((len(P), 1))])
    X = R @ P.T + t.reshape(3, 1)
    uv = K @ X
    return (uv[:2] / uv[2]).T


_SHIFT = 8          # fixed-point bits for fillPoly: vertices placed to 1/256 px


def _poly(pts):
    """Projected vertices as fillPoly fixed-point, ROUNDED.

    This used to be np.int32(pts) — a truncation, at whole-pixel resolution. At 25 degrees of
    tilt several marker corners project to exact integers (800.0, 950.0, 600.0), and the last
    bit of a matrix product depends on which BLAS kernel the CPU selects: 949.9999999999999 on
    one machine, 950.0 on another, truncating to different pixels. The rendered "ground
    truth" then differed by machine (different image hashes), a detected corner moved from
    0.0 px to exactly 2.0 px off, and the tilted depth-correction check failed on GitHub's
    runners (+4.19% -> -6.49%) while passing locally (+7.26% -> -2.55%). Reproduced locally
    with OPENBLAS_CORETYPE=Haswell. Rounding at 1/256 px is not on a knife edge at integers,
    and places every edge where the geometry says it is.
    """
    return np.round(np.asarray(pts, float) * (1 << _SHIFT)).astype(np.int32)


def render(edge_mm, f_px, W, H, tilt_deg, dist_mm, blur=0.0):
    """A photo-like frame of the marker card. Returns (image, K, R, t)."""
    K, R, t = camera(f_px, W, H, tilt_deg, dist_mm)
    img = np.full((H, W, 3), 225, np.uint8)
    card = np.array([(-12, -14), (edge_mm + 30, -14),
                     (edge_mm + 30, edge_mm + 12), (-12, edge_mm + 12)], float)
    cv2.fillPoly(img, [_poly(project(K, R, t, card))], (245, 245, 245), shift=_SHIFT)
    w = marker_world(edge_mm)
    cv2.fillPoly(img, [_poly(project(K, R, t, w[:4]))], (18, 18, 18), shift=_SHIFT)
    for i in range(4):
        cv2.fillPoly(img, [_poly(project(K, R, t, w[4 + 4 * i:8 + 4 * i]))], (242, 242, 242),
                     shift=_SHIFT)
    if blur:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    return img, K, R, t


def measure_mm(H, edge_mm, p, q):
    """Distance in mm between two IMAGE points, exactly as assets/calib.measure.js does it:
    project through the homography to unit-square coords, then scale by the marker size."""
    H = np.asarray(H, float)

    def mm(pt):
        x, y = pt
        d = H[2, 0] * x + H[2, 1] * y + H[2, 2]
        return np.array([(H[0, 0] * x + H[0, 1] * y + H[0, 2]) / d * edge_mm,
                         (H[1, 0] * x + H[1, 1] * y + H[1, 2]) / d * edge_mm])

    return float(np.linalg.norm(mm(q) - mm(p)))


def jpeg_with_focal35(img, focal_35mm):
    """JPEG bytes carrying FocalLengthIn35mmFilm, for testing the EXIF reader.

    Built by hand rather than with an imaging library so the test needs no extra
    dependency — a little-endian TIFF header, one IFD entry, spliced in as APP1.
    """
    import struct
    ok, enc = cv2.imencode(".jpg", img)
    if not ok:
        raise RuntimeError("imencode failed")
    jpg = enc.tobytes()
    tiff = b"II" + struct.pack("<HI", 42, 8)            # little-endian, magic, IFD0 offset
    entry = struct.pack("<HHI", 0xA405, 3, 1) + struct.pack("<HH", int(focal_35mm), 0)
    ifd = struct.pack("<H", 1) + entry + struct.pack("<I", 0)   # 1 entry, no next IFD
    payload = b"Exif\x00\x00" + tiff + ifd
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    return jpg[:2] + app1 + jpg[2:]                    # after SOI
