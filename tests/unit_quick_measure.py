"""Quick measure's footing: the ± on a length, the wallet marker card, and its download.

On a job the question is "is this number good enough to print a part from?". Each length
now carries a ± (CalibGeom.lengthUncertainty), and the marker that makes a phone photo
measure true comes as a wallet card. Checked here:
  - the ± follows its model: tap error and the scale's own error; a homography's grows with
    how far past the reference the length reaches, a line scale's does not but is flagged
    square-on only; bad input is refused;
  - the card on file is what tools/make_wallet_card.py makes, its marker exactly 30 mm, and
    the app's detector finds it on a photo, flat and tilted 30 degrees;
  - /markers/ serves the card and nothing else.

    python tests/unit_quick_measure.py
"""
import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
_failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)


SCRIPT = r"""
global.window = {};
require(process.argv[1]);
const G = window.CalibGeom, U = G.lengthUncertainty;
console.log(JSON.stringify({
  line: U({lengthMM: 300, lengthPx: 1500, refPx: 1500, refPickPx: 1, perspective: false}),
  line3x: U({lengthMM: 900, lengthPx: 4500, refPx: 1500, refPickPx: 1, perspective: false}),
  // pickPx tiny: the scale's own term alone
  card1: U({lengthMM: 30, lengthPx: 300, refPx: 300, refPickPx: 0.35, perspective: true, pickPx: 1e-6}),
  card5: U({lengthMM: 150, lengthPx: 1500, refPx: 300, refPickPx: 0.35, perspective: true, pickPx: 1e-6}),
  noref: U({lengthMM: 50, lengthPx: 500}),
  bad: [U(null), U({lengthMM: 0, lengthPx: 10}), U({lengthMM: 10, lengthPx: NaN}), U({lengthMM: -1, lengthPx: 5})],
}));
"""


def main():
    import cv2
    import numpy as np

    node = shutil.which("node") or ("/opt/node22/bin/node" if pathlib.Path("/opt/node22/bin/node").exists() else None)
    if node is None:
        check("lengthUncertainty (needs node)", True, "skipped — no node runtime")
    else:
        r = subprocess.run([node, "-e", SCRIPT, str(ROOT / "assets" / "calib.geometry.js")],
                           capture_output=True, text=True, timeout=60)
        o = json.loads(r.stdout.strip())
        # Line: ends sqrt2*1*0.2 = 0.283 mm; scale sqrt2/1500 of 300 mm = 0.283 mm -> sigma 0.4, ± 0.8.
        check("a 300 mm length on a 30 cm ruler line reads ± 0.8 mm, square-on only",
              abs(o["line"]["pm"] - 0.8) < 0.01 and o["line"]["squareOn"] is True, str(o["line"]))
        # Uniform scale: past the ruler only the taps' share shrinks, toward the scale's 0.189 %.
        check("a line scale's relative error does not grow past the ruler (uniform scale)",
              0.188 < o["line3x"]["relPct"] < o["line"]["relPct"], f"{o['line']['relPct']:.3f} -> {o['line3x']['relPct']:.3f} %")
        check("a homography's error grows with how far past the reference the length reaches",
              abs(o["card5"]["relPct"] / o["card1"]["relPct"] - 5) < 0.01 and o["card1"]["squareOn"] is False,
              f"card at 1x {o['card1']['relPct']:.2f} %, at 5x {o['card5']['relPct']:.2f} %")
        check("with no reference known, only the taps count", abs(o["noref"]["pm"] - 2 * 2 ** 0.5 * 0.1) < 1e-6, str(o["noref"]))
        check("bad input gives no ±", o["bad"] == [None, None, None, None], str(o["bad"]))

    import make_wallet_card as w
    svg_path = ROOT / "markers" / "camscan-wallet-card.svg"
    on_file = svg_path.read_text(encoding="utf-8") if svg_path.exists() else ""
    check("the wallet card on file is what tools/make_wallet_card.py makes", on_file == w.svg(),
          "regenerate with: python tools/make_wallet_card.py" if on_file != w.svg() else "")
    sq = re.findall(r'<rect x="[\d.]+" y="[\d.]+" width="([\d.]+)" height="([\d.]+)" fill="#000"/>', on_file)
    check("its markers are exactly 30 mm on an 85.6 x 54 mm card",
          len(sq) == 2 and all(float(a) == 30.0 and float(b) == 30.0 for a, b in sq)
          and on_file.count(f'width="{w.CW}" height="{w.CH}"') == 4, f"squares {sq}")

    import calibration_core as cc
    sheet = w.png(dpi=600)
    k = 600 / 25.4
    front = sheet[int(10.0 * k):int((10.0 + w.CH) * k), int(8.0 * k):int((8.0 + w.CW) * k)]
    found = []
    for tilt in (0, 30):
        rng = np.random.default_rng(1)
        bg = np.clip(np.full((2400, 3200, 3), (120, 128, 135), float) + rng.normal(0, 6, (2400, 3200, 3)), 0, 255).astype(np.uint8)
        h, wd = front.shape[:2]
        sc, t = 10.0 / k, np.radians(tilt)
        cw, ch, s = wd * sc, h * sc, 1 - 0.5 * np.sin(t)
        dst = np.float32([[1600 - cw / 2 * s, 1200 - ch / 2 * np.cos(t)], [1600 + cw / 2 * s, 1200 - ch / 2 * np.cos(t)],
                          [1600 + cw / 2, 1200 + ch / 2 * np.cos(t)], [1600 - cw / 2, 1200 + ch / 2 * np.cos(t)]])
        M = cv2.getPerspectiveTransform(np.float32([[0, 0], [wd, 0], [wd, h], [0, h]]), dst)
        warped = cv2.warpPerspective(front, M, (3200, 2400))
        mask = cv2.warpPerspective(np.full((h, wd), 255, np.uint8), M, (3200, 2400))
        img = bg.copy()
        img[mask > 0] = warped[mask > 0]
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            cal, _ = cc.calibrate_image(cv2.GaussianBlur(img, (0, 0), 0.8), edge_mm=30.0)
        found.append((len(cal.get("markers") or []), cal.get("calibration_usable")))
    check("the app's detector finds the card's marker on a photo, flat and tilted 30 degrees",
          all(n == 1 and u is not False for n, u in found), str(found))

    import app as appmod
    cli = appmod.server.test_client()
    ok = cli.get("/markers/camscan-wallet-card.svg")
    # Anything else is a 404 — or the app's own page, for a path the router normalises away
    # from /markers/ (never the file asked for).
    bad = [cli.get(u) for u in ("/markers/app.py", "/markers/..%2Fapp.py", "/markers/camscan-x.txt")]
    leaked = [r.status_code for r in bad if r.status_code == 200 and (b"import " in r.data or "html" not in r.content_type)]
    check("/markers/ serves the wallet card, and only marker files",
          ok.status_code == 200 and b"CamScan marker card" in ok.data and not leaked
          and bad[0].status_code == 404 and bad[2].status_code == 404,
          f"card {ok.status_code}, others {[r.status_code for r in bad]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                      # a crash is a failure, not a skip
        import traceback
        traceback.print_exc()
        check("unit_quick_measure ran to the end", False, repr(e))
    print()
    print("UNIT_RESULT: " + ("PASS" if not _failures else "FAIL"))
    sys.exit(1 if _failures else 0)
