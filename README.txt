
CamScan — Calibration Exporter (Dash)

1) pip install -r requirements.txt
   - or run StartApp.bat on Windows (creates venv, installs deps, launches)
2) python app.py
3) Open http://127.0.0.1:8059 (also on LAN via your host IP:8059)
4) Upload an image with 47.5 mm squares; preview shows overlay; click "Open interactive viewer" to measure.

Artifacts in /uploads:
  - <name>.jpg               original upload
  - <name>_overlay.jpg       overlay preview
  - <name>.calibration.json  calibration data for JS viewer
