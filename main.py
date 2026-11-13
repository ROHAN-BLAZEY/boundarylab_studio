
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
    global phone_params
    loop = asyncio.get_running_loop()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("text"):
                d = json.loads(msg["text"])
                if d.get("action") not in ("subscribe_phone", "unsubscribe_phone"):
                    params = processor.Params.from_dict(d)
                    params.stages = bool(d.get("stages"))
                    try:
                        params.stage_index = int(d.get("stage_index") if d.get("stage_index") is not None else -1)
                    except (TypeError, ValueError):
                        params.stage_index = -1
                    if params.stage_index not in range(8):
                        params.stage_index = -1
                    params.inspect_stage = params.stage_index
                    phone_params = params
            elif msg.get("bytes"):
                try:
                    res = await loop.run_in_executor(pool, lambda b=msg["bytes"], p=params: (processor.run(decode(b), p)))
                except ValueError:
                    continue
                reply = {"out": processor.to_data_url(res.out), "w": res.out.shape[1], "h": res.out.shape[0]}
                if getattr(params, "stages", False) and getattr(params, "stage_index", -1) in range(8):
                    key = ("grayscale", "contrast", "smoothed", "sobel", "threshold", "edges", "morphology", "contours")[params.stage_index]
                    reply["stage_index"] = params.stage_index
                    reply["stages"] = [processor.to_data_url(res.stages[key], 55)]
                await ws.send_text(json.dumps(reply))
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/camera/feed")
async def camera_feed(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_running_loop()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect": break
            if msg.get("text"):
                if json.loads(msg["text"]).get("type") == "capture" and getattr(ws, "last_frame", None) is not None:
                    await save_capture(ws.last_frame, {"source": "phone"}, phone_params)
                    await ws.send_text(json.dumps({"event": "captured"}))
                continue
            data = msg.get("bytes")
            if not data: continue
            try:
                frame = decode(data)
                result = await loop.run_in_executor(pool, processor.run, frame, phone_params)
                ws.last_frame = frame
                payload = json.dumps({"event": "phone_frame", "src": processor.to_data_url(frame, 62),
                    "out": processor.to_data_url(result.out, 65), "w": frame.shape[1], "h": frame.shape[0]})
                for viewer in tuple(viewers):
                    try: await viewer.send_text(payload)
                    except Exception: viewers.discard(viewer)
                await ws.send_text(json.dumps({"event": "frame"}))
            except (ValueError, cv2.error): continue
    except WebSocketDisconnect: pass


async def save_capture(image: np.ndarray, body: dict, p: processor.Params):
    result = await asyncio.get_running_loop().run_in_executor(pool, processor.run, image, p)
    uid = uuid.uuid4().hex[:10]
    cv2.imwrite(str(DATA / f"{uid}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    cv2.imwrite(str(DATA / f"{uid}.png"), result.out)
    (DATA / f"{uid}.svg").write_text(vectorizer.edges_to_svg(result.edges, p.style, p.thickness, neon_color=p.neon_color), encoding="utf-8")
    item = {"id": uid, "source": "phone" if body.get("source") == "phone" else "laptop",
            "style": STYLE_NAMES[p.style], "time": time.strftime("%I:%M %p").lstrip("0").lower(),
            "png": f"/api/gallery/{uid}/png", "svg": f"/api/gallery/{uid}/svg",
            "source_image": f"/api/gallery/{uid}/source"}
    GALLERY.insert(0, item); del GALLERY[48:]
    for v in tuple(viewers):
        try: await v.send_text(json.dumps({"event": "new", "item": item}))
        except Exception: viewers.discard(v)
    return item


# -------------------------------------------------------------------- gallery
GALLERY = []
for saved in sorted(DATA.glob("*.png"), key=lambda path: path.stat().st_mtime, reverse=True)[:48]:
    uid = saved.stem
    if (DATA / f"{uid}.svg").exists():
        GALLERY.append({"id": uid, "source": "laptop", "style": "Saved capture",
            "time": time.strftime("%I:%M %p").lstrip("0").lower(),
            "png": f"/api/gallery/{uid}/png", "svg": f"/api/gallery/{uid}/svg",
            "source_image": f"/api/gallery/{uid}/source" if (DATA / f"{uid}.jpg").exists() else f"/api/gallery/{uid}/png"})
viewers = set()
phone_params = processor.Params()


class FrameIn(BaseModel):
    image: str                  # data URL (jpeg/png)
    params: dict = {}
    source: str = "laptop"      # "laptop" | "phone"


@app.post("/api/svg")
async def svg(body: FrameIn):
    p = processor.Params.from_dict(body.params)
    res = await asyncio.get_running_loop().run_in_executor(pool, processor.run, decode_data_url(body.image), p)
    text = vectorizer.edges_to_svg(res.edges, p.style, p.thickness, neon_color=p.neon_color)
    return Response(text, media_type="image/svg+xml",
                    headers={"Content-Disposition": 'attachment; filename="edgeseg.svg"'})


@app.post("/api/capture")
async def capture(body: FrameIn):
    p = processor.Params.from_dict(body.params)
    return await save_capture(decode_data_url(body.image), body.dict(), p)


@app.get("/api/gallery")
async def gallery():
    return GALLERY


@app.delete("/api/gallery/{uid}")
async def delete_gallery_item(uid: str):
    if not uid.isalnum() or not any(item["id"] == uid for item in GALLERY): raise HTTPException(404)
    for kind in ("png", "svg", "jpg"): (DATA / f"{uid}.{kind}").unlink(missing_ok=True)
    GALLERY[:] = [item for item in GALLERY if item["id"] != uid]
    for v in tuple(viewers):
        try: await v.send_text(json.dumps({"event": "delete", "id": uid}))
        except Exception: viewers.discard(v)
    return {"deleted": uid}


@app.get("/api/gallery/{uid}/{kind}")
async def gallery_file(uid: str, kind: str):
    if kind not in ("png", "svg", "source", "jpeg", "jpg", "webp") or not uid.isalnum():
        raise HTTPException(404)
    path = DATA / f"{uid}.{'jpg' if kind == 'source' else kind}"
    if kind in ("jpeg", "jpg", "webp"):
        stored = DATA / f"{uid}.png"
        if not stored.exists(): raise HTTPException(404)
        image = cv2.imread(str(stored), cv2.IMREAD_COLOR)
        ext = ".webp" if kind == "webp" else ".jpg"
        ok, encoded = cv2.imencode(ext, image)
        if not ok: raise HTTPException(500)
        media = "image/webp" if ext == ".webp" else "image/jpeg"
        return Response(encoded.tobytes(), media_type=media)
    if not path.exists(): raise HTTPException(404)
    return FileResponse(path)


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
