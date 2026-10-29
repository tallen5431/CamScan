"""Refine rounds the corners at the END of a long, narrow part.

Refine treats a side much shorter than both its neighbours as a big rounding traced as a
straight piece, and folds it into one corner. On a step pad's top photo the cover's whole
end (226 px against 1100 px sides) met that test, so it was folded away: the two long sides
it joined are parallel, nothing could be fitted between them, and the end's rounded corner
stayed the trace's straight chamfer. An end (neighbours running opposite ways) is now kept.

    python tests/unit_refine_end.py
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def main():
    import cv2
    import numpy as np
    import outline_cleanup as oc

    # A 230 x 1100 px cover, its top-left corner rounded (R 32 px), on a light ground.
    X0, Y0, X1, Y1, R = 520, 30, 750, 1130, 32
    img = np.full((1180, 1000, 3), 185, np.uint8)
    cv2.rectangle(img, (X0 + R, Y0), (X1, Y1), (60, 60, 64), -1)
    cv2.rectangle(img, (X0, Y0 + R), (X1, Y1), (60, 60, 64), -1)
    cv2.circle(img, (X0 + R, Y0 + R), R, (60, 60, 64), -1, lineType=cv2.LINE_AA)
    # The trace: straight sides, the rounded corner cut as a chamfer (as the tracer gave it).
    c = 12
    trace = [[X0 + c, Y0 + 1], [X1, Y0], [X1, Y1], [X0, Y1], [X0 + 1, Y0 + c]]
    dense = []
    for a, b in zip(trace, trace[1:] + trace[:1]):
        for t in np.linspace(0, 1, 12, endpoint=False):
            dense.append([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t])

    out = oc.refine_outline(img, dense)
    check("Refine returns an outline", out is not None)
    if out is None:
        return
    pts = np.asarray(out["points"], float)
    near = pts[(pts[:, 0] < X0 + R + 2) & (pts[:, 1] < Y0 + R + 2)]
    ctr = np.array([X0 + R, Y0 + R], float)
    err = np.abs(np.hypot(*(near - ctr).T) - R) if len(near) else np.array([99.0])
    check("the end's rounded corner is fitted as an arc, not kept as the chamfer",
          out.get("rounded", 0) >= 1 and len(near) >= 4 and float(err.max()) < 3.0,
          f"rounded {out.get('rounded')}, {len(near)} dots at the corner, worst {float(err.max()):.1f} px off R {R}")
    xs, ys = pts[:, 0], pts[:, 1]
    check("the end stays a side: the other end corner stays sharp, the size unchanged",
          abs(xs.max() - X1) < 3 and abs(ys.min() - Y0) < 3 and abs(ys.max() - Y1) < 3,
          f"x {xs.min():.0f}-{xs.max():.0f}, y {ys.min():.0f}-{ys.max():.0f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                      # a crash is a failure, not a skip
        import traceback
        traceback.print_exc()
        check("unit_refine_end ran to the end", False, repr(e))
    print()
    print("UNIT_RESULT: " + ("PASS" if not _failures else "FAIL"))
    sys.exit(1 if _failures else 0)
