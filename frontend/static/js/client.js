/* EdgeSeg Studio client: camera/video/photo sources -> WebSocket -> OpenCV backend -> live preview + shared gallery */
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
let toastTimer;
function toast(msg) {
  const t = $('#toast'); if (!t) return;
  t.textContent = msg; t.classList.add('on');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('on'), 3000);
}
const wsUrl = path => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`;

function openQrModal() {
  const m = $('#qrModal');
  if (m) m.classList.add('open');
}

function closeQrModal() {
  const m = $('#qrModal');
  if (m) m.classList.remove('open');
}

class Studio {
  constructor(source) {
    this.source = source;                       // 'laptop' | 'phone'
    this.params = { style: 'classic', threshold1: 28, threshold2: 75, smoothing: 30, cleanup: 14, thickness: 1, auto_contrast: true, shading: 60, grid: 0 };
    this.cap = document.createElement('canvas');
    this.vid = $('#vid'); this.out = $('#outImg'); this.view = $('#srcView');
    this.stageImgs = $$('.stage img');
    this.mode = 'video'; this.paused = false; this.dirty = true; this.busy = false; this.hasFrame = false;
    this.img = null; this.stream = null; this.last = 0; this.frames = 0; this.t0 = performance.now(); this.pending = false;
    this.phoneConnected = false;
    this.viewMode = 'outline'; // 'outline' | 'camera' | 'split'
    this.lastPhoneSrc = null;
    this.lastPhoneOut = null;
    
    this.bind();
    this.connect();
    this.useSample();
    requestAnimationFrame(t => this.tick(t));
  }

  /* ---------- sources ---------- */
  stopStream() {
    if (this.stream) {
      this.stream.getTracks().forEach(t => t.stop());
      this.stream = null;
    }
  }

  setMode(mode, label) {
    if (this.mode === 'phone' && mode !== 'phone' && this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify({ action: 'unsubscribe_phone' }));
    }
    this.mode = mode; this.paused = false; this.hasFrame = false; this.dirty = true;
    const n = $('#srcName'); if (n) n.textContent = label;
    $$('[data-src]').forEach(b => b.setAttribute('aria-pressed', b.dataset.src === mode));
    this.syncPause();
  }

  useSample() {
    this.stopStream(); this.vid.srcObject = null; this.vid.src = '/static/media/sample.mp4';
    this.vid.loop = true; this.vid.muted = true; this.vid.play().catch(() => {});
    this.setMode('video', 'Sample video');
    this.setViewMode('outline');
  }

  async useCamera() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      toast('Live camera needs HTTPS on mobile browsers. Use Take photo or open Wireless Camera.');
      openQrModal();
      return;
    }
    try {
      this.stopStream();
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: 'environment', width: { ideal: 1280 } },
        audio: false
      });
      this.vid.removeAttribute('src');
      this.vid.srcObject = this.stream;
      await this.vid.play();
      this.setMode('camera', 'Local camera');
    } catch (e) {
      toast('Camera blocked. Allow camera access, or use Take photo.');
    }
  }

  usePhoneCamera() {
    this.stopStream();
    this.vid.pause();
    this.setMode('phone', 'Phone camera');
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify({ action: 'subscribe_phone' }));
    }
    if (!this.phoneConnected) {
      openQrModal();
      toast('Scan the QR code with your phone to connect the camera.');
    } else {
      toast('Connected to wireless phone camera');
    }
  }

  loadFile(file, { label, auto } = {}) {
    if (!file) return;
    const url = URL.createObjectURL(file);
    if (file.type.startsWith('video')) {
      this.stopStream(); this.vid.srcObject = null; this.vid.src = url; this.vid.loop = true; this.vid.play().catch(() => {});
      this.setMode('video', label || file.name); return;
    }
    const im = new Image();
    im.onload = () => {
      this.stopStream(); this.vid.pause(); this.img = im;
      this.setMode('image', label || 'Photo');
      this.pending = !!auto;
    };
    im.onerror = () => toast('That image could not be opened.');
    im.src = url;
  }

  togglePause() {
    if (this.mode === 'image') return;
    this.paused = !this.paused;
    if (this.mode === 'video') {
      this.paused ? this.vid.pause() : this.vid.play().catch(() => {});
    }
    this.dirty = true;
    this.syncPause();
  }

  syncPause() {
    const b = $('#pauseBtn'); if (!b) return;
    b.disabled = this.mode === 'image';
    b.setAttribute('aria-pressed', this.paused);
    let noun = 'video';
    if (this.mode === 'camera') noun = 'camera';
    else if (this.mode === 'phone') noun = 'phone feed';
    b.textContent = this.paused ? `Resume ${noun}` : (this.mode === 'camera' ? 'Freeze camera' : (this.mode === 'phone' ? 'Freeze phone feed' : 'Pause video'));
  }

  /* ---------- controls ---------- */
  bind() {
    $$('[data-param]').forEach(el => {
      const k = el.dataset.param, o = el.parentElement.querySelector('output');
      const read = () => {
        const v = el.type === 'checkbox' ? el.checked : Number(el.value);
        this.params[k] = v; if (o) o.textContent = el.value; this.sendParams();
      };
      el.addEventListener('input', read); el.addEventListener('change', read);
      if (el.type === 'checkbox') el.checked = this.params[k]; else el.value = this.params[k];
      if (o) o.textContent = el.value;
    });

    $$('[data-style]').forEach(b => b.addEventListener('click', () => this.setStyle(b.dataset.style)));
    this.setStyle('classic');

    const on = (id, fn) => { const e = $(id); if (e) e.addEventListener('click', fn); };
    on('#sampleBtn', () => this.useSample());
    on('#camBtn', () => this.useCamera());
    on('#phoneCamBtn', () => this.usePhoneCamera());
    on('#pauseBtn', () => this.togglePause());
    on('#uploadBtn', () => $('#fileIn').click());
    on('#photoBtn', () => $('#photoIn').click());
    on('#shutter', () => this.capture());
    on('#saveBtn', () => this.capture());
    on('#svgBtn', () => this.exportSvg());
    on('#qrBtn', () => openQrModal());
    on('#qrClose', () => closeQrModal());

    const qrModal = $('#qrModal');
    if (qrModal) {
      qrModal.addEventListener('click', e => {
        if (e.target === qrModal) closeQrModal();
      });
    }

    const copyBtn = $('#copyUrlBtn');
    if (copyBtn) {
      copyBtn.addEventListener('click', () => {
        const link = $('#camLink');
        if (link && navigator.clipboard) {
          navigator.clipboard.writeText(link.href).then(() => toast('Camera URL copied!')).catch(() => {});
        }
      });
    }

    // View switch for phone dashboard mockup
    $$('#phoneViewSwitch button').forEach(b => {
      b.addEventListener('click', () => this.setViewMode(b.dataset.view));
    });

    const f = $('#fileIn'); if (f) f.onchange = e => { this.loadFile(e.target.files[0]); e.target.value = ''; };
    const p = $('#photoIn'); if (p) p.onchange = e => { this.loadFile(e.target.files[0], { label: 'Phone photo', auto: true }); e.target.value = ''; };
  }

  setViewMode(mode) {
    this.viewMode = mode;
    const switchEl = $('#phoneViewSwitch');
    if (!switchEl) return; // Only apply view switching on pages with #phoneViewSwitch
    
    $$('#phoneViewSwitch button').forEach(b => {
      b.classList.toggle('active', b.dataset.view === mode);
    });
    const out = $('#outImg');
    const view = $('#srcView');
    if (!out || !view) return;

    if (mode === 'camera') {
      out.style.display = 'none';
      view.style.display = 'block';
      view.style.position = 'relative';
      view.style.height = '100%';
    } else if (mode === 'outline') {
      out.style.display = 'block';
      out.style.height = '100%';
      view.style.display = 'none';
    } else if (mode === 'split') {
      out.style.display = 'block';
      out.style.height = '50%';
      view.style.display = 'block';
      view.style.height = '50%';
      view.style.position = 'absolute';
      view.style.bottom = '0';
      view.style.top = 'auto';
    }
  }

  setStyle(s) {
    this.params.style = s;
    $$('[data-style]').forEach(b => b.setAttribute('aria-pressed', b.dataset.style === s));
    $$('[data-for-style]').forEach(el => { el.hidden = el.dataset.forStyle !== s; });
    const tag = $('#styleName'); if (tag) tag.textContent = ($(`[data-style="${s}"]`) || {}).textContent || s;
    this.sendParams();
  }

  /* ---------- transport ---------- */
  connect() {
    this.ws = new WebSocket(wsUrl('/ws/stream'));
    this.ws.onopen = () => {
      this.setStatus('Connected');
      this.sendParams();
      if (this.mode === 'phone') {
        this.ws.send(JSON.stringify({ action: 'subscribe_phone' }));
      }
    };
    this.ws.onclose = () => {
      this.setStatus('Reconnecting');
      this.busy = false;
      setTimeout(() => this.connect(), 1500);
    };
    this.ws.onmessage = e => {
      this.busy = false;
      try {
        const d = JSON.parse(e.data);
        if (!d) return;
        
        // Handle phone connection status updates
        if (d.event === 'phone_status') {
          this.phoneConnected = !!d.connected;
          $$('.dot').forEach(el => {
            if (this.phoneConnected) el.classList.add('online');
            else el.classList.remove('online');
          });
          const mst = $('#modalPhoneStatusText');
          if (mst) {
            mst.textContent = this.phoneConnected ? 'Phone camera connected! Streaming live 🟢' : 'Waiting for phone camera...';
          }
          if (this.phoneConnected) {
            toast('Wireless phone camera is live!');
            // On phone dashboard mockup on computer, auto-connect to live phone camera!
            if (this.source === 'phone' && this.mode !== 'phone') {
              this.usePhoneCamera();
            }
          }
          return;
        }

        // Handle live phone camera frames arriving from server
        if (d.event === 'phone_frame') {
          if (d.src) this.lastPhoneSrc = d.src;
          if (d.out) this.lastPhoneOut = d.out;
          
          if ((this.mode === 'phone' || this.source === 'phone') && !this.paused) {
            if (d.out && this.out) this.out.src = d.out;
            if (d.stages && Array.isArray(d.stages)) {
              d.stages.forEach((u, i) => { if (this.stageImgs[i] && u) this.stageImgs[i].src = u; });
            }
            
            if (d.src) {
              const im = new Image();
              im.onload = () => {
                const w = d.w || 640, h = d.h || 360;
                this.cap.width = w; this.cap.height = h;
                this.cap.getContext('2d').drawImage(im, 0, 0);
                if (this.view) {
                  this.view.width = w; this.view.height = h;
                  this.view.getContext('2d').drawImage(im, 0, 0);
                }
                this.hasFrame = true;
              };
              im.src = d.src;
            }

            this.frames++;
            const now = performance.now();
            if (now - this.t0 > 1000) {
              const f = $('#fps'); if (f) f.textContent = Math.round(this.frames * 1000 / (now - this.t0));
              this.frames = 0; this.t0 = now;
            }
            if (this.pending) {
              this.pending = false;
              this.capture();
            }
          }
          return;
        }

        // Standard local stream reply
        if (d.out && this.out) this.out.src = d.out;
        if (d.stages && Array.isArray(d.stages)) {
          d.stages.forEach((u, i) => { if (this.stageImgs[i] && u) this.stageImgs[i].src = u; });
        }
        this.frames++;
        const now = performance.now();
        if (now - this.t0 > 1000) {
          const f = $('#fps'); if (f) f.textContent = Math.round(this.frames * 1000 / (now - this.t0));
          this.frames = 0; this.t0 = now;
        }
        if (this.pending) { this.pending = false; this.capture(); }
      } catch (err) {}
    };
  }

  setStatus(t) {
    const s = $('#status');
    if (!s) return;
    const dot = s.querySelector('.dot');
    s.textContent = t;
    if (dot) s.appendChild(dot);
  }

  sendParams() {
    this.dirty = true;
    if (this.ws && this.ws.readyState === 1) {
      this.ws.send(JSON.stringify({ ...this.params, stages: this.stageImgs.length > 0, source: this.source }));
    }
  }

  el() {
    if (this.mode === 'image') return this.img && { e: this.img, w: this.img.naturalWidth, h: this.img.naturalHeight };
    return this.vid.videoWidth ? { e: this.vid, w: this.vid.videoWidth, h: this.vid.videoHeight } : null;
  }

  drawFrame() {
    const s = this.el(); if (!s) return false;
    const w = Math.min(640, s.w), h = Math.round(s.h * w / s.w);
    this.cap.width = w; this.cap.height = h;
    this.cap.getContext('2d').drawImage(s.e, 0, 0, w, h);
    if (this.view) {
      this.view.width = w; this.view.height = h;
      this.view.getContext('2d').drawImage(this.cap, 0, 0);
    }
    this.hasFrame = true; return true;
  }

  tick(ts) {
    requestAnimationFrame(t => this.tick(t));
    if (this.mode === 'phone') return; // In phone mode, frames arrive pushed from server
    if (ts - this.last < 66 || this.busy || !this.ws || this.ws.readyState !== 1) return;
    const live = !this.paused && (this.mode === 'camera' || (this.mode === 'video' && !this.vid.paused));
    if (!live && !this.dirty) return;
    if ((live || !this.hasFrame) && !this.drawFrame()) return;
    this.last = ts; this.busy = true; this.dirty = false;
    this.cap.toBlob(b => this.ws && this.ws.readyState === 1 && this.ws.send(b), 'image/jpeg', 0.72);
    setTimeout(() => { this.busy = false; }, 2500);
  }

  /* ---------- actions ---------- */
  body() {
    let imgData = this.cap.toDataURL('image/jpeg', 0.88);
    if (this.mode === 'phone' && this.lastPhoneSrc) {
      imgData = this.lastPhoneSrc;
    }
    return JSON.stringify({ image: imgData, params: this.params, source: this.source });
  }

  async capture() {
    if (!this.hasFrame && !this.lastPhoneSrc) {
      toast('Nothing to capture yet.'); return;
    }
    try {
      await fetch('/api/capture', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: this.body()
      });
      toast(this.source === 'phone' ? 'Sent to the shared gallery' : 'Saved to the shared gallery');
    } catch (e) {
      toast('Could not reach the server.');
    }
  }

  async exportSvg() {
    if (!this.hasFrame && !this.lastPhoneSrc) return;
    const r = await fetch('/api/svg', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: this.body()
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = `edgeseg-${Date.now()}.svg`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast('SVG exported');
  }
}

class Gallery {
  constructor(root) {
    this.root = root; this.items = [];
    fetch('/api/gallery').then(r => r.json()).then(l => { this.items = Array.isArray(l) ? l : []; this.render(); });
    const ws = new WebSocket(wsUrl('/ws/gallery'));
    ws.onmessage = e => {
      try {
        const m = JSON.parse(e.data);
        if (m.event === 'new' && m.item) {
          this.items.unshift(m.item);
          this.render();
        }
      } catch (err) {}
    };
    ws.onclose = () => setTimeout(() => new Gallery(root), 2000);
    setInterval(() => ws.readyState === 1 && ws.send('ping'), 20000);
    this.render();
  }
  render() {
    const n = $('#galCount'); if (n) n.textContent = this.items.length;
    if (!this.items.length) {
      this.root.innerHTML = '<div class="empty">Nothing captured yet. Use the shutter on the phone or Save to gallery on the laptop.</div>';
      return;
    }
    this.root.innerHTML = this.items.map(i => `
      <article class="gcard">
        <img class="gcard__thumb" alt="Captured outline" src="${i.png || ''}">
        <div class="gcard__meta"><span class="badge badge--${i.source}">${i.source === 'phone' ? 'Phone' : 'Laptop'}</span><small>${i.style} · ${i.time}</small></div>
        <div class="gcard__acts"><a href="${i.png || '#'}" download="edgeseg-${i.id || 'capture'}.png">PNG</a><a href="${i.svg || '#'}" download="edgeseg-${i.id || 'capture'}.svg">SVG</a></div>
      </article>`).join('');
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const body = document.body;
  if (body.dataset.studio) window.studio = new Studio(body.dataset.studio);
  const g = $('#galleryGrid'); if (g) window.gallery = new Gallery(g);

  // Replace the local QR URL once the Cloudflare HTTPS tunnel is ready.
  function updateSecureUrl() {
    fetch('/api/status').then(r => r.json()).then(s => {
      if (s && s.cam_url) {
        const link = $('#camLink');
        if (link) { link.href = s.cam_url; link.textContent = s.cam_url; }
        if (s.qr_code) {
          $$('.modal-qr img').forEach(img => { img.src = s.qr_code; });
        }
        if (s.tunnel_ready && window.cloudflareStatusTimer) {
          clearInterval(window.cloudflareStatusTimer);
          window.cloudflareStatusTimer = null;
        }
      }
    }).catch(() => {});
  }
  updateSecureUrl();
  window.cloudflareStatusTimer = setInterval(updateSecureUrl, 2500);
});

})();
