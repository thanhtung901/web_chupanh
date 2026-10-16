import os
import re
import unicodedata
import uuid
from datetime import datetime

from flask import Flask, jsonify, render_template_string, request, send_from_directory

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # tối đa 15MB/ảnh

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(BASE_DIR, "captured_photos")
os.makedirs(SAVE_DIR, exist_ok=True)

IMG_EXTS = {"jpg", "png", "webp"}


def detect_ext(data: bytes):
    """Kiểm tra định dạng ảnh bằng magic bytes (không tin tên file từ client)."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def safe_folder_name(name: str):
    """Chuyển tên người dùng nhập thành tên folder an toàn (giữ tiếng Việt có dấu).
    Trả về None nếu tên không hợp lệ."""
    name = unicodedata.normalize("NFC", (name or "").strip())
    name = re.sub(r"\s+", "_", name)                 # khoảng trắng -> _
    name = re.sub(r"[^\w\-]", "", name, flags=re.UNICODE)  # bỏ ký tự đặc biệt, / \ .
    name = name.strip("_-")[:50]
    return name or None


PAGE = """
<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chụp ảnh</title>
<style>
  :root { --bg:#f5f5f7; --card:#fff; --text:#1d1d1f; --accent:#0071e3; --muted:#6e6e73; --border:#d2d2d7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#111; --card:#1c1c1e; --text:#f5f5f7; --muted:#98989d; --border:#3a3a3c; }
  }
  * { box-sizing: border-box; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         background:var(--bg); color:var(--text); }
  .wrap { max-width:640px; margin:0 auto; padding:16px; }
  h1 { font-size:1.3rem; margin:8px 0 16px; }
  .card { background:var(--card); border-radius:14px; padding:12px; margin-bottom:16px; }
  label.lbl { display:block; font-weight:600; margin-bottom:6px; }
  #nameInput { width:100%; padding:12px; font-size:1rem; border-radius:10px;
         border:1px solid var(--border); background:var(--bg); color:var(--text); }
  #nameInput.error { border-color:#ff3b30; }
  video, #preview { width:100%; border-radius:10px; background:#000; display:block; }
  #preview { display:none; }
  .row { display:flex; gap:8px; flex-wrap:wrap; margin-top:12px; }
  button, .filebtn { flex:1; min-width:120px; padding:12px; border:0; border-radius:10px;
         font-size:1rem; cursor:pointer; background:var(--accent); color:#fff; text-align:center; }
  button.secondary, .filebtn { background:#8e8e93; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  #status { margin-top:10px; font-size:.95rem; color:var(--muted); min-height:1.2em; }
  .grid { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; }
  .grid img { width:100%; aspect-ratio:1; object-fit:cover; border-radius:8px; }
  input[type=file] { display:none; }
</style>
</head>
<body>
<div class="wrap">
  <h1>📷 Chụp ảnh &amp; lưu lên server</h1>

  <div class="card">
    <label class="lbl" for="nameInput">Tên (ảnh sẽ lưu vào folder theo tên này)</label>
    <input id="nameInput" type="text" list="nameList" placeholder="Ví dụ: Nguyen Van A" autocomplete="off" maxlength="50">
    <datalist id="nameList"></datalist>
  </div>

  <div class="card">
    <video id="video" autoplay playsinline muted></video>
    <img id="preview" alt="Ảnh vừa chụp">
    <canvas id="canvas" hidden></canvas>

    <div class="row">
      <button id="btnStart">Bật camera</button>
      <button id="btnSwitch" class="secondary" disabled>Đổi camera</button>
    </div>
    <div class="row">
      <button id="btnShot" disabled>Chụp &amp; lưu</button>
      <label class="filebtn">Dùng camera hệ thống
        <input id="fileInput" type="file" accept="image/*" capture="environment">
      </label>
    </div>
    <div id="status"></div>
  </div>

  <div class="card">
    <strong id="galleryTitle">Ảnh đã lưu</strong>
    <div class="grid" id="gallery" style="margin-top:10px"></div>
  </div>
</div>

<script>
const video = document.getElementById('video');
const canvas = document.getElementById('canvas');
const preview = document.getElementById('preview');
const statusEl = document.getElementById('status');
const btnStart = document.getElementById('btnStart');
const btnSwitch = document.getElementById('btnSwitch');
const btnShot = document.getElementById('btnShot');
const fileInput = document.getElementById('fileInput');
const nameInput = document.getElementById('nameInput');

let stream = null;
let facing = 'environment';

const setStatus = (t) => statusEl.textContent = t;

// Nhớ tên lần trước
try { nameInput.value = localStorage.getItem('photoName') || ''; } catch (e) {}

function getName() {
  const n = nameInput.value.trim();
  if (!n) {
    nameInput.classList.add('error');
    nameInput.focus();
    setStatus('⚠️ Vui lòng nhập tên trước khi chụp.');
    return null;
  }
  nameInput.classList.remove('error');
  try { localStorage.setItem('photoName', n); } catch (e) {}
  return n;
}

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setStatus('Trình duyệt không cho mở camera trực tiếp (cần HTTPS hoặc localhost). ' +
              'Hãy dùng nút "Dùng camera hệ thống".');
    return;
  }
  stopCamera();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false
    });
    video.srcObject = stream;
    video.style.display = 'block';
    preview.style.display = 'none';
    btnShot.disabled = false;
    btnSwitch.disabled = false;
    btnStart.textContent = 'Tắt camera';
    setStatus('Camera đã sẵn sàng.');
  } catch (e) {
    setStatus('Không mở được camera: ' + e.message);
  }
}

function stopCamera() {
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
  video.srcObject = null;
  btnShot.disabled = true;
  btnSwitch.disabled = true;
  btnStart.textContent = 'Bật camera';
}

btnStart.onclick = () => stream ? stopCamera() : startCamera();
btnSwitch.onclick = () => { facing = facing === 'environment' ? 'user' : 'environment'; startCamera(); };

btnShot.onclick = () => {
  const name = getName();
  if (!name || !video.videoWidth) return;
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext('2d').drawImage(video, 0, 0);
  canvas.toBlob(blob => upload(blob, 'capture.jpg', name), 'image/jpeg', 0.92);
};

fileInput.onchange = () => {
  const f = fileInput.files[0];
  fileInput.value = '';
  if (!f) return;
  const name = getName();
  if (name) upload(f, f.name, name);
};

async function upload(blob, filename, name) {
  setStatus('Đang lưu...');
  preview.src = URL.createObjectURL(blob);
  preview.style.display = 'block';
  video.style.display = 'none';
  const fd = new FormData();
  fd.append('photo', blob, filename);
  fd.append('name', name);
  try {
    const res = await fetch('/upload', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Lỗi không xác định');
    setStatus('✅ Đã lưu: ' + data.folder + '/' + data.filename);
    loadNames();
    loadGallery();
  } catch (e) {
    setStatus('❌ ' + e.message);
  }
  if (stream) setTimeout(() => { preview.style.display = 'none'; video.style.display = 'block'; }, 1500);
}

async function loadNames() {
  const res = await fetch('/api/names');
  const data = await res.json();
  const dl = document.getElementById('nameList');
  dl.innerHTML = '';
  data.names.forEach(n => {
    const o = document.createElement('option');
    o.value = n.replace(/_/g, ' ');
    dl.appendChild(o);
  });
}

async function loadGallery() {
  const name = nameInput.value.trim();
  const title = document.getElementById('galleryTitle');
  const g = document.getElementById('gallery');
  g.innerHTML = '';
  if (!name) { title.textContent = 'Ảnh đã lưu (nhập tên để xem)'; return; }
  const res = await fetch('/api/photos?name=' + encodeURIComponent(name));
  const data = await res.json();
  title.textContent = data.folder ? 'Ảnh của "' + data.folder + '" (' + data.photos.length + ')' : 'Ảnh đã lưu';
  data.photos.forEach(n => {
    const url = '/photos/' + encodeURIComponent(data.folder) + '/' + encodeURIComponent(n);
    const a = document.createElement('a');
    a.href = url; a.target = '_blank';
    const img = document.createElement('img');
    img.src = url; img.loading = 'lazy';
    a.appendChild(img); g.appendChild(a);
  });
}

nameInput.addEventListener('input', () => nameInput.classList.remove('error'));
nameInput.addEventListener('change', loadGallery);
loadNames();
loadGallery();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/upload", methods=["POST"])
def upload():
    folder = safe_folder_name(request.form.get("name"))
    if folder is None:
        return jsonify(error="Vui lòng nhập tên hợp lệ"), 400

    file = request.files.get("photo")
    if file is None:
        return jsonify(error="Không có file ảnh"), 400

    data = file.read()
    ext = detect_ext(data)
    if ext is None:
        return jsonify(error="File không phải ảnh hợp lệ (jpg/png/webp)"), 400

    # Nếu folder theo tên chưa có thì tạo mới, có rồi thì dùng lại
    person_dir = os.path.join(SAVE_DIR, folder)
    os.makedirs(person_dir, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{stamp}_{uuid.uuid4().hex[:6]}.{ext}"
    with open(os.path.join(person_dir, filename), "wb") as f:
        f.write(data)

    return jsonify(ok=True, folder=folder, filename=filename)


@app.route("/api/names")
def list_names():
    names = [n for n in os.listdir(SAVE_DIR) if os.path.isdir(os.path.join(SAVE_DIR, n))]
    names.sort(key=str.lower)
    return jsonify(names=names)


@app.route("/api/photos")
def list_photos():
    folder = safe_folder_name(request.args.get("name"))
    person_dir = os.path.join(SAVE_DIR, folder) if folder else None
    if not folder or not os.path.isdir(person_dir):
        return jsonify(folder=folder, photos=[])
    names = [n for n in os.listdir(person_dir) if n.rsplit(".", 1)[-1].lower() in IMG_EXTS]
    names.sort(reverse=True)
    return jsonify(folder=folder, photos=names[:30])


@app.route("/photos/<folder>/<filename>")
def get_photo(folder, filename):
    safe = safe_folder_name(folder)
    if safe != folder:
        return "Not found", 404
    return send_from_directory(os.path.join(SAVE_DIR, safe), filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Điện thoại chỉ mở được camera trực tiếp qua HTTPS -> đặt HTTPS=1
    if os.environ.get("HTTPS") == "1":
        app.run(host="0.0.0.0", port=port, ssl_context="adhoc")
    else:
        app.run(host="127.0.0.1", port=port)
