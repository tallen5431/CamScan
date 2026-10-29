# CamScan — developer notes

For using CamScan see `README.txt`; for hosting and the Datum Labs site, `INTEGRATION.md`.
This file is the map for changing it.

## How it fits together

A Dash/Flask server (`app.py`) serves one page. The page is plain JavaScript modules in
`assets/` (loaded in a fixed order by `app.py`) drawing on a `<canvas>`; the heavy lifting —
detection, tracing, squaring up, building solids — is Python behind small JSON endpoints.

```
photo ─► upload (Dash callback) ─► calibration_core.calibrate_image ─► marker scale, if any
      ─► canvas overlay (calibrationOverlay.js): scale, outline, tidy, measure
             │  /api/trace ──────────► auto_outline.py      (GrabCut, holes, shadow checks)
             │  /api/outline/* ──────► outline_cleanup.py   (Straighten, Refine, Symmetric)
             │  /api/square_up ──────► square_up.py + ruler_check.py
             │  /api/plane ──────────► camera_geometry.py   (raised / sunken face)
      ─► 🧩 views (calib.part3d.js) ─► /api/export/part ─► part_builder.py ─► solid_export.py
                                                                          └─► cad_export.py (STEP/OBJ)
      ─► job store (calib.job.js, IndexedDB) ─► /api/submit (or postMessage when embedded)
```

## Python modules

| File | What it does |
|------|--------------|
| `app.py` | Flask + Dash app: upload callback, landing page, every endpoint, security headers, upload/submission housekeeping. |
| `calibration_core.py` | Finds the printed calibration square and turns it into a scale and a perspective homography; confidence rules. |
| `detect_squares.py`, `edge_finder.py`, `detect_paper.py` | Candidate squares, sub-pixel edges/corners of the marker, sheet-of-paper detection. `EDGE_FINDER_CONFIG` in `edge_finder.py` holds the marker-edge tunables. |
| `camera_geometry.py` | Focal length (EXIF, else assumed, always from the image's long side), working distance, pose, parallax error, homographies for a plane above/below the reference. |
| `auto_outline.py` | One-tap tracing: coarse-to-fine GrabCut seeded by the tap, Area box and "not the part" taps; holes (round ones snap to circles); runaway, background and shadow-jog checks returned as `hint` / `warnings`. |
| `outline_cleanup.py` | Straighten (fit sides, snap to the photo's edges), Refine (fit corner radii), Symmetric. |
| `square_up.py` | Perspective from two edges parallel on the part (the ruler's edges, else the part's long sides). |
| `ruler_check.py` | Finds the ruler's edges along the scale line, to sub-pixel, with an error estimate; one-edge recovery; the ruler/taper report. |
| `part_builder.py` | The multi-view 3D part: orient and deskew views, fit their sizes (rescale / stretch / known sizes / chosen view), intersect prisms (manifold3d), hollow a cover, typed ends and cross-section, cavity, print orientation, warnings. |
| `solid_export.py` | Single-outline extrusions and covers, mesh checks (`mesh_report`), `manifold_tris` (weld + sliver repair), binary STL. |
| `cad_export.py` | STEP (AP214 B-rep, one face per flat face) and OBJ writers. |
| `circle_detection.py` | `detect_circles_hough`, `detect_circles_contour` (circularity metrics) and `export_to_dxf(geometry, filename, mm_per_px, image_height_px)` used by `/api/export/dxf`. Circle auto-detection is not wired into the page; the page's circles are drawn by the user. |
| `tools/make_marker.py` | Generates the printable marker (`markers/`). |

## Endpoints

All take and return JSON unless noted; bad input is a 4xx with a reason, never a 500.

| Route | Purpose |
|-------|---------|
| `POST /api/trace` | Trace the part at a tap (Area box, fg/bg taps, exclude boxes). |
| `POST /api/outline/straighten` | `mode`: `straighten` or `refine`. |
| `POST /api/outline/symmetric` | Average an outline with its mirror image. |
| `POST /api/square_up` | Perspective from the ruler's edges (crop sent as `ruler_image`) or the part's sides. |
| `POST /api/plane` | Homography for a face above/below the marker plane. |
| `POST /api/export/part` | Build the multi-view part; `preview: true` for the dialog, else `format`: `stl` / `step` / `obj` (file response). |
| `POST /api/export/stl` | A single extruded outline (or cover) as STL. |
| `POST /api/export/dxf` | DXF of the measurements (needs ezdxf). |
| `POST /api/submit` | Save (and e-mail) a multi-view submission. |
| `GET /uploads/<name>` | The uploaded photos and their `.calibration.json`. |

## JavaScript modules (`assets/`)

| File | What it does |
|------|--------------|
| `calibrationOverlay.js` | The overlay: modes, scale (line / rectangle / marker), tracing requests, Tidy actions, Square up, undo/redo, toasts, restore state. |
| `calib.ui.enhanced.js` | Toolbar, tool rail, context buttons (Tidy ▾, ⋯ More), Set-scale chooser, coach, Settings sheet. |
| `calib.part3d.js` | 🧩 view assignment, the Build 3D part dialog, its 3D canvas and the request it sends. |
| `calib.job.js` | The job: photos + 3D views in IndexedDB (localStorage fallback), autosave, side switcher, Save/Load job, submission. |
| `calib.simple.js` | Simple mode's guided card. |
| `calib.export.js` | PNG, JSON, CSV, DXF request, SVG. |
| `calib.viewport.js`, `calib.gestures.js` | Image↔canvas mapping and zoom; pointer/pinch gestures. |
| `calib.annotations.js`, `calib.measure.js`, `calib.geometry.js`, `calib.units.js` | Annotation store and hit tests; perspective-aware lengths/areas; geometry helpers (homographies); units. |
| `calib.draw.js`, `calib.circles.js` | Drawing helpers and label placement; circle fitting (`fitCircleFromPoints`), drawing and JSON. |

`window.CalibJob`, `window.CalibPart3D`, `window.CalibToast` and each `.cal-view`'s
`__overlay` are the seams between modules (and what the browser tests drive).

## Tests and CI

```
pip install -r requirements.txt -r requirements-dev.txt
python tests/smoke_test.py            # broad, fast: geometry, endpoints, exports, rules
python tests/unit_<topic>.py          # focused checks, one script each
python tests/test_<topic>_e2e.py      # real browser (Playwright + Chromium)
```

- Every new check prints `[PASS]/[FAIL] <name> — <detail>`; unit scripts end with
  `UNIT_RESULT: PASS|FAIL`, browser suites with `E2E_RESULT: PASS|FAIL|SKIP`.
- Browser suites start their own server on `CAMSCAN_E2E_PORT` (each has a default; give
  each a different port to run them side by side) and SKIP without a browser unless
  `CAMSCAN_E2E_REQUIRE=1`, which CI sets.
- Tests use synthetic images. Never commit customer photos.
- CI (`.github/workflows/ci.yml`): flake8 `--select=F`, `node --check` on every asset,
  `tests/check_line_endings.py`, the smoke suite, every `tests/unit_*.py`, every
  `tests/test_*_e2e.py`.

## Conventions

- **Line endings.** The repo mixes CRLF and LF with no `.gitattributes`.
  `calibrationOverlay.js`, `calib.draw.js`, `calib.circles.js`, `calib.viewport.js`,
  `calib.gestures.js` and `calib.annotations.js` are CRLF. Edit them in a way that keeps CRLF
  (a text-mode rewrite converts the file and buries the change);
  `tests/check_line_endings.py <base>` fails a change that flips a file's endings.
- **Comments say why**, usually with the measurement that motivated the code ("on the
  customer's step-pad photo, …"). Keep that style; it is how the next change avoids
  undoing a fix.
- **Untrusted input everywhere.** Every endpoint validates shapes, finiteness and sizes and
  bounds the work a request can cause (see the smoke checks for the limits).
- **Units.** Server geometry is in mm; overlay coordinates are image pixels of the photo as
  loaded (a reopened job's photo is downscaled, with its scale and annotations scaled by the
  same factor).

## Debugging

| Variable | Effect |
|----------|--------|
| `CAMSCAN_DEBUG=1` | Verbose marker-edge logging (`edge_finder.py`). |
| `CAMSCAN_DEBUG_IMAGES=1` | Annotated detection images in `./debug_out`. |
| `CALIB_EDGE_MM` | Default printed marker size at upload. |

Deployment limits (`MAX_CONTENT_LENGTH_BYTES`, `MAX_IMAGE_PIXELS`, `MAX_UPLOAD_*`,
`MAX_SUBMISSIONS_BYTES`, `FRAME_ANCESTORS`, `SUBMIT_TO`, `SMTP_*`) are in `README.txt` and
`INTEGRATION.md`.
