# EdgeSeg Studio

Real-time edge segmentation, drawing guide, wireless phone camera, and one-tap SVG export. Python (FastAPI + OpenCV) backend, browser frontend.

## Run

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

On startup, the app launches the bundled `bin/cloudflared.exe` and creates a temporary Cloudflare Quick Tunnel. The QR code and **Connect Phone** link update to its HTTPS `/cam` URL as soon as Cloudflare provides it. Keep the terminal open while using the tunnel. To run without Cloudflare, use `python main.py --no-cloudflare`; to use a named tunnel, set `CLOUDFLARE_TUNNEL_URL` to its public origin before starting the app.

### URLs

- **Command Center (Laptop):** `http://localhost:8501/laptop`
- **Phone Dashboard (Mockup):** `http://localhost:8501/mobile`
- **Wireless Camera (Phone):** scan the QR code displayed on the dashboard, or open the Cloudflare HTTPS URL printed in the terminal.
  - Local fallback: `http://<your-lan-ip>:8501/cam` (available with `--no-cloudflare`)

### Phone Camera Setup

1. Open `http://localhost:8501/laptop` or `/mobile` on your computer.
2. Click **Connect Phone** or click **Phone camera** to display the QR code.
3. Scan the QR code with your phone camera to open `/cam`.
4. Allow camera permissions:
   - On the first connection to the local HTTPS server, tap **Advanced** &rarr; **Proceed** (Chrome) or **Show Details** &rarr; **visit this website** (Safari).
5. What the camera sees is immediately displayed in real time on your computer dashboard under the Phone version (`/mobile`) and in the Command center (`/laptop`)!

## Layout

| Path | Role |
|---|---|
| `backend/processor.py` | Grayscale, auto contrast, bilateral filter, Sobel, Canny, small-fragment clean-up |
| `backend/styles.py` | Classic, Blueprint, Coloring book, Neon glow, Drawing guide, proportion grid |
| `backend/vectorizer.py` | Contours to simplified SVG paths |
| `frontend/static/js/client.js` | Sources (sample video, camera, phone stream, upload, photo), pause, WebSockets, gallery |
| `frontend/templates/` | `index.html`, `laptop.html`, `mobile.html`, `cam.html` |
| `frontend/static/media/sample.mp4` | Sample clip (640 px wide) |

## API

`WS /ws/stream` (dashboard frames & phone subscription) · `WS /ws/camera/feed` (phone camera video feed) · `WS /ws/gallery` (live gallery updates) · `POST /api/capture` · `POST /api/svg` · `POST /api/camera/photo` · `GET /api/gallery`
