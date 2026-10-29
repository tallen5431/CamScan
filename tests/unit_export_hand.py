"""Which way the 3D part's files come, and that none is a mirror image of the photo.

A user opened the STL in Fusion and its end looked mirrored against the preview; the STL is
turned deck-down for printing (a rotation), and seen from above that shows the underside.
This pins down what each file holds, so the dialog's words can rely on it:
  - the preview and STEP/OBJ come as the part sits, Z up: a notch at the Top photo's
    top-left is at X-min, Y-max seen from above;
  - the STL of a cover is that turned over about X — the same hand: turned back, it is the
    STEP's part exactly.

    python tests/unit_export_hand.py
"""
import pathlib
import re
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def _notch(V):
    """Which XY corner of the part's box has no vertex near it (the notch)."""
    import numpy as np
    V = np.asarray(V, float).reshape(-1, 3)
    lo, hi = V.min(0), V.max(0)
    out = []
    for cx, cy, lab in ((lo[0], hi[1], "x-min,y-max"), (hi[0], hi[1], "x-max,y-max"),
                        (lo[0], lo[1], "x-min,y-min"), (hi[0], lo[1], "x-max,y-min")):
        if np.hypot(V[:, 0] - cx, V[:, 1] - cy).min() > 20:
            out.append(lab)
    return out


def main():
    import numpy as np
    import app as appmod
    cli = appmod.server.test_client()
    # The Top photo (px, y down): a 600 x 150 slab, its top-left corner cut off by a notch.
    top = [[100, 160], [160, 100], [700, 100], [700, 250], [100, 250]]
    end = [[0, 0], [150, 0], [150, 40], [0, 40]]
    body = {"views": {"top": {"points": top, "mm_per_px": 1, "image_height": 400},
                      "end": {"points": end, "mm_per_px": 1, "image_height": 100}}, "wall_mm": 3}

    pv = cli.post("/api/export/part", json=dict(body, preview=True)).get_json()
    step = cli.post("/api/export/part", json=dict(body, format="step")).data.decode("latin1")
    stl = cli.post("/api/export/part", json=dict(body, format="stl")).data
    P = np.asarray(pv["preview"]["mesh"]["v"], float)
    S = np.array([[float(a) for a in m.groups()] for m in
                  re.finditer(r"CARTESIAN_POINT\('[^']*',\(([-\d.E+]+),([-\d.E+]+),([-\d.E+]+)\)\)", step)])
    n = struct.unpack("<I", stl[80:84])[0]
    T = np.frombuffer(stl[84:84 + n * 50], dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))["v"]
    T = T.reshape(-1, 3).astype(float)

    check("the preview and STEP keep the photo's top-left notch at X-min, Y-max (Z up, not mirrored)",
          _notch(P) == ["x-min,y-max"] and _notch(S) == ["x-min,y-max"],
          f"preview {_notch(P)}, STEP {_notch(S)}")
    check("the preview says the STL comes deck-down", bool(pv["info"].get("plate_down")), str(pv["info"].get("plate_down")))
    # Turn the STL back over about X (y and z both negate): the STEP's part, the same hand.
    back = T.copy()
    back[:, 1] = (T[:, 1].min() + T[:, 1].max()) - T[:, 1]
    back[:, 2] = (T[:, 2].min() + T[:, 2].max()) - T[:, 2]
    same = (np.allclose(back.min(0), S.min(0), atol=0.05) and np.allclose(back.max(0), S.max(0), atol=0.05)
            and _notch(back) == _notch(S))
    check("the STL is that part turned over about X — notch at X-min, Y-min from above — never mirrored",
          _notch(T) == ["x-min,y-min"] and same, f"STL {_notch(T)}, turned back {_notch(back)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                      # a crash is a failure, not a skip
        import traceback
        traceback.print_exc()
        check("unit_export_hand ran to the end", False, repr(e))
    print()
    print("UNIT_RESULT: " + ("PASS" if not _failures else "FAIL"))
    sys.exit(1 if _failures else 0)
