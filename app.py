# CamScan — Calibration Exporter (single-page viewer)
import os, base64, uuid, time, tempfile
from flask import Flask, send_from_directory, url_for
from dash import Dash, html, dcc, Input, Output, State, no_update
import cv2, numpy as np
from werkzeug.middleware.proxy_fix import ProxyFix  # NEW: respect X-Forwarded-* behind Caddy
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge

from calibration_core import calibrate_image, save_outputs

APP_PORT = int(os.getenv("PORT", "8059"))
APP_HOST = os.getenv("HOST", "0.0.0.0")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")

# Base URL prefix when served behind Caddy (e.g. /camscan)
CALIB_PREFIX = os.getenv("URL_PREFIX", "/camscan").rstrip("/")
if not CALIB_PREFIX.startswith("/"):
    CALIB_PREFIX = "/" + CALIB_PREFIX

# ---- Explicit load order for overlay JS modules (served via URL_PREFIX, e.g. /camscan/assets) ----
ORDERED_SCRIPTS = [
    f"{CALIB_PREFIX}/assets/calib.units.js",
    f"{CALIB_PREFIX}/assets/calib.geometry.js",
    f"{CALIB_PREFIX}/assets/calib.draw.js",
    f"{CALIB_PREFIX}/assets/calib.annotations.js",
    f"{CALIB_PREFIX}/assets/calib.export.js",
    f"{CALIB_PREFIX}/assets/calib.viewport.js",
    f"{CALIB_PREFIX}/assets/calib.gestures.js",
    f"{CALIB_PREFIX}/assets/calib.ui.enhanced.js",
    f"{CALIB_PREFIX}/assets/calibrationOverlay.js",
]

# Allowed image extensions (lowercase, no leading dot)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}

# Maximum upload size in bytes (default 8 MiB) — override with env MAX_CONTENT_LENGTH_BYTES
MAX_CONTENT_BYTES = int(os.getenv("MAX_CONTENT_LENGTH_BYTES", 8 * 1024 * 1024))


def _resolve_edge_mm_from_env():
    v = os.getenv("CALIB_EDGE_MM")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        print(f"[App] ⚠️ Invalid CALIB_EDGE_MM='{v}', ignoring.")
        return None


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


def _is_allowed_filename(filename: str) -> bool:
    if not filename:
        return False
    name = filename.rsplit('/', 1)[-1].rsplit('\\\\', 1)[-1]
    ext = os.path.splitext(name)[1].lstrip('.').lower()
    return ext in ALLOWED_EXTENSIONS


app = Dash(
    __name__,
    server=server,
    suppress_callback_exceptions=True,
    external_scripts=ORDERED_SCRIPTS,  # guaranteed JS order
    assets_ignore=(
        r'.*\\.ipynb_checkpoints.*'
        r'|calib\\..*\\.js'          # let ORDERED_SCRIPTS control all calib.* JS
        r'|calibrationOverlay\\.js'  # avoid auto-loading overlay twice
        r'|calib\\.ui\\.js$'         # use enhanced version only
    ),
    # Served under /camscan/ when behind Caddy
    requests_pathname_prefix=CALIB_PREFIX + "/",
    routes_pathname_prefix=CALIB_PREFIX + "/",
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
      html,body{margin:0;padding:0;background:#0f0f10;color:#e6e6e6;font-family: Segoe UI, system-ui, sans-serif;}
      .container{max-width:1000px;margin:0 auto;padding:10px;}
      @media (max-width:768px){ .container{padding:8px;} }

      /* Scrollable wrapper for the viewer so tall images don't block the page */
      .cal-wrap{
        position: relative;
        height: 100dvh;             /* fill dynamic viewport height on mobile */
        overflow: auto;
        -webkit-overflow-scrolling: touch; /* iOS momentum scroll */
      }

      /* Keep toolbar visible while scrolling the image (matches JS styling) */
      .cal-toolbar{
        position: sticky;
        top: 0;
        z-index: 10;
        background: #111;
        padding-top: 6px;
        border-bottom: 1px solid #2a2a2a;
      }

      /* Make the upload zone a bit shorter on phones */
      @media (max-width:768px){
        #uploader{height:96px !important; line-height:96px !important;}
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


def _decode_b64_image(contents: str):
    header, b64data = contents.split(",", 1)
    img_bytes = base64.b64decode(b64data)
    np_arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    return img


app.layout = html.Div([
    html.Div([
        html.H2("📸 CamScan — Calibration Exporter"),
        dcc.Upload(
            id="uploader",
            children=html.Div(["Tap ", html.B("to snap a photo"), " or drop an image"]),
            multiple=False,
            style={
                "width": "100%", "height": "120px", "lineHeight": "120px", "borderWidth": "2px",
                "borderStyle": "dashed", "borderRadius": "8px", "textAlign": "center", "margin": "10px 0"
            },
            accept="image/*",
        ),
        html.Div(id="status", style={"margin": "8px 0"}),
    ], id="top-panel"),
    html.Div(id="viewer", style={"position": "relative"}),
    html.Div(id="cal-kpi", className="cal-kpi"),
    html.Div("", style={"height": "12px"})
], style={"maxWidth": "1000px", "margin": "0 auto", "fontFamily": "Segoe UI, sans-serif"})


@app.callback(
    Output("status", "children"),
    Output("viewer", "children"),
    Output("top-panel", "style"),
    Input("uploader", "contents"),
    State("uploader", "filename"),
    prevent_initial_call=True
)
def on_upload(contents, filename):
    if not contents:
        return "⚠️ No file.", no_update, no_update

    # Basic validation of filename extension
    if not _is_allowed_filename(filename):
        return "⚠️ Unsupported file type.", no_update, no_update

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stem = os.path.splitext(filename or "image")[0]
    safe = "".join(c for c in stem if c.isalnum() or c in ("-", "_")).strip("_") or "image"
    out_name = f"{safe}-{uuid.uuid4().hex[:8]}.jpg"

    img = _decode_b64_image(contents)
    if img is None:
        return "⚠️ Uploaded file is not a valid image.", no_update, no_update

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
        return f"⚠️ Failed to save upload: {e}", no_update, no_update

    edge_mm_env = _resolve_edge_mm_from_env()
    if edge_mm_env is not None:
        print(f"[App] Using CALIB_EDGE_MM from env: {edge_mm_env} mm")
    else:
        print("[App] No CALIB_EDGE_MM set; using calibration_core module default")

    try:
        cal, overlay = calibrate_image(img, edge_mm=edge_mm_env)
    except Exception as e:
        return f"⚠️ Processing error: {e}", no_update, no_update

    json_path, _ = save_outputs(out_name, cal, overlay, UPLOAD_DIR)

    ts = int(time.time() * 1000)
    img_url_data = contents
    img_url_file = url_for("downloads", fname=out_name, v=ts)
    base, _ext = os.path.splitext(out_name)
    json_url = url_for("downloads", fname=f"{base}.calibration.json", v=ts)

    viewer = html.Div([
        html.Div([
            html.Div(id="cal-toolbar", className="cal-toolbar"),
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
    status = (
        f"✅ Processed '{filename}' — {len(cal.get('markers', []))} marker(s). "
        f"Marker size: {marker_mm} mm. Tap/click to annotate."
    )
    return status, viewer, {"display": "none"}


@server.route(f"{CALIB_PREFIX}/uploads/<path:fname>")
def downloads(fname):
    return send_from_directory(UPLOAD_DIR, fname, as_attachment=False)


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
