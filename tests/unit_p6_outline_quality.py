"""Trace and tidy quality on a dark part with shadows, chrome and rounded corners.

Standalone: `python tests/unit_p6_outline_quality.py`. Every image and ring here is synthetic,
built to the shapes and grey levels measured on the customer's step-pad photos (dark rubber
~60-70 grey, its shadow ~25-57, concrete ~170, a chrome step ramping 110-150). Prints one
[PASS]/[FAIL] line per check, then UNIT_RESULT, and exits non-zero on a failure.
"""
import contextlib
import io
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

_failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


@contextlib.contextmanager
def _quiet():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _perim(p):
    p = np.asarray(p, float)
    return float(np.hypot(*(np.roll(p, -1, axis=0) - p).T).sum())


def _dense(poly, step=6.0, jit=0.8, seed=0):
    """A traced-looking ring round a polygon: a dot every `step` px, each nudged across the
    edge by `jit` px of noise."""
    rng = np.random.default_rng(seed)
    out = []
    P = np.asarray(poly, float)
    for a, b in zip(P, np.roll(P, -1, axis=0)):
        L = float(np.hypot(*(b - a)))
        m = max(1, int(L / step))
        nrm = np.array([-(b - a)[1], (b - a)[0]]) / L
        for t in np.linspace(0, 1, m, endpoint=False):
            out.append(a + t * (b - a) + nrm * rng.normal(0, jit))
    return np.asarray(out)


def _x_at(P, y):
    """Largest x where the closed polyline P crosses the row y (its right edge there)."""
    P = np.asarray(P, float)
    xs = [a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
          for a, b in zip(P, np.roll(P, -1, axis=0)) if (a[1] - y) * (b[1] - y) <= 0 and a[1] != b[1]]
    return max(xs) if xs else None


# ---------------------------------------------------------------------------------------
# Straighten: corners
# ---------------------------------------------------------------------------------------
def _rounded_rect_ring(x0, y0, x1, y1, R, jit, seed):
    """A jittered trace of a rounded rectangle with one dot per corner arc — sparse enough
    that straightening absorbs the corner dots into the sides, as on the customer's trace."""
    rng = np.random.default_rng(seed)
    pts = []
    corners = [(x1 - R, y0 + R, -90), (x1 - R, y1 - R, 0), (x0 + R, y1 - R, 90), (x0 + R, y0 + R, 180)]
    sides = [((x0 + R, y0), (x1 - R, y0)), ((x1, y0 + R), (x1, y1 - R)),
             ((x1 - R, y1), (x0 + R, y1)), ((x0, y1 - R), (x0, y0 + R))]
    for (cx, cy, a0), (a, b) in zip(corners, sides):
        a, b = np.array(a, float), np.array(b, float)
        L = float(np.hypot(*(b - a)))
        nrm = np.array([-(b - a)[1], (b - a)[0]]) / L
        for t in np.linspace(0, 1, max(2, int(L / 12)), endpoint=False):
            pts.append(a + t * (b - a) + nrm * rng.normal(0, jit))
        for j in range(2):
            ang = math.radians(a0 + 45.0 * j)
            pts.append([cx + R * math.cos(ang), cy + R * math.sin(ang)])
    return np.asarray(pts, float)


def _rounded_rect_img(x0, y0, x1, y1, R, shape=(420, 820)):
    m = np.zeros(shape, np.uint8)
    cv2.rectangle(m, (x0 + R, y0), (x1 - R, y1), 255, -1)
    cv2.rectangle(m, (x0, y0 + R), (x1, y1 - R), 255, -1)
    for cx, cy in ((x0 + R, y0 + R), (x1 - R, y0 + R), (x0 + R, y1 - R), (x1 - R, y1 - R)):
        cv2.circle(m, (cx, cy), R, 255, -1)
    img = np.full(shape + (3,), 190, np.uint8)
    img[m > 0] = 60
    return cv2.GaussianBlur(img, (3, 3), 0)


def test_corner_overshoot():
    from outline_cleanup import straighten_outline, refine_outline
    worst, worst_growth, cases, bad = 0.0, -1.0, 0, []
    for R in (45, 60):
        img = _rounded_rect_img(100, 100, 700, 300, R)
        for jit in (2.0, 3.0):
            for seed in range(20):
                p = _rounded_rect_ring(100, 100, 700, 300, R, jit, seed)
                ring32 = p.astype(np.float32).reshape(-1, 1, 2)
                r = max(4.0, 0.012 * float(np.hypot(*np.ptp(p, axis=0))))
                for fn in (straighten_outline, refine_outline):
                    res = fn(img, p.tolist())
                    if res is None:
                        continue
                    cases += 1
                    q = np.asarray(res["points"])
                    out = max(-cv2.pointPolygonTest(ring32, (float(x), float(y)), True) for x, y in q)
                    growth = _perim(q) / _perim(p) - 1.0
                    worst, worst_growth = max(worst, out / r), max(worst_growth, growth)
                    if out > r or growth > 0.005:
                        bad.append((R, jit, seed, fn.__name__, round(out, 1), round(100 * growth, 2)))
    check("Straighten/Refine put no corner out beyond a rounded corner", cases >= 100 and not bad,
          f"{cases} outlines, worst vertex {worst:.2f} r outside the trace, perimeter growth "
          f"at most {100 * worst_growth:+.2f}%" + (f", failures {bad[:4]}" if bad else ""))


# ---------------------------------------------------------------------------------------
# Straighten: a shadow traced as part of one side
# ---------------------------------------------------------------------------------------
def _shadow_scene(strip=True, same_as_part=False):
    """A dark part x 150..450, y 100..900 on concrete, with a 30 px strip beside the last 15%
    of its right side: a black shadow (or, for a real widening, more of the part)."""
    img = np.full((1000, 600, 3), 185, np.uint8)
    img[100:900, 150:450] = 60
    if strip:
        img[780:900, 450:480] = 60 if same_as_part else 25
    rng = np.random.default_rng(1)
    img = np.clip(img.astype(np.int16) + rng.integers(-4, 5, img.shape), 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (3, 3), 0)


def test_shadow_jog():
    from outline_cleanup import straighten_outline, refine_outline
    from auto_outline import auto_outline_full
    img = _shadow_scene()
    ring = _dense([(150, 100), (450, 100), (450, 780), (480, 780), (480, 900), (150, 900)])
    with _quiet():
        traced = auto_outline_full(img, seed=[300, 500])
    rows = range(790, 895, 5)
    for name, r0 in (("dense trace", ring), ("auto trace", np.asarray(traced["outer"]))):
        before = max(abs(_x_at(r0, y) - 450.0) for y in rows)
        st = straighten_outline(img, r0.tolist())
        rf = refine_outline(img, r0.tolist())
        err_s = max(abs(_x_at(st["points"], y) - 450.0) for y in rows)
        err_r = max(abs(_x_at(rf["points"], y) - 450.0) for y in rows)
        note = (st.get("notes") or [""])[0]
        check(f"Straighten pulls a shadow's step back onto the side ({name})",
              err_s <= 3.0 and err_r <= 3.0 and "right side" in note and "bottom end" in note
              and rf.get("notes") == st.get("notes"),
              f"right side off the true edge by up to {before:.0f} px before, {err_s:.1f} after "
              f"Straighten, {err_r:.1f} after Refine; note {note!r}")

    # A real widening (no edge along the side's line through it) and a taper stay as they are.
    wide = _shadow_scene(same_as_part=True)
    st = straighten_outline(wide, ring.tolist())
    kept = min(_x_at(st["points"], y) for y in rows)
    trap = np.array([(150, 100), (450, 100), (480, 900), (150, 900)], float)
    timg = np.full((1000, 600, 3), 185, np.uint8)
    cv2.fillPoly(timg, [trap.astype(np.int32)], (60, 60, 60))
    timg = cv2.GaussianBlur(timg, (3, 3), 0)
    tt = straighten_outline(timg, _dense(trap).tolist())
    taper = [_x_at(tt["points"], y) for y in (200, 500, 850)]
    want = [450 + 30 * (y - 100) / 800.0 for y in (200, 500, 850)]
    check("a real widening and a taper are not pulled in",
          "jogs" not in st and kept >= 476 and "jogs" not in tt
          and max(abs(a - b) for a, b in zip(taper, want)) <= 3.0,
          f"widening kept out to x={kept:.0f} (true 480); taper right edge {[round(v) for v in taper]} "
          f"vs true {[round(v) for v in want]}")


# ---------------------------------------------------------------------------------------
# Straighten with role="end": the deck is one straight line
# ---------------------------------------------------------------------------------------
def test_end_top_edge():
    from outline_cleanup import straighten_outline, refine_outline
    shape = np.array([(130, 100), (420, 100), (440, 110), (452, 130), (455, 180),
                      (95, 180), (98, 130), (110, 110)], float)
    img = np.full((300, 560, 3), 190, np.uint8)
    cv2.fillPoly(img, [shape.astype(np.int32)], (60, 60, 60))
    img = cv2.GaussianBlur(img, (3, 3), 0)
    worst, worst_plain, ok_all = 0, 0, True
    for seed in range(6):
        rng = np.random.default_rng(seed)
        top = [(x, 100 + rng.normal(0, 1.2)) for x in np.arange(130, 420, 5)]
        bottom = [(x, 180 + rng.normal(0, 0.4)) for x in np.arange(455, 95, -40)]
        p = np.array(top + [(440, 110), (452, 130), (455, 180)] + bottom + [(95, 180), (98, 130), (110, 110)])
        for fn in (straighten_outline, refine_outline):
            res = fn(img, p.tolist(), role="end")
            if not res:
                ok_all = False
                continue
            q = np.asarray(res["points"])
            ntop = int(((q[:, 1] < 106) & (q[:, 0] > 115) & (q[:, 0] < 435)).sum())
            worst = max(worst, ntop)
            ok_all &= bool(res.get("top_merged"))
        plain = straighten_outline(img, p.tolist())
        q = np.asarray(plain["points"]) if plain else p
        worst_plain = max(worst_plain, int(((q[:, 1] < 106) & (q[:, 0] > 115) & (q[:, 0] < 435)).sum()))
    check("a jittered End profile's top edge becomes one straight side", ok_all and worst <= 3,
          f"top-edge vertices: at most {worst} with role='end' (Straighten and Refine), "
          f"{worst_plain} without")


# ---------------------------------------------------------------------------------------
# Trace: one "Not the part" tap removes a whole chrome strip
# ---------------------------------------------------------------------------------------
def _chrome_scene():
    rng = np.random.default_rng(0)
    img = np.zeros((700, 900, 3), np.float32)
    img[:] = (168, 172, 176)
    img += rng.normal(0, 6, img.shape)
    img[150:550, 150:600] = (52, 50, 48)                           # the part
    ramp = 110 + 40 * (0.5 + 0.5 * np.sin(np.linspace(0, 1, 450) * 9))   # reflections along it
    img[550:610, 150:600] = ramp[None, :, None] * np.array([1.0, 1.02, 1.05])[None, None, :]
    img += rng.normal(0, 4, img.shape)
    return cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (3, 3), 0)


def _shadow_beside_scene():
    """A dark part (71 grey) with its own shadow (57) beside it and a groove across it."""
    rng = np.random.default_rng(2)
    img = np.full((600, 800, 3), 169, np.float32)
    cv2.rectangle(img, (240, 200), (600, 420), (57, 57, 57), -1)
    cv2.rectangle(img, (200, 160), (560, 380), (71, 71, 71), -1)
    cv2.rectangle(img, (500, 160), (506, 380), (40, 40, 40), -1)
    img += rng.normal(0, 4, img.shape)
    return cv2.GaussianBlur(np.clip(img, 0, 255).astype(np.uint8), (5, 5), 0)


def test_not_the_part_flood():
    from auto_outline import auto_outline_full
    img = _chrome_scene()
    roi = [130, 130, 490, 500]
    with _quiet():
        before = auto_outline_full(img, seed=[375, 350], roi=roi)
        after = auto_outline_full(img, seed=[375, 350], roi=roi, bg_points=[[250, 580]])
    yb = float(np.asarray(before["outer"])[:, 1].max())
    ya = float(np.asarray(after["outer"])[:, 1].max())
    check("one Not-the-part tap on a chrome strip removes all of it", yb > 600 and ya <= 553,
          f"traced bottom y {yb:.0f} with the box alone, {ya:.0f} after one tap (part ends at 550, "
          f"chrome at 610)")

    img2 = _shadow_beside_scene()
    with _quiet():
        r = auto_outline_full(img2, seed=[350, 270], bg_points=[[580, 400]], roi=[180, 140, 440, 300])
    P = np.asarray(r["outer"])
    check("a tap on a part's own shadow floods the shadow, not the part",
          abs(P[:, 0].min() - 200) <= 4 and abs(P[:, 0].max() - 560) <= 4 and abs(P[:, 1].max() - 380) <= 4,
          f"bbox {P.min(axis=0).round().tolist()} - {P.max(axis=0).round().tolist()} (part 200,160 - 560,380)")


# ---------------------------------------------------------------------------------------
# Trace: the background taken for the part
# ---------------------------------------------------------------------------------------
def test_background_as_part():
    import auto_outline as ao
    H, W = 600, 800
    img = np.full((H, W, 3), 170, np.uint8)
    cv2.rectangle(img, (250, 180), (560, 420), (60, 60, 60), -1)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    # What the colour model got wrong on the customer's photo, handed straight to the rest of
    # the trace: everything but the part is "foreground", the part a hole in it.
    frame = np.full((H, W), 255, np.uint8)
    frame[180:421, 250:561] = 0
    real = ao._segment
    ao._segment = lambda *a, **k: (frame.copy(), 1.0, H, W, ao._gradient_mag(img))
    try:
        with _quiet():
            got = ao.auto_outline_full(img, seed=[400, 300])
            near = ao.auto_outline_full(img, seed=[245, 300])       # on the frame, 5 px off the part
    finally:
        ao._segment = real
    P = np.asarray(got["outer"]) if got and got.get("outer") else np.zeros((1, 2))
    lo, hi = P.min(axis=0), P.max(axis=0)
    ok = (np.all(np.abs(lo - [250, 180]) <= 3) and np.all(np.abs(hi - [560, 420]) <= 3)
          and not got["holes"] and got.get("warnings"))
    check("a trace of the frame round the tapped part returns the part", bool(ok),
          f"outer {lo.round().tolist()} - {hi.round().tolist()} (part 250,180 - 560,420), "
          f"holes {len(got['holes'])}, warnings {got.get('warnings')}")
    Pn = np.asarray(near["outer"])
    check("a tap on the frame beside the part: the part, or a hint — never a silent frame",
          bool(near.get("hint")) or (Pn[:, 0].min() >= 245 and Pn[:, 0].max() <= 565),
          f"outer x {Pn[:, 0].min():.0f}-{Pn[:, 0].max():.0f}, hint {near.get('hint')!r}")

    # A real part that fills most of the frame, tapped on it: left alone.
    big = np.full((H, W, 3), 200, np.uint8)
    cv2.rectangle(big, (40, 30), (760, 570), (60, 60, 60), -1)
    with _quiet():
        rb = ao.auto_outline_full(big, seed=[400, 300])
    Pb = np.asarray(rb["outer"])
    check("a close-up part is not mistaken for background",
          not rb.get("hint") and not rb.get("warnings") and Pb[:, 0].min() < 45 and Pb[:, 0].max() > 755,
          f"outer x {Pb[:, 0].min():.0f}-{Pb[:, 0].max():.0f}, hint {rb.get('hint')}")

    # Nothing found: the caller is told what to try, not just "no".
    flat = np.full((H, W, 3), 128, np.uint8)
    flat[:] = np.random.default_rng(3).integers(120, 136, flat.shape)
    with _quiet():
        rn = ao.auto_outline_full(flat, seed=[5000, 5000])
    check("no outline comes with Area-box advice",
          rn is not None and rn.get("outer") is None and "Area" in (rn.get("hint") or ""),
          f"result {rn and {k: rn[k] for k in ('outer', 'hint')}}")


def test_straighten_endpoint_passes_notes():
    import app
    cli = app.server.test_client()
    _ok, enc = cv2.imencode(".png", _shadow_scene())
    import base64
    durl = "data:image/png;base64," + base64.b64encode(enc.tobytes()).decode()
    ring = _dense([(150, 100), (450, 100), (450, 780), (480, 780), (480, 900), (150, 900)])
    r = cli.post("/api/outline/straighten", json={"image": durl, "points": ring.tolist()})
    j = r.get_json() or {}
    check("/api/outline/straighten passes the shadow note through",
          r.status_code == 200 and j.get("ok") and j.get("notes") and j.get("jogs"),
          f"status {r.status_code}, notes {j.get('notes')}")


def main():
    for t in (test_corner_overshoot, test_shadow_jog, test_end_top_edge, test_not_the_part_flood,
              test_background_as_part, test_straighten_endpoint_passes_notes):
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                t()
        except Exception as e:  # a crash is a failure of that check, not of the run
            check(t.__name__, False, repr(e))
    print()
    print("UNIT_RESULT: " + ("PASS" if not _failures else "FAIL"))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
