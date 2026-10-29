"""Snap to edge follows the photo's edge near the user's dots, with the dots its curves need.

Refine fits straight sides and one radius per corner; a cover's end in shadow, traced along
the chrome bar under it, is neither, and Refine left it on the wrong line. With its dots
dragged near the right edge, Snap to edge moves the outline onto the edge within a few px —
smoothly, so a strong edge beside it (the chrome) does not pull it off — and keeps the dots
the edge's shape needs.

    python tests/unit_snap_edge.py
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


def _true_y(x):
    return 730 - 0.0012 * (x - 350) ** 2


def _photo():
    import cv2
    import numpy as np
    img = np.full((900, 700, 3), 190, np.uint8)
    cv2.rectangle(img, (200, 744), (500, 800), (245, 245, 245), -1)        # the chrome bar
    xs = np.arange(200, 501)
    poly = np.vstack([[[200, 100], [500, 100]], np.stack([xs[::-1], _true_y(xs[::-1])], 1)]).astype(np.int32)
    cv2.fillPoly(img, [poly], (60, 62, 66), lineType=cv2.LINE_AA)
    img = cv2.GaussianBlur(img, (0, 0), 0.8)
    rng = np.random.default_rng(1)
    return np.clip(img.astype(float) + rng.normal(0, 4, img.shape), 0, 255).astype(np.uint8)


def main():
    import cv2
    import numpy as np
    import outline_cleanup as oc

    img = _photo()
    # The user's dots: corners ~4 px off, three along the curved end 5 px low (towards the chrome).
    user = [[204, 104], [496, 97], [497, 705], [430, 727], [350, 735], [270, 727], [203, 705]]
    r = oc.snap_outline_to_edges(img, user)
    check("Snap to edge returns an outline", r is not None)
    if r is None:
        return
    q = np.asarray(r["points"])
    end = q[(q[:, 1] > 600) & (q[:, 0] > 215) & (q[:, 0] < 485)]
    err = np.abs(end[:, 1] - _true_y(end[:, 0])) if len(end) else np.array([99.0])
    check("the curved end is followed with dots of its own, onto the part's edge — not the chrome's 14 px off",
          len(end) >= 5 and float(err.max()) < 1.0,
          f"{len(end)} dots on the end, worst {float(err.max()):.2f} px off the true edge")
    corners = [(200, 100), (500, 100), (500, 703), (200, 703)]
    off = [min(float(np.hypot(*(q - np.array(c)).T).min()), 99) for c in corners]
    check("corners stay sharp corners on the edge (no spike off the corner)",
          max(off) < 2.0 and q[:, 1].min() > 97 and q[:, 0].max() < 503,
          f"nearest dot to each corner {[round(o, 1) for o in off]}, top {q[:, 1].min():.1f}, right {q[:, 0].max():.1f}")
    sides = q[(q[:, 1] > 150) & (q[:, 1] < 650)]
    check("a straight side gets no extra dots", len(sides) == 0, f"{len(sides)} dots mid-side")
    # A knot of dots round a corner (a trace's jog plus a few drags) tied the outline in
    # knots on a user's photo: whatever comes back must never cross itself.
    rng = np.random.default_rng(3)
    crossed, kept = 0, 0
    for _ in range(40):
        cl = sorted(([500 + rng.normal(0, 6), 100 + rng.normal(0, 6)] for _ in range(10)), key=lambda q: q[0] - q[1])
        messy = [[204, 104], [300, 101], [430, 99]] + cl + [[497, 300], [497, 705], [350, 735], [203, 705]]
        rr = oc.snap_outline_to_edges(img, messy)
        if rr and "points" in rr:
            kept += 1
            crossed += oc._crossings(np.asarray(rr["points"], float)) > 0
    check("a knot of dots never comes back tangled (snapped clean, or refused and left as it was)",
          crossed == 0 and kept >= 25, f"{kept}/40 snapped, {crossed} crossing themselves")
    # Fix one area: only what is inside the box changes; every dot outside stays exactly.
    wobbly = [[200, 100], [300, 103], [400, 97], [500, 100], [500, 705], [430, 727], [350, 735], [270, 727], [200, 705]]
    bx = [190, 650, 510, 760]
    rb = oc.snap_outline_to_edges(img, wobbly, box=bx)
    qb = np.asarray(rb["points"]) if rb and "points" in rb else np.zeros((0, 2))
    inside = (qb[:, 0] > bx[0]) & (qb[:, 0] < bx[2]) & (qb[:, 1] > bx[1]) & (qb[:, 1] < bx[3])
    kept_out = all(any(np.allclose(q, w) for w in wobbly) for q in qb[~inside & (qb[:, 1] < bx[1] - 1)])
    endb = qb[inside & (qb[:, 0] > 215) & (qb[:, 0] < 485)]
    eb = float(np.abs(endb[:, 1] - _true_y(endb[:, 0])).max()) if len(endb) else 99.0
    check("Fix one area: the end inside the box is snapped, the wobbly dots outside it are kept exactly",
          kept_out and [300, 103] in qb.tolist() and [400, 97] in qb.tolist() and eb < 1.0,
          f"outside kept={kept_out}, end worst {eb:.2f} px")
    check("a box clear of the outline changes nothing", oc.splice_in_box(wobbly, user, [0, 0, 10, 10]) is None)
    check("no edges in the photo: nothing to snap to",
          oc.snap_outline_to_edges(np.full_like(img, 128), user) is None)

    import app as appmod
    cli = appmod.server.test_client()
    durl = "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()
    ok = cli.post("/api/outline/straighten", json={"image": durl, "points": user, "mode": "snap"})
    noimg = cli.post("/api/outline/straighten", json={"image": None, "points": user, "mode": "snap"})
    j = ok.get_json() or {}
    rbx = cli.post("/api/outline/straighten", json={"image": durl, "points": wobbly, "mode": "straighten", "box": bx})
    jb = rbx.get_json() or {}
    junk = cli.post("/api/outline/straighten", json={"image": durl, "points": wobbly, "mode": "snap", "box": [1, 2, "x"]})
    check("Straighten with a box keeps the outside dots; a junk box is refused",
          rbx.status_code == 200 and jb.get("ok") and [300, 103] in jb.get("points", []) and junk.status_code == 400,
          f"{rbx.status_code} ok={jb.get('ok')}, junk {junk.status_code}")
    check("/api/outline/straighten mode snap answers with the snapped outline; refuses without the photo",
          ok.status_code == 200 and j.get("ok") and len(j.get("points", [])) >= 8 and "found_pct" in j
          and noimg.status_code == 400,
          f"{ok.status_code} {len(j.get('points', []))} pts, no image {noimg.status_code}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                      # a crash is a failure, not a skip
        import traceback
        traceback.print_exc()
        check("unit_snap_edge ran to the end", False, repr(e))
    print()
    print("UNIT_RESULT: " + ("PASS" if not _failures else "FAIL"))
    sys.exit(1 if _failures else 0)
