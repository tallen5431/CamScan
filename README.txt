
CamScan — Calibration Exporter (Dash)

Measure real-world objects in a photo using a printed calibration square as the
size reference, then export the measurements for CAD / 3D modelling.

--------------------------------------------------------------------------------
QUICK START
--------------------------------------------------------------------------------
1) pip install -r requirements.txt
   - or run Start.sh (Linux/Mac) / Start.bat (Windows) to create a venv + launch
2) python app.py
3) Open http://127.0.0.1:8059  (also reachable on your LAN at http://<host-ip>:8059)
4) Tap "snap a photo" (or drop an image) that contains your calibration square.

--------------------------------------------------------------------------------
CALIBRATION SQUARE
--------------------------------------------------------------------------------
The calibration target is a solid black square (optionally with 4 white inner
pads for easier detection). CamScan detects the black square's outer edge and
uses its known real edge length to convert pixels to millimetres.

IMPORTANT — set your real square size:
  Open "More Settings & Options" → Calibration → "Calibration square size (mm)"
  and enter the printed edge length of YOUR square. Every measurement rescales
  instantly (no re-processing). The default assumes a 40 mm square (the recommended marker).

  You can also set the CALIB_EDGE_MM environment variable to change the default
  used at upload time, e.g.  CALIB_EDGE_MM=47.5 python app.py

Deployment / safety environment variables (all optional):
  MAX_CONTENT_LENGTH_BYTES  max REQUEST BODY size in bytes (default 32 MiB). Larger than
                            the 8 MB photo limit shown in the UI because uploads arrive
                            base64-encoded (~1.37x) and a multi-view submission carries a
                            reloadable snapshot per view.
  MAX_IMAGE_PIXELS          reject images that DECODE larger than this (default
                            50,000,000 px) — guards against decompression bombs
  MAX_UPLOAD_FILES          cap on retained UPLOADS in uploads/ — a photo and its
                            .calibration.json count as one (default 400; 0 = off)
  MAX_UPLOAD_AGE_HOURS      drop uploads older than this (default 72)
  MAX_SUBMISSIONS_BYTES     refuse NEW part submissions once submissions/ reaches this size
                            (default 2 GiB; 0 = no cap). Submissions are business records
                            and are never auto-deleted — the cap only stops an unbounded
                            /api/submit from filling the volume. Archive submissions/ (or
                            raise this) if customers start seeing "out of room".
  CAMSCAN_DEBUG_IMAGES=1    write annotated detection debug images to ./debug_out
                            (OFF by default; never writes into the served uploads dir)

No square detected? Use the "Set Scale" tool (📐): draw a line across anything of
known length and type in that length — CamScan calibrates from it.

--------------------------------------------------------------------------------
ACCURACY — WHERE THE ERROR ACTUALLY COMES FROM
--------------------------------------------------------------------------------
The single biggest error in CamScan is not camera angle. It is measuring a face that
is not in the SAME PLANE as the card.

The calibration square gives a perspective correction for the card's own plane. Any
feature above that plane — the top face of a part with thickness — is magnified by
the camera, and reads too LARGE. Measured against a virtual camera with a known pose
(tests/synthetic_camera.py), at a typical 400 mm working distance:

    face height above the card :    0 mm    5 mm   10 mm   20 mm   40 mm
    measurement reads          :   0.0%   +1.3%   +2.6%   +5.3%  +11.1%

Two things worth knowing about that:

  * It does NOT go away in a perfectly square-on photo. The same +11.1% is there
    whether you shoot straight down or at 30 degrees.
  * Camera tilt, by contrast, is already handled well: a length lying IN the card's
    plane measures within about 1% out to 40 degrees of tilt.

THE FIX (free, and exact): rest the card ON the face you are measuring, so the card
and the feature are in the same plane. For a part 20 mm thick, that is the difference
between a 5 mm error on a 100 mm dimension and no error at all.

If the photo is already taken, open "Set Scale" (📐) → "Measuring a raised face?" and
enter how far above the card the face sits. CamScan re-derives the calibration for
that plane. This is a correction, not a measurement — it is most accurate when the
photo carries lens data (EXIF), and is an estimate otherwise; the panel says which.

Also worth doing: shoot from further back. The error scales as height/distance, so a
20 mm-thick part reads +8.7% at 250 mm and +3.4% at 600 mm. Just keep the card large
enough in frame to be detected.

--------------------------------------------------------------------------------
MEASURING
--------------------------------------------------------------------------------
Tools (toolbar):
  Pan / Select / Set Scale / Measure (distance) / Path (perimeter) /
  Area (rectangle) / Angle / Circle (2-point) / 3-Point circle / Note
  - Path: click points, then double-click or press Enter (or the ✓ button) to finish.
  - Circle: drag the centre to move, drag the ring to resize.
Keyboard: 0 Pan · 1 Select · 2 Measure · 3 Path · 4 Area · 5 Angle · 6 Circle ·
  7 3-Point · 8 Note · Enter finish path · Esc cancel · +/- zoom · Space to pan ·
  Ctrl+Z undo · Ctrl+Shift+Z redo · Delete remove selected · C copy selected value.

--------------------------------------------------------------------------------
EXPORT (for models)
--------------------------------------------------------------------------------
Download menu:
  PNG   annotated image
  JSON  full calibration + measurement data (incl. the perspective homography)
  CSV   spreadsheet of every measurement (mm + selected unit)
  DXF   CAD geometry (mm, Y-axis oriented like the photo) for AutoCAD / FreeCAD /
        Fusion 360 / etc. Requires the ezdxf package. Rectangles/areas export as
        CLOSED (directly extrudable) profiles; every measurement's value is written
        as text; angles and notes are included; entities are split onto colored
        layers (CIRCLES / LINES / RECTANGLES / POLYLINES / ANGLES / NOTES / DIMTEXT).
  SVG   vector geometry in real millimetres — imports into Illustrator / Inkscape /
        Fusion and most laser cutters. Generated in-browser (no ezdxf needed).

--------------------------------------------------------------------------------
ARTIFACTS in /uploads
--------------------------------------------------------------------------------
  <name>.jpg               original upload
  <name>.calibration.json  calibration data for the viewer

--------------------------------------------------------------------------------
WEBSITE INTEGRATION, SUBMISSIONS & MAILED MARKER
--------------------------------------------------------------------------------
See INTEGRATION.md for deploying CamScan into a website, configuring the
"Send to Datum" email submission (SUBMIT_TO / SMTP_* env vars), and the
ready-to-print calibration marker in markers/ (auto-detected by CamScan).
Generate a marker at any size with:  python tools/make_marker.py <edge_mm>
