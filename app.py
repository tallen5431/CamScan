# CamScan — Calibration Exporter (single-page viewer)
import os, base64, uuid, time, tempfile, math

# Cap the pixels OpenCV will decode BEFORE importing cv2 (the env var is read at import
# time). A tiny, highly-compressible upload can otherwise decode to hundreds of megapixels
# and OOM-kill the process — a classic decompression bomb. 50 MP comfortably covers real
# phone/DSLR photos while refusing pathological inputs. Override with MAX_IMAGE_PIXELS.
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", 50 * 1000 * 1000))
MAX_PROFILE_HOLES = 64
# Let OpenCV decode up to a small margin PAST our cap, so an image just over the limit still
# decodes far enough for _decode_b64_image's explicit pixel check to reject it with the clear
# "image is too large, resize it" message — instead of imdecode raising first and surfacing a
# generic "not a valid image". Anything egregiously larger is still refused by OpenCV, so the
# decompression-bomb guard holds (a 10% margin adds at most ~10% to a bounded allocation).
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(int(MAX_IMAGE_PIXELS * 1.1)))

from flask import Flask, send_from_directory, url_for
from dash import Dash, html, dcc, Input, Output, State, no_update
import cv2, numpy as np
from werkzeug.middleware.proxy_fix import ProxyFix  # NEW: respect X-Forwarded-* behind Caddy
from werkzeug.exceptions import RequestEntityTooLarge

from calibration_core import calibrate_image, save_outputs

APP_PORT = int(os.getenv("PORT", "8059"))
APP_HOST = os.getenv("HOST", "0.0.0.0")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")

# The overlay JS modules live in ./assets and are served + injected automatically by Dash.
# Dash prefixes their URLs correctly when running behind a reverse proxy with a path prefix
# (see ProxyFix below), so we do NOT hard-code "/assets/..." script tags. The modules are
# order-independent: each publishes a `window.Calib*` global and looks its dependencies up
# lazily, and calibrationOverlay.js waits for every dependency before booting.


# Allowed image extensions (lowercase, no leading dot)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}

# Maximum request-body size in bytes. This must comfortably exceed the largest RAW image we
# advertise (8 MB), because uploads arrive base64-encoded inside a Dash JSON callback body
# (~1.37x the raw bytes + envelope) — an 8 MB photo is ~11 MB on the wire. It must ALSO fit a
# multi-view "Submit to Datum" payload, which now carries a reloadable snapshot (raw image +
# calibration + annotations) per view. 32 MiB covers both with headroom while still bounding
# request size. Override with MAX_CONTENT_LENGTH_BYTES.
MAX_CONTENT_BYTES = int(os.getenv("MAX_CONTENT_LENGTH_BYTES", 32 * 1024 * 1024))

# Retention caps for the uploads/ dir. Every upload persists a .jpg + .calibration.json
# and nothing deleted them, so the dir grew without bound. Keep at most this many recent
# artifacts and drop anything older than the age cap. Set MAX_UPLOAD_FILES=0 to disable.
MAX_UPLOAD_FILES = int(os.getenv("MAX_UPLOAD_FILES", 400))
MAX_UPLOAD_AGE_HOURS = float(os.getenv("MAX_UPLOAD_AGE_HOURS", 72))
# Extensions this reaper is allowed to remove (upload artifacts only — never .tmp/.dxf
# in flight, never dot-prefixed atomic-write temp files).
_REAPABLE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".json")

# --- Part-submission ("Send to Datum") config ---------------------------------------
# Where multi-view part submissions are emailed. Set SUBMIT_TO for your deployment; the
# default is the address confirmed for Datum Laboratories. Email is sent via SMTP when
# SMTP_HOST is configured; otherwise submissions are still saved to SUBMISSIONS_DIR and
# the customer can download the summary — so the flow works before mail is wired up.
SUBMIT_TO = os.getenv("SUBMIT_TO", "thomas.allen@datumlaboratories.com")
SUBMIT_FROM = os.getenv("SUBMIT_FROM", "")          # falls back to SMTP_USER
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_TLS = os.getenv("SMTP_TLS", "1").strip().lower() in ("1", "true", "yes", "on")
SUBMISSIONS_DIR = os.path.join(os.path.dirname(__file__), "submissions")
# Bound what the unauthenticated /api/submit can write to disk. uploads/ has a reaper, but
# submissions are business records — a customer's part, photos and contact details — so they
# are NEVER auto-deleted. Instead, once the directory reaches this cap we refuse new
# submissions and say so, pointing the customer at "Download summary". That bounds disk
# without discarding anything someone already sent us, and without it a loop of submits fills
# the volume and takes the whole app down with it. Set MAX_SUBMISSIONS_BYTES=0 to disable.
MAX_SUBMISSIONS_BYTES = int(os.getenv("MAX_SUBMISSIONS_BYTES", 2 * 1024 * 1024 * 1024))

# --- Embedding: who may iframe CamScan ------------------------------------------------
# CamScan is embedded on the Datum Labs replacement-parts page via an <iframe>. A CSP
# 'frame-ancestors' directive both PERMITS that specific parent and blocks clickjacking
# from anywhere else (top-level use at measure.datumlaboratories.com is unaffected — the
# directive only governs framing). Override FRAME_ANCESTORS to add hosts, e.g. append
# "http://localhost:*" while testing the embed locally. Set it empty to send no header.
FRAME_ANCESTORS = os.getenv(
    "FRAME_ANCESTORS",
    "'self' https://datumlaboratories.com https://*.datumlaboratories.com",
).strip()


def _upload_stem(name):
    """The logical-upload key shared by a photo and its calibration JSON:
    'foo-ab12.jpg' and 'foo-ab12.calibration.json' both map to 'foo-ab12'."""
    low = name.lower()
    if low.endswith(".calibration.json"):
        return name[:-len(".calibration.json")]
    return os.path.splitext(name)[0]


def _reap_uploads(keep_paths=()):
    """Bound the uploads/ dir by count and age so disk can't grow without limit.
    Only removes finished upload artifacts; never the paths in keep_paths (the pair we
    just wrote) or in-flight temp files. Best-effort: any error is swallowed so reaping
    can never break an upload."""
    if MAX_UPLOAD_FILES <= 0 and MAX_UPLOAD_AGE_HOURS <= 0:
        return
    keep = {os.path.abspath(p) for p in keep_paths}
    try:
        entries = []
        for name in os.listdir(UPLOAD_DIR):
            if name.startswith("."):
                continue  # atomic-write temp files (.<name>.tmp)
            if not name.lower().endswith(_REAPABLE_SUFFIXES):
                continue
            path = os.path.join(UPLOAD_DIR, name)
            ap = os.path.abspath(path)
            if ap in keep or not os.path.isfile(path):
                continue
            try:
                entries.append((path, os.path.getmtime(path)))
            except OSError:
                continue
        # Group by logical upload: each upload writes a "<stem>.jpg" and its
        # "<stem>.calibration.json", so reap them together — never orphan one half of a pair
        # (which left the viewer's data-json pointing at a JSON whose image was already gone).
        # The count/age caps now bound UPLOADS, not individual files.
        groups = {}
        for path, mtime in entries:
            g = groups.setdefault(_upload_stem(os.path.basename(path)), {"paths": [], "mtime": 0.0})
            g["paths"].append(path)
            g["mtime"] = max(g["mtime"], mtime)   # the newer file dates the whole upload
        ordered = sorted(groups.values(), key=lambda g: g["mtime"], reverse=True)  # newest first

        now = time.time()
        age_limit = MAX_UPLOAD_AGE_HOURS * 3600 if MAX_UPLOAD_AGE_HOURS > 0 else None
        for idx, g in enumerate(ordered):
            too_many = MAX_UPLOAD_FILES > 0 and idx >= MAX_UPLOAD_FILES
            too_old = age_limit is not None and (now - g["mtime"]) > age_limit
            if too_many or too_old:
                for path in g["paths"]:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass
    except OSError:
        pass


def _resolve_edge_mm_from_env():
    v = os.getenv("CALIB_EDGE_MM")
    if not v:
        return None
    try:
        val = float(v)
    except ValueError:
        print(f"[App] ⚠️ Invalid CALIB_EDGE_MM='{v}', ignoring.")
        return None
    # A non-positive edge length would yield a zero/negative mm_per_px (a mirrored or
    # divide-by-zero scale) that is still reported high-confidence — reject it like the DXF
    # endpoint rejects a non-positive mm_per_px.
    if not (val > 0):
        print(f"[App] ⚠️ Non-positive CALIB_EDGE_MM='{v}', ignoring.")
        return None
    return val


def _json_body():
    """The request's JSON body as a dict — never raises on a non-object or invalid body.
    A bare number / array / string, or malformed JSON, all coerce to {} so an endpoint can
    .get() safely and return a clean 4xx instead of letting an AttributeError escape as a 500."""
    from flask import request
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


server = Flask(__name__)
# Enforce max upload size
server.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_BYTES

# NEW: make Flask/Dash respect X-Forwarded-Proto/Host/Port/Prefix from Caddy
server.wsgi_app = ProxyFix(
    server.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_port=1,
    x_prefix=1,
)

# Helpful error handler for oversized uploads
@server.errorhandler(RequestEntityTooLarge)
def handle_too_large(e):
    return "File too large (max bytes={})".format(MAX_CONTENT_BYTES), 413


# Allow the Datum Labs site to embed CamScan in an iframe, and only that site. Sent on
# every response (Dash pages, assets, /api/*) so the embed works no matter which URL the
# parent frames. frame-ancestors supersedes the legacy X-Frame-Options for browsers that
# support it; we send X-Frame-Options too only when framing is locked to same-origin.
@server.after_request
def _set_frame_policy(resp):
    if FRAME_ANCESTORS:
        resp.headers["Content-Security-Policy"] = "frame-ancestors " + FRAME_ANCESTORS
        # X-Frame-Options can't express an allow-list, so we omit it when a cross-origin
        # parent is permitted (a stray SAMEORIGIN here would defeat the embed in old browsers).
        if FRAME_ANCESTORS in ("'self'", "'none'"):
            resp.headers["X-Frame-Options"] = "DENY" if FRAME_ANCESTORS == "'none'" else "SAMEORIGIN"
    return resp


def _is_allowed_filename(filename: str) -> bool:
    if not filename:
        return False
    name = filename.rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    ext = os.path.splitext(name)[1].lstrip('.').lower()
    return ext in ALLOWED_EXTENSIONS


app = Dash(
    __name__,
    server=server,
    suppress_callback_exceptions=True,
    # Only skip editor/backup artefacts that may land in ./assets. Every real module is
    # auto-loaded exactly once, so there is no double-execution or old/new UI conflict.
    assets_ignore=r'.*\.ipynb_checkpoints.*|.*\.bak$|untitled.*',
)

app.title = "CamScan — Calibration Exporter"

# --- Force the uploader's <input type=file> to open the camera when possible ---
# Works on Android Chrome and most modern mobile browsers. iOS honors it on Safari if 'accept' is set.
app.index_string = """
<!DOCTYPE html>
<html>
  <head>
    {%metas%}
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, viewport-fit=cover">
    <title>CamScan — Calibration Exporter</title>
    {%favicon%}
    {%css%}
    <style>
      /* Subtle mobile-friendly defaults */
      :root{ --cal-accent:#00d4ff; }
      html,body{margin:0;padding:0;background:#0d0d0f;color:#e6e6e6;font-family: Segoe UI, system-ui, sans-serif;}
      .container{max-width:1400px;margin:0 auto;}

      /* Landing / upload screen — a centered card instead of a bare dashed box floating
         in a big empty void. (#top-panel is the container the upload callback hides once
         an image is loaded.) */
      #top-panel{ min-height:100dvh; display:flex; align-items:center; justify-content:center; padding:24px; box-sizing:border-box; }
      .landing-card{ width:100%; max-width:460px; background:#161618; border:1px solid #262629; border-radius:16px; padding:26px 24px 22px; box-shadow:0 12px 40px rgba(0,0,0,.5); box-sizing:border-box; }
      .landing-logo{ font-size:34px; line-height:1; }
      .landing-title{ font-size:22px; font-weight:700; margin:8px 0 2px; }
      .landing-sub{ color:#9a9aa4; font-size:14px; margin:0; }
      .landing-how{ color:#c9c9d0; font-size:13px; line-height:1.5; background:rgba(0,212,255,.06); border:1px solid rgba(0,212,255,.18); border-radius:10px; padding:10px 12px; margin:16px 0; }
      .landing-how b{ color:var(--cal-accent); }
      /* Sets honest expectations before the customer shoots: a single photo is a flat
         profile, not a 3D model. Muted so it informs without competing with the CTA. */
      .landing-note{ color:#8a8a92; font-size:12px; line-height:1.5; margin:10px 2px 0; }
      .landing-formats{ color:#76767e; font-size:12px; text-align:center; margin-top:12px; }
      #status{ margin-top:12px; text-align:center; }
      /* The uploader is a real drop zone with an accent-tinted, interactive affordance
         (it inherited a plain gray currentColor dashed border before). */
      #uploader{ margin:0 !important; transition:border-color .15s ease, background .15s ease; }
      #uploader:hover{ border-color:var(--cal-accent) !important; background:rgba(0,212,255,.06); cursor:pointer; }

      /* Scrollable wrapper for the viewer so tall images don't block the page */
      .cal-wrap{
        position: relative;
        height: 100dvh;             /* fill dynamic viewport height on mobile */
        overflow: auto;
        -webkit-overflow-scrolling: touch; /* iOS momentum scroll */
      }
    </style>
  </head>
  <body>
    <div class="container">
      {%app_entry%}
    </div>
    <footer>
      {%config%}
      {%scripts%}
      {%renderer%}
      <script>
        (function () {
          function patchCapture() {
            var host = document.getElementById('uploader');
            if (!host) return;
            var input = host.querySelector('input[type="file"]');
            if (!input) return;
            input.setAttribute('accept', 'image/*');
            input.setAttribute('capture', 'environment'); // prefer back camera
          }
          if (document.readyState !== 'loading') patchCapture();
          else document.addEventListener('DOMContentLoaded', patchCapture);
          new MutationObserver(patchCapture).observe(document.documentElement, {childList:true, subtree:true});
        })();
      </script>
    </footer>
  </body>
</html>
"""


class ImageTooLarge(Exception):
    """Decoded image exceeds MAX_IMAGE_PIXELS (decompression-bomb guard)."""


def _decode_b64_image(contents: str):
    """Returns (decoded_bgr_image, original_file_bytes).

    The RAW bytes come back too because they still carry EXIF, and cv2.imdecode drops it.
    The camera's focal length is the one thing that turns the marker's plane into a usable
    3D reference — it buys the working distance, and with it the size of the out-of-plane
    error that dominates this app's accuracy (see camera_geometry). We re-encode to JPEG
    before saving, so this is the only point at which EXIF still exists.
    """
    header, b64data = contents.split(",", 1)
    img_bytes = base64.b64decode(b64data)
    np_arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    # OPENCV_IO_MAX_IMAGE_PIXELS is set 10% above MAX_IMAGE_PIXELS, so an image just over our
    # cap still decodes far enough to reach this explicit check and be rejected with a clear
    # "too large, resize it" message; anything egregiously larger is refused by OpenCV first
    # (imdecode raises / returns None) and surfaces as the generic "not a valid image".
    if img is not None:
        h, w = img.shape[:2]
        if w * h > MAX_IMAGE_PIXELS:
            raise ImageTooLarge(f"{w}x{h} = {w * h:,}px exceeds cap {MAX_IMAGE_PIXELS:,}")
    return img, img_bytes


# The landing card (which hosts #status) is tucked off-screen once a photo loads, so anything
# written to #status from then on is invisible. Upload FAILURES must still be seen — otherwise
# tapping "Add another photo" and picking an unsupported file looks like the tap did nothing at
# all. This banner lives outside #top-panel, so it shows whether the card is on-screen or tucked.
_BANNER_HIDDEN = {"display": "none"}
_BANNER_SHOWN = {
    "display": "block", "position": "fixed", "left": "50%", "transform": "translateX(-50%)",
    "top": "10px", "zIndex": "80", "maxWidth": "min(560px, calc(100vw - 24px))",
    "boxSizing": "border-box", "padding": "11px 14px", "borderRadius": "10px",
    "background": "#2a1416", "border": "1px solid #6b2b30", "color": "#ffd9dc",
    "font": "14px/1.45 Segoe UI, system-ui, sans-serif",
    "boxShadow": "0 10px 30px rgba(0,0,0,.55)", "textAlign": "center",
}


app.layout = html.Div([
    html.Div([
        html.Div([
            html.Div("📸", className="landing-logo"),
            html.Div("CamScan", className="landing-title"),
            html.P("Turn 2-3 photos into a 3D part (STL / STEP / OBJ)", className="landing-sub"),
            # The scale holds only in the plane the ruler lies in: a ruler beside the part, on
            # the ground, reads a face 20 mm above it about 4% large from 0.5 m — so it
            # goes ON the face being photographed, one photo per face.
            html.Div([
                "For each view (top, side, end) lay a ruler or tape ",
                html.B("FLAT ON the face you are photographing"), ", not beside the part. "
                "Shoot square-on from about 0.5 m with the whole part in frame.",
            ], className="landing-how"),
            html.Div(
                "Then, on each photo: set the scale along the ruler, box the part and tap it "
                "to trace it, and use it in the 3D part as Top, Side or End. Build, then "
                "download STL to print or STEP for CAD. A printed calibration square works "
                "too and scales automatically.",
                className="landing-note",
            ),
            dcc.Upload(
                id="uploader",
                children=html.Div(["Tap ", html.B("to take a photo"), " or drop an image"]),
                multiple=False,
                style={
                    "width": "100%", "minHeight": "120px", "display": "flex",
                    "alignItems": "center", "justifyContent": "center",
                    "borderWidth": "2px", "borderStyle": "dashed",
                    "borderColor": "rgba(0,212,255,0.35)", "borderRadius": "12px",
                    "textAlign": "center", "padding": "18px", "boxSizing": "border-box",
                },
                accept="image/*",
            ),
            html.Div("PNG · JPG · WebP · BMP · TIFF — up to 8 MB", className="landing-formats"),
            html.Div(id="status"),
        ], className="landing-card"),
    ], id="top-panel"),
    html.Div(id="viewer", style={"position": "relative"}),
    # role/aria-live so the load-bearing calibration state + measurement readout the JS
    # writes here are announced to screen readers (it's polite so it won't interrupt).
    html.Div(id="cal-kpi", className="cal-kpi", role="status", **{"aria-live": "polite"}),
    # Always-visible upload-failure banner (see _BANNER_SHOWN). role=alert so a rejected
    # upload is announced immediately rather than sitting silently off-screen.
    html.Div(id="upload-error", role="alert", style=_BANNER_HIDDEN),
], id="landing-root", style={"fontFamily": "Segoe UI, sans-serif"})


# After the first photo, tuck the landing/upload card off-screen (not display:none)
# so the "Add another photo" button can re-open the file picker WITHOUT a page
# reload — the multi-photo, one-at-a-time submission flow depends on it.
_UPLOADER_TUCKED = {"position": "fixed", "left": "-9999px", "top": "0",
                    "width": "1px", "height": "1px", "overflow": "hidden", "opacity": "0"}


_BANNER_NOTICE = dict(_BANNER_SHOWN,
                      background="#2a2416", border="1px solid #6b5a2b", color="#ffe9b8")


def _upload_notice(status, viewer):
    """A loaded photo that still needs something from the user — the photo is fine, but it
    has no usable scale, so nothing measured on it means anything yet.

    This goes in the always-visible banner rather than #status for the same reason upload
    FAILURES do: #status lives inside the landing card, which this very return tucks to
    left:-9999px. Writing the one message the user has to act on into an element that is
    being hidden in the same breath left only a small KPI chip to carry it.
    """
    # The class lets the client tell this notice from an upload failure: the notice goes
    # away by itself once a scale is set; a failure stays until dismissed or replaced.
    return (status, viewer, _UPLOADER_TUCKED, None,
            html.Span(status, className="cal-scale-notice"), _BANNER_NOTICE)


def _upload_failed(msg):
    """Return value for a failed upload: keep the current viewer, reset the uploader so the
    same file can be picked again, and put `msg` in the always-visible banner as well as
    #status (which is off-screen once a photo has loaded)."""
    return msg, no_update, no_update, None, msg, _BANNER_SHOWN


@app.callback(
    Output("status", "children"),
    Output("viewer", "children"),
    Output("top-panel", "style"),
    Output("uploader", "contents"),  # Reset upload to allow new uploads
    Output("upload-error", "children"),
    Output("upload-error", "style"),
    Input("uploader", "contents"),
    State("uploader", "filename"),
    prevent_initial_call=True
)
def on_upload(contents, filename):
    if not contents:
        # This callback resets its own `contents` Input to None (below) so the same file
        # can be re-uploaded. That reset re-fires the callback with contents=None; ignore
        # it rather than overwriting the success status with a spurious warning.
        from dash.exceptions import PreventUpdate
        raise PreventUpdate

    # Basic validation of filename extension
    if not _is_allowed_filename(filename):
        return _upload_failed("⚠️ Unsupported file type.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stem = os.path.splitext(filename or "image")[0]
    safe = "".join(c for c in stem if c.isalnum() or c in ("-", "_")).strip("_") or "image"
    out_name = f"{safe}-{uuid.uuid4().hex[:8]}.jpg"

    # Decode defensively: a malformed data-URI (missing comma, bad base64 padding)
    # would otherwise raise inside the callback and surface as an opaque HTTP 500.
    try:
        img, raw_bytes = _decode_b64_image(contents)
    except ImageTooLarge:
        return _upload_failed(f"⚠️ Image is too large (over {MAX_IMAGE_PIXELS:,} pixels). "
                              "Resize it and try again.")
    except Exception:
        return _upload_failed("⚠️ Uploaded file is not a valid image.")
    if img is None:
        return _upload_failed("⚠️ Uploaded file is not a valid image.")

    # write atomically to avoid partial files
    tmp_fd, tmp_path = tempfile.mkstemp(dir=UPLOAD_DIR, prefix=f".{out_name}.", suffix=".tmp")
    os.close(tmp_fd)
    try:
        ok, enc = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if not ok:
            raise ValueError('Failed to encode image')
        with open(tmp_path, 'wb') as f:
            f.write(enc.tobytes())
        final_path = os.path.join(UPLOAD_DIR, out_name)
        os.replace(tmp_path, final_path)
    except Exception as e:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        return _upload_failed(f"⚠️ Failed to save upload: {e}")

    edge_mm_env = _resolve_edge_mm_from_env()
    if edge_mm_env is not None:
        print(f"[App] Using CALIB_EDGE_MM from env: {edge_mm_env} mm")
    else:
        print("[App] No CALIB_EDGE_MM set; using calibration_core module default")

    # The .jpg is already on disk. If anything below fails we return without a viewer, so that
    # file is an orphan no session references — and the early return also skips _reap_uploads,
    # so nothing trims it until the next SUCCESSFUL upload. Drop it on the way out instead.
    def _discard_partial():
        try:
            os.unlink(final_path)
        except OSError:
            pass

    # Focal length from the photo's own EXIF when it has any. Best-effort: a photo without
    # it still calibrates, the app just has to assume a typical phone lens for the working
    # distance and says so (cal["camera"]["focal_source"]).
    focal_px = None
    try:
        from camera_geometry import focal_px_from_jpeg_bytes
        focal_px = focal_px_from_jpeg_bytes(raw_bytes, img.shape[1], img.shape[0])
    except Exception as e:
        print(f"[App] EXIF focal unavailable: {e}")
    print(f"[App] Focal: {focal_px if focal_px else 'not in EXIF — assuming a typical phone lens'}")

    try:
        cal, overlay = calibrate_image(img, edge_mm=edge_mm_env, focal_px=focal_px)
    except Exception as e:
        _discard_partial()
        return _upload_failed(f"⚠️ Processing error: {e}")

    try:
        json_path, _ = save_outputs(out_name, cal, overlay, UPLOAD_DIR)
    except Exception as e:
        _discard_partial()
        return _upload_failed(f"⚠️ Failed to save results: {e}")

    # Keep the uploads dir bounded, preserving the pair we just wrote.
    _reap_uploads(keep_paths=(os.path.join(UPLOAD_DIR, out_name), json_path))

    ts = int(time.time() * 1000)
    img_url_data = contents
    img_url_file = url_for("downloads", fname=out_name, v=ts)
    base, _ext = os.path.splitext(out_name)
    json_url = url_for("downloads", fname=f"{base}.calibration.json", v=ts)

    viewer = html.Div([
        html.Div([
            html.Canvas(id="cal-canvas", style={"display": "block", "margin": "0 auto"})
        ], id="cal-view", className="cal-view",
           **{
               "data-img": img_url_data,
               "data-img-fallback": img_url_file,
               "data-json": json_url
           },
           style={"textAlign": "center"})
    ], className="cal-wrap")

    marker_mm = cal.get("marker_size_mm", "—")
    n_markers = len(cal.get("markers", []))
    confidence = cal.get("calibration_confidence")

    # No calibration reference was found. The image loaded fine, but there is NO scale —
    # every measurement will be in PIXELS until the user sets one. Do NOT dress this up as
    # a green "✅ success": that led people to submit un-scaled photos believing they were
    # measured in mm. Be honest and point straight at the recovery tool. (Matches the
    # app's pixel-honesty philosophy — see the uncalibrated export confirm.)
    # `calibration_usable` is False when nothing in the frame was CONFIRMED to be the
    # calibration target — including the common case where the detector latched onto a
    # shadow or some other dark square-ish object and measured it perfectly well. Reporting
    # "1 marker, 40 mm" there is exactly the dressed-up success this branch exists to avoid,
    # so an unconfirmed detection takes the same honest path as finding nothing at all.
    usable = cal.get("calibration_usable", n_markers > 0)
    if n_markers == 0 or confidence == "none" or not usable:
        found_something = n_markers > 0 and not usable
        # Kept short: it sits over the photo while the user does the very thing it asks for,
        # and the client hides it as soon as a scale is being set (calib.ui.enhanced.js).
        lead = ("⚠️ No scale yet — I saw a square-ish shape but can't confirm it's a "
                "calibration target, so it isn't used"
                if found_something else
                "⚠️ No scale yet — no calibration target in this photo")
        status = (
            f"{lead}. Measurements are in pixels until you tap 📐 and draw along a ruler or "
            "anything of known length (or tap the 4 corners of a known rectangle — that also "
            "corrects camera angle)."
        )
        if not focal_px:
            # Square up uses the lens's focal length. A photo passed through a chat app or
            # a screenshot has lost it, and a typical phone lens is assumed instead — close
            # enough for a photo taken nearly square-on, less so the steeper it was. The full
            # tip comes with Square up's result, where it matters; this only flags it.
            status += " 📷 No camera data in this file (chat apps strip it)."
        return _upload_notice(status, viewer)

    status = (
        f"✅ Processed '{filename}' — {n_markers} marker(s). "
        f"Marker size: {marker_mm} mm. Tap/click to annotate."
    )
    # Warn when the auto-calibration had to fall back to a rough estimate, so the
    # user knows to double-check the scale rather than trusting a wrong value.
    if confidence == "low":
        status += " ⚠️ Auto-calibration is approximate — verify with the Set Scale tool (📐)."
    return status, viewer, _UPLOADER_TUCKED, None, "", _BANNER_HIDDEN  # Reset upload for next file


@server.route("/uploads/<path:fname>")
def downloads(fname):
    return send_from_directory(UPLOAD_DIR, fname, as_attachment=False)


@server.route("/api/export/dxf", methods=["POST"])
def export_dxf():
    """DXF export endpoint for CAD software integration.

    The DXF is written to a temporary file, read into memory, and the file is
    deleted before responding — so there is no cleanup race and nothing leaks on
    disk regardless of how slowly the client downloads.
    """
    from flask import Response

    try:
        from circle_detection import export_to_dxf
    except ImportError:
        return "DXF export requires ezdxf: pip install ezdxf", 500

    data = _json_body()
    geometry = data.get("geometry", [])
    if not isinstance(geometry, list) or not geometry:
        return "No geometry provided", 400
    # Keep only dict items. export_to_dxf skips a malformed item so one bad shape can't drop
    # the rest of the drawing, but its per-item guard calls item.get() first — a bare string or
    # number in the list raises AttributeError past that guard and 500s the whole export.
    geometry = [g for g in geometry if isinstance(g, dict)]
    if not geometry:
        return "No geometry provided", 400

    # Require a positive scale. `... or 1.0` would silently turn a client-sent 0 into 1.0
    # (exporting pixels-as-mm) and let a negative through (mirrored/negatively-scaled DXF);
    # reject a present-but-invalid scale instead, and only default when it is missing.
    raw_scale = data.get("mm_per_px", None)
    if raw_scale is None:
        mm_per_px = 1.0
    else:
        try:
            mm_per_px = float(raw_scale)
        except (TypeError, ValueError):
            return "Invalid mm_per_px", 400
        # `inf > 0` is True, so the positivity test alone lets Infinity through and every
        # exported coordinate becomes inf — a 200 OK carrying a DXF no CAD tool will open.
        if not (mm_per_px > 0) or not math.isfinite(mm_per_px):
            return "mm_per_px must be a finite number > 0", 400

    # Image height (pixels) lets us flip the Y axis: image coordinates grow downward,
    # CAD coordinates grow upward. Without this the exported part comes out mirrored.
    try:
        image_height = float(data.get("image_height", 0)) or None
        if image_height is not None and not math.isfinite(image_height):
            image_height = None
    except (TypeError, ValueError):
        image_height = None

    # System temp, NOT UPLOAD_DIR. uploads/ is created lazily by the upload callback, so
    # exporting before any photo was uploaded in this container (e.g. straight after "Load a
    # saved job") raised FileNotFoundError here — outside the try — and returned a bare 500.
    # It is also served publicly at /uploads/<name>, and a scratch file has no business there.
    dxf_fd, dxf_path = tempfile.mkstemp(suffix=".dxf")
    os.close(dxf_fd)
    try:
        ok = export_to_dxf(geometry, dxf_path, mm_per_px, image_height_px=image_height)
        if not ok:
            return "DXF export failed", 500
        with open(dxf_path, "rb") as f:
            payload = f.read()
    except Exception as e:
        # Log the detail server-side; don't echo the raw exception back to the client (it can
        # carry filesystem paths and internals). Matches how /api/submit reports its failures.
        print(f"[DXF Export] Error: {e}")
        import traceback
        traceback.print_exc()
        return "DXF export failed", 500
    finally:
        try:
            os.unlink(dxf_path)
        except OSError:
            pass

    return Response(
        payload,
        mimetype="application/dxf",
        headers={"Content-Disposition": "attachment; filename=geometry.dxf"},
    )


def _points_mm(pts, mm_per_px, image_height):
    """Exported outline points in millimetres, Y flipped to CAD's up, or None.

    Mirrors export_to_dxf's conventions so the STL, the DXF and the 3D part all describe the
    same part rather than mirror images of it. A tilt-corrected ("plane") export arrives
    already in millimetres with Y up, mm_per_px 1 and image_height 0 — which reads as "no
    flip" here — so each axis is flipped exactly once either way.
    """
    if not isinstance(pts, (list, tuple)) or len(pts) < 3:
        return None
    out = []
    for p in pts:
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            return None
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        if image_height is not None:
            y = image_height - y
        out.append([x * mm_per_px, y * mm_per_px])
    return out


@server.route("/api/export/stl", methods=["POST"])
def export_stl():
    """A watertight 3D solid, ready to slice and print.

    Every other export here is flat — PNG, JSON, CSV, DXF, SVG — so a customer who
    photographed a broken part still needed somebody to open CAD and extrude the profile by
    hand. For the large class of replacement parts that ARE an extrusion (running boards,
    mouldings, sills, trim, brackets, gaskets) the traced outline plus one depth is the whole
    part, so the last step belongs here.

    Takes the same geometry list the DXF endpoint takes. The outer profile is the closed
    polyline carrying a positive depth_mm; any other closed polyline whose centroid falls
    inside it becomes a through-hole, which is exactly what the auto-trace already produces
    for a bolt pattern or a row of clip slots.

    A profile that also carries wall_mm is built as a COVER instead — a plate that thick with
    a wall that thick round its edge, open underneath, delivered plate-down the way it prints
    (see solid_export.build_shell). Most replacement covers are that shape, and the solid
    version of one is several times the plastic and will not fit over what it covers.

    Body: { geometry: [...], mm_per_px, image_height }
    Returns the STL, or 400/422 with a reason. Refuses rather than returning a leaky mesh:
    a slicer either rejects one or, worse, silently prints it wrong.
    """
    from flask import Response

    try:
        from solid_export import build_extrusion, build_shell, write_binary_stl, mesh_report
    except Exception:
        return "3D export unavailable", 500

    data = _json_body()
    geometry = data.get("geometry", [])
    if not isinstance(geometry, list) or not geometry:
        return "No geometry provided", 400
    geometry = [g for g in geometry if isinstance(g, dict)]

    try:
        raw_scale = data.get("mm_per_px", None)
        mm_per_px = 1.0 if raw_scale is None else float(raw_scale)
    except (TypeError, ValueError):
        return "Invalid mm_per_px", 400
    if not (mm_per_px > 0) or not math.isfinite(mm_per_px):
        return "mm_per_px must be a finite number > 0", 400

    try:
        image_height = float(data.get("image_height", 0)) or None
        if image_height is not None and not math.isfinite(image_height):
            image_height = None
    except (TypeError, ValueError):
        image_height = None

    def _ring(item):
        return _points_mm(item.get("points"), mm_per_px, image_height)

    closed = [g for g in geometry if g.get("type") == "polyline" and g.get("closed")]
    outer_item = next((g for g in closed
                       if isinstance(g.get("depth_mm"), (int, float)) and g.get("depth_mm", 0) > 0),
                      None)
    if outer_item is None:
        return ("No extrudable profile. Close an outline around the part, then set its "
                "depth — how far the shape runs — and export again."), 422

    outer = _ring(outer_item)
    if outer is None:
        return "The profile's points are not usable", 400
    try:
        depth_mm = float(outer_item["depth_mm"])
    except (TypeError, ValueError):
        return "Invalid depth_mm", 400

    holes = []
    for g in closed:
        if g is outer_item:
            continue
        r = _ring(g)
        if r is not None:
            holes.append(r)
    # A trace finds at most a handful of holes. The cover builder rasterises every hole over
    # the whole part, once per pass, so an unbounded count is unbounded time and memory
    # (measured: 100 tiny holes in a 10 KB request took 1.8 GB).
    if len(holes) > MAX_PROFILE_HOLES:
        return (f"Too many holes in the profile ({len(holes)}; at most {MAX_PROFILE_HOLES}). "
                "Delete the stray closed outlines inside the part."), 400

    wall_raw = outer_item.get("wall_mm")
    info = {"kind": "solid", "note": None}
    if wall_raw is not None:
        try:
            wall_mm = float(wall_raw)
        except (TypeError, ValueError):
            return "Invalid wall_mm", 400
        if not math.isfinite(wall_mm):
            return "Invalid wall_mm", 400
    else:
        wall_mm = None

    if wall_mm is not None and wall_mm > 0:
        tris, info = build_shell(outer, holes, depth_mm, wall_mm)
        if tris is None:
            return info.get("reason") or "Could not build the cover from this profile.", 422
    else:
        tris = build_extrusion(outer, holes, depth_mm)
        if tris is None:
            return ("Could not build a closed solid from this profile. Check that the outline "
                    "does not cross itself and that the depth is sensible."), 422
    rep = mesh_report(tris)
    if not (rep.get("watertight") and rep.get("oriented")):
        # Never hand a slicer a leaky mesh; it either refuses it or prints it wrong.
        return "Could not build a watertight solid from this profile.", 422

    dxf_fd, stl_path = tempfile.mkstemp(suffix=".stl")
    os.close(dxf_fd)
    try:
        header = ("CamScan cover, plate-down" if info.get("kind") == "shell"
                  else "CamScan extruded profile")
        if not write_binary_stl(tris, stl_path, header):
            return "Could not write the STL", 500
        with open(stl_path, "rb") as f:
            payload = f.read()
    except Exception as e:
        print(f"[STL Export] Error: {e}")
        return "3D export failed", 500
    finally:
        try:
            os.unlink(stl_path)
        except OSError:
            pass

    print(f"[STL Export] {rep['triangles']} triangles, {rep['volume_mm3'] / 1000.0:.1f} cm3")
    headers = {"Content-Disposition": "attachment; filename=part.stl",
               "X-CamScan-Triangles": str(rep["triangles"]),
               "X-CamScan-Volume-MM3": f"{rep['volume_mm3']:.1f}",
               "X-CamScan-Kind": str(info.get("kind") or "solid")}
    if info.get("kind") == "shell":
        headers["X-CamScan-Cavities"] = str(info.get("cavities", 0))
        headers["X-CamScan-Bossed-Holes"] = str(info.get("bossed_holes", 0))
    if info.get("note"):
        # Headers are latin-1 on the wire; the notes are plain ASCII, but never risk a 500
        # over a dash.
        headers["X-CamScan-Note"] = str(info["note"]).encode("ascii", "replace").decode()
    return Response(payload, mimetype="model/stl", headers=headers)


def _num(value, default=None):
    """A finite float from untrusted JSON, or `default` when absent/blank. Raises ValueError
    for something that is present but not a finite number."""
    if value is None or value == "":
        return default
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("not finite")
    return v


@server.route("/api/export/part", methods=["POST"])
def export_part():
    """A part built from several traced views — see part_builder.

    Body: { views: { top|side|end: {points, mm_per_px, image_height, rot, flip_h, flip_v,
                                    scale, rot_deg, keep_tilt,
                                    source, covers: all|start|end (side only)},
                     cavity: {..same.., view, align, open, depth_mm} },
            depth_mm, clearance_mm, wall_mm, mirror, upside_down, preview, format: stl|step|obj,
            known_mm: {length, width, height}, axis_source: {length|width|height: view},
            ends: {slope_deg, radius_mm} }

    Each outline arrives exactly as the DXF/STL exports send it and goes through the same
    pixel->mm rule (_points_mm). `source` identifies the photo an outline was traced on: a
    cavity traced on the same photo as its view keeps its exact place in that view.

    preview=true returns JSON (normalised outlines, the result's silhouette in all three
    views, fits and warnings) so the user can check the views line up before building.
    Otherwise returns the file: STL by default, or STEP / OBJ for editing in CAD. Only the
    STL is turned for printing (a cover plate-down); STEP and OBJ come as traced, Z up, which
    is how a CAD user expects to find the part. 400 for malformed input, 422 with a reason for
    a part that cannot be built — and, when views disagree, `detail` {view, axis, have_mm,
    want_mm, setter} so the dialog can offer the fix (JSON for a preview, the
    X-CamScan-Detail header for a download).
    """
    from flask import Response, jsonify
    try:
        from part_builder import build_part, PartError, available
    except Exception:
        return "3D part building unavailable", 500
    if not available():
        return "3D part building needs the manifold3d package on the server.", 500

    data = _json_body()
    raw_views = data.get("views")
    if not isinstance(raw_views, dict):
        return "No views provided", 400

    def _spec(entry, what):
        if not isinstance(entry, dict):
            raise ValueError(f"{what} is not an object")
        mpp = _num(entry.get("mm_per_px"), 1.0)
        if not (mpp and mpp > 0):
            raise ValueError(f"{what} has no usable scale")
        ih = _num(entry.get("image_height"), 0.0) or None
        pts = _points_mm(entry.get("points"), mpp, ih)
        if pts is None:
            raise ValueError(f"{what} points are not usable")
        try:
            rot = int(entry.get("rot") or 0) % 4
        except (TypeError, ValueError, OverflowError):     # OverflowError: JSON Infinity
            raise ValueError(f"{what} rotation is not a number")
        covers = entry.get("covers") or "all"
        if covers not in ("all", "start", "end"):
            raise ValueError(f"{what} coverage must be all, start or end")
        # The dialog's Match buttons and fine turn; part_builder checks their ranges.
        scale = _num(entry.get("scale"), 1.0)
        rot_deg = _num(entry.get("rot_deg"), 0.0)
        return {"points": pts, "rot": rot, "flip_h": bool(entry.get("flip_h")),
                "flip_v": bool(entry.get("flip_v")), "covers": covers,
                "scale": scale, "rot_deg": rot_deg, "keep_tilt": bool(entry.get("keep_tilt")),
                "source": str(entry.get("source") or "")[:500]}

    def _obj(value, what):
        if value is None or value == "":
            return None
        if not isinstance(value, dict):
            raise ValueError(f"{what} is not an object")
        return value

    try:
        views = {}
        for role in ("top", "side", "end"):
            if raw_views.get(role):
                views[role] = _spec(raw_views[role], role)
        cavity = None
        if raw_views.get("cavity"):
            c = raw_views["cavity"]
            cavity = _spec(c, "cavity")
            cavity["view"] = c.get("view")
            cavity["align"] = c.get("align") or "bottom"
            cavity["open"] = c.get("open") or "through"
            cavity["depth_mm"] = _num(c.get("depth_mm"))
            host = views.get(cavity["view"]) if isinstance(cavity["view"], str) else None
            cavity["same_photo"] = bool(host and cavity["source"] and host["source"] == cavity["source"])
        depth_mm = _num(data.get("depth_mm"))
        clearance_mm = _num(data.get("clearance_mm"), 0.0)
        wall_mm = _num(data.get("wall_mm"))
        known = _obj(data.get("known_mm"), "known_mm")
        known_mm = {k: _num(known.get(k)) for k in ("length", "width", "height")} if known else None
        axis_src = _obj(data.get("axis_source"), "axis_source")
        axis_source = ({str(k)[:20]: (str(v)[:20] if v else None) for k, v in axis_src.items()}
                       if axis_src else None)
        ends_in = _obj(data.get("ends"), "ends")
        ends = ({"slope_deg": _num(ends_in.get("slope_deg"), 0.0),
                 "radius_mm": _num(ends_in.get("radius_mm"), 0.0)} if ends_in else None)
    except (TypeError, ValueError, OverflowError) as e:
        return f"Invalid input: {e}", 400

    preview = bool(data.get("preview"))
    # STL for the printer; STEP and OBJ for CAD, where the part has to be EDITED — see
    # cad_export: one face per flat face of the part instead of one per triangle.
    fmt = data.get("format") or "stl"
    if not preview and fmt not in ("stl", "step", "obj"):
        return "Format must be stl, step or obj", 400
    import json as _json
    try:
        # The preview reports what the STL download would be, as it always has.
        res = build_part(views, depth_mm=depth_mm, cavity=cavity, clearance_mm=clearance_mm,
                         wall_mm=wall_mm, mirror=bool(data.get("mirror")),
                         upside_down=bool(data.get("upside_down")), preview=preview,
                         known_mm=known_mm, axis_source=axis_source, ends=ends,
                         for_print=preview or fmt == "stl")
    except PartError as e:
        detail = getattr(e, "detail", None)
        if preview:
            return jsonify(ok=False, error=str(e), detail=detail), 422
        return Response(str(e), status=422, mimetype="text/plain", headers=(
            {"X-CamScan-Detail": _json.dumps(detail, ensure_ascii=True)} if detail else {}))
    except Exception as e:
        print(f"[Part] error: {e}")
        return "3D part build failed", 500

    info = res["info"]
    if preview:
        return jsonify(ok=True, preview=res["preview"], info=info)

    fd, path = tempfile.mkstemp(suffix="." + fmt)
    os.close(fd)
    try:
        if fmt == "stl":
            from solid_export import write_binary_stl
            header = "CamScan part" + (" (mirrored)" if info.get("mirrored") else "")
            if not write_binary_stl(res["tris"], path, header):
                return "Could not write the STL", 500
        else:
            import cad_export
            try:
                (cad_export.write_step if fmt == "step" else cad_export.write_obj)(res["tris"], path)
            except Exception as e:
                print(f"[Part] {fmt} export error: {e}")
                return f"Could not write the {fmt.upper()} file", 500
        with open(path, "rb") as f:
            payload = f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    name = ("part-mirrored" if info.get("mirrored") else "part") + "." + fmt
    mime = {"stl": "model/stl", "step": "model/step", "obj": "model/obj"}[fmt]
    return Response(payload, mimetype=mime, headers={
        "Content-Disposition": f"attachment; filename={name}",
        "X-CamScan-Kind": str(info.get("kind") or "solid"),
        "X-CamScan-Volume-MM3": f"{info.get('volume_mm3', 0):.1f}",
        # Everything the UI reports (fits, warnings, size), ASCII-safe for a header.
        "X-CamScan-Info": _json.dumps({k: info[k] for k in ("kind", "size_mm", "fits", "warnings",
                                                            "mirrored", "upside_down", "plate_down",
                                                            "orientation", "deskew", "note") if k in info},
                                      ensure_ascii=True)})


@server.route("/api/plane", methods=["POST"])
def api_plane():
    """Homography for a plane parallel to the marker's, `height_mm` above it.

    The marker's homography rectifies the marker's plane and nothing else, so a feature on a
    raised face — the top of a part with thickness — reads d/(d-h) too large. That is the
    largest error in this app (+5.3% for a part only 20 mm thick at a 400 mm working
    distance) and, unlike tilt, it does NOT go away in a square-on shot.

    Body: { homography: 3x3, marker_size_mm, focal_px, image_size:{width,height},
            height_mm }
    Returns { ok, homography, applied_height_mm, error_removed_pct } — feed the returned
    matrix to the same measurement code and the raised face measures true.

    The exact fix is still to rest the card ON that face and re-shoot; this is for photos
    already taken. Correcting is worthwhile even when the focal length was assumed rather
    than read from EXIF: on synthetic renders a 21% focal error still turned +11.1% into
    -2.9%, because the focal error scales only the (small) correction term.
    """
    from flask import jsonify
    data = _json_body()

    try:
        from camera_geometry import (homography_at_height, parallax_error_pct,
                                     working_distance_mm, default_focal_px)
    except Exception:
        return jsonify(ok=False, error="unavailable"), 500

    H = data.get("homography")
    if not (isinstance(H, list) and len(H) == 3
            and all(isinstance(r, list) and len(r) == 3 for r in H)):
        return jsonify(ok=False, error="bad_homography"), 400
    try:
        H = [[float(v) for v in row] for row in H]
    except (TypeError, ValueError):
        return jsonify(ok=False, error="bad_homography"), 400
    if not all(math.isfinite(v) for row in H for v in row):
        return jsonify(ok=False, error="bad_homography"), 400

    def _pos(key):
        try:
            v = float(data.get(key))
        except (TypeError, ValueError):
            return None
        return v if math.isfinite(v) and v > 0 else None

    edge_mm = _pos("marker_size_mm")
    if edge_mm is None:
        return jsonify(ok=False, error="bad_marker_size"), 400

    size = data.get("image_size") or {}
    w = _pos_from(size, "width")
    h = _pos_from(size, "height")
    if w is None or h is None:
        return jsonify(ok=False, error="bad_image_size"), 400

    focal = _pos("focal_px") or default_focal_px(w, h)

    try:
        height_mm = float(data.get("height_mm", 0.0))
    except (TypeError, ValueError):
        return jsonify(ok=False, error="bad_height"), 400
    if not math.isfinite(height_mm):
        return jsonify(ok=False, error="bad_height"), 400
    # A raised face is toward the camera; a recess is away. Bound it so a typo can't ask for
    # a plane at or behind the camera, where the transform degenerates.
    if not (-500.0 <= height_mm <= 500.0):
        return jsonify(ok=False, error="height_out_of_range"), 400

    Hc = homography_at_height(H, edge_mm, focal, w / 2.0, h / 2.0, height_mm)
    if Hc is None:
        return jsonify(ok=False, error="pose_unavailable"), 200

    removed = None
    try:
        edge_px = _pos("edge_px")
        dist = working_distance_mm(edge_px, edge_mm, focal) if edge_px else None
        if dist:
            removed = parallax_error_pct(height_mm, dist)
    except Exception:
        removed = None

    return jsonify(ok=True, homography=Hc, applied_height_mm=height_mm,
                   error_removed_pct=(round(removed, 2) if removed is not None else None))


def _pos_from(d, key):
    """A strictly-positive finite float from dict `d`, or None."""
    try:
        v = float(d.get(key))
    except (TypeError, ValueError, AttributeError):
        return None
    return v if math.isfinite(v) and v > 0 else None


@server.route("/api/trace", methods=["POST"])
def api_trace():
    """Auto-trace a part outline from a seed tap.

    Body: { image: dataURL, seed: [x,y]|null, exclude: [[x,y,w,h],...], simplify: float,
            roi: [x,y,w,h]|null }   # roi (the user's Area box) restricts segmentation to a region
    Returns { ok, points:[[x,y],...] } in the POSTed image's pixel coords, which the client
    turns into an editable, closed outline. Kept lenient — a failure to segment is a normal
    200 {ok:false} so the UI can say 'tap the part' rather than showing an error page.
    A `hint` (what to do next, in words) and `warnings` (a list of them) the tracer gives are
    passed on, with the outline or with the refusal; a refusal always carries a hint.
    """
    from flask import jsonify
    try:
        from auto_outline import auto_outline_full
    except Exception:
        return jsonify(ok=False, error="unavailable"), 500

    data = _json_body()
    durl = data.get("image")
    if not isinstance(durl, str) or "," not in durl:
        return jsonify(ok=False, error="no_image"), 400
    try:
        _mime, raw = _decode_data_url(durl)
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        img = None
    if img is None:
        return jsonify(ok=False, error="bad_image"), 400
    # Same decompression-bomb guard the upload path applies (this endpoint decodes untrusted
    # image bytes too): refuse an over-cap frame rather than segmenting a huge allocation.
    if img.shape[0] * img.shape[1] > MAX_IMAGE_PIXELS:
        return jsonify(ok=False, error="too_large"), 400

    seed = data.get("seed")
    if isinstance(seed, (list, tuple)) and len(seed) == 2:
        try:
            seed = [float(seed[0]), float(seed[1])]
            if not all(math.isfinite(v) for v in seed):
                seed = None
        except (TypeError, ValueError):
            seed = None
    else:
        seed = None

    exclude = []
    raw_exclude = data.get("exclude")
    for box in (raw_exclude if isinstance(raw_exclude, (list, tuple)) else []):
        try:
            if len(box) == 4:
                vals = tuple(float(v) for v in box)
                # NaN/Infinity reaches an int() deep in the segmenter and raises, turning a
                # bad box into a 500 — this endpoint's contract is to degrade to 200 {ok:false}.
                # `roi` and `seed` are already screened this way; screen `exclude` too.
                # Finite is not enough: 1e300 overflows the int the box is drawn with. Nothing
                # off the photo by more than its own size can exclude anything on it.
                if all(math.isfinite(v) and abs(v) <= 1e6 for v in vals):
                    exclude.append(vals)
        except (TypeError, ValueError):
            continue
        if len(exclude) >= 64:                 # as the fg/bg taps are capped
            break

    try:
        simplify = min(0.05, max(0.0003, float(data.get("simplify", 0.0006))))
    except (TypeError, ValueError):
        simplify = 0.0006

    roi = data.get("roi")
    if isinstance(roi, (list, tuple)) and len(roi) == 4:
        try:
            roi = [float(v) for v in roi]
            if not all(math.isfinite(v) for v in roi):
                roi = None
        except (TypeError, ValueError):
            roi = None
    else:
        roi = None

    # Taps saying "this IS the part" / "this is NOT". A background tap switches segmentation
    # to GrabCut, which is the only thing that separates a part from something the same
    # colour beside it — a dark part next to its own shadow differs from it by ~15 grey
    # levels, so no threshold can draw that line.
    def _points(key):
        out = []
        raw = data.get(key)
        for p in (raw if isinstance(raw, (list, tuple)) else []):
            try:
                x, y = float(p[0]), float(p[1])
            except (TypeError, ValueError, IndexError):
                continue
            if math.isfinite(x) and math.isfinite(y):
                out.append([x, y])
            if len(out) >= 64:          # a tap count no human reaches; bounds the work
                break
        return out

    fg_points, bg_points = _points("fg"), _points("bg")

    try:
        res = auto_outline_full(img, seed=seed, exclude_boxes=exclude, simplify=simplify, roi=roi,
                                fg_points=fg_points, bg_points=bg_points)
    except Exception as e:
        print(f"[trace] error: {e}")
        return jsonify(ok=False, error="trace_failed"), 500

    # What the tracer has to say about its result (auto_outline may add a `hint` and
    # `warnings`; an older one adds neither). Only plain text goes back to the page.
    extra = {}
    if isinstance(res, dict):
        if isinstance(res.get("hint"), str) and res["hint"].strip():
            extra["hint"] = res["hint"].strip()[:400]
        warns = res.get("warnings")
        if isinstance(warns, (list, tuple)):
            warns = [w.strip()[:400] for w in warns if isinstance(w, str) and w.strip()][:8]
            if warns:
                extra["warnings"] = warns

    if not res or not res.get("outer"):
        # A tap ON the part that finds nothing is the usual case (measured on a customer's top
        # photo: a dark cover on dark concrete), and the remedy that works is the one the
        # user would not guess — a box round the part first. Without a hint the page could
        # only repeat "tap the part", which they had just done.
        extra.setdefault("hint", "Couldn't separate the part from the background inside your Area box — "
                                 "draw the box snugly round the part, then tap the part again."
                         if roi else
                         "Couldn't separate the part from the background here. Draw a box round "
                         "the part (▭ Area), then tap the part again.")
        return jsonify(ok=False, error="no_outline", **extra), 200
    # A trace that IS the frame is not an outline — the tap landed on something featureless
    # (sky, a plain wall, bare concrete) and the segmenter had no edge to find. Handing it
    # back would let the user measure and export the image border as their part.
    if res.get("runaway"):
        return jsonify(ok=False, error="runaway",
                       hint=extra.get("hint") or
                       "That trace covers the whole photo — the spot you tapped has no "
                       "edge to find. Tap right on the part, draw an Area box around it "
                       "first, or tap ✖ Not the part on the background.",
                       **({"warnings": extra["warnings"]} if "warnings" in extra else {})), 200
    # `points` is the outer boundary (unchanged shape for older clients); `holes` are interior
    # loops (a box-end ring, bolt holes) the client drops in as extra closed profiles.
    return jsonify(ok=True, points=res["outer"], holes=res.get("holes") or [], **extra)


@server.route("/api/outline/straighten", methods=["POST"])
def api_straighten():
    """Straighten a traced outline onto the part's edges (see outline_cleanup.py).

    Body: { image: dataURL | null, points: [[x, y], ...], mode: "straighten" | "refine" } —
    points in the POSTed image's pixel coords. Returns { ok, points, straight, snapped[, rounded] }. Without an image the sides are still
    straightened, through their own dots. A failure is a 200 {ok:false}: the outline as it was
    is always a fine answer.
    """
    from flask import jsonify
    from outline_cleanup import refine_outline, straighten_outline

    data = _json_body()
    pts = data.get("points")
    if not isinstance(pts, list) or not (3 <= len(pts) <= 5000):
        return jsonify(ok=False, error="bad_points"), 400
    try:
        ring = [[float(p[0]), float(p[1])] for p in pts]
    except (TypeError, ValueError, IndexError):
        return jsonify(ok=False, error="bad_points"), 400
    if not all(math.isfinite(v) for q in ring for v in q):
        return jsonify(ok=False, error="bad_points"), 400

    img = None
    durl = data.get("image")
    if isinstance(durl, str) and "," in durl:
        try:
            _mime, raw = _decode_data_url(durl)
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            img = None
        if img is None:
            return jsonify(ok=False, error="bad_image"), 400
        if img.shape[0] * img.shape[1] > MAX_IMAGE_PIXELS:
            return jsonify(ok=False, error="too_large"), 400
        # The search for each side's edge scales with the outline's size, so an outline far
        # bigger than the photo it was traced on is not one — and was minutes and gigabytes.
        h_, w_ = img.shape[:2]
        if any(not (-w_ <= x <= 2 * w_ and -h_ <= y <= 2 * h_) for x, y in ring):
            return jsonify(ok=False, error="bad_points"), 400
    # "refine" also gives each corner the radius the photo shows (it needs the photo).
    refine = data.get("mode") == "refine"
    if refine and img is None:
        return jsonify(ok=False, error="no_image"), 400
    try:
        res = refine_outline(img, ring) if refine else straighten_outline(img, ring)
    except Exception as e:
        print(f"[straighten] error: {e}")
        return jsonify(ok=False, error="failed"), 500
    if not res:
        return jsonify(ok=False, error="no_change"), 200
    return jsonify(ok=True, **res)


@server.route("/api/outline/symmetric", methods=["POST"])
def api_symmetric():
    """Make an outline symmetric about its long or short axis (see outline_cleanup).

    Body: { points: [[x, y], ...], axis: "auto" | "long" | "short" } in image pixel coords.
    Returns { ok, points, axis, mismatch_pct, other } or 200 { ok:false, error } in words.
    """
    from flask import jsonify
    from outline_cleanup import symmetrize_outline

    data = _json_body()
    pts = data.get("points")
    if not isinstance(pts, list) or not (3 <= len(pts) <= 5000):
        return jsonify(ok=False, error="Select a closed outline first."), 400
    try:
        ring = [[float(p[0]), float(p[1])] for p in pts]
    except (TypeError, ValueError, IndexError):
        return jsonify(ok=False, error="The outline is not a list of points."), 400
    if not all(math.isfinite(v) for q in ring for v in q):
        return jsonify(ok=False, error="The outline has non-finite points."), 400
    axis = data.get("axis") if data.get("axis") in ("long", "short") else "auto"
    try:
        res = symmetrize_outline(ring, axis)
    except Exception as e:
        print(f"[symmetric] error: {e}")
        return jsonify(ok=False, error="Could not make this outline symmetric."), 500
    if not res:
        return jsonify(ok=False, error="This outline's halves differ by more than a quarter — making "
                                       "it symmetric would change its shape, not tidy it."), 200
    return jsonify(ok=True, **res)


@server.route("/api/square_up", methods=["POST"])
def api_square_up():
    """Perspective from two edges that are parallel on the part — see square_up.py.

    Body: { outline: [[x, y], ...], ruler: [[x, y], [x, y]], ruler_mm, image_w, image_h,
            focal_px | null,
            ruler_image: dataURL | null, ruler_box: [x0, y0, k] | null,
            reference: "auto" | "ruler" | "part" }   (image pixel coords)
    `ruler_image` is the photo round the scale line, cropped at (x0, y0) and scaled by k. The
    ruler's own edges are found in it (ruler_check.py); a ruler is a rectangle for certain,
    which the part may not be, so "auto" squares up from the ruler when its edges are found
    and from the part's long sides otherwise. Whichever is used, the other is measured through
    the result, as a check.
    Returns { ok, H (image px -> plane mm, 3x3), reference, sides, ruler_edges, tilt_deg,
              width_change_pct, focal_source, part: {angle_deg, width_mm, width_change_pct},
              ruler_check: {...} | null, other: "ruler" | "part" | null,
              partial: bool, ruler_reason: str | null, ruler_found: bool }
    `partial`: only one of the ruler's edges was clear, and the other is assumed parallel to
    it — that lines the photo up but cannot see a tilt along the ruler, so the part's taper
    read through it is marked as less sure. `ruler_reason` says why the ruler's edges were not
    found (or only one was), or why edges that were found did not square up (`ruler_found`
    true). `partial` and `ruler_reason` are also on a 200 { ok:false } refusal.
    """
    from flask import jsonify
    from ruler_check import find_ruler_edges, ruler_report
    from square_up import SquareUpError, long_sides, square_up

    data = _json_body()
    pts = data.get("outline")
    if not isinstance(pts, list) or not (4 <= len(pts) <= 5000):
        return jsonify(ok=False, error="Select the part's traced outline first."), 400
    try:
        ring = [[float(p[0]), float(p[1])] for p in pts]
    except (TypeError, ValueError, IndexError):
        return jsonify(ok=False, error="The outline is not a list of points."), 400
    if not all(math.isfinite(v) for q in ring for v in q):
        return jsonify(ok=False, error="The outline has non-finite points."), 400
    want = data.get("reference") if data.get("reference") in ("ruler", "part") else "auto"
    ruler = data.get("ruler")
    args = (ruler, data.get("ruler_mm"), data.get("image_w"), data.get("image_h"), data.get("focal_px"))

    # The ruler's edges, if the crop round the scale line shows them.
    edges, edge_sigma, partial, ruler_reason = None, None, False, None
    durl, box = data.get("ruler_image"), data.get("ruler_box")
    if isinstance(durl, str) and "," in durl and isinstance(box, list) and len(box) == 3:
        try:
            x0, y0, k = (float(v) for v in box)
            ra, rb = ruler
            ra, rb = (float(ra[0]), float(ra[1])), (float(rb[0]), float(rb[1]))
            if not all(math.isfinite(v) for v in (x0, y0, k, *ra, *rb)) or not (0 < k <= 4):
                raise ValueError("bad box")
            _mime, raw = _decode_data_url(durl)
            crop = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if crop is not None and crop.shape[0] * crop.shape[1] <= MAX_IMAGE_PIXELS:
                det = find_ruler_edges(crop, [(ra[0] - x0) * k, (ra[1] - y0) * k],
                                       [(rb[0] - x0) * k, (rb[1] - y0) * k], detail=True)
                e, edge_sigma, ruler_reason = det["edges"], det["sigma"], det["reason"]
                if e:
                    edges = [[[p[0] / k + x0, p[1] / k + y0] for p in ed] for ed in e]
                    partial = bool(det["partial"])
            else:
                ruler_reason = "the photo round the scale line could not be read"
        except Exception as ex:
            print(f"[square_up] ruler edges skipped: {ex}")
            edges, partial = None, False
            ruler_reason = ruler_reason or "the scale line could not be read"
    elif want != "part":
        ruler_reason = "no photo of the ruler was sent"

    def refuse(msg, code=200):
        return jsonify(ok=False, error=msg, partial=partial, ruler_reason=ruler_reason), code

    try:
        sides = None
        try:
            sides = long_sides(ring)
        except SquareUpError:
            if edges is None:
                raise
        res_r = res_p = None
        err_r = None
        if edges is not None:
            try:
                res_r = square_up(edges[0], edges[1], *args, min_converge_deg=0.0)
            except SquareUpError as ex:
                err_r = ex
        if sides is not None and (want == "part" or res_r is None):
            res_p = square_up(sides[0], sides[1], *args)
        if want == "ruler" and res_r is None:
            raise err_r or SquareUpError("Could not find the ruler's edges along the scale line" +
                                         (f" ({ruler_reason})" if ruler_reason else "") +
                                         " — draw it down the middle of the ruler, end to end.")
        if want == "part" and res_p is None:
            raise SquareUpError("This outline has no two long parallel sides to square up from.")
        if res_r is None and res_p is None:
            raise err_r or SquareUpError("Could not square up this photo.")
    except SquareUpError as e:
        return refuse(str(e))
    except Exception as e:
        print(f"[square_up] error: {e}")
        return refuse("Could not square up this photo.", 500)
    if want == "part" or res_r is None:
        res, ref = res_p, "part"
    else:
        res, ref = res_r, "ruler"
    if not all(math.isfinite(v) for row in res["H"] for v in row):
        return refuse("Could not square up this photo.")
    # Edges found that would not square up are not "no edges": say which, so the message
    # does not tell the user to redraw a line that found the ruler fine.
    ruler_found = edges is not None
    if ruler_found and res_r is None and err_r is not None:
        ruler_reason = (f"{ruler_reason}; " if ruler_reason else "") + str(err_r)
    H = res["H"]
    other = None
    if ref == "ruler" and sides is not None:
        other = "part"
    elif ref == "part" and res_r is not None:
        # The ruler is offered whenever squaring up from it worked, one edge or two.
        other = "ruler"
    part = ruler_report(sides, H) if sides is not None else None
    check = ruler_report(edges, H) if edges is not None else None
    if part is not None and ref == "ruler" and partial:
        # One edge was assumed parallel to the other, so the ruler says nothing about a tilt
        # along it, and whatever taper the part's sides show may be that tilt.
        part["taper_note"] = "one ruler edge assumed parallel"
    elif part is not None and ref == "ruler" and edge_sigma:
        # How sure the part's taper is: the ruler is short next to the part, so a small doubt
        # in the angle between its edges grows along the part. Redo it with the edges turned
        # apart and together by one standard error; half the spread is the taper's.
        tapers = []
        for sgn in (1.0, -1.0):
            p0, p1 = np.array(edges[1][0]), np.array(edges[1][1])
            m_ = (p0 + p1) / 2.0
            c_, s_ = math.cos(sgn * edge_sigma), math.sin(sgn * edge_sigma)
            Rm = np.array([[c_, -s_], [s_, c_]])
            e2 = [(m_ + Rm @ (p0 - m_)).tolist(), (m_ + Rm @ (p1 - m_)).tolist()]
            try:
                w_ = ruler_report(sides, square_up(edges[0], e2, *args, min_converge_deg=0.0)["H"])["width_mm"]
                tapers.append(w_[1] - w_[0])
            except SquareUpError:
                pass
        if len(tapers) == 2:
            part["taper_sigma_mm"] = round(abs(tapers[0] - tapers[1]) / 2.0, 2)
    return jsonify(ok=True, reference=ref, other=other, sides=sides, ruler_edges=edges,
                   part=part, ruler_check=check, partial=partial, ruler_reason=ruler_reason,
                   ruler_found=ruler_found, **res)


def _decode_data_url(durl):
    """('data:image/jpeg;base64,...') -> (mimetype, raw_bytes)."""
    header, b64 = durl.split(",", 1)
    mime = ""
    if ":" in header and ";" in header:
        mime = header.split(";")[0].split(":", 1)[1]
    return mime, base64.b64decode(b64)


def _submission_lines(data):
    """Human-readable summary lines shared by the email body and the saved record.

    Everything here is the customer's JSON, and it is read only after the record and images
    are written — so a field of the wrong type must be shown, not crash the request (a
    quantity typed as a number, restored from a saved job, did, and left an orphan record)."""
    def text(v):
        return "" if v is None or isinstance(v, (dict, list)) else str(v).strip()

    def obj(v):
        return v if isinstance(v, dict) else {}
    brief = obj(data.get("brief"))
    lines = ["Part submission — Datum Laboratories", ""]
    for k, label in (("part", "Part"), ("material", "Material"), ("quantity", "Quantity"),
                     ("whatBroke", "What broke / needed"), ("contact", "Contact"), ("notes", "Notes")):
        v = text(brief.get(k))
        if v:
            lines.append(f"{label}: {v}")
    lines.append("")
    views = data.get("views")
    for i, v in enumerate(views if isinstance(views, list) else []):
        v = obj(v)
        scale = obj(v.get("scale"))
        src = text(scale.get("source")) or "no scale"
        if scale.get("perspective"):
            src += " (tilt-corrected)"
        lines.append(f"— {text(v.get('label')) or ('View ' + str(i + 1))}  [{src}]")
        ms = v.get("measurements")
        for m in (ms if isinstance(ms, list) else []):
            if isinstance(m, dict):
                lines.append(f"    {text(m.get('label'))}: {text(m.get('text'))}")
            else:
                lines.append(f"    {text(m)}")
    return lines


def _save_submission(data):
    """Persist a submission (JSON record without the bulky image data URLs + the decoded
    images) under SUBMISSIONS_DIR so nothing is lost even if email isn't configured."""
    import json
    os.makedirs(SUBMISSIONS_DIR, exist_ok=True)
    raw_id = "".join(c for c in str(data.get("id", "")) if c.isalnum() or c in ("-", "_"))[:40]
    sub_id = raw_id or f"job-{uuid.uuid4().hex[:8]}"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = os.path.join(SUBMISSIONS_DIR, f"{stamp}-{sub_id}")
    os.makedirs(out_dir, exist_ok=True)

    images = []
    for i, v in enumerate(data.get("views") or []):
        if not isinstance(v, dict):
            continue
        durl = v.get("image")
        if not durl:
            continue
        try:
            mime, raw = _decode_data_url(durl)
        except Exception:
            continue
        ext = ".jpg" if "jpeg" in (mime or "") else (".png" if "png" in (mime or "") else ".img")
        # Cap the label: an over-long one makes open() raise ENAMETOOLONG, which _save_submission
        # does not catch, so /api/submit loses the WHOLE submission over one view's name. The UI
        # only offers short labels, but a loaded .camscan.json or a direct POST can carry any.
        label = "".join(c for c in str(v.get("label", "view")) if c.isalnum() or c in ("-", "_"))[:60] or f"view{i + 1}"
        fn = f"{i + 1:02d}-{label}{ext}"
        with open(os.path.join(out_dir, fn), "wb") as f:
            f.write(raw)
        images.append((fn, os.path.join(out_dir, fn)))

    meta = {k: val for k, val in data.items() if k != "views"}
    # submission.json stays lean: drop the bulky per-view image AND the reloadable snapshot
    # (raw image + annotations) — those live in job.camscan.json below.
    meta["views"] = [{kk: vv for kk, vv in v.items() if kk not in ("image", "restore")}
                     for v in (data.get("views") or [])]
    with open(os.path.join(out_dir, "submission.json"), "w") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write("\n".join(_submission_lines(data)))

    # Full reloadable job bundle: raw images + calibration + editable annotations, so the
    # part can be re-OPENED in CamScan with every side editable (Load job) — not just
    # re-measured from a photo. Written only when a view actually carries a snapshot.
    bundle_path = None
    try:
        if any((v.get("restore") or {}).get("raw") for v in (data.get("views") or [])):
            bundle = {"kind": "camscan.job", "version": 1,
                      "id": data.get("id"), "createdAt": data.get("createdAt"),
                      "submittedAt": data.get("submittedAt"), "brief": data.get("brief") or {},
                      "views": data.get("views") or []}
            bundle_path = os.path.join(out_dir, "job.camscan.json")
            with open(bundle_path, "w") as f:
                json.dump(bundle, f)
    except Exception as e:
        print(f"[Submit] bundle save failed: {e}")
        bundle_path = None

    return {"dir": out_dir, "images": images, "bundle": bundle_path}


def _submissions_bytes():
    """Total bytes stored under SUBMISSIONS_DIR (0 when it doesn't exist yet).
    Best-effort: an entry we can't stat is skipped rather than failing a submission."""
    total = 0
    for root, _dirs, files in os.walk(SUBMISSIONS_DIR):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def _header_safe(value, limit=200):
    """A single-line, length-capped string safe to place in an e-mail header (strips CR/LF
    and other control characters that could inject headers or break serialization)."""
    s = "".join(ch for ch in str(value or "") if ch == " " or (ch.isprintable() and ch not in "\r\n"))
    return s.strip()[:limit]


def _send_submission_email(data, record):
    """Email the submission to SUBMIT_TO via SMTP with the annotated views attached.
    Raises on failure so the caller can fall back."""
    import smtplib
    from email.message import EmailMessage
    brief = data.get("brief") or {}
    msg = EmailMessage()
    msg["From"] = SUBMIT_FROM or SMTP_USER
    msg["To"] = SUBMIT_TO
    # Customer-supplied fields go into headers — strip CR/LF (and cap length) so a stray
    # newline can't inject headers or make send_message raise and turn a real submission into
    # a reported "couldn't email it" failure.
    contact = _header_safe(brief.get("contact"))
    if contact:
        msg["Reply-To"] = contact
    part = _header_safe(brief.get("part") or data.get("id") or "part") or "part"
    msg["Subject"] = f"CamScan part submission — {part}"
    msg.set_content("\n".join(_submission_lines(data)))
    for fn, path in record.get("images", []):
        try:
            with open(path, "rb") as f:
                raw = f.read()
            subtype = "jpeg" if fn.endswith(".jpg") else ("png" if fn.endswith(".png") else "octet-stream")
            maintype = "image" if subtype in ("jpeg", "png") else "application"
            msg.add_attachment(raw, maintype=maintype, subtype=subtype, filename=fn)
        except Exception:
            pass
    # Attach the reloadable job bundle so you can save it from the e-mail and re-open the
    # part in CamScan (Load job). Guarded by size so a big multi-view job can't bloat the
    # message past typical mailbox limits — it's still saved on the server regardless.
    bundle_path = record.get("bundle")
    if bundle_path:
        try:
            if os.path.getsize(bundle_path) <= 20 * 1024 * 1024:
                with open(bundle_path, "rb") as f:
                    msg.add_attachment(f.read(), maintype="application", subtype="json",
                                       filename="job.camscan.json")
        except Exception:
            pass
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        if SMTP_TLS:
            s.starttls()
        if SMTP_USER:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


@server.route("/api/submit", methods=["POST"])
def submit_part():
    """Receive a multi-view part submission, save it, and email it to Datum (if SMTP is
    configured). Always saves so nothing is lost; returns an honest message either way."""
    data = _json_body()
    views = data.get("views")
    if not isinstance(views, list) or not views:
        return {"ok": False, "error": "No views to submit — add at least one."}, 400
    if not all(isinstance(v, dict) for v in views):
        return {"ok": False, "error": "Malformed submission — each view must be an object."}, 400
    if data.get("brief") is not None and not isinstance(data.get("brief"), dict):
        return {"ok": False, "error": "Malformed submission — the part details must be an object."}, 400

    # Refuse rather than fill the volume (see MAX_SUBMISSIONS_BYTES). Checked before we write
    # anything, so a refused submission leaves no partial record behind, and the customer gets
    # the same "Download summary" fallback the e-mail failure path offers.
    if MAX_SUBMISSIONS_BYTES > 0 and _submissions_bytes() >= MAX_SUBMISSIONS_BYTES:
        print(f"[Submit] submissions dir is at the {MAX_SUBMISSIONS_BYTES:,}-byte cap — refusing. "
              "Archive SUBMISSIONS_DIR or raise MAX_SUBMISSIONS_BYTES.")
        return {"ok": False,
                "error": "The server is out of room for new submissions. "
                         "Please use “Download summary” and email it to us."}, 507

    try:
        record = _save_submission(data)
    except Exception as e:
        # Log the detail server-side; don't echo the raw exception string back to the client.
        print(f"[Submit] save failed: {e}")
        return {"ok": False, "error": "Could not save the submission on the server."}, 500

    if SMTP_HOST:
        try:
            _send_submission_email(data, record)
            return {"ok": True, "message": "Sent to Datum Laboratories — we'll be in touch."}
        except Exception as e:
            print(f"[Submit] email failed: {e}")
            return {"ok": False,
                    "error": "We saved your submission but couldn't email it. "
                             "Please use “Download summary” and email it to us."}, 502

    # No mail transport configured (e.g. before deployment): saved on the server, and the
    # customer still has the downloadable summary. Be honest rather than pretend it sent.
    return {"ok": True,
            "message": "Submission received. (Email delivery isn't configured on this server yet.)"}


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
