"""Trace helpers: the ruler on the part is part of the part; stretches with no edge are found.

A steel rule lying on a dark cover is bright where the cover is dark, so the trace went round
it — on a user's top photo the outline stepped in along the ruler for 30 cm. And nothing
said where an outline was guesswork (a shadowed end, the seam with a chrome bar), so every
stretch had to be checked by eye. Now:
  - include_ruler adds the strip between the ruler's edges, found along the scale line, where
    it lies within the traced part's hull; a ruler beside the part adds nothing;
  - weak_stretches returns the stretches of an outline with no clear edge under them;
  - /api/trace does both, given the scale line as `ruler`.

    python tests/unit_trace_assist.py
"""
import base64
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def _photo():
    import cv2
    import numpy as np
    img = np.full((600, 800, 3), 190, np.uint8)
    cv2.rectangle(img, (200, 200), (600, 380), (60, 62, 66), -1)           # the part
    cv2.rectangle(img, (200, 270), (500, 300), (236, 236, 232), -1)        # a steel rule on it, off its left edge
    for x in range(206, 500, 10):
        cv2.line(img, (x, 270), (x, 276), (40, 40, 40), 1)                 # its ticks
    rng = np.random.default_rng(2)
    return np.clip(img.astype(float) + rng.normal(0, 3, img.shape), 0, 255).astype(np.uint8)


def main():
    import cv2
    import numpy as np
    import outline_cleanup as oc

    img = _photo()
    # The trace as it went: round the rule, stepping in along it.
    notched = [[200, 200], [600, 200], [600, 380], [200, 380], [200, 300], [500, 300], [500, 270], [200, 270]]
    filled = oc.include_ruler(img, notched, [210, 285], [490, 285])
    ok = filled is not None
    area = abs(cv2.contourArea(np.asarray(filled, np.float32))) if ok else 0
    inside = ok and cv2.pointPolygonTest(np.asarray(filled, np.float32).reshape(-1, 1, 2), (350.0, 285.0), False) >= 0
    check("the ruler on the part is added back into the outline",
          ok and inside and abs(area - 400 * 180) < 0.02 * 400 * 180,
          f"area {area:.0f} (the part {400 * 180}), rule inside={inside}")
    xs = np.asarray(filled)[:, 0] if ok else np.array([0])
    check("only within the part: the outline does not grow past the part's edge", ok and xs.min() >= 198,
          f"left edge {xs.min():.0f}")
    check("a ruler beside the part adds nothing",
          oc.include_ruler(img, notched, [210, 450], [490, 450]) is None)
    check("a trace that already holds the ruler is left alone",
          oc.include_ruler(img, [[200, 200], [600, 200], [600, 380], [200, 380]], [210, 285], [490, 285]) is None)

    # Weak stretches: a patch the part's colour above part of its top edge.
    w = img.copy()
    cv2.rectangle(w, (330, 150), (480, 199), (64, 66, 70), -1)
    weak = oc.weak_stretches(w, [[200, 200], [600, 200], [600, 380], [200, 380]])
    runs = [np.asarray(r) for r in weak]
    top = [r for r in runs if np.all(np.abs(r[:, 1] - 200) < 2)]
    span = (float(min(r[:, 0].min() for r in top)), float(max(r[:, 0].max() for r in top))) if top else (0, 0)
    check("weak_stretches finds the stretch with no edge under it, and nothing else",
          len(runs) == 1 and len(top) == 1 and 320 < span[0] < 350 and 460 < span[1] < 490,
          f"{len(runs)} stretch(es), top one x {span[0]:.0f}-{span[1]:.0f}")
    check("a clean outline has none", oc.weak_stretches(img, [[200, 200], [600, 200], [600, 380], [200, 380]]) == [])

    import app as appmod
    cli = appmod.server.test_client()
    durl = "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()).decode()
    r = cli.post("/api/trace", json={"image": durl, "seed": [400, 340], "roi": [185, 185, 430, 210],
                                     "ruler": [[210, 285], [490, 285]]})
    j = r.get_json() or {}
    pts = np.asarray(j.get("points") or [[0, 0]], np.float32).reshape(-1, 1, 2)
    held = cv2.pointPolygonTest(pts, (350.0, 285.0), False) >= 0
    check("/api/trace with the scale line: the outline holds the ruler",
          r.status_code == 200 and j.get("ok") and held, f"ok={j.get('ok')} ruler_filled={j.get('ruler_filled')} holds={held}")
    bad = cli.post("/api/trace", json={"image": durl, "seed": [400, 340], "ruler": [["x", 1], [2]]})
    check("a junk ruler is ignored, the trace still goes through", bad.status_code == 200 and (bad.get_json() or {}).get("ok"))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                      # a crash is a failure, not a skip
        import traceback
        traceback.print_exc()
        check("unit_trace_assist ran to the end", False, repr(e))
    print()
    print("UNIT_RESULT: " + ("PASS" if not _failures else "FAIL"))
    sys.exit(1 if _failures else 0)
