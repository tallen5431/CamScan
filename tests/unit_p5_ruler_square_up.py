"""Ruler edges beside a groove, Square up on one edge, trace hints and the landing card.

Standalone: python tests/unit_p5_ruler_square_up.py. Prints one [PASS]/[FAIL] line per check
and UNIT_RESULT: PASS / FAIL; exits non-zero on a failure. Every photo here is drawn: a
ruler strip on a dark part, with the thing that broke the edge finder on a customer's top
photo — something dark and straight running a few px off one of the ruler's edges.
"""
import base64
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")


WIDTH = 56.0                       # the ruler's width in px, as drawn
X0, Y0, Y1, DRIFT = 200.0, 120.0, 780.0, 0.012


def ruler_photo(groove=None, jagged=None, ruler=True, seed=1):
    """A 500 x 900 photo: a bright ruler strip (205) on a dark part (95), drifting slightly
    across the frame, ticks along its left edge. groove=(px off at the top, px off at the
    bottom, grey, fraction of the length): a dark groove that far off the RIGHT edge.
    jagged=(amplitude, period) px: a ragged right edge, so no straight line fits it.
    Returns (BGR image, scale line [a, b] down the ruler's middle)."""
    H, W, ss = 900, 500, 8
    big = np.full((H * ss, W * ss), 95, np.uint8)

    def P(x, y):
        return [int(round(((x + DRIFT * (y - Y0)) + .5) * ss - .5)), int(round((y + .5) * ss - .5))]
    if ruler:
        if jagged:
            amp, per = jagged
            ys = np.arange(Y0, Y1 + 0.01, 2.0)
            right = [P(X0 + WIDTH + amp * (abs(((y - Y0) % per) / per * 2 - 1) * 2 - 1), y) for y in ys]
            cv2.fillPoly(big, [np.array([P(X0, Y0)] + right + [P(X0, Y1)], np.int32)], 205)
        else:
            cv2.fillPoly(big, [np.array([P(X0, Y0), P(X0 + WIDTH, Y0), P(X0 + WIDTH, Y1), P(X0, Y1)], np.int32)], 205)
        for i in range(int((Y1 - Y0) / 1.95)):
            y = Y0 + 3 + i * 1.95
            ln = 9 if i % 10 == 0 else (6 if i % 5 == 0 else 3.5)
            cv2.line(big, tuple(P(X0, y)), tuple(P(X0 + ln, y)), 50, max(1, ss // 3))
    if groove:
        g0, g1, grey, frac = groove
        ye = Y0 + (Y1 - Y0) * frac
        g1e = g0 + (g1 - g0) * frac
        e = X0 + WIDTH
        cv2.fillPoly(big, [np.array([P(e + g0, Y0 - 40), P(e + g0 + 3, Y0 - 40),
                                     P(e + g1e + 3, ye), P(e + g1e, ye)], np.int32)], grey)
    img = cv2.resize(big, (W, H), interpolation=cv2.INTER_AREA).astype(np.float32)
    img = cv2.GaussianBlur(img, (0, 0), 0.9)
    img = np.clip(img + np.random.default_rng(seed).normal(0, 3, img.shape), 0, 255).astype(np.uint8)
    line = [[X0 + WIDTH / 2 + DRIFT * 15, Y0 + 15], [X0 + WIDTH / 2 + DRIFT * (Y1 - Y0 - 15), Y1 - 15]]
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR), line


def widths(edges):
    """The ruler's width at each end of the found edges, in px (they run up the photo)."""
    (a0, a1), (b0, b1) = edges
    return [abs(b0[0] - a0[0]), abs(b1[0] - a1[0])]


def data_url(img):
    return "data:image/png;base64," + base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode()


def main():
    from ruler_check import find_ruler_edges

    # 1) A dark groove 3-6 px off the ruler's right edge. The two cases marked `old None`
    #    returned no edges at all before (the groove-side fit failed its rms test); the dark
    #    full-length groove was worse — it passed, 4-6 px too wide, a false taper.
    cases = [((3, 6, 30, 1.0), "groove 3->6 px, grey 30"),
             ((4, 6, 10, 1.0), "groove 4->6 px, near black (was 60-62 px wide)"),
             ((3, 6, 10, 0.5), "groove 3->6 px along half the ruler (old None)"),
             ((6, 3, 20, 1.0), "groove 6->3 px"),
             ((5, 5, 10, 0.6), "groove 5 px along 60% (old None)")]
    for groove, label in cases:
        img, line = ruler_photo(groove=groove)
        r = find_ruler_edges(img, *line, detail=True)
        w = widths(r["edges"]) if r["edges"] else None
        check(f"ruler edges beside a groove: {label}",
              w is not None and all(abs(v - WIDTH) <= 2.0 for v in w),
              f"width {w and [round(v, 2) for v in w]} px (true {WIDTH}), partial={r['partial']}, reason={r['reason']}")

    # 2) One edge too ragged for a straight line: the other is assumed parallel, one ruler
    #    width away, and the result says so and why.
    img, line = ruler_photo(jagged=(5, 40))
    r = find_ruler_edges(img, *line, detail=True)
    w = widths(r["edges"]) if r["edges"] else None
    check("one clear edge: the other assumed parallel, flagged partial, with the side named",
          w is not None and r["partial"] and r["reason"] == "right edge unclear"
          and all(abs(v - WIDTH) <= 2.0 for v in w) and abs(w[0] - w[1]) < 1e-6,
          f"width {w and [round(v, 2) for v in w]}, partial={r['partial']}, reason={r['reason']!r}")
    # Callers that predate `detail` must never mistake an assumed edge for a measured one.
    old_plain = find_ruler_edges(img, *line)
    old_err = find_ruler_edges(img, *line, with_error=True)
    check("a partial result is invisible to the old call forms",
          old_plain is None and old_err == (None, None), f"plain={old_plain is None}, with_error={old_err}")

    # 3) No ruler at all: no edges, and a reason in words.
    img_none, line_none = ruler_photo(ruler=False, groove=(3, 6, 30, 1.0))
    r = find_ruler_edges(img_none, *line_none, detail=True)
    check("no ruler: no edges, a reason", r["edges"] is None and bool(r["reason"]) and not r["partial"],
          f"reason={r['reason']!r}")

    # 4) Through /api/square_up as the page calls it: the ragged ruler gives reference
    #    'ruler' with the partial flag, the part's taper is marked as unchecked, and the
    #    part's sides are offered instead.
    import app as appmod
    cli = appmod.server.test_client()
    img, line = ruler_photo(jagged=(5, 40))
    corners = [[60, 60], [460, 60], [460, 860], [60, 860]]
    outline = []
    for i in range(4):
        a_, b_ = np.array(corners[i], float), np.array(corners[(i + 1) % 4], float)
        outline += [(a_ + (b_ - a_) * t).tolist() for t in np.linspace(0, 1, 10, endpoint=False)]
    body = {"outline": outline, "ruler": line, "ruler_mm": 300.0, "image_w": 500, "image_h": 900,
            "focal_px": None, "ruler_image": data_url(img), "ruler_box": [0, 0, 1.0]}
    j = cli.post("/api/square_up", json=body).get_json()
    check("/api/square_up on one ruler edge: reference=ruler, partial, taper marked unchecked",
          j.get("ok") and j.get("reference") == "ruler" and j.get("partial") is True
          and j.get("ruler_reason") == "right edge unclear" and j.get("other") == "part"
          and (j.get("part") or {}).get("taper_note") == "one ruler edge assumed parallel"
          and "taper_sigma_mm" not in (j.get("part") or {}),
          f"reference={j.get('reference')}, partial={j.get('partial')}, reason={j.get('ruler_reason')!r}, "
          f"other={j.get('other')}, part={j.get('part')}")
    jp = cli.post("/api/square_up", json=dict(body, reference="part")).get_json()
    check("asked for the part's sides: the one-edge ruler is still offered",
          jp.get("ok") and jp.get("reference") == "part" and jp.get("other") == "ruler" and jp.get("partial") is True,
          f"reference={jp.get('reference')}, other={jp.get('other')}, partial={jp.get('partial')}")

    # Both edges clean: not partial, as before.
    img_g, line_g = ruler_photo(groove=(4, 6, 10, 1.0))
    jg = cli.post("/api/square_up", json=dict(body, ruler=line_g, ruler_image=data_url(img_g))).get_json()
    check("/api/square_up with the groove: both edges, not partial, the ruler reads its width",
          jg.get("ok") and jg.get("reference") == "ruler" and jg.get("partial") is False and jg.get("ruler_reason") is None
          and all(abs(v - WIDTH * 300.0 / float(np.hypot(*np.subtract(line_g[1], line_g[0])))) < 1.5
                  for v in jg["ruler_check"]["width_mm"]),
          f"partial={jg.get('partial')}, ruler width {jg.get('ruler_check', {}).get('width_mm')} mm")

    # No ruler under the line: the part's sides, no ruler offered, and why.
    jn = cli.post("/api/square_up", json=dict(body, ruler=line_none, ruler_image=data_url(img_none))).get_json()
    check("/api/square_up with no ruler: part's sides, ruler not offered, the reason given",
          jn.get("ok") and jn.get("reference") == "part" and jn.get("other") is None and bool(jn.get("ruler_reason")),
          f"reference={jn.get('reference')}, other={jn.get('other')}, reason={jn.get('ruler_reason')!r}")
    jr = cli.post("/api/square_up", json=dict(body, ruler=line_none, ruler_image=data_url(img_none),
                                               reference="ruler")).get_json()
    check("asked for the ruler where there is none: refused, with the reason in the message",
          jr.get("ok") is False and jr.get("ruler_reason") and jr["ruler_reason"] in jr.get("error", ""),
          f"error={jr.get('error')!r}")

    # 5) /api/trace passes the tracer's hint and warnings on, and a refusal always has a hint.
    import auto_outline
    real = auto_outline.auto_outline_full
    tiny = "data:image/png;base64," + base64.b64encode(
        cv2.imencode(".png", np.full((60, 80, 3), 120, np.uint8))[1].tobytes()).decode()
    try:
        auto_outline.auto_outline_full = lambda *a, **k: None
        j1 = cli.post("/api/trace", json={"image": tiny, "seed": [40, 30]}).get_json()
        j1b = cli.post("/api/trace", json={"image": tiny, "seed": [40, 30], "roi": [5, 5, 50, 40]}).get_json()
        auto_outline.auto_outline_full = lambda *a, **k: {"outer": None, "hint": "Tap nearer the middle.",
                                                          "warnings": ["dark photo"]}
        j2 = cli.post("/api/trace", json={"image": tiny, "seed": [40, 30]}).get_json()
        auto_outline.auto_outline_full = lambda *a, **k: {"outer": [[1, 1], [50, 1], [50, 40]], "holes": [],
                                                          "hint": "Check the shadowed end.", "warnings": ["", 7, "touches the edge"]}
        j3 = cli.post("/api/trace", json={"image": tiny, "seed": [40, 30]}).get_json()
    finally:
        auto_outline.auto_outline_full = real
    check("/api/trace no_outline carries a hint (the Area box)",
          j1.get("error") == "no_outline" and "Area" in (j1.get("hint") or "") and "Area box" in (j1b.get("hint") or ""),
          f"hint={j1.get('hint')!r}")
    check("/api/trace passes the tracer's own hint and warnings through",
          j2.get("hint") == "Tap nearer the middle." and j2.get("warnings") == ["dark photo"]
          and j3.get("ok") and j3.get("hint") == "Check the shadowed end." and j3.get("warnings") == ["touches the edge"],
          f"no_outline={j2.get('hint')!r}/{j2.get('warnings')}, ok={j3.get('hint')!r}/{j3.get('warnings')}")

    # 6) The landing card describes photos -> 3D part, with the ruler ON the face.
    layout = str(appmod.app.layout)
    want = ["Turn 2-3 photos into a 3D part (STL / STEP / OBJ)", "FLAT ON the face you are photographing",
            "not beside the part", "Shoot square-on from about 0.5 m with the whole part in frame."]
    gone = ["same flat surface", "coin, a card", "Measure real-world millimeters"]
    check("landing card: photos -> 3D part, ruler on the face",
          all(w_ in layout for w_ in want) and not any(g_ in layout for g_ in gone),
          f"missing={[w_ for w_ in want if w_ not in layout]}, stale={[g_ for g_ in gone if g_ in layout]}")

    ok = all(RESULTS)
    print(f"UNIT_RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
