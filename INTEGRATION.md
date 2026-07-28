# CamScan — website integration & intake flow

This branch turns CamScan into a customer-facing **intake tool**: a customer photographs a
broken part (with a reference for scale), measures it, and submits several views to Datum
Laboratories in one go. This doc covers how to deploy it into your website and configure
the pieces.

## 1. Hosting it on your site

CamScan is a Flask/Dash app and is already built to run behind a reverse proxy at a path
prefix (`ProxyFix` in `app.py`). The simplest integration:

- Host it at a subdomain, e.g. `tool.datumlaboratories.com`, or serve it under a path
  (e.g. `datumlaboratories.com/measure/`) behind your reverse proxy, and link/iframe it.
- **HTTPS is required.** The "take a photo" flow uses the camera, and browsers only grant
  camera access over a secure context. Over plain HTTP the capture button gets no camera.
- Brand it by editing the landing card and accent colour in `app.py` (`--cal-accent`).

Run: `python app.py` (default `PORT=8059`). Put it behind Caddy/nginx with TLS.

## 2. Submissions ("Send to Datum")

When a customer taps **Submit to Datum**, the browser POSTs the job (annotated view images
+ measurements + a short part brief) to `POST /api/submit`. The server saves it and, if SMTP
is configured, emails it to you.

Configure with environment variables:

| Variable      | Default                              | Purpose                                   |
|---------------|--------------------------------------|-------------------------------------------|
| `SUBMIT_TO`   | `thomas.allen@datumlaboratories.com` | Where submissions are emailed             |
| `SUBMIT_FROM` | (falls back to `SMTP_USER`)          | From address                              |
| `SMTP_HOST`   | (unset)                              | SMTP relay host — **set this to send**    |
| `SMTP_PORT`   | `587`                                | SMTP port                                 |
| `SMTP_USER`   | (unset)                              | SMTP username                             |
| `SMTP_PASS`   | (unset)                              | SMTP password                             |
| `SMTP_TLS`    | `1`                                  | STARTTLS (set `0` to disable)             |

- **With SMTP set:** each submission is emailed to `SUBMIT_TO` with the annotated views
  attached and the customer's address as `Reply-To`, so you can reply directly.
- **Without SMTP (e.g. before you wire up mail):** the submission is still saved on the
  server under `submissions/<timestamp-id>/` (annotated images + `submission.json` +
  `summary.txt`), and the customer can use **Download summary** to email you a self-contained
  HTML packet. The flow works either way; the confirmation message is honest about which
  happened.

Example (using your mail provider's SMTP):
```
SUBMIT_TO=thomas.allen@datumlaboratories.com \
SMTP_HOST=smtp.yourprovider.com SMTP_PORT=587 \
SMTP_USER=intake@datumlaboratories.com SMTP_PASS=... \
python app.py
```

`submissions/` is git-ignored. It grows with each submission — back it up / prune per your
retention policy. Customer photos are customer data; add a short retention note to your site.

## 3. The mailed calibration marker (recommended)

The most reliable, zero-tap reference is a **rigid card with the calibration pattern
printed on it** — CamScan auto-detects it (high confidence) and derives both scale and a
perspective (tilt) correction. Ready-to-print files are in `markers/`:

- `markers/camscan-marker-30mm.svg` — **print master** (vector, exact millimetres).
- `markers/camscan-marker-30mm.png` — preview / quick print (300 DPI).

Regenerate at another size with `python tools/make_marker.py <edge_mm>` (the app's default
is 30 mm — match it, or set `CALIB_EDGE_MM`).

**Printing & mailing:**
- Print **at 100% / actual size** (turn OFF "fit to page"). The card has a verification bar
  the customer (and you) can check against a ruler — this defeats the "printers silently
  rescale" trap.
- Print on **cardstock**, or laminate / glue to a rigid card so it stays flat. Rigidity +
  keeping it **flat and in the same plane as the measured feature** are what make it accurate.
- A business-card-sized card fits a standard envelope — cheap to mail, and you can print a
  sheet of them.

Why a printed pattern over "any card": a reference only sets scale if its **size is known**.
The printed square has a known edge *and* the high-contrast pattern CamScan detects
automatically, so the customer doesn't tap corners. (A standard credit/ID card — ISO ID-1,
85.60 × 53.98 mm — also works as a known-size reference via the manual Paper/known-length
tools, but it isn't auto-detected.)

## 4. What one photo can and can't measure (set expectations)

A single photo + a flat reference measures accurately only **in the plane of the reference**.
A feature standing above it (the top face of a thick part) reads a few percent large
(parallax). That's why the intake flow collects **multiple orthographic views** (Top / Front
/ Side) — each scaled in its own plane — which is what a modeller needs to CAD a replacement
without a full 3D scan. For genuinely freeform parts, collect the raw multi-view photos for
your-side photogrammetry (turntable + fixed camera + the scale marker).
