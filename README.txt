
CamScan — photos to a 3D part (STL / STEP / OBJ)

Turn two or three phone photos of a part, each with a ruler lying on it, into a 3D model:
STL to print, STEP to edit in Fusion 360 or any CAD program. It measures in real
millimetres along the way, and exports 2D drawings (DXF / SVG) too.

--------------------------------------------------------------------------------
QUICK START: PHOTOS TO 3D PART
--------------------------------------------------------------------------------
1) Per view (top, side, end): lay a ruler FLAT ON that face; shoot square-on, ~0.5 m back.
2) Set scale (📐): a line down the ruler's middle between two numbered marks; type the length.
3) ✨ Trace outline and tap the part. Missed it (a shadowed end)? Tap "▭ Draw a box" in
   the message and drag a box round the part: it traces as soon as you let go.
4) ✨ Tidy: Straighten or Refine the sides; Square up if the photo was taken at an angle.
5) 🧩 Use in 3D part → Top, Side or End. Repeat 1-5 for each photo.
6) 🧩 Build 3D part: type the height (if no Side/End view) and wall; download STL or STEP.

  * HOLLOW COVERS whose outline is the BOTTOM edge (a step-pad cover seen from above shows
    its lower outer edge, not its top face): the scale holds only in the ruler's plane, so
    put the ruler level with that bottom edge (on the ground or a block beside it). Already
    shot with the ruler on the top face? 📐 → "Measuring a raised face?" and enter the part's
    height as a NEGATIVE number (-45 for an edge 45 mm below the ruler). Worked through for
    a customer's cover photo: 45 mm below the ruler reads every top-view size 7.4% small.
  * Square up uses the ruler's own edges, found along your scale line — another reason to
    draw that line down the ruler's middle, between two numbered marks far apart. See
    SQUARE UP under REFERENCE.
  * Downloads: STL comes out plate-down, ready to print; STEP and OBJ come as traced, the
    way the part sits on the vehicle, for editing in CAD.

--------------------------------------------------------------------------------
RUNNING IT
--------------------------------------------------------------------------------
1) pip install -r requirements.txt
   - or run Start.sh (Linux/Mac) / Start.bat (Windows) to create a venv + launch
2) python app.py
3) Open http://127.0.0.1:8059  (also reachable on your LAN at http://<host-ip>:8059)
4) Tap "take a photo" (or drop an image) of the part with a ruler on it. A printed
   calibration square works too and scales automatically (see REFERENCE).

--------------------------------------------------------------------------------
3D MODEL FOR PRINTING (STL)
--------------------------------------------------------------------------------
Most replacement trim is an EXTRUSION — a running board, a moulding, a sill, a
bracket, a gasket, a spacer. For those, one traced cross-section plus one depth is
the whole part, so CamScan can finish the job rather than hand you a drawing to
re-draw in CAD.

  1) Trace a CLOSED outline around the part (the Path/Outline tool, or ✨ Auto-trace).
     Auto-trace also finds interior holes — a bolt pattern, a row of clip slots —
     and they become real through-holes in the solid.
     The clean-up tools are under "✨ Tidy" (with an outline selected): Straighten, Refine,
     Symmetric and Square up.
     "⇋ Symmetric" averages the outline with its own mirror image — for a part that is
     symmetric but traced lopsided (light on one side, shadow on the other). It picks the axis
     its halves already agree about more; the message offers the other axis. It refuses an
     outline whose halves differ by more than a quarter.
     Sides that came out wobbly? Select the outline and press "📏 Straighten": each long
     straight side becomes one line on the part's edge in the photo, sides meet at clean
     corners, and rounded corners keep their dots. Undo puts the dots back. It straightens
     what the trace found — a stretch that spilled onto the background still needs a drag.
     "✨ Refine" does the same and then gives each corner the radius the photo shows — a
     straightened corner is sharp, a cover's usually is not — or keeps it sharp if that is
     what the photo shows. If the outline is still as traced (or as Straighten left it), Refine
     first re-traces it at full detail with the same Area box and taps, so gentle curves the
     first trace smoothed away come back (a few seconds). Use either; both undo in one step.
     Photo taken at an angle? With the scale set from a line along your ruler, select the
     outline and press "⊞ Square up". It corrects every measurement and export for the
     camera's tilt, from how the ruler's two edges narrow in the photo. When it cannot find
     them it says why, uses the part's sides instead, and offers "Redraw scale line". How it
     works and what the message tells you: SQUARE UP under REFERENCE.
  2) Select the outline and press "⇥ Extrude" (on a phone it is under "⋯"). Enter how far
     the shape runs: for a top outline that is the part's HEIGHT; for a cross-section (an end
     or side outline), the part's length. For a COVER, also fill in the wall thickness (see
     below).
  3) 💾 menu → "Save STL (3D print)".

COVERS (hollow parts). Most replacement covers — a running-board step pad, a trim cap,
an end cover — are a thin plate with a wall round its edge, open underneath. Enter a wall
thickness in the ⇥ Extrude box and the STL comes out that way instead of as a solid block:

    900 x 180 mm step pad, 25 mm deep    solid: 4.05 L (~5.0 kg PLA)
                                         3 mm wall: 0.63 L (~0.78 kg PLA)

  * The plate and the wall are both that thick; the outer surface is your traced outline
    exactly. The wall holds its thickness to within 2% (about 0.05 mm on a 3 mm wall).
  * It comes out PLATE-DOWN, open side up — the way a cover prints without supports. It is
    turned over by rotation, so it is never mirrored into the opposite-hand part.
  * A hole over the hollow pierces the plate. A hole near the wall gets a solid boss round
    it and runs the full depth, as a designer would draw it. The save message says so.
  * Where the part is narrower than two walls there is no room for a hollow, so that part
    of it is solid — and if that is all of it, you get the solid and a note saying why.
  * Walls under 0.4 mm (one nozzle) are refused.

The result is a watertight mesh: every edge closed, every triangle wound consistently with
its neighbours, normals facing out, volume equal to what the profile says it should be.
Those are checked, not assumed — if a closed
solid cannot be built the export refuses and says why, because a slicer either
rejects a leaky mesh or, worse, prints it wrong without telling you.

PARTS THAT CHANGE SHAPE (several views). A part whose shape is not the same all the way
along — a chamfered or rounded toe, a tapered end, an inside that has to fit over something —
is built from several outlines instead of one, each traced on whichever photo shows it best:

  1) Trace an outline, select it, press "🧩 Use in 3D part" and say what it is:
       Top     outline from above            (length x width)
       Side    profile from the side         (length x height)  — gives the toe/taper
       End     cross-section from the end    (width x height)   — gives the rounded edges
       Cavity  the inside: what the part fits over, traced in any of the views
     Do this across as many photos as you need; the views are kept with the job. The
     picker marks a view that is already set (and from which photo) — choosing it replaces it.
  2) Press "🧩 Build 3D part" — in the top bar once any view is set, or on the message that
     confirms each view (also in the 💾 menu). The dialog shows the part in 3D (drag to turn,
     scroll or pinch to zoom; Iso / Top / Side / End / Under), and below it draws Top, Side
     and End like an engineering drawing: blue lines are your outlines, the filled shape is the part. If a
     view is turned or reversed, use ⟲ / ⇋ / ⇵ on its row until they line up.
     Starting a different part? The first page shows "Part in progress" with its photos and
     views — press "Start a new part" there (or "💾 Save job" first to keep a copy), or the
     new photos join the old part.
  3) Set the fit clearance for the cavity (0.4 mm default), where it runs (all the way
     through, or in from one end by a depth), and download:
       STL                 for the printer.
       STEP (edit in CAD)  a real solid: Fusion 360 / any CAD program opens it as a body with
                           one face per flat side, ready to press-pull, fillet or shell. An
                           STL opens as a mesh, and converting that makes one face per
                           triangle, which is hard to change.
       OBJ                 a mesh with one polygon per flat side, for programs that only take
                           meshes (in Fusion: Insert Mesh, then Convert Mesh).
     STEP and OBJ come as traced (the way the part sits); STL comes plate-down for printing.
  HOLLOW: put a wall thickness in "Hollow — wall (mm)" and the part comes out as a cover of
  that wall, open underneath, turned plate-down for printing — with Top alone or with Side
  and End views too. Leave it empty for a solid part.
  NO GOOD SIDE OR END PHOTO? A photo of the end taken at an angle traces as a skewed, wavy
  profile, and the cover built from it barely touches the print bed (the status says so).
  Remove that view (✕ on its row) and type the shape instead: the height, "End slope" and
  "End edge radius" for the ends (no Side view), and "Long edge radius" and "Side draft" for
  the cross-section (no End view). The Top photo alone then gives a clean, printable cover.
  Each view follows the outline it was assigned from: Refine, Square up, a dragged dot or a
  new scale on that photo carries through to the part without assigning it again (the row
  says "follows its outline"). If the outline is deleted, the view keeps its last copy and
  the row says so.
  Assigned an outline to the wrong view? Use "Move to…" on its row (swaps with whatever held
  that view) — no need to go back to the photo.
  A Side profile of only ONE END (a toe photographed up close): on the Side row choose
  "covers only the left/right end". It shapes that end; the rest of the length carries on at
  the height of its cut edge. The Top view sets the full length.

How it works: each outline is pushed straight through the part and only what every view
agrees is material is kept, then the cavity — grown by the clearance — is cut out. Things to
know:
  * A view photographed a degree or two off square is turned square first (up to 15°); the
    dialog says by how much.
  * The Top view sets length and width; the Side (else End) view sets height. Each size line
    in the dialog says which view set it. Another view that disagrees by up to 8% is fitted
    to it — rescaled as a whole where it can be, else stretched on that one size — and past
    3% the line warns and offers "Use <view>'s width" or "Match" buttons that say what else
    they move. More than 8% is refused, naming both views: that is a wrong scale, or a photo
    taken at an angle.
  * Measured the real part with a tape? Type it under "Known overall length / width /
    height": the views are scaled to it, and each view's error against it is shown.
  * A cavity traced on the SAME photo as its view keeps exactly where it was drawn. One from
    another photo (say, the bare board end) is centred and sat at the bottom, centre or top.
  * Walls under 0.8 mm are flagged as too thin to print reliably.
  * MIRROR makes the opposite-hand part: measure the intact cover from one side of the
    vehicle and download the missing one from the other. Mirroring is the only thing that
    changes a part's hand — "Upside down for printing" is a rotation. (For a hollow cover,
    which already downloads plate-down, that box reads "Turn over: open side down".)
  * The status says "ready to print" only when the cover's deck really sits flat on the bed,
    and flags a part larger than your print bed (set its size in the dialog) with where to
    cut it.
  * Needs the manifold3d package (in requirements.txt).

What this does NOT do: lettering, textures and raised ribs are left to CAD, and a part whose
cross-section varies in ways no single view shows (a twist, say) needs more than outlines.

What the single-outline STL does NOT do: it sweeps one profile along a straight line. A part that
tapers, twists, or has a different shape at each end (end caps, for instance) needs
those modelled separately. Measure them from a second view and add them in CAD.

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
    plane measures within about 1% at 20 degrees of tilt and 2% at 40.

THE FIX (free, and exact): rest the card ON the face you are measuring, so the card
and the feature are in the same plane. For a part 20 mm thick, that is the difference
between a 5 mm error on a 100 mm dimension and no error at all.

If the photo is already taken, open "Set Scale" (📐) → "Measuring a raised face?" and
enter how far above the card (or ruler) the face sits — NEGATIVE for a face below it,
such as a cover's bottom edge traced from above with the ruler on its top face. CamScan
re-derives the scale for that plane: from the square's own geometry, or, for a ruler
line, from the working distance (focal length x mm per px). This is a correction, not a
measurement — it is most accurate when the photo carries lens data (EXIF), and is an
estimate otherwise; the message says which. It does not apply on top of Square up or a
paper rectangle: set the scale from the ruler line, correct for the plane, and measure.
(Square up over a corrected line drops the correction and says so; Undo brings it back.)
The correction is kept with the photo, and drawing a new scale line clears it.

Also worth doing: shoot from further back. The error scales as height/distance, so a
20 mm-thick part reads +8.7% at 250 mm and +3.4% at 600 mm. Just keep the card large
enough in frame to be detected.

--------------------------------------------------------------------------------
SHOOTING A PART CAMSCAN CAN ACTUALLY MEASURE
--------------------------------------------------------------------------------
Worth sending to whoever is holding the camera. The same list is in the app, under
"Suggested shots" -> "What makes a photo measurable". Every number below is measured
(see ACCURACY above and tests/smoke_test.py), not a rule of thumb.

 1. PUT THE REFERENCE ON THE FACE YOU ARE MEASURING.
    The biggest error, and free to avoid. A face raised above the card is magnified:
    +2.6% at 10 mm up, +5.3% at 20 mm, +11.1% at 40 mm. It does NOT go away in a
    square-on photo. Rest the card on that face and the error is zero.
    "The face you are measuring" is the one the OUTLINE follows. A hollow cover seen from
    above traces along its bottom edge, so the ruler goes level with that edge, not on top.

 2. USE A REFERENCE AT LEAST A FIFTH AS LONG AS THE PART.
    Error multiplies by how far you extrapolate. A 40 mm card on a 1.5 m running board
    is 1/38th of the span, so a pixel of error on the card becomes centimetres on the
    board. A steel rule, a sheet of A4, a tape measure — anything big and rigid.

 3. A RECTANGLE BEATS A LINE.
    Four corners of something of known size let CamScan undo the camera angle: a length
    in that plane then measures within about 1% at 20 degrees of tilt and 2% at 40. A two-point
    line cannot — it scales uniformly, so at 40 degrees the same photo reads -22% across
    the part and -50% along it.

 4. STAND BACK AND ZOOM IN, RATHER THAN MOVING IN CLOSE.
    Depth error scales as height/distance: a 20 mm-thick part reads +8.7% at 250 mm and
    +3.4% at 600 mm. Just keep the reference big enough in frame to be found.

 5. GET THE WHOLE PART IN FRAME, WITH BACKGROUND AROUND IT.
    The tracer needs to see where the part ends. A part photographed against something
    its own colour — a dark part lying on its own shadow — needs the "✖ Not the part"
    tap to separate the two; no brightness threshold can (measured on a real photo: part
    71 grey, its shadow 57, the concrete 169).
    A shadow falling ACROSS the part (yours, holding the phone) is the other common miss:
    the shadowed end reads as background. Draw an Area box snugly round the part, then
    tap the part: everything outside the box is taken as "not the part", and the shadowed
    end comes back (a real step-pad photo: 60% of the cover traced without the box, 92%
    with it). Keep the box snug — a box reaching into a dark gap beside the part lets the
    trace reach into it too. A failed trace offers "▭ Draw a box": drag it and the trace
    runs inside it at once.
    When the trace comes back with a side standing out over part of its length (a shadow
    beside it), the message says "check it", names the side and end, and offers
    Straighten — the part would otherwise be built that much too wide (on a real step-pad
    photo: 175 mm for a cover of about 157).

 6. SHOOT THE SAME PART FROM MORE THAN ONE SIDE.
    One photo gives an outline; thickness and profile come from the second and third.
    Measuring one dimension in two views also lets CamScan check the scales against each
    other — the only way a wrong scale ever shows itself. When two views disagree about a
    dimension by more than 10%, the job panel says so.

--------------------------------------------------------------------------------
MEASURING
--------------------------------------------------------------------------------
Tools (toolbar):
  Pan / Select / Set Scale / Measure (distance) / Path (perimeter) / Outline (closed part
  outline) / Area (rectangle) / Angle / Circle (2-point) / 3-Point circle / Note
  - Path: click points, then double-click or press Enter (or the ✓ button) to finish.
  - Outline: tap round the part, then tap the first point (or double-tap) to close it — a
    to-scale profile for the 3D part. "✨ Auto-trace" traces it from one tap on the part.
  - Circle (centre + edge) and 3-Point circle (three taps on a round edge): drag the centre
    to move, the ring to resize; the label gives the diameter. Auto-trace also snaps a round
    hole it finds to a true circle. Circles go to the DXF on their own CIRCLES layer.
Keyboard: 0 Pan · 1 Select · 2 Measure · 3 Path · 4 Area · 5 Angle · 6 Circle ·
  7 3-Point · 8 Note · 9 Outline · Enter finish path · Esc cancel · +/- zoom · Space to pan ·
  Ctrl+Z undo · Ctrl+Shift+Z redo · Delete remove selected · C copy selected value.
⚙️ Settings (top bar): calibration square size, units, snap to marker corners, default
  note text, show markers / grid / measurement labels (turn labels off to edit a dense
  outline), annotation text size, line thickness, point size, and Clear all annotations.

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
  STL   the selected outline extruded (see 3D MODEL above); "Build 3D part from views…"
        opens the multi-view dialog, which downloads STL / STEP / OBJ.

SAVING THE WHOLE PART (job panel, 📋 Submit to Datum in the top bar)
  💾 Save job writes every photo — image, scale and outlines — and the 3D part (which
  outline is the Top / Side / End / Cavity, its alignment, and the Build settings: wall,
  sizes, ends) to a .camscan.json. ⤓ Load job opens it again later, on any device: each side
  editable, and 🧩 Build 3D part ready with the same views. A submission carries the part
  too. (A job file saved before this carries only the photos: assign the views again.)

--------------------------------------------------------------------------------
TESTING
--------------------------------------------------------------------------------
  pip install -r requirements.txt -r requirements-dev.txt
  python -m playwright install chromium        # once; skip if a Chromium is provisioned

  python tests/smoke_test.py                   # fast: geometry, endpoints, exports, rules
  python tests/unit_p1_part_builder.py         # focused checks: one per tests/unit_*.py
  python tests/test_newtools_e2e.py            # real browser: one per tests/test_*_e2e.py
                                               # (CAMSCAN_E2E_PORT picks the port)

The browser suites report SKIP (and exit 0) where no browser is available, so they never
break a machine that cannot run one. Set CAMSCAN_E2E_REQUIRE=1 to make a skip a failure —
CI does, so the browser layer can never pass by not running.

CI (.github/workflows/ci.yml) runs on every push and pull request: lint (flake8 --select=F),
JavaScript syntax, tests/check_line_endings.py, the smoke suite, every tests/unit_*.py and
every browser suite. Code map, conventions and the internal APIs: docs/DEVELOPER.md.
That last one exists because this repository mixes CRLF and LF files with no .gitattributes:
a text-mode edit silently converts a CRLF file, and the real change disappears under
thousands of lines of line-ending churn. It fails a change that flips a file's endings.

--------------------------------------------------------------------------------
REFERENCE
--------------------------------------------------------------------------------

SQUARE UP
Photo taken at an angle? With the scale set from a line along your ruler, select the
outline and press "⊞ Square up". A ruler is a rectangle: its two edges are parallel, so
how they narrow in the photo gives the camera's tilt, and every measurement and export
is corrected for it. CamScan finds the ruler's edges itself, along the scale line (draw
it down the middle of the ruler, lying flat on the part's face). The message then says:
  - the tilt it corrected;
  - how wide the ruler reads — compare it with your ruler: if they match, the
    correction is right;
  - whether the part's own long sides come out parallel. If not, it gives the width at
    each end, ± how sure it is: the part tapers, or its side walls show at one end.
    The outline keeps that taper.
Only ONE edge clear (the other runs beside a groove or a shadow)? It finds the other
again from the clear one, a ruler's width away, keeping only what steps from ruler to
background. If that still fails it assumes the second edge parallel and says so: that
lines the photo up but cannot see a tilt along the ruler, so the part's taper is reported
unchecked. No ruler edges along the line at all (a tape, a printed scale)? It uses the
part's two long sides instead — that assumes the part is a true rectangle — and the
message says why the ruler was not used, offers "Redraw scale line" (down the ruler's
middle, between two numbered marks far apart), and shows "Use the ruler" greyed out. When both work, it offers to
switch between the two. Measured on a customer's step-pad photo, the cover's sides
narrowed about twice as much as the ruler lying on it: squaring up from the cover would
have over-corrected and hidden a ~3.6 mm taper.
Exact for tilt along the part; within ~1-2% with a few degrees sideways as well. It uses
the photo's focal length when the original photo carries it. A photo sent through a chat
app has lost it: the upload message flags that, and Square up's message gives the tip.
A rectangle of known size on the face (Set scale) is exact in every case.

CALIBRATION SQUARE
The calibration target is a solid black square (optionally with 4 white inner
pads for easier detection). CamScan detects the black square's outer edge and
uses its known real edge length to convert pixels to millimetres.

IMPORTANT — set your real square size:
  Open ⚙️ Settings → 📐 Calibration → "Calibration square size (mm)"
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
ARTIFACTS in /uploads
--------------------------------------------------------------------------------
  <name>.jpg               original upload
  <name>.calibration.json  calibration data for the viewer

--------------------------------------------------------------------------------
WEBSITE INTEGRATION, SUBMISSIONS & MAILED MARKER
--------------------------------------------------------------------------------
See INTEGRATION.md for deploying CamScan into a website (and running it as a public
service), configuring the "Submit to Datum" email submission (SUBMIT_TO / SMTP_* env
vars), and the ready-to-print calibration marker in markers/ (auto-detected by CamScan).
Generate a marker at any size with:  python tools/make_marker.py <edge_mm>

--------------------------------------------------------------------------------
DOCUMENTS
--------------------------------------------------------------------------------
  README.txt          this file: using CamScan, accuracy, exports, settings, testing
  INTEGRATION.md      hosting, embedding on the Datum Labs site, submissions, the marker
  docs/DEVELOPER.md   code map, endpoints, tests and CI, conventions, debugging, APIs
