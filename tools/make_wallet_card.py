"""Generate the CamScan wallet marker card: a fold-over, credit-card-size (ISO ID-1,
85.6 x 54 mm) card with the calibration marker on the front and how to shoot on the back.

On a job the marker is what makes a phone photo measure true: it gives the scale and corrects
the camera's tilt, which a ruler alone cannot. At close range (a knob, a hinge cover) a 30 mm
marker fills enough of the frame to be read to a fraction of a pixel, and a card fits in a
wallet. The sheet holds two cards; each is a front and a back side by side — cut round the
pair, fold on the dashed line, and glue or laminate.

Outputs markers/camscan-wallet-card.svg (exact millimetres — print at 100 %) and a .png
preview. Usage: python tools/make_wallet_card.py [edge_mm]   (default 30)
"""
import os
import sys

import cv2
import numpy as np

EDGE_MM = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "markers")

# The detector's target, as in make_marker.py: a black square with four white pads in a 2x2
# grid — pad 26.67 % of the edge, pad corners at 16.67 % and 66.67 %.
PAD, P0, P1 = 0.26667, 0.16667, 0.66667
CW, CH = 85.6, 54.0                      # ISO ID-1
GAP = 0.0                                # front and back share the fold line
PAGE_W, PAGE_H = 2 * CW + 2 * 8.0, 2 * CH + 3 * 8.0 + 8.0
MX, MY = 6.0, 10.0                       # the marker square on the front, from its top-left

BACK = [
    ("How to shoot", True),
    ("1  Lay this card FLAT on the face you measure,", False),
    ("    next to the part — not on the floor beside it.", False),
    ("2  Phone straight above, parallel to the surface.", False),
    ("3  Fill the frame with the part and the card.", False),
    ("4  Sharp and no glare on the card.", False),
    ("5  Measure up to ~5x the card's size; bigger", False),
    ("    parts: a ruler or sheet along the part.", False),
    ("Read the ± in CamScan; calipers for tight fits.", True),
]


def front_svg(x0, y0):
    s, p = EDGE_MM, EDGE_MM * PAD
    out = [f'<rect x="{x0}" y="{y0}" width="{CW}" height="{CH}" fill="#fff" stroke="#999" stroke-width="0.2"/>',
           f'<text x="{x0 + MX}" y="{y0 + 6.5}" font-family="sans-serif" font-size="3.2" font-weight="700">CamScan marker card</text>',
           f'<rect x="{x0 + MX}" y="{y0 + MY}" width="{s}" height="{s}" fill="#000"/>']
    for fx in (P0, P1):
        for fy in (P0, P1):
            out.append(f'<rect x="{x0 + MX + fx * s:.3f}" y="{y0 + MY + fy * s:.3f}" width="{p:.3f}" height="{p:.3f}" fill="#fff"/>')
    rx, by = x0 + MX + s + 7, y0 + MY + 4
    out += [f'<line x1="{rx}" y1="{by}" x2="{rx + s}" y2="{by}" stroke="#000" stroke-width="0.4"/>',
            f'<line x1="{rx}" y1="{by - 1.4}" x2="{rx}" y2="{by + 1.4}" stroke="#000" stroke-width="0.4"/>',
            f'<line x1="{rx + s}" y1="{by - 1.4}" x2="{rx + s}" y2="{by + 1.4}" stroke="#000" stroke-width="0.4"/>',
            f'<text x="{rx}" y="{by + 4.5}" font-family="sans-serif" font-size="2.6" fill="#222">Check: this bar = {s:.0f} mm</text>',
            f'<text x="{rx}" y="{by + 9}" font-family="sans-serif" font-size="2.6" fill="#222">Square = {s:.0f} mm</text>',
            f'<text x="{rx}" y="{by + 15}" font-family="sans-serif" font-size="2.3" fill="#444">Lay flat on the face</text>',
            f'<text x="{rx}" y="{by + 18.5}" font-family="sans-serif" font-size="2.3" fill="#444">you photograph.</text>',
            f'<text x="{x0 + MX}" y="{y0 + CH - 4}" font-family="sans-serif" font-size="2.3" fill="#444">Printed at 100 %? The bar must measure {s:.0f} mm.</text>']
    return out


def back_svg(x0, y0):
    out = [f'<rect x="{x0}" y="{y0}" width="{CW}" height="{CH}" fill="#fff" stroke="#999" stroke-width="0.2"/>']
    y = y0 + 8.0
    for text, bold in BACK:
        size = 3.2 if bold and text == BACK[0][0] else 2.6
        weight = ' font-weight="700"' if bold else ''
        out.append(f'<text x="{x0 + 5}" y="{y:.2f}" font-family="sans-serif" font-size="{size}"{weight}>'
                   f'{text.replace("&", "&amp;")}</text>')
        y += 5.0 if text == BACK[0][0] else 4.6
    return out


def svg():
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{PAGE_W}mm" height="{PAGE_H}mm" viewBox="0 0 {PAGE_W} {PAGE_H}">',
             f'<rect x="0" y="0" width="{PAGE_W}" height="{PAGE_H}" fill="#fff"/>',
             '<text x="8" y="6" font-family="sans-serif" font-size="3" fill="#333">Print at 100 % (no "fit to page"). '
             'Cut round each pair, fold on the dashed line, glue or laminate.</text>']
    for k in range(2):
        y0 = 10.0 + k * (CH + 8.0)
        parts += front_svg(8.0, y0) + back_svg(8.0 + CW, y0)
        parts.append(f'<line x1="{8.0 + CW}" y1="{y0 - 2}" x2="{8.0 + CW}" y2="{y0 + CH + 2}" stroke="#666" '
                     f'stroke-width="0.25" stroke-dasharray="1.2,1"/>')
    parts.append('</svg>')
    return "\n".join(parts)


def png(dpi=300):
    """A preview of the sheet (the SVG is the print master). The marker is drawn exactly."""
    k = dpi / 25.4
    W, H = int(PAGE_W * k), int(PAGE_H * k)
    img = np.full((H, W, 3), 255, np.uint8)

    def rect(x, y, w, h, col, fill=True):
        cv2.rectangle(img, (int(round(x * k)), int(round(y * k))), (int(round((x + w) * k)), int(round((y + h) * k))),
                      col, -1 if fill else 1)

    def text(x, y, t, size=2.6, bold=False):
        cv2.putText(img, t, (int(x * k), int(y * k)), cv2.FONT_HERSHEY_SIMPLEX, size * k / 30.0, (30, 30, 30),
                    2 if bold else 1, cv2.LINE_AA)

    s, p = EDGE_MM, EDGE_MM * PAD
    for kk in range(2):
        y0 = 10.0 + kk * (CH + 8.0)
        x0 = 8.0
        rect(x0, y0, CW, CH, (150, 150, 150), fill=False)
        rect(x0 + CW, y0, CW, CH, (150, 150, 150), fill=False)
        text(x0 + MX, y0 + 6.5, "CamScan marker card", 3.2, True)
        rect(x0 + MX, y0 + MY, s, s, (0, 0, 0))
        for fx in (P0, P1):
            for fy in (P0, P1):
                rect(x0 + MX + fx * s, y0 + MY + fy * s, p, p, (255, 255, 255))
        rx, by = x0 + MX + s + 7, y0 + MY + 4
        cv2.line(img, (int(rx * k), int(by * k)), (int((rx + s) * k), int(by * k)), (0, 0, 0), 2)
        text(rx, by + 4.5, f"Check: this bar = {s:.0f} mm")
        text(rx, by + 9, f"Square = {s:.0f} mm")
        y = y0 + 8.0
        for t, bold in BACK:
            text(x0 + CW + 5, y, t.replace("±", "+/-"), 3.2 if t == BACK[0][0] else 2.6, bold)
            y += 5.0 if t == BACK[0][0] else 4.6
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    sp = os.path.join(OUT, "camscan-wallet-card.svg")
    with open(sp, "w", encoding="utf-8") as f:
        f.write(svg())
    pp = os.path.join(OUT, "camscan-wallet-card.png")
    cv2.imwrite(pp, png())
    print("wrote", sp)
    print("wrote", pp)


if __name__ == "__main__":
    main()
