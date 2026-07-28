# CamScan — Calibration Exporter (single-page viewer)
import os, base64, uuid, time, tempfile

# Cap the pixels OpenCV will decode BEFORE importing cv2 (the env var is read at import
# time). A tiny, highly-compressible upload can otherwise decode to hundreds of megapixels
# and OOM-kill the process — a classic decompression bomb. 50 MP comfortably covers real
# phone/DSLR photos while refusing pathological inputs. Override with MAX_IMAGE_PIXELS.
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", 50 * 1000 * 1000))
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(MAX_IMAGE_PIXELS))

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

# The overlay JS modules live in ./assets and are served + injected automatically by Dash.
# Dash prefixes their URLs correctly when running behind a reverse proxy with a path prefix
# (see ProxyFix below), so we do NOT hard-code "/assets/..." script tags. The modules are
# order-independent: each publishes a `window.Calib*` global and looks its dependencies up
# lazily, and calibrationOverlay.js waits for every dependency before booting.


# Allowed image extensions (lowercase, no leading dot)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}

# Maximum upload size in bytes (default 8 MiB) — override with env MAX_CONTENT_LENGTH_BYTES
MAX_CONTENT_BYTES = int(os.getenv("MAX_CONTENT_LENGTH_BYTES", 8 * 1024 * 1024))

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
        entries.sort(key=lambda e: e[1], reverse=True)  # newest first

        now = time.time()
        age_limit = MAX_UPLOAD_AGE_HOURS * 3600 if MAX_UPLOAD_AGE_HOURS > 0 else None
        for idx, (path, mtime) in enumerate(entries):
            too_many = MAX_UPLOAD_FILES > 0 and idx >= MAX_UPLOAD_FILES
            too_old = age_limit is not None and (now - mtime) > age_limit
            if too_many or too_old:
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
    header, b64data = contents.split(",", 1)
    img_bytes = base64.b64decode(b64data)
    np_arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    # Defense in depth: OPENCV_IO_MAX_IMAGE_PIXELS makes imdecode return None on an
    # over-cap image, but re-check explicitly so oversized inputs are rejected with a
    # clear message rather than a generic "not a valid image".
    if img is not None:
        h, w = img.shape[:2]
        if w * h > MAX_IMAGE_PIXELS:
            raise ImageTooLarge(f"{w}x{h} = {w * h:,}px exceeds cap {MAX_IMAGE_PIXELS:,}")
    return img


app.layout = html.Div([
    html.Div([
        html.Div([
            html.Div("📸", className="landing-logo"),
            html.Div("CamScan", className="landing-title"),
            html.P("Measure real-world millimeters from a photo.", className="landing-sub"),
            html.Div([
                html.B("No printer needed."), " Lay a coin, a card, or a sheet of paper "
                "flat beside your part for scale, then ", html.B("measure in real mm"),
                ". A printed calibration square works too and scales automatically.",
            ], className="landing-how"),
            html.Div(
                "Lay the paper on the same flat surface as the part and measure features "
                "that sit flat on it — a raised top face reads slightly large. One photo "
                "captures the front outline and holes; a full 3D quote may need a few "
                "angles, shot flat-on for best accuracy.",
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
], id="landing-root", style={"fontFamily": "Segoe UI, sans-serif"})


# After the first photo, tuck the landing/upload card off-screen (not display:none)
# so the "Add another photo" button can re-open the file picker WITHOUT a page
# reload — the multi-photo, one-at-a-time submission flow depends on it.
_UPLOADER_TUCKED = {"position": "fixed", "left": "-9999px", "top": "0",
                    "width": "1px", "height": "1px", "overflow": "hidden", "opacity": "0"}


@app.callback(
    Output("status", "children"),
    Output("viewer", "children"),
    Output("top-panel", "style"),
    Output("uploader", "contents"),  # Reset upload to allow new uploads
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
        return "⚠️ Unsupported file type.", no_update, no_update, None

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stem = os.path.splitext(filename or "image")[0]
    safe = "".join(c for c in stem if c.isalnum() or c in ("-", "_")).strip("_") or "image"
    out_name = f"{safe}-{uuid.uuid4().hex[:8]}.jpg"

    # Decode defensively: a malformed data-URI (missing comma, bad base64 padding)
    # would otherwise raise inside the callback and surface as an opaque HTTP 500.
    try:
        img = _decode_b64_image(contents)
    except ImageTooLarge:
        return (f"⚠️ Image is too large (over {MAX_IMAGE_PIXELS:,} pixels). "
                "Resize it and try again."), no_update, no_update, None
    except Exception:
        return "⚠️ Uploaded file is not a valid image.", no_update, no_update, None
    if img is None:
        return "⚠️ Uploaded file is not a valid image.", no_update, no_update, None

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
        return f"⚠️ Failed to save upload: {e}", no_update, no_update, None

    edge_mm_env = _resolve_edge_mm_from_env()
    if edge_mm_env is not None:
        print(f"[App] Using CALIB_EDGE_MM from env: {edge_mm_env} mm")
    else:
        print("[App] No CALIB_EDGE_MM set; using calibration_core module default")

    try:
        cal, overlay = calibrate_image(img, edge_mm=edge_mm_env)
    except Exception as e:
        return f"⚠️ Processing error: {e}", no_update, no_update, None

    try:
        json_path, _ = save_outputs(out_name, cal, overlay, UPLOAD_DIR)
    except Exception as e:
        return f"⚠️ Failed to save results: {e}", no_update, no_update, None

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
    if n_markers == 0 or confidence == "none":
        status = (
            f"⚠️ Loaded '{filename}', but no calibration square was found — "
            "measurements would be in pixels, not mm. Use the Set Scale tool (📐): draw a "
            "line across something of known size (a coin, a card, a sheet of paper, or a "
            "ruler) and type its real length."
        )
        return status, viewer, _UPLOADER_TUCKED, None

    status = (
        f"✅ Processed '{filename}' — {n_markers} marker(s). "
        f"Marker size: {marker_mm} mm. Tap/click to annotate."
    )
    # Warn when the auto-calibration had to fall back to a rough estimate, so the
    # user knows to double-check the scale rather than trusting a wrong value.
    if confidence == "low":
        status += " ⚠️ Auto-calibration is approximate — verify with the Set Scale tool (📐)."
    return status, viewer, _UPLOADER_TUCKED, None  # Reset upload for next file


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
    from flask import request, Response

    try:
        from circle_detection import export_to_dxf
    except ImportError:
        return "DXF export requires ezdxf: pip install ezdxf", 500

    data = request.get_json(silent=True) or {}
    geometry = data.get("geometry", [])
    if not isinstance(geometry, list) or not geometry:
        return "No geometry provided", 400

    try:
        mm_per_px = float(data.get("mm_per_px", 1.0)) or 1.0
    except (TypeError, ValueError):
        mm_per_px = 1.0

    # Image height (pixels) lets us flip the Y axis: image coordinates grow downward,
    # CAD coordinates grow upward. Without this the exported part comes out mirrored.
    try:
        image_height = float(data.get("image_height", 0)) or None
    except (TypeError, ValueError):
        image_height = None

    dxf_fd, dxf_path = tempfile.mkstemp(suffix=".dxf", dir=UPLOAD_DIR)
    os.close(dxf_fd)
    try:
        ok = export_to_dxf(geometry, dxf_path, mm_per_px, image_height_px=image_height)
        if not ok:
            return "DXF export failed", 500
        with open(dxf_path, "rb") as f:
            payload = f.read()
    except Exception as e:
        print(f"[DXF Export] Error: {e}")
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}", 500
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


def _decode_data_url(durl):
    """('data:image/jpeg;base64,...') -> (mimetype, raw_bytes)."""
    header, b64 = durl.split(",", 1)
    mime = ""
    if ":" in header and ";" in header:
        mime = header.split(";")[0].split(":", 1)[1]
    return mime, base64.b64decode(b64)


def _submission_lines(data):
    """Human-readable summary lines shared by the email body and the saved record."""
    import datetime
    brief = data.get("brief") or {}
    lines = ["Part submission — Datum Laboratories", ""]
    for k, label in (("part", "Part"), ("material", "Material"), ("quantity", "Quantity"),
                     ("whatBroke", "What broke / needed"), ("contact", "Contact"), ("notes", "Notes")):
        v = (brief.get(k) or "").strip()
        if v:
            lines.append(f"{label}: {v}")
    lines.append("")
    for i, v in enumerate(data.get("views") or []):
        scale = v.get("scale") or {}
        src = scale.get("source") or "no scale"
        if scale.get("perspective"):
            src += " (tilt-corrected)"
        lines.append(f"— {v.get('label') or ('View ' + str(i + 1))}  [{src}]")
        for m in (v.get("measurements") or []):
            lines.append(f"    {m.get('label', '')}: {m.get('text', '')}")
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
        durl = v.get("image")
        if not durl:
            continue
        try:
            mime, raw = _decode_data_url(durl)
        except Exception:
            continue
        ext = ".jpg" if "jpeg" in (mime or "") else (".png" if "png" in (mime or "") else ".img")
        label = "".join(c for c in str(v.get("label", "view")) if c.isalnum() or c in ("-", "_")) or f"view{i + 1}"
        fn = f"{i + 1:02d}-{label}{ext}"
        with open(os.path.join(out_dir, fn), "wb") as f:
            f.write(raw)
        images.append((fn, os.path.join(out_dir, fn)))

    meta = {k: val for k, val in data.items() if k != "views"}
    meta["views"] = [{kk: vv for kk, vv in v.items() if kk != "image"} for v in (data.get("views") or [])]
    with open(os.path.join(out_dir, "submission.json"), "w") as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write("\n".join(_submission_lines(data)))
    return {"dir": out_dir, "images": images}


def _send_submission_email(data, record):
    """Email the submission to SUBMIT_TO via SMTP with the annotated views attached.
    Raises on failure so the caller can fall back."""
    import smtplib
    from email.message import EmailMessage
    brief = data.get("brief") or {}
    msg = EmailMessage()
    msg["From"] = SUBMIT_FROM or SMTP_USER
    msg["To"] = SUBMIT_TO
    contact = (brief.get("contact") or "").strip()
    if contact:
        msg["Reply-To"] = contact
    part = (brief.get("part") or data.get("id") or "part").strip()
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
    from flask import request
    data = request.get_json(silent=True) or {}
    views = data.get("views")
    if not isinstance(views, list) or not views:
        return {"ok": False, "error": "No views to submit — add at least one."}, 400

    try:
        record = _save_submission(data)
    except Exception as e:
        print(f"[Submit] save failed: {e}")
        return {"ok": False, "error": f"Could not save the submission: {e}"}, 500

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
