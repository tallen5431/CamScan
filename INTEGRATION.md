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

### Embedding it on the replacement-parts page

The Datum Labs site embeds CamScan in a full-screen overlay on `/replacement-parts` rather
than sending the customer to another site. The page's `#measure` button opens an `<iframe>`
pointing at your hosted CamScan URL (set `MEASURE_URL` in `site/replacement-parts.html`;
today it's `https://measure.datumlaboratories.com`). The iframe URL carries `?embed=1` so
CamScan can tell it's embedded. Modifier-click still opens CamScan in its own tab, and with
JavaScript off the button falls back to the quote form — so the page is always safe to ship.

For a browser to *allow* that embed, CamScan must permit the parent site to frame it. CamScan
now sends a `Content-Security-Policy: frame-ancestors` header that allows **only** the Datum
Labs site (and blocks clickjacking from anywhere else):

| Variable          | Default                                                              | Purpose                                            |
|-------------------|---------------------------------------------------------------------|----------------------------------------------------|
| `FRAME_ANCESTORS` | `'self' https://datumlaboratories.com https://*.datumlaboratories.com` | Who may iframe CamScan (CSP `frame-ancestors`)     |

- The default lets `datumlaboratories.com` and any `*.datumlaboratories.com` subdomain frame
  the tool; every other site is refused. Top-level use at `measure.datumlaboratories.com` is
  unaffected — the directive governs *framing* only.
- **Testing the embed locally?** Append your dev origin, e.g.
  `FRAME_ANCESTORS="'self' https://datumlaboratories.com https://*.datumlaboratories.com http://localhost:*"`.
- Set `FRAME_ANCESTORS=''` to send no framing header at all, or `'self'`/`'none'` to lock it
  to same-origin / block all framing (those two also set the legacy `X-Frame-Options`).
- If a reverse proxy or Cloudflare in front of CamScan injects its own `X-Frame-Options:
  SAMEORIGIN`/`DENY`, the embed will be blocked regardless of this header — strip that at the
  proxy so only CamScan's `frame-ancestors` policy applies.

## 2. Submissions ("Send to Datum")

When a customer taps **Submit to Datum**, the browser POSTs the job (annotated view images
+ measurements + a short part brief) to `POST /api/submit`. The server saves it and, if SMTP
is configured, emails it to you.

**CAD-ready data travels with every view.** Each submitted view carries, besides the annotated
image, the data that makes it fast to model: a **DXF** (mm, tilt-corrected, layered — generated
client-side so no backend is needed), a **dimension CSV**, and the structured **geometry** (mm,
CAD Y-up — the same input a solid generator needs). The **Outline** tool (trace around a flat
part and close the loop) adds a **closed, extrudable profile** to that DXF — for a flat part
that profile plus a thickness is the whole model, no CAD tracing on your end. **Auto-trace**
(the ✨ button / simple-mode "Trace outline") takes it further: tap once on the part and the
server segments it (`/api/trace` → `auto_outline.py`) into an editable outline you nudge if
needed — a near-one-tap trace instead of clicking every vertex. The site archives the DXF/CSV and a combined
`measurements.json` to R2 alongside the photos, and the notification email attaches them and shows
a dimension table per view. So a quote request arrives as importable geometry + numbers, not just
a photo to re-measure.

**Embedded on the site (the usual case):** when CamScan runs inside the replacement-parts
embed (`?embed=1`), "Submit" instead reads **"Add to quote →"** and hands the finished views
straight to the page's quote form via `postMessage` — so the whole job goes through the site's
one `/api/quote` pipeline (Resend + R2 + KV) rather than a second one. The two sides do an
origin-checked handshake first (the page only accepts the job from CamScan's exact origin, and
CamScan only talks to `*.datumlaboratories.com`). If the page never acknowledges — an older
page, or CamScan opened standalone — CamScan **falls back to `/api/submit`** below, so a
submission is never lost. This means CamScan needs no SMTP config to work as an embedded intake
tool; SMTP only matters for the standalone/fallback path.

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

- `markers/camscan-marker-40mm.svg` / `.png` — **recommended** (see size note below).
- `markers/camscan-marker-30mm.*`, `markers/camscan-marker-60mm.*` — other sizes.

Regenerate at any size with `python tools/make_marker.py <edge_mm>`; the card auto-sizes to
fit (and stays within a letter envelope). **Set the app to match your chosen marker** so
scale is correct out of the box: `CALIB_EDGE_MM=40` (or change `EDGE_MM_DEFAULT` in
`calibration_core.py`).

**Ideal size: ~40 mm.** It's the sweet spot for a customer-facing tool:
- Detects at **high confidence** with ≤~1% scale error from a small part (≈200 mm scene)
  to a large one (≈550 mm) in testing — more pixel headroom than 30 mm for tilted/dim/glary
  phone photos, the case that actually matters.
- Small enough to lay flat beside small parts and to reposition on each face for the
  multi-view (top/front/side) shots.
- Its card (~103 × 64 mm) fits a standard letter envelope with room for the verification bar.

Go **~60 mm** (card ~143 × 84 mm, still envelope-friendly) if your parts routinely exceed
~250 mm — a bigger marker gives a longer scale baseline across a large face. Below ~30 mm
starts to get marginal on imperfect photos. One marker per photo is correct; scattering
several loose markers into one shot adds friction without helping the per-view workflow.

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
