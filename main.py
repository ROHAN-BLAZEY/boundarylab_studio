
import argparse
import asyncio
import base64
from io import BytesIO
import json
import os
import re
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import qrcode

from backend import processor, vectorizer
from backend.styles import STYLE_NAMES

ROOT = Path(__file__).parent
FRONT = ROOT / "frontend"
DATA = ROOT / "data" / "gallery"
DATA.mkdir(parents=True, exist_ok=True)
PORT = 8501
PUBLIC_TUNNEL_URL = os.environ.get("CLOUDFLARE_TUNNEL_URL", "").rstrip("/")

app = FastAPI(title="EdgeSeg Studio")
app.mount("/static", StaticFiles(directory=FRONT / "static"), name="static")
templates = Jinja2Templates(directory=FRONT / "templates")
pool = ThreadPoolExecutor(max_workers=2)


def lan_url(scheme="http"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return f"{scheme}://{ip}:{PORT}"


def camera_url(request: Request):
    if PUBLIC_TUNNEL_URL:
        return f"{PUBLIC_TUNNEL_URL}/cam"
    scheme = "https" if request.url.scheme == "https" else "http"
    return f"{lan_url(scheme)}/cam"


def cloudflare_output(reader):
    global PUBLIC_TUNNEL_URL
    for line in reader:
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line, re.IGNORECASE)
        if match:
            PUBLIC_TUNNEL_URL = match.group(0).rstrip("/")
            print(f"\n  Cloudflare camera URL: {PUBLIC_TUNNEL_URL}/cam\n", flush=True)
        elif line.strip():
            print(f"[cloudflared] {line.rstrip()}", flush=True)


def start_cloudflare_tunnel(port: int):
    executable = ROOT / "bin" / "cloudflared.exe"
    if not executable.exists():
        executable = ROOT / "bin" / "cloudflared"
    if not executable.exists():
        print("Cloudflare tunnel unavailable: cloudflared was not found in bin/.", flush=True)
        return None
    try:
        process = subprocess.Popen(
            [str(executable), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except OSError as exc:
        print(f"Cloudflare tunnel could not start: {exc}", flush=True)
        return None
    threading.Thread(target=cloudflare_output, args=(process.stdout,), daemon=True).start()
    return process


def qr_data_url(value: str):
    image = qrcode.make(value)
    output = BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def page(name):
    async def view(request: Request):
        cam_url = camera_url(request)
        return templates.TemplateResponse(request, name, {
            "styles": STYLE_NAMES,
            "lan_url": lan_url("https" if request.url.scheme == "https" else "http"),
            "cam_url": cam_url,
            "cam_url_https": f"{PUBLIC_TUNNEL_URL}/cam" if PUBLIC_TUNNEL_URL else f"{lan_url('https')}/cam",
            "qr_code": qr_data_url(cam_url),
        })
    return view


app.get("/")(page("index.html"))
app.get("/laptop")(page("laptop.html"))
app.get("/mobile")(page("mobile.html"))
app.get("/cam")(page("cam.html"))


@app.get("/api/status")
async def status(request: Request):
    cam_url = camera_url(request)
    return {"cam_url": cam_url, "qr_code": qr_data_url(cam_url), "tunnel_ready": bool(PUBLIC_TUNNEL_URL)}


def decode(data: bytes):
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("bad image")
    return img


def decode_data_url(s: str):
    return decode(base64.b64decode(s.split(",", 1)[-1]))


# ---------------------------------------------------------------- live stream
@app.websocket("/ws/stream")
async def stream(ws: WebSocket):
    """Client sends JSON text (params) and JPEG bytes (frames); server replies with the styled frame."""
    await ws.accept()
    params = processor.Params()
    loop = asyncio.get_running_loop()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("text"):
                d = json.loads(msg["text"])
                params = processor.Params.from_dict(d)
                params.stages = bool(d.get("stages"))
            elif msg.get("bytes"):
                try:
                    res = await loop.run_in_executor(pool, lambda b=msg["bytes"], p=params: (processor.run(decode(b), p)))
                except ValueError:
                    continue
                reply = {"out": processor.to_data_url(res.out), "w": res.out.shape[1], "h": res.out.shape[0]}
                if getattr(params, "stages", False):
                    reply["stages"] = [processor.to_data_url(res.stages[k], 60) for k in ("gray", "bilateral", "sobel", "canny")]
                await ws.send_text(json.dumps(reply))
    except WebSocketDisconnect:
        pass


# -------------------------------------------------------------------- gallery
GALLERY = []
viewers = set()


class FrameIn(BaseModel):
    image: str                  # data URL (jpeg/png)
    params: dict = {}
    source: str = "laptop"      # "laptop" | "phone"


@app.post("/api/svg")
async def svg(body: FrameIn):
    p = processor.Params.from_dict(body.params)
    res = await asyncio.get_running_loop().run_in_executor(pool, processor.run, decode_data_url(body.image), p)
    text = vectorizer.edges_to_svg(res.edges, p.style, p.thickness)
    return Response(text, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'attachment; filename="edgeseg.svg"'})


@app.post("/api/capture")
async def capture(body: FrameIn):
    p = processor.Params.from_dict(body.params)
    res = await asyncio.get_running_loop().run_in_executor(pool, processor.run, decode_data_url(body.image), p)
    uid = uuid.uuid4().hex[:10]
    cv2.imwrite(str(DATA / f"{uid}.png"), res.out)
    (DATA / f"{uid}.svg").write_text(vectorizer.edges_to_svg(res.edges, p.style, p.thickness))
    item = {"id": uid, "source": "phone" if body.source == "phone" else "laptop",
            "style": STYLE_NAMES[p.style], "time": time.strftime("%I:%M %p").lstrip("0").lower(),
            "png": f"/api/gallery/{uid}/png", "svg": f"/api/gallery/{uid}/svg"}
    GALLERY.insert(0, item)
    del GALLERY[48:]
    for v in list(viewers):
        try:
            await v.send_text(json.dumps({"event": "new", "item": item}))
        except Exception:
            viewers.discard(v)
    return item


@app.get("/api/gallery")
async def gallery():
    return GALLERY


@app.get("/api/gallery/{uid}/{kind}")
async def gallery_file(uid: str, kind: str):
    if kind not in ("png", "svg") or not uid.isalnum() or not (DATA / f"{uid}.{kind}").exists():
        raise HTTPException(404)
    return FileResponse(DATA / f"{uid}.{kind}")


@app.websocket("/ws/gallery")
async def gallery_ws(ws: WebSocket):
    await ws.accept()
    viewers.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        viewers.discard(ws)


def ensure_cert():
    cert = ROOT / "certs"
    cert.mkdir(exist_ok=True)
    if not (cert / "cert.pem").exists():
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
                        "-keyout", str(cert / "key.pem"), "-out", str(cert / "cert.pem"), "-subj", "/CN=edgeseg"],
                       check=True)
    return str(cert / "key.pem"), str(cert / "cert.pem")


if __name__ == "__main__":
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--https", action="store_true", help="serve over HTTPS (self-signed) so phones allow the live camera")
    ap.add_argument("--no-cloudflare", action="store_true", help="run locally without starting a Cloudflare Quick Tunnel")
    a = ap.parse_args()
    PORT = a.port
    kw = {}
    if a.https:
        kw["ssl_keyfile"], kw["ssl_certfile"] = ensure_cert()
    tunnel = None
    if not a.no_cloudflare and not PUBLIC_TUNNEL_URL:
        tunnel = start_cloudflare_tunnel(a.port)
    print(f"\n  EdgeSeg Studio\n  Laptop : {'https' if a.https else 'http'}://localhost:{PORT}/laptop"
          f"\n  Phone  : {lan_url('https' if a.https else 'http')}/mobile"
          f"\n  Cloudflare tunnel is starting; the QR will switch to its HTTPS URL when ready.\n")
    try:
        uvicorn.run(app, host=a.host, port=a.port, **kw)
    finally:
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()
