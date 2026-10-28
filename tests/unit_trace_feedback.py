"""What the tracer says about a trace, and that it reaches the page's /api/trace reply.

Standalone: `python tests/unit_trace_feedback.py`. Every image here is synthetic, built to the
grey levels and sizes measured on a customer's step-pad top photo (dark cover ~62 grey on
concrete ~150, its shadow a little darker beside one side near the end). Checks:

  - a stretch of a long side standing proud of the rest (auto_outline.proud_stretches) is
    found over 6-30% of the length, whether the part lies along or across the photo or at an
    angle, and not on a plain, tapered or round-ended part, or on trace wobble;
  - it is found on the photo the way the page posts it (JPEG q85), where the photo-based
    step check missed the customer's 19% bulge, and comes back as a warning with an ok:true
    trace from /api/trace;
  - an outline out to all four edges of the photo, with no Area box, is refused as a
    runaway (a centre tap on the customer's photo came back as the whole frame).

Prints one [PASS]/[FAIL] line per check, then UNIT_RESULT, and exits non-zero on a failure.
"""
import base64
import contextlib
import io
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import auto_outline as ao  # noqa: E402

_failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


@contextlib.contextmanager
def _quiet():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _bulged(frac, L=700.0, W=155.0, step=18.0):
    """A 700 x 155 outline whose bottom side stands out `step` over `frac` of the length."""
    x0, x1 = (1 - frac - 0.03) * L, 0.97 * L
    return [[0, 0], [L, 0], [L, W], [x1, W], [x1, W + step], [x0, W + step], [x0, W], [0, W]]


def geometry():
    for frac in (0.08, 0.12, 0.19, 0.30):
        got = ao.proud_stretches(_bulged(frac))
        check(f"a stretch over {frac:.0%} of the length is found",
              len(got) == 1 and got[0]["side"] == "bottom" and got[0]["where"] == "near the right end"
              and abs(got[0]["px"] - 18) < 1.5, f"{got}")
    check("a stretch over 40% is how wide the part is there", ao.proud_stretches(_bulged(0.40)) == [],
          f"{ao.proud_stretches(_bulged(0.40))}")
    L, W = 700.0, 155.0
    check("a plain rectangle: nothing", ao.proud_stretches([[0, 0], [L, 0], [L, W], [0, W]]) == [])
    check("a 4 px taper: nothing", ao.proud_stretches([[0, 0], [L, 0], [L, W], [0, W - 4]]) == [])
    th = np.linspace(-np.pi / 2, np.pi / 2, 30)
    r = W / 2
    stadium = [[L - r + r * np.cos(a), r + r * np.sin(a)] for a in th] + \
              [[r - r * np.cos(a), r - r * np.sin(a)] for a in th]
    check("round ends: nothing", ao.proud_stretches(stadium) == [], f"{ao.proud_stretches(stadium)}")
    rng = np.random.default_rng(1)
    xs = np.linspace(0, L, 120)
    noisy = [[x, rng.normal(0, 1.5)] for x in xs] + [[x, W + rng.normal(0, 1.5)] for x in xs[::-1]]
    check("trace wobble: nothing", ao.proud_stretches(noisy) == [], f"{ao.proud_stretches(noisy)}")
    check("a squarish part is not judged", ao.proud_stretches([[0, 0], [200, 0], [200, 150], [0, 150]]) == [])
    R = np.array([[np.cos(0.35), -np.sin(0.35)], [np.sin(0.35), np.cos(0.35)]])
    rot = ao.proud_stretches((np.asarray(_bulged(0.19)) @ R.T).tolist())
    check("at 20 degrees: found", len(rot) == 1 and abs(rot[0]["px"] - 18) < 1.5, f"{rot}")
    # Along the photo, as the customer's cover lay: the right side, near the bottom end.
    tall = [[650, 170], [878, 170], [878, 1230], [906, 1240], [906, 1480], [878, 1490], [878, 1540], [650, 1540]]
    got = ao.proud_stretches(tall)
    check("the customer's shape: right side, near the bottom end",
          len(got) == 1 and got[0]["side"] == "right" and got[0]["where"] == "near the bottom end", f"{got}")

    check("all four edges: spans the frame", ao.spans_frame([[2, -3], [1199, -3], [1199, 1601], [2, 1601]], 1200, 1600))
    check("a close-up 40 px in: does not", not ao.spans_frame([[40, 30], [760, 30], [760, 570], [40, 570]], 800, 600))
    check("three edges: does not", not ao.spans_frame([[0, 0], [1199, 0], [1199, 1500], [0, 1500]], 1200, 1600))


def _cover(shadow=True):
    rng = np.random.default_rng(0)
    img = np.clip(rng.normal(150, 6, (1600, 1200, 3)), 0, 255).astype(np.uint8)
    cv2.rectangle(img, (450, 170), (778, 1540), (62, 62, 62), -1)
    if shadow:                             # beside the right side, ~19% of the length
        cv2.rectangle(img, (779, 1230), (806, 1480), (50, 50, 50), -1)
    img = cv2.GaussianBlur(img, (5, 5), 0)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])     # as the page posts it
    return cv2.imdecode(buf, cv2.IMREAD_COLOR), buf.tobytes()


def traced():
    img, jpg = _cover()
    for roi in (None, [400, 120, 460, 1470]):
        with _quiet():
            r = ao.auto_outline_full(img, seed=[600, 900], roi=roi)
        w = " ".join(r.get("warnings") or [])
        check(f"a shadow stretch on the posted JPEG is warned about (box {roi})",
              "right side" in w and "bottom end" in w and not r.get("runaway"), w or "no warning")
    clean, _ = _cover(shadow=False)
    with _quiet():
        r = ao.auto_outline_full(clean, seed=[600, 900])
    check("the same cover with no shadow: no warning", not r.get("warnings"), f"{r.get('warnings')}")

    try:
        with _quiet():
            import app
    except Exception as e:                 # pragma: no cover - the smoke test covers app import
        check("app imports", False, str(e))
        return
    cli = app.server.test_client()
    durl = "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
    with _quiet():
        j = cli.post("/api/trace", json={"image": durl, "seed": [600, 900]}).get_json()
    check("/api/trace: ok:true with the warning",
          j.get("ok") is True and any("stands out" in w for w in j.get("warnings") or []),
          f"ok {j.get('ok')}, warnings {j.get('warnings')}")

    # A whole-frame outline with no box is refused as a runaway, with advice.
    real = ao._segment
    try:
        H, W = 1600, 1200
        ao._segment = lambda *a, **k: (np.full((H, W), 255, np.uint8), 1.0, H, W, ao._gradient_mag(img))
        with _quiet():
            r = ao.auto_outline_full(img, seed=[600, 900])
            j = cli.post("/api/trace", json={"image": durl, "seed": [600, 900]}).get_json()
    finally:
        ao._segment = real
    check("a whole-frame trace is a runaway", bool(r and r.get("runaway")), f"runaway {r and r.get('runaway')}")
    check("/api/trace refuses it with a hint", j.get("ok") is False and j.get("error") == "runaway" and j.get("hint"),
          f"{ {k: j.get(k) for k in ('ok', 'error', 'hint')} }")


def main():
    geometry()
    traced()
    ok = not _failures
    print("UNIT_RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
