"""Canvas labels keep off the chips fixed over the top of the photo (assets/calib.draw.js).

On a phone an outline's label was drawn under the Sides bar and the "◂ Simple view" pill and
read "8754 m" between the two. The live redraw now hands their rectangles to
CalibDraw.setReserved; boxLabel treats them like labels already placed. Drives the real
calib.draw.js in node with a recording canvas context:

  - with nothing reserved, a label sits above its anchor;
  - with a chip over that spot, it drops below the anchor instead (with a leader line);
  - a label well away from the chip is not moved;
  - setReserved([]) puts it back, so an export (which never sets any) is unaffected.

Plain python; needs node. Prints one [PASS]/[FAIL] line per check and UNIT_RESULT.
"""
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")


SCRIPT = r"""
global.window = global;
require(process.argv[1]);
const D = window.CalibDraw;
function ctx(){
  const rects = [];
  return { rects, globalAlpha: 1, save(){}, restore(){}, setLineDash(){}, beginPath(){}, moveTo(){}, lineTo(){},
    stroke(){}, strokeRect(){}, fillText(){}, measureText(t){ return { width: 10 * t.length }; },
    fillRect(x, y, w, h){ rects.push([x, y, w, h]); } };
}
const canvas = { width: 1000, height: 800 };
function place(cx, cy){ const c = ctx(); D.resetLabels(); D.boxLabel(c, canvas, cx, cy, 'peri 670.3 mm', 1); return c.rects[0]; }
const out = {};
out.free = place(500, 300);
D.setReserved([{ x: 300, y: 150, w: 400, h: 140 }]);       // a chip over the spot above the anchor
out.under = place(500, 300);
out.away = place(500, 700);
D.setReserved([]);
out.cleared = place(500, 300);
console.log(JSON.stringify(out));
"""


def main():
    node = shutil.which("node")
    if node is None:
        check("node available", False, "these checks drive calib.draw.js in node")
        print("UNIT_RESULT: FAIL")
        return 1
    r = subprocess.run([node, "-e", SCRIPT, str(ROOT / "assets" / "calib.draw.js")],
                       capture_output=True, text=True, timeout=60)
    try:
        o = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        check("calib.draw.js runs in node", False, (r.stdout + r.stderr)[-600:])
        print("UNIT_RESULT: FAIL")
        return 1
    free, under, away, cleared = o["free"], o["under"], o["away"], o["cleared"]
    check("a free label sits above its anchor", free[1] + free[3] <= 300, f"box {free}")
    check("under a chip it drops below the anchor", under[1] >= 300 and not (under[1] < 290 and under[1] + under[3] > 150),
          f"box {under}")
    check("a label away from the chip is not moved", away[1] + away[3] <= 700 and away[1] > 290, f"box {away}")
    check("clearing the chips puts it back", cleared == free, f"{cleared} vs {free}")
    ok = all(RESULTS)
    print("UNIT_RESULT: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
