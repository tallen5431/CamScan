"""Part builder accuracy: the sizes a few hand-held photos give, and a cover that prints.

    python tests/unit_p1_part_builder.py

Every case is synthetic (exact outlines in mm), each chosen to reproduce a way a real build of
the running-board step-pad cover came out wrong:

  * a photo tilted a degree or two read 12 mm too wide, or was refused as "scaled wrong";
  * an end photo at x1.05 was fitted on its width only, so the height kept the 5% error;
  * one blob of shadow in the top trace widened the whole part;
  * 0.35 mm of trace noise left 18 of 122,500 mm^2 of the deck on the bed;
  * the status said "turned plate-down" for a file that was open side down;
  * STEP files for CAD came in upside down;
  * with no Side photo the sloped ends came out square.

Prints one [PASS]/[FAIL] line per check and UNIT_RESULT: PASS|FAIL; exits non-zero on failure.
"""
import math
import pathlib
import struct
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

_failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


def rect(w, h):
    return np.array([[0, 0], [w, 0], [w, h], [0, h]], float)


def turned(p, deg, at=(120.0, 60.0)):
    """An outline as a photo tilted `deg` would hand it over, somewhere in the frame."""
    p = np.asarray(p, float)
    t = math.radians(deg)
    c = p.mean(axis=0)
    q = p - c
    return (np.c_[q[:, 0] * math.cos(t) - q[:, 1] * math.sin(t),
                  q[:, 0] * math.sin(t) + q[:, 1] * math.cos(t)] + c + at).tolist()


def jittered_profile(length, height, sigma, rng, step=2.0):
    """A side/end profile whose top edge is densely traced with sub-pixel noise."""
    xs = np.arange(length, -1e-9, -step)
    top = np.c_[xs, height + rng.normal(0.0, sigma, len(xs))]
    return np.vstack([[0, 0], [length, 0], top]).tolist()


def rounded_bottom(w, h, r, n=12):
    """An end profile with both bottom corners rounded by r."""
    a = np.linspace(0.0, math.pi / 2, n + 1)
    pts = [[r - r * math.cos(k), r - r * math.sin(k)] for k in a]
    pts += [[w - r + r * math.sin(k), r - r * math.cos(k)] for k in a]
    return pts + [[w, h], [0, h]]


def as_manifold(tris):
    import manifold3d as mf
    m = mf.Mesh(vert_properties=np.asarray(tris, np.float32).reshape(-1, 3),
                tri_verts=np.arange(len(tris) * 3, dtype=np.uint32).reshape(-1, 3))
    m.merge()
    return mf.Manifold(m)


def x_span_at(points, z):
    """Length (X) of the part's face lying at height z."""
    pts = np.asarray(points, float).reshape(-1, 3)
    on = np.abs(pts[:, 2] - z) < 1e-4
    return float(np.ptp(pts[on, 0])) if on.any() else 0.0


def main():
    import part_builder as pb
    from part_builder import build_part, PartError
    from solid_export import mesh_report

    if not pb.available():
        check("manifold3d available", False, "install manifold3d")
        return

    TOP, SIDE, END = rect(700, 175), rect(700, 50), rect(175, 50)

    # 1) Tilt. The same tilt in every photo, as a hand-held phone gives: the size must come
    #    back exact, with nothing stretched and nothing refused.
    rows, bad = [], []
    for deg in (1.0, 2.0, 5.0, -3.0):
        try:
            i = build_part({"top": {"points": turned(TOP, deg)}, "side": {"points": turned(SIDE, deg)},
                            "end": {"points": turned(END, deg)}}, preview=True)["info"]
        except PartError as e:
            bad.append(f"{deg} deg refused: {e}")
            continue
        err = max(abs(a - b) for a, b in zip(i["size_mm"], (700, 175, 50)))
        worst = max([abs(f["pct"]) for f in i["fits"]] + [0.0])
        removed = {d["view"]: d["deg"] for d in i["deskew"]}
        if err > 0.5 or worst > 0.5 or len(removed) != 3 or any(abs(v - deg) > 0.05 for v in removed.values()):
            bad.append(f"{deg} deg: size {i['size_mm']}, worst fit {worst}%, deskew {i['deskew']}")
        rows.append(f"{deg:+.0f}: {err:.2f} mm")
    check("a photo tilted 1-5 degrees still gives 700 x 175 x 50, no stretch, no refusal",
          not bad, "; ".join(bad) or ", ".join(rows))

    #    keep_tilt and a fine turn: the user can switch it off, or turn by hand.
    try:
        raw = build_part({"top": {"points": turned(TOP, 1.0), "keep_tilt": True}},
                         depth_mm=10, preview=True)["info"]
        hand = build_part({"top": {"points": turned(TOP, 1.0), "keep_tilt": True, "rot_deg": -1.0}},
                          depth_mm=10, preview=True)["info"]
        ok = (raw["deskew"] == [] and raw["size_mm"][1] > 186 and abs(hand["size_mm"][1] - 175) < 0.05)
        check("keep_tilt leaves the tilt; rot_deg turns it by hand", ok,
              f"kept: {raw['size_mm']}, turned by hand: {hand['size_mm']}")
    except PartError as e:
        check("keep_tilt leaves the tilt; rot_deg turns it by hand", False, repr(e))

    #    An outline with no edge to square (a round) is left alone.
    tt = np.linspace(0, 2 * math.pi, 90, endpoint=False)
    circle = np.c_[60 * np.cos(tt) + 60, 60 * np.sin(tt) + 60]
    check("a round outline is not 'deskewed'", pb._tilt_deg(circle) == 0.0,
          f"tilt {pb._tilt_deg(circle)}")

    # 2) A photo has one scale: End at x1.05 and x1.075 against a right Top gives the right
    #    height, and says the whole view was rescaled.
    bad, rows = [], []
    for s in (1.05, 1.075):
        i = build_part({"top": {"points": TOP.tolist()}, "end": {"points": (rect(175, 45) * s).tolist()}},
                       preview=True)["info"]
        f = [x for x in i["fits"] if x["view"] == "end"]
        if not (abs(i["size_mm"][2] - 45.0) <= 0.1 and len(f) == 1 and f[0]["kind"] == "rescale"
                and abs(f[0]["pct"] - (s - 1) * 100) < 0.01 and f[0]["setter"] == "top"
                and abs(f[0]["have_mm"] - 175 * s) < 0.01 and f[0]["want_mm"] == 175.0):
            bad.append(f"x{s}: size {i['size_mm']}, fits {i['fits']}")
        if not any("rescaled" in w for w in i["warnings"]):
            bad.append(f"x{s}: no warning past 3%: {i['warnings']}")
        rows.append(f"x{s}: height {i['size_mm'][2]}")
    #    Both axes fitted to other views (three views): the end is stretched, not rescaled.
    i = build_part({"top": {"points": TOP.tolist()}, "side": {"points": SIDE.tolist()},
                    "end": {"points": (END * [1.02, 1.0]).tolist()}}, preview=True)["info"]
    kinds = {(f["view"], f["axis"]): f["kind"] for f in i["fits"]}
    if kinds.get(("end", "width")) != "stretch" or i["size_mm"] != [700, 175, 50]:
        bad.append(f"three views: {i['fits']} {i['size_mm']}")
    check("a mis-scaled End is rescaled whole: its height is corrected too", not bad,
          "; ".join(bad) or ", ".join(rows) + "; in a 3-view build it is stretched")

    # 3) A blob of shadow near one end of the Top trace does not set the width.
    blob = [[0, 0], [700, 0], [700, 155], [660, 155], [655, 173], [630, 173], [625, 155], [0, 155]]
    try:
        i = build_part({"top": {"points": blob}, "end": {"points": rect(156, 50).tolist()}},
                       preview=True)["info"]
        w = i["size_mm"][1]
        warn = [x for x in i["warnings"] if "18 mm wider" in x and "% of its length" in x and "Tidy" in x]
        check("a shadow blob on the Top trace: width from the body, and it says where",
              155 <= w <= 157 and warn, f"width {w}, warnings {i['warnings']}")
    except PartError as e:
        check("a shadow blob on the Top trace: width from the body, and it says where", False, repr(e))
    #    ...but when the End view agrees with the wide box, the box stands.
    i = build_part({"top": {"points": blob}, "end": {"points": rect(172, 50).tolist()}}, preview=True)["info"]
    check("a wide part the End view confirms keeps its full width",
          abs(i["size_mm"][1] - 173) < 0.01 and not i["warnings"], f"{i['size_mm']} {i['warnings']}")

    # 4) Overrides: known sizes, which view sets an axis, a per-view scale.
    bad = []
    i = build_part({"top": {"points": TOP.tolist()}, "end": {"points": END.tolist()}},
                   known_mm={"length": 720}, preview=True)["info"]
    k = [f for f in i["fits"] if f["setter"] == "known"]
    if not (np.allclose(i["size_mm"], [720, 180, 51.429], atol=0.01) and len(k) == 1
            and k[0]["view"] == "top" and abs(k[0]["pct"] - (700 / 720 - 1) * 100) < 0.01):
        bad.append(f"known length: {i['size_mm']} {i['fits']}")
    i = build_part({"top": {"points": TOP.tolist()}, "end": {"points": END.tolist()}},
                   known_mm={"length": 700, "width": 180}, preview=True)["info"]
    if not np.allclose(i["size_mm"], [700, 180, 51.429], atol=0.01):
        bad.append(f"known length+width: {i['size_mm']}")
    i = build_part({"top": {"points": TOP.tolist()}}, known_mm={"height": 30}, preview=True)["info"]
    if not np.allclose(i["size_mm"], [700, 175, 30]):
        bad.append(f"known height for a lone Top: {i['size_mm']}")
    try:
        build_part({"top": {"points": TOP.tolist()}, "end": {"points": END.tolist()}}, known_mm={"length": 70})
        bad.append("a typo (70 for 700) was accepted")
    except PartError as e:
        if not (e.detail and e.detail["setter"] == "known"):
            bad.append(f"typo refusal has no detail: {e}")
    i = build_part({"top": {"points": TOP.tolist()}, "end": {"points": rect(180, 50).tolist()}},
                   axis_source={"width": "end"}, preview=True)["info"]
    if abs(i["size_mm"][1] - 180) > 0.01 or not any(f["view"] == "top" and f["setter"] == "end"
                                                     for f in i["fits"]):
        bad.append(f"axis_source width=end: {i['size_mm']} {i['fits']}")
    i = build_part({"top": {"points": (TOP / 1.1).tolist(), "scale": 1.1}, "end": {"points": END.tolist()}},
                   preview=True)["info"]
    if not np.allclose(i["size_mm"], [700, 175, 50], atol=0.01):
        bad.append(f"per-view scale: {i['size_mm']}")
    for nm, kw in (("bad axis", dict(axis_source={"depth": "top"})),
                   ("view without that axis", dict(axis_source={"length": "end"})),
                   ("scale out of range", dict(views={"top": {"points": TOP.tolist(), "scale": 50}}))):
        try:
            build_part(**{"views": {"top": {"points": TOP.tolist()}, "end": {"points": END.tolist()}}, **kw})
            bad.append(f"accepted: {nm}")
        except PartError:
            pass
    check("known sizes, axis_source and per-view scale each do what they say", not bad,
          "; ".join(bad) or "known length scales every view; width=end makes End win; x1.1 undone")

    # 5) A refusal names the likely causes, and carries the numbers as data.
    try:
        build_part({"top": {"points": rect(679, 164).tolist()}, "side": {"points": rect(160, 133).tolist()}})
        check("a 76% mismatch is refused with its causes and detail", False, "accepted")
    except PartError as e:
        d = e.detail or {}
        check("a 76% mismatch is refused with its causes and detail",
              "76%" in str(e) and "angle" in str(e) and "straight-on side view" in str(e)
              and d.get("view") == "side" and d.get("axis") == "length" and d.get("setter") == "top"
              and d.get("have_mm") == 160.0 and d.get("want_mm") == 679.0, f"{e} {d}")
    try:
        build_part({"top": {"points": TOP.tolist()}, "end": {"points": rect(175 * 1.12, 50).tolist()}})
        check("a 12% mismatch is refused, blaming scale or a blob", False, "accepted")
    except PartError as e:
        check("a 12% mismatch is refused, blaming scale or a blob",
              "12%" in str(e) and "scaled wrong" in str(e) and e.detail and e.detail["view"] == "end", str(e))

    # 6) The deck of a hollow cover sits on the bed even when the traces are noisy.
    rng = np.random.default_rng(7)
    contact, notes, warns = [], [], []
    for trial in range(3):
        r = build_part({"top": {"points": TOP.tolist()},
                        "side": {"points": jittered_profile(700, 40, 0.35, rng)},
                        "end": {"points": jittered_profile(175, 40, 0.35, rng)}}, wall_mm=2.5)
        contact.append(as_manifold(r["tris"]).slice(0.2).area() / (700 * 175))
        notes.append(r["info"].get("note") or "")
        warns += r["info"]["warnings"]
    check("0.35 mm trace noise: at least 90% of the deck is within 0.2 mm of the bed, no warning",
          min(contact) >= 0.9 and not warns and all("Flattened the face that prints on the bed" in n for n in notes),
          f"on the bed: {', '.join(f'{c * 100:.1f}%' for c in contact)}; warnings {warns}")
    clean = build_part({"top": {"points": TOP.tolist()}, "side": {"points": rect(700, 40).tolist()},
                        "end": {"points": rect(175, 40).tolist()}}, wall_mm=2.5)
    want = 700 * 175 * 40 - 695 * 170 * 37.5
    check("a clean cover is unchanged: exact volume, no flattening",
          abs(clean["info"]["volume_mm3"] - want) < 1e-6 * want and "Flattened" not in (clean["info"].get("note") or ""),
          f"{clean['info']['volume_mm3']:.1f} vs {want:.1f} mm3")
    #    A deck the flattening cannot help (tilted 4 degrees) is flagged.
    slant = [[0, 0], [700, 0], [700, 40], [0, 40 + 700 * math.tan(math.radians(4))]]
    r = build_part({"top": {"points": TOP.tolist()}, "side": {"points": slant, "keep_tilt": True}}, wall_mm=2.5)
    check("a deck that barely touches the bed is warned about",
          any("of the deck lies on the bed" in w for w in r["info"]["warnings"]), str(r["info"]["warnings"]))

    # 7) Orientation: the user's flag reported as given, the automatic turn on its own, and the
    #    note says what the file is.
    bad = []
    for u in (False, True):
        r = build_part({"top": {"points": TOP.tolist()}, "side": {"points": rect(700, 40).tolist()}},
                       wall_mm=2.5, upside_down=u)
        i = r["info"]
        bed = as_manifold(r["tris"]).slice(0.2).area()
        if i["upside_down"] is not u:
            bad.append(f"upside_down={u} reported as {i['upside_down']}")
        if u is False and not (i["plate_down"] is True and "ready to print" in i["orientation"]
                               and bed > 0.95 * 700 * 175 and "ready to print" in i["note"]
                               and not i["warnings"]):
            bad.append(f"default: {i} bed {bed:.0f}")
        if u is True and not (i["plate_down"] is False and "needs supports" in i["orientation"]
                              and any("supports" in w for w in i["warnings"])
                              and "plate-down" not in i["note"] and bed < 0.1 * 700 * 175):
            bad.append(f"upside down: {i} bed {bed:.0f}")
    for u in (False, True):                              # a Top-only cover, the other path
        r = build_part({"top": {"points": TOP.tolist()}}, depth_mm=30, wall_mm=2.5, upside_down=u)
        i = r["info"]
        if i["upside_down"] is not u or i["plate_down"] is u or ("needs supports" in i["orientation"]) is not u:
            bad.append(f"Top-only, upside_down={u}: {i}")
    check("a cover reports the user's flag and the print turn separately, and the note matches",
          not bad, "; ".join(bad) or "plate-down by default, open side down (with a warning) when asked")

    # 8) Only the STL is turned for printing; for CAD the cover comes Z up, as traced.
    r_cad = build_part({"top": {"points": TOP.tolist()}, "side": {"points": rect(700, 40).tolist()}},
                       wall_mm=2.5, for_print=False)
    i = r_cad["info"]
    bed = as_manifold(r_cad["tris"]).slice(0.2).area()
    top_z = float(np.asarray(r_cad["tris"])[:, :, 2].max())
    deck = as_manifold(r_cad["tris"]).slice(top_z - 0.2).area()
    check("for_print=False: the cover stays Z up (open side down), as traced",
          i["plate_down"] is False and bed < 0.1 * 700 * 175 and deck > 0.95 * 700 * 175
          and "for CAD" in i["orientation"] and not i["warnings"], f"{i['orientation']}; bed {bed:.0f} mm2")

    # 9) Sloped and rounded ends typed in, for a part with no Side photo.
    r = build_part({"top": {"points": TOP.tolist()}, "end": {"points": END.tolist()}},
                   wall_mm=3, ends={"slope_deg": 30, "radius_mm": 10}, for_print=False)
    rep = mesh_report(r["tris"])
    pts = np.asarray(r["tris"])
    zt = float(pts[:, :, 2].max())
    short = 700 - x_span_at(pts, zt)
    want = 2 * 50 * math.tan(math.radians(30))
    check("ends slope 30 degrees: the top face is 2*Z*tan(30) shorter, and the cover is closed",
          rep["watertight"] and rep["oriented"] and abs(x_span_at(pts, 0.0) - 700) < 0.01
          and want - 0.5 <= short <= want + 25 and r["info"].get("ends") == {"slope_deg": 30.0, "radius_mm": 10.0},
          f"top face {700 - short:.1f} mm vs bottom 700 (expected about {700 - want:.1f}, the round "
          f"trims a little more); closed={rep['watertight']}")
    r = build_part({"top": {"points": TOP.tolist()}, "end": {"points": END.tolist()}},
                   ends={"slope_deg": 30}, for_print=False)
    short = 700 - x_span_at(np.asarray(r["tris"]), 50.0)
    check("ends slope without a radius cuts exactly 2*Z*tan(slope)", abs(short - want) < 0.01,
          f"{short:.3f} vs {want:.3f} mm")

    # 10) A rounded outer bottom corner feathers the open rim; say so.
    r = build_part({"top": {"points": TOP.tolist()}, "end": {"points": rounded_bottom(175, 55, 3.0)}}, wall_mm=3)
    sharp = build_part({"top": {"points": TOP.tolist()}, "end": {"points": rect(175, 55).tolist()}}, wall_mm=3)
    check("a rim thinned by a rounded bottom corner is warned about, a square one is not",
          any("open edge thins" in w for w in r["info"]["warnings"]) and not sharp["info"]["warnings"],
          f"rounded: {r['info']['warnings']}; square: {sharp['info']['warnings']}")

    # 11) The endpoint: detail in the 422, new fields parsed, STEP Z up and STL plate-down.
    import app as _app
    cli = _app.server.test_client()
    pv = cli.post("/api/export/part", json={"views": {"top": {"points": rect(679, 164).tolist(), "mm_per_px": 1},
                                                      "side": {"points": rect(160, 133).tolist(), "mm_per_px": 1}},
                                            "preview": True})
    j = pv.get_json() or {}
    dl = cli.post("/api/export/part", json={"views": {"top": {"points": rect(679, 164).tolist(), "mm_per_px": 1},
                                                      "side": {"points": rect(160, 133).tolist(), "mm_per_px": 1}}})
    check("/api/export/part: a refusal carries its detail (preview JSON, download header)",
          pv.status_code == 422 and (j.get("detail") or {}).get("setter") == "top" and "angle" in (j.get("error") or "")
          and dl.status_code == 422 and '"view": "side"' in (dl.headers.get("X-CamScan-Detail") or ""),
          f"{pv.status_code} {j}; download {dl.status_code} {dl.headers.get('X-CamScan-Detail')}")

    body = {"views": {"top": {"points": turned(TOP, 2.0), "mm_per_px": 1},
                      "end": {"points": (END * 1.04).tolist(), "mm_per_px": 1, "scale": 1 / 1.04}},
            "wall_mm": 3, "ends": {"slope_deg": 30, "radius_mm": 0}}
    stl = cli.post("/api/export/part", json=body)
    step = cli.post("/api/export/part", json=dict(body, format="step"))
    bad = []
    if stl.status_code != 200 or step.status_code != 200:
        bad.append(f"status {stl.status_code}/{step.status_code}: {stl.data[:200]!r}")
    else:
        blob = stl.data
        n = struct.unpack("<I", blob[80:84])[0]
        tri = np.frombuffer(blob[84:], dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                            count=n)["v"].astype(float)
        lo, hi = tri.reshape(-1, 3).min(0), tri.reshape(-1, 3).max(0)
        if not np.allclose(hi - lo, [700, 175, 50], atol=0.05):
            bad.append(f"STL box {hi - lo}")
        if not (x_span_at(tri, lo[2]) < x_span_at(tri, hi[2]) - 50):      # short deck on the bed
            bad.append(f"STL not plate-down: bed {x_span_at(tri, lo[2]):.1f}, top {x_span_at(tri, hi[2]):.1f}")
        import re
        txt = step.data.decode("ascii", "replace")
        sp = np.array([[float(c) for c in m.split(",")] for m in
                       re.findall(r"CARTESIAN_POINT\('',\(([^)]*)\)\)", txt)])
        sp = sp[np.isfinite(sp).all(1)] if len(sp) else sp
        vlo, vhi = sp.min(0), sp.max(0)
        if not np.allclose(vhi - vlo, [700, 175, 50], atol=0.05):
            bad.append(f"STEP box {vhi - vlo}")
        if not (x_span_at(sp, vhi[2]) < x_span_at(sp, vlo[2]) - 50):      # short deck on top
            bad.append(f"STEP not Z up: bottom {x_span_at(sp, vlo[2]):.1f}, top {x_span_at(sp, vhi[2]):.1f}")
        info = __import__("json").loads(stl.headers.get("X-CamScan-Info") or "{}")
        if not (info.get("plate_down") is True and info.get("deskew") and "orientation" in info):
            bad.append(f"STL header {info}")
    check("STL downloads plate-down, STEP Z up; tilt, scale and ends parsed by the endpoint",
          not bad, "; ".join(bad) or "both 700 x 175 x 50; deck on the bed in the STL, on top in the STEP")
    junk = {nm: cli.post("/api/export/part", json=dict(body, **extra)).status_code for nm, extra in {
        "known not an object": {"known_mm": [1, 2]},
        "known not a number": {"known_mm": {"length": "abc"}},
        "ends not an object": {"ends": "steep"},
        "rot_deg infinite": {"views": {"top": {"points": TOP.tolist(), "rot_deg": "Infinity"}}},
        "axis_source bad view": {"axis_source": {"width": "roof"}},
    }.items()}
    check("/api/export/part: junk in the new fields is a clean 400/422",
          all(c in (400, 422) for c in junk.values()), str(junk))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory():
        try:
            main()
        except Exception as e:                      # a crash is a failure, not a skip
            import traceback
            traceback.print_exc()
            check("unit_p1_part_builder ran to the end", False, repr(e))
    print()
    print("UNIT_RESULT: " + ("PASS" if not _failures else "FAIL"))
    sys.exit(1 if _failures else 0)
