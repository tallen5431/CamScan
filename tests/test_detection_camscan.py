import os
import sys
import pathlib
import cv2

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from detect_squares import detect_dark_squares, draw_squares

def find_sample():
    up = ROOT / 'uploads'
    if not up.exists():
        print('No uploads/ dir. Put a sample image there named sample.jpg')
        return None
    for p in up.iterdir():
        if p.is_file() and p.suffix.lower() in ('.jpg','.jpeg','.png','.bmp','.tif','.tiff','.webp'):
            return str(p)
    return None

def main():
    sample = find_sample()
    if not sample:
        print('No sample image found in uploads/. Place a failing image named sample.jpg and re-run')
        return
    img = cv2.imread(sample)
    if img is None:
        print('Failed to read', sample)
        return
    dets = detect_dark_squares(img, debug=True)
    print('Candidates found:', len(dets))
    overlay = draw_squares(img, dets)
    outp = os.path.join(os.path.dirname(sample), 'debug.camscan.overlay.png')
    cv2.imwrite(outp, overlay)
    print('Wrote overlay to', outp)

if __name__ == '__main__':
    main()
