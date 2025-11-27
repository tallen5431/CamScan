# CamScan — Calibration Exporter (single-page viewer)
import os, io, base64, uuid
from flask import Flask, send_from_directory
from dash import Dash, html, dcc, Input, Output, State, no_update
import cv2, numpy as np

from calibration_core import calibrate_image, save_outputs

APP_PORT = int(os.getenv("PORT", "8059"))
APP_HOST = os.getenv("HOST", "0.0.0.0")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")

server = Flask(__name__)
app = Dash(__name__, server=server, suppress_callback_exceptions=True)
app.title = "CamScan — Calibration Exporter"

def _decode_b64_image(contents: str):
    header, b64data = contents.split(",", 1)
    img_bytes = base64.b64decode(b64data)
    np_arr = np.frombuffer(img_bytes, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    return img

app.layout = html.Div([
    html.H2("📸 CamScan — Calibration Exporter"),
    dcc.Upload(
        id="uploader",
        children=html.Div(["Drag & drop or ", html.B("click to upload a photo")]),
        multiple=False,
        style={
            "width":"100%","height":"120px","lineHeight":"120px","borderWidth":"2px",
            "borderStyle":"dashed","borderRadius":"8px","textAlign":"center","margin":"10px 0"
        },
        accept="image/*"
    ),
    html.Div(id="status", style={"margin":"8px 0"}),
    # The viewer (canvas + toolbar) will be rendered right here after upload
    html.Div(id="viewer", style={"position":"relative"}),
    html.Div("", style={"height":"12px"})
], style={"maxWidth":"1000px","margin":"0 auto","fontFamily":"Segoe UI, sans-serif"})

@app.callback(
    Output("status","children"),
    Output("viewer","children"),
    Input("uploader","contents"),
    State("uploader","filename"),
    prevent_initial_call=True
)
def on_upload(contents, filename):
    if not contents:
        return "⚠️ No file.", no_update

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    stem = os.path.splitext(filename)[0]
    safe = "".join(c for c in stem if c.isalnum() or c in ("-","_")).strip("_") or "image"
    out_name = f"{safe}-{uuid.uuid4().hex[:8]}.jpg"

    img = _decode_b64_image(contents)
    cv2.imwrite(os.path.join(UPLOAD_DIR, out_name), img)

    cal, overlay = calibrate_image(img, edge_mm=47.5)
    json_path, overlay_path = save_outputs(out_name, cal, overlay, UPLOAD_DIR)

    img_url = f"/uploads/{out_name}"
    base, _ = os.path.splitext(out_name)
    json_url = f"/uploads/{base}.calibration.json"

    viewer = html.Div([
        html.Div(id="cal-toolbar", className="cal-toolbar"),
        html.Canvas(id="cal-canvas", style={"display":"block", "margin":"0 auto"})
    ], id="cal-view", className="cal-view", **{"data-img": img_url, "data-json": json_url},
       style={"textAlign":"center"})

    status = f"✅ Processed '{filename}' — {len(cal.get('markers',[]))} marker(s) found. Click two points to measure."
    return status, viewer

@server.route("/uploads/<path:fname>")
def downloads(fname):
    return send_from_directory(UPLOAD_DIR, fname, as_attachment=False)

if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT, debug=False)
