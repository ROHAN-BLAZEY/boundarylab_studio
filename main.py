
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
import sqlite3
import secrets
import hmac
import hashlib
import shutil
from contextlib import contextmanager
from urllib.parse import parse_qs
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
from pydantic import BaseModel
import qrcode

from backend import processor, vectorizer
from backend.styles import STYLE_NAMES

ROOT = Path(__file__).parent
FRONT = ROOT / "frontend"
DATA_ROOT = Path(os.environ.get("BOUNDARYLAB_DATA_DIR", ROOT / "data"))
DATA = DATA_ROOT / "gallery"
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_ROOT / "gallery.sqlite3"
PORT = int(os.environ.get("PORT", "8501"))
APP_ENV = os.environ.get("APP_ENV", "development").lower()
APP_USERNAME = os.environ.get("APP_USERNAME", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")
if APP_ENV == "production" and (len(SESSION_SECRET) < 32 or not APP_USERNAME or len(APP_PASSWORD) < 12):
    raise RuntimeError("Production requires SESSION_SECRET (32+ chars), APP_USERNAME, and APP_PASSWORD (12+ chars).")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(48)
AUTH_ENABLED = bool(APP_USERNAME and APP_PASSWORD)
PUBLIC_TUNNEL_URL = os.environ.get("CLOUDFLARE_TUNNEL_URL", "").rstrip("/")

app = FastAPI(title="BoundaryLab Studio")
app.mount("/static", StaticFiles(directory=FRONT / "static"), name="static")
templates = Jinja2Templates(directory=FRONT / "templates")
pool = ThreadPoolExecutor(max_workers=2)


@contextmanager
def gallery_db():
    connection = sqlite3.connect(DB_PATH, timeout=15)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_database():
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with gallery_db() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user', created_at REAL NOT NULL)")
        admin = db.execute("SELECT id FROM users WHERE username = ?", (APP_USERNAME or "local-admin",)).fetchone()
        previous_local = db.execute("SELECT id FROM users WHERE username = 'local-admin'").fetchone()
        migrate_local_id = None
        if admin:
            admin_id = admin[0]
            if APP_PASSWORD:
                db.execute("UPDATE users SET password_hash = ?, role = 'admin' WHERE id = ?",
                           (hash_password(APP_PASSWORD), admin_id))
        else:
            admin_id = uuid.uuid4().hex if AUTH_ENABLED else "local"
            db.execute("INSERT INTO users VALUES (?, ?, ?, 'admin', ?)",
                       (admin_id, APP_USERNAME or "local-admin", hash_password(APP_PASSWORD or secrets.token_urlsafe(32)), time.time()))
            if AUTH_ENABLED and previous_local:
                db.execute("UPDATE users SET role = 'user' WHERE id = ?", (previous_local[0],))
                migrate_local_id = previous_local[0]
        db.execute("CREATE TABLE IF NOT EXISTS gallery (id TEXT PRIMARY KEY, source TEXT NOT NULL, style TEXT NOT NULL, created_at REAL NOT NULL)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(gallery)").fetchall()}
        if "owner_id" not in columns:
            db.execute("ALTER TABLE gallery ADD COLUMN owner_id TEXT")
        if migrate_local_id:
            db.execute("UPDATE gallery SET owner_id = ? WHERE owner_id = ?", (admin_id, migrate_local_id))
            old_dir, new_dir = DATA / migrate_local_id, DATA / admin_id
            if old_dir.exists() and not new_dir.exists(): shutil.move(str(old_dir), str(new_dir))
        db.execute("UPDATE gallery SET owner_id = ? WHERE owner_id IS NULL", (admin_id,))
        db.execute("CREATE INDEX IF NOT EXISTS gallery_owner_created ON gallery(owner_id, created_at DESC)")
        for saved in DATA.glob("*.png"):
            uid = saved.stem
            if (DATA / f"{uid}.svg").exists():
                db.execute("INSERT OR IGNORE INTO gallery (id, source, style, created_at, owner_id) VALUES (?, ?, ?, ?, ?)",
                           (uid, "laptop", "Saved capture", saved.stat().st_mtime, admin_id))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, expected = encoded.split("$", 2)
        if algorithm != "pbkdf2_sha256": return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 310_000).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def user_gallery_dir(owner_id: str) -> Path:
    if not owner_id.isalnum(): raise HTTPException(400, "Invalid account")
    directory = DATA / owner_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def owner_for(request: Request) -> str:
    if not AUTH_ENABLED:
        request.session.update({"user_id": "local", "username": "local-admin", "role": "admin"})
        return "local"
    owner_id = request.session.get("user_id")
    if not owner_id: raise HTTPException(401, "Authentication required")
    return owner_id


def owner_for_ws(ws: WebSocket) -> str:
    return ws.session.get("user_id", "local")


def gallery_item(row):
    uid, source, style, created, owner_id = row
    owner_dir = DATA / owner_id
    source_path = owner_dir / f"{uid}.jpg"
    if not source_path.exists(): source_path = DATA / f"{uid}.jpg"  # pre-account captures
    return {"id": uid, "source": source, "style": style,
            "time": time.strftime("%I:%M %p", time.localtime(created)).lstrip("0").lower(),
            "png": f"/api/gallery/{uid}/png", "svg": f"/api/gallery/{uid}/svg",
            "source_image": f"/api/gallery/{uid}/source" if source_path.exists() else f"/api/gallery/{uid}/png"}


class DashboardAccessMiddleware:
    def __init__(self, app: ASGIApp): self.app = app
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if not AUTH_ENABLED or scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in ("/login", "/register", "/health", "/favicon.ico", "/manifest.webmanifest", "/sw.js") or path.startswith("/static/"):
            return await self.app(scope, receive, send)
        if scope.get("session", {}).get("user_id"):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 4401})
        if path.startswith("/api/"):
            body = b'{"detail":"Authentication required"}'
            await send({"type":"http.response.start", "status":401, "headers":[(b"content-type",b"application/json"),(b"content-length",str(len(body)).encode())]})
            return await send({"type":"http.response.body", "body":body})
        await send({"type":"http.response.start", "status":303, "headers":[(b"location",b"/login"),(b"content-length",b"0")]})
        await send({"type":"http.response.body", "body":b""})


app.add_middleware(DashboardAccessMiddleware)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=(APP_ENV == "production"), same_site="lax")


@app.on_event("startup")
async def startup(): init_database()


@app.get("/health")
async def health(): return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(FRONT / "static" / "icons" / "app.svg", media_type="image/svg+xml")


@app.get("/manifest.webmanifest")
async def manifest(): return FileResponse(FRONT / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker(): return FileResponse(FRONT / "static" / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed":"/", "Cache-Control":"no-cache"})


@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": "", "configured": AUTH_ENABLED})


@app.post("/login")
async def login(request: Request):
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    username = form.get("username", [""])[0]
    password = form.get("password", [""])[0]
    with gallery_db() as db:
        user = db.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?", (username,)).fetchone()
    if not user or not verify_password(password, user[2]):
        return templates.TemplateResponse(request, "login.html", {"error": "Check your username and password.", "configured": AUTH_ENABLED}, status_code=401)
    request.session.clear()
    request.session.update({"user_id": user[0], "username": user[1], "role": user[3]})
    return Response(status_code=303, headers={"Location":"/laptop"})


@app.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"error": "", "enabled": AUTH_ENABLED})


@app.post("/register")
async def register(request: Request):
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    username = form.get("username", [""])[0].strip()
    password = form.get("password", [""])[0]
    if not AUTH_ENABLED:
        raise HTTPException(404)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        return templates.TemplateResponse(request, "register.html", {"error": "Use 3–32 letters, numbers, dots, dashes, or underscores.", "enabled": True}, status_code=400)
    if len(password) < 12:
        return templates.TemplateResponse(request, "register.html", {"error": "Use a password with at least 12 characters.", "enabled": True}, status_code=400)
    uid = uuid.uuid4().hex
    try:
        with gallery_db() as db:
            db.execute("INSERT INTO users (id, username, password_hash, role, created_at) VALUES (?, ?, ?, 'user', ?)",
                       (uid, username, hash_password(password), time.time()))
    except sqlite3.IntegrityError:
        return templates.TemplateResponse(request, "register.html", {"error": "That username is already taken.", "enabled": True}, status_code=409)
    request.session.clear()
    request.session.update({"user_id": uid, "username": username, "role": "user"})
    return Response(status_code=303, headers={"Location":"/laptop"})


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return Response(status_code=303, headers={"Location":"/login" if AUTH_ENABLED else "/"})


@app.get("/admin")
async def admin_page(request: Request):
    if request.session.get("role", "admin" if not AUTH_ENABLED else "user") != "admin":
        raise HTTPException(403, "Admin access required")
    with gallery_db() as db:
        users = db.execute("SELECT username, role, created_at FROM users ORDER BY created_at").fetchall()
    return templates.TemplateResponse(request, "admin.html", {
        "users": users, "username": request.session.get("username", "local-admin")
    })


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
            "is_https": request.url.scheme == "https",
            "https_active": bool(PUBLIC_TUNNEL_URL),
            "qr_code": qr_data_url(cam_url),
            "auth_enabled": AUTH_ENABLED,
            "user_role": request.session.get("role", "admin" if not AUTH_ENABLED else "user"),
            "username": request.session.get("username", "local-admin" if not AUTH_ENABLED else ""),
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
    if len(s) > 24 * 1024 * 1024:
        raise ValueError("image exceeds the 18 MB upload limit")
    return decode(base64.b64decode(s.split(",", 1)[-1]))


# ---------------------------------------------------------------- live stream
phone_subscribers = set()
phone_processing_subscribers = set()
phone_preview_subscribers = set()
phone_camera_owners = {}
phone_camera_clients = {}


async def broadcast_phone_status(owner_id: str, connected: bool):
    message = json.dumps({"event": "phone_status", "connected": connected})
    for viewer in tuple(phone_subscribers):
        if owner_for_ws(viewer) != owner_id:
            continue
        try:
            await viewer.send_text(message)
        except Exception:
            phone_subscribers.discard(viewer)
            phone_processing_subscribers.discard(viewer)
            phone_preview_subscribers.discard(viewer)


async def broadcast_phone_settings(owner_id: str, params):
    message = json.dumps({"event": "phone_settings", "params": params.__dict__})
    for camera in tuple(phone_camera_clients.get(owner_id, set())):
        try:
            await camera.send_text(message)
        except Exception:
            phone_camera_clients.get(owner_id, set()).discard(camera)
    for viewer in tuple(phone_subscribers):
        if owner_for_ws(viewer) != owner_id:
            continue
        try:
            await viewer.send_text(message)
        except Exception:
            phone_subscribers.discard(viewer)
            phone_processing_subscribers.discard(viewer)
            phone_preview_subscribers.discard(viewer)


@app.websocket("/ws/stream")
async def stream(ws: WebSocket):
    """Client sends JSON text (params) and JPEG bytes (frames); server replies with the styled frame."""
    await ws.accept()
    params = processor.Params()
    owner_id = owner_for_ws(ws)
    loop = asyncio.get_running_loop()
    frame_number = 0
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("text"):
                d = json.loads(msg["text"])
                if d.get("action") == "subscribe_phone":
                    phone_subscribers.add(ws)
                    if d.get("dashboard") == "phone": phone_processing_subscribers.add(ws)
                    else: phone_processing_subscribers.discard(ws)
                    if d.get("dashboard") == "laptop": phone_preview_subscribers.add(ws)
                    else: phone_preview_subscribers.discard(ws)
                    await ws.send_text(json.dumps({"event": "phone_status", "connected": owner_id in phone_camera_owners}))
                elif d.get("action") == "unsubscribe_phone":
                    phone_subscribers.discard(ws)
                    phone_processing_subscribers.discard(ws)
                    phone_preview_subscribers.discard(ws)
                else:
                    params = processor.Params.from_dict(d)
                    params.stages = bool(d.get("stages"))
                    params.all_stages = bool(d.get("all_stages"))
                    try:
                        params.stage_index = int(d.get("stage_index") if d.get("stage_index") is not None else -1)
                    except (TypeError, ValueError):
                        params.stage_index = -1
                    if params.stage_index not in range(8):
                        params.stage_index = -1
                    params.inspect_stage = 7 if params.all_stages else params.stage_index
                    phone_params[owner_id] = params
                    await broadcast_phone_settings(owner_id, params)
            elif msg.get("bytes"):
                frame_number += 1
                try:
                    res = await loop.run_in_executor(pool, lambda b=msg["bytes"], p=params: (processor.run(decode(b), p)))
                except ValueError:
                    continue
                reply = {"out": processor.to_data_url(res.out), "w": res.out.shape[1], "h": res.out.shape[0]}
                stage_keys = ("grayscale", "contrast", "smoothed", "sobel", "threshold", "edges", "morphology", "contours")
                if getattr(params, "stages", False) and getattr(params, "all_stages", False) and frame_number % 3 == 1:
                    reply["stages"] = [processor.to_data_url(res.stages[key], 62) for key in stage_keys]
                elif getattr(params, "stages", False) and getattr(params, "stage_index", -1) in range(len(stage_keys)):
                    key = stage_keys[params.stage_index]
                    reply["stage_index"] = params.stage_index
                    reply["stages"] = [processor.to_data_url(res.stages[key], 62)]
                await ws.send_text(json.dumps(reply))
    except WebSocketDisconnect:
        pass
    finally:
        phone_subscribers.discard(ws)
        phone_processing_subscribers.discard(ws)
        phone_preview_subscribers.discard(ws)


@app.websocket("/ws/camera/feed")
async def camera_feed(ws: WebSocket):
    """Relay color frames immediately and process only the newest frame for the lower preview."""
    await ws.accept()
    owner_id = owner_for_ws(ws)
    camera_announced = False
    phone_camera_clients.setdefault(owner_id, set()).add(ws)
    current_params = phone_params.get(owner_id, processor.Params())
    await ws.send_text(json.dumps({"event": "phone_settings", "params": current_params.__dict__}))
    loop = asyncio.get_running_loop()
    frames = asyncio.Queue(maxsize=1)
    processed_frames = asyncio.Queue(maxsize=1)
    state = {"last_frame": None}

    async def relay_color():
        while True:
            data = await frames.get()
            try:
                if not phone_subscribers:
                    continue
                message = json.dumps({"event": "phone_frame",
                    "src": "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")})
                for viewer in tuple(phone_subscribers):
                    if owner_for_ws(viewer) != owner_id: continue
                    try:
                        await viewer.send_text(message)
                    except Exception:
                        phone_subscribers.discard(viewer)
                        phone_processing_subscribers.discard(viewer)
                        phone_preview_subscribers.discard(viewer)
            finally:
                frames.task_done()

    async def process_preview():
        while True:
            data = await processed_frames.get()
            started = time.perf_counter()
            try:
                frame = decode(data)
                height, width = frame.shape[:2]
                if width > 480:
                    frame = cv2.resize(frame, (480, max(2, round(height * 480 / width))), interpolation=cv2.INTER_AREA)
                params = processor.Params.from_dict(phone_params.get(owner_id, processor.Params()).__dict__)
                params.inspect_stage = -1
                result = await loop.run_in_executor(pool, processor.run, frame, params)
                message = json.dumps({"event": "phone_processed",
                    "gray": processor.to_data_url(result.stages["grayscale"], 60),
                    "out": processor.to_data_url(result.out, 60)})
                try:
                    await ws.send_text(message)
                except Exception:
                    pass
                for viewer in tuple(phone_processing_subscribers | phone_preview_subscribers):
                    if owner_for_ws(viewer) != owner_id: continue
                    try:
                        await viewer.send_text(message)
                    except Exception:
                        phone_subscribers.discard(viewer)
                        phone_processing_subscribers.discard(viewer)
                        phone_preview_subscribers.discard(viewer)
            except (ValueError, cv2.error):
                pass
            finally:
                processed_frames.task_done()
            await asyncio.sleep(max(0, 0.1 - (time.perf_counter() - started)))

    relay_worker = asyncio.create_task(relay_color())
    process_worker = asyncio.create_task(process_preview())
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("text"):
                request = json.loads(msg["text"])
                if request.get("type") == "params":
                    params = processor.Params.from_dict(request.get("params", {}))
                    phone_params[owner_id] = params
                    await broadcast_phone_settings(owner_id, params)
                    continue
                if request.get("type") == "capture":
                    image = decode_data_url(request["image"]) if request.get("image") else (decode(state["last_frame"]) if state["last_frame"] else None)
                    if image is not None:
                        await save_capture(image, {"source": "phone"}, phone_params.get(owner_id, processor.Params()), owner_id)
                        await ws.send_text(json.dumps({"event": "captured"}))
                continue
            data = msg.get("bytes")
            if not data:
                continue
            if not camera_announced:
                camera_announced = True
                phone_camera_owners[owner_id] = phone_camera_owners.get(owner_id, 0) + 1
                await broadcast_phone_status(owner_id, True)
            state["last_frame"] = data
            if frames.full():
                try:
                    frames.get_nowait()
                    frames.task_done()
                except asyncio.QueueEmpty:
                    pass
            frames.put_nowait(data)
            if processed_frames.full():
                try:
                    processed_frames.get_nowait()
                    processed_frames.task_done()
                except asyncio.QueueEmpty:
                    pass
            processed_frames.put_nowait(data)
    except WebSocketDisconnect:
        pass
    finally:
        for worker in (relay_worker, process_worker):
            worker.cancel()
        for worker in (relay_worker, process_worker):
            try:
                await worker
            except asyncio.CancelledError:
                pass
        phone_camera_clients.get(owner_id, set()).discard(ws)
        if not phone_camera_clients.get(owner_id):
            phone_camera_clients.pop(owner_id, None)
        if camera_announced:
            remaining_cameras = phone_camera_owners.get(owner_id, 1) - 1
            if remaining_cameras > 0:
                phone_camera_owners[owner_id] = remaining_cameras
            else:
                phone_camera_owners.pop(owner_id, None)
                await broadcast_phone_status(owner_id, False)


async def save_capture(image: np.ndarray, body: dict, p: processor.Params, owner_id: str):
    result = await asyncio.get_running_loop().run_in_executor(pool, processor.run, image, p)
    uid = uuid.uuid4().hex[:10]
    directory = user_gallery_dir(owner_id)
    cv2.imwrite(str(directory / f"{uid}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    cv2.imwrite(str(directory / f"{uid}.png"), result.out)
    (directory / f"{uid}.svg").write_text(vectorizer.edges_to_svg(result.edges, p.style, p.thickness, neon_color=p.neon_color), encoding="utf-8")
    item = {"id": uid, "source": "phone" if body.get("source") == "phone" else "laptop",
            "style": STYLE_NAMES[p.style], "time": time.strftime("%I:%M %p").lstrip("0").lower(),
            "png": f"/api/gallery/{uid}/png", "svg": f"/api/gallery/{uid}/svg",
            "source_image": f"/api/gallery/{uid}/source"}
    created = time.time()
    with gallery_db() as db:
        db.execute("INSERT INTO gallery (id, source, style, created_at, owner_id) VALUES (?, ?, ?, ?, ?)",
                   (uid, item["source"], item["style"], created, owner_id))
    for v in tuple(viewers):
        if viewers.get(v) != owner_id: continue
        try: await v.send_text(json.dumps({"event": "new", "item": item}))
        except Exception: viewers.pop(v, None)
    return item


# -------------------------------------------------------------------- gallery
viewers = {}
phone_params = {}


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
async def capture(request: Request, body: FrameIn):
    p = processor.Params.from_dict(body.params)
    return await save_capture(decode_data_url(body.image), body.dict(), p, owner_for(request))


@app.get("/api/gallery")
async def gallery(request: Request):
    owner_id = owner_for(request)
    with gallery_db() as db:
        recover_gallery_index(db, owner_id)
        rows = db.execute("SELECT id, source, style, created_at, owner_id FROM gallery WHERE owner_id = ? ORDER BY created_at DESC LIMIT 48", (owner_id,)).fetchall()
    return [gallery_item(row) for row in rows]


def recover_gallery_index(db, owner_id: str):
    """Re-index saved PNG files if gallery metadata is missing, without crossing accounts."""
    account = db.execute("SELECT role FROM users WHERE id = ?", (owner_id,)).fetchone()
    if not account:
        return
    directories = [DATA / owner_id]
    # Legacy flat captures belong to the administrator. Never expose these to regular users.
    if account[0] == "admin":
        directories.append(DATA)
    for directory in directories:
        if not directory.is_dir():
            continue
        for saved in directory.glob("*.png"):
            uid = saved.stem
            if not uid.isalnum():
                continue
            try:
                created = saved.stat().st_mtime
            except OSError:
                continue
            db.execute(
                "INSERT OR IGNORE INTO gallery (id, source, style, created_at, owner_id) VALUES (?, ?, ?, ?, ?)",
                (uid, "laptop", "Recovered capture", created, owner_id),
            )


@app.delete("/api/gallery/{uid}")
async def delete_gallery_item(request: Request, uid: str):
    if not uid.isalnum(): raise HTTPException(404)
    owner_id = owner_for(request)
    with gallery_db() as db:
        deleted = db.execute("DELETE FROM gallery WHERE id = ? AND owner_id = ?", (uid, owner_id)).rowcount
    if not deleted: raise HTTPException(404)
    directory = DATA / owner_id
    for kind in ("png", "svg", "jpg"):
        (directory / f"{uid}.{kind}").unlink(missing_ok=True)
        (DATA / f"{uid}.{kind}").unlink(missing_ok=True)  # legacy capture location
    for v in tuple(viewers):
        if viewers.get(v) != owner_id: continue
        try: await v.send_text(json.dumps({"event": "delete", "id": uid}))
        except Exception: viewers.pop(v, None)
    return {"deleted": uid}


@app.get("/api/gallery/{uid}/{kind}")
async def gallery_file(request: Request, uid: str, kind: str):
    if kind not in ("png", "svg", "source", "jpeg", "jpg", "webp") or not uid.isalnum():
        raise HTTPException(404)
    owner_id = owner_for(request)
    with gallery_db() as db:
        owned = db.execute("SELECT 1 FROM gallery WHERE id = ? AND owner_id = ?", (uid, owner_id)).fetchone()
    if not owned: raise HTTPException(404)
    directory = DATA / owner_id
    path = directory / f"{uid}.{'jpg' if kind == 'source' else kind}"
    if not path.exists(): path = DATA / f"{uid}.{'jpg' if kind == 'source' else kind}"
    if kind in ("jpeg", "jpg", "webp"):
        stored = directory / f"{uid}.png"
        if not stored.exists(): stored = DATA / f"{uid}.png"
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
    viewers[ws] = owner_for_ws(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        viewers.pop(ws, None)


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
    ap.add_argument("--quick-tunnel", action="store_true", help="start a temporary Cloudflare Quick Tunnel for development")
    a = ap.parse_args()
    PORT = a.port
    kw = {}
    if a.https:
        kw["ssl_keyfile"], kw["ssl_certfile"] = ensure_cert()
    tunnel = None
    if a.quick_tunnel and not PUBLIC_TUNNEL_URL:
        tunnel = start_cloudflare_tunnel(a.port)
    print(f"\n  BoundaryLab Studio\n  Laptop : {'https' if a.https else 'http'}://localhost:{PORT}/laptop"
          f"\n  Phone  : {lan_url('https' if a.https else 'http')}/mobile"
          f"\n  Set CLOUDFLARE_TUNNEL_URL to your stable HTTPS origin when using a named tunnel.\n")
    try:
        uvicorn.run(app, host=a.host, port=a.port, **kw)
    finally:
        if tunnel and tunnel.poll() is None:
            tunnel.terminate()
