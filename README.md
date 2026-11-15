# BoundaryLab Studio

Live edge segmentation with a laptop dashboard, phone camera stream, image-processing controls, Under the Hood stages, and a saved gallery.

## Run locally on Windows

Open PowerShell in this project folder and run each command separately:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Open `http://localhost:8501/laptop`. The phone camera page is `/cam` and the mobile page is `/mobile`.

To start a temporary Cloudflare Quick Tunnel as well, run:

```powershell
.venv\Scripts\python.exe main.py --quick-tunnel
```

The generated `trycloudflare.com` URL is temporary. Phone camera access requires HTTPS; use that HTTPS tunnel URL on the phone.

## Preserve the sample and gallery

The dashboard sample video is `frontend/static/media/sample.mp4`. Saved gallery images and the SQLite index are in `data/`. Keep the existing files in both locations when updating the project, and back up `data/` to preserve captures.

## Main processing code

| File | Responsibility |
|---|---|
| `backend/processor.py` | OpenCV pipeline: grayscale, contrast, smoothing, thresholds, edges, morphology, contours, and output styling |
| `backend/styles.py` | Output styles, including Blueprint, Coloring book, Neon glow, and Drawing guide |
| `backend/vectorizer.py` | Converts processed contours into SVG paths |
| `main.py` | FastAPI routes, camera and video streams, capture/gallery handling, account sessions, and live processing WebSockets |
| `frontend/static/js/client.js` | Browser camera/video/image sources, processing controls, stream connection, and gallery UI |
| `frontend/templates/` | Dashboard, phone, camera, login, registration, and admin pages |
