"""Generate a print-ready CamScan calibration marker.

The marker is the pattern CamScan already auto-detects: a solid black square with
four white pads in a 2x2 grid (same proportions as the detector's target). Printed
on a rigid card and laid flat beside the part, CamScan finds it automatically and
derives scale + a perspective (tilt) correction — no tapping.

Outputs an SVG (vector, exact millimetres — the print master) and a PNG preview.
Usage: python tools/make_marker.py [edge_mm]   (default 30, matching the app default)
"""
import sys, os
import numpy as np, cv2

EDGE_MM = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
OUT = os.path.join(os.path.dirname(__file__), "..", "markers")
os.makedirs(OUT, exist_ok=True)

# Detector proportions (from the target the pipeline is tuned for): pad = 26.67% of the
# square edge, pad top-left corners at 16.67% and 66.67% of the edge.
PAD = 0.26667
P0, P1 = 0.16667, 0.66667
CARD_W, CARD_H = 85.6, 54.0            # ISO ID-1 card (mm); fits a standard envelope
MX, MY = 5.0, 9.0                      # square top-left on the card (mm)

def pads(x0, y0, s):
    p = s * PAD
    return [(x0 + P0*s, y0 + P0*s), (x0 + P1*s, y0 + P0*s),
            (x0 + P0*s, y0 + P1*s), (x0 + P1*s, y0 + P1*s)], p

# ---- SVG (exact mm) ----
sq = EDGE_MM
padpts, p = pads(MX, MY, sq)
rx = MX + sq + 8            # right column x
svg = []
svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{CARD_W}mm" height="{CARD_H}mm" viewBox="0 0 {CARD_W} {CARD_H}">')
svg.append(f'<rect x="0" y="0" width="{CARD_W}" height="{CARD_H}" fill="#ffffff"/>')
svg.append(f'<text x="{MX}" y="6" font-family="sans-serif" font-size="3" font-weight="700" fill="#000">CamScan calibration marker</text>')
svg.append(f'<rect x="{MX}" y="{MY}" width="{sq}" height="{sq}" fill="#000000"/>')
for (px, py) in padpts:
    svg.append(f'<rect x="{px:.3f}" y="{py:.3f}" width="{p:.3f}" height="{p:.3f}" fill="#ffffff"/>')
# verification bar (exact EDGE_MM) so the customer can confirm the print scale
by = MY + 4
svg.append(f'<line x1="{rx}" y1="{by}" x2="{rx+EDGE_MM}" y2="{by}" stroke="#000" stroke-width="0.4"/>')
svg.append(f'<line x1="{rx}" y1="{by-1.4}" x2="{rx}" y2="{by+1.4}" stroke="#000" stroke-width="0.4"/>')
svg.append(f'<line x1="{rx+EDGE_MM}" y1="{by-1.4}" x2="{rx+EDGE_MM}" y2="{by+1.4}" stroke="#000" stroke-width="0.4"/>')
svg.append(f'<text x="{rx}" y="{by+4}" font-family="sans-serif" font-size="2.4" fill="#333">Verify: this bar = {EDGE_MM:.0f} mm</text>')
svg.append(f'<text x="{rx}" y="{by+9}" font-family="sans-serif" font-size="2.4" fill="#333">Square = {EDGE_MM:.0f} mm</text>')
svg.append(f'<text x="{MX}" y="{CARD_H-6.5}" font-family="sans-serif" font-size="2.4" fill="#333">Print at 100% (no &quot;fit to page&quot;). Keep flat &amp; rigid, in the same</text>')
svg.append(f'<text x="{MX}" y="{CARD_H-3}" font-family="sans-serif" font-size="2.4" fill="#333">plane as the part. CamScan finds it automatically to scale + de-tilt.</text>')
svg.append('</svg>')
svg_path = os.path.join(OUT, f"camscan-marker-{int(EDGE_MM)}mm.svg")
open(svg_path, "w").write("\n".join(svg))

# ---- PNG preview (300 DPI) ----
dpi = 300; mm2px = dpi / 25.4
W, H = int(CARD_W*mm2px), int(CARD_H*mm2px)
img = np.full((H, W, 3), 255, np.uint8)
def R(x, y, w, h, col): cv2.rectangle(img, (int(x*mm2px), int(y*mm2px)), (int((x+w)*mm2px), int((y+h)*mm2px)), col, -1)
R(MX, MY, sq, sq, (0,0,0))
for (px, py) in padpts: R(px, py, p, p, (255,255,255))
cv2.line(img, (int(rx*mm2px), int(by*mm2px)), (int((rx+EDGE_MM)*mm2px), int(by*mm2px)), (0,0,0), 2)
cv2.putText(img, "CamScan calibration marker", (int(MX*mm2px), int(6*mm2px)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,0), 2, cv2.LINE_AA)
cv2.putText(img, f"Square = {EDGE_MM:.0f} mm  (verify bar = {EDGE_MM:.0f} mm)", (int(rx*mm2px), int((by+5)*mm2px)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60,60,60), 1, cv2.LINE_AA)
cv2.putText(img, "Print at 100%. Keep flat & rigid, level with the part.", (int(MX*mm2px), int((CARD_H-4)*mm2px)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60,60,60), 1, cv2.LINE_AA)
png_path = os.path.join(OUT, f"camscan-marker-{int(EDGE_MM)}mm.png")
cv2.imwrite(png_path, img)
print("wrote", svg_path)
print("wrote", png_path)
