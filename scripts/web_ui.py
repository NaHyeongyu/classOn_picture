#!/usr/bin/env python
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Any
import uuid
import json
import shutil
from threading import Thread

from flask import Flask, Response, flash, redirect, render_template_string, request, send_from_directory, url_for, jsonify, send_file

# Resolve project paths so src package remains importable in PyInstaller bundles
IS_FROZEN = getattr(sys, "frozen", False)
if IS_FROZEN:
    APP_ROOT = Path(sys.executable).resolve().parent
    BASE_DIR = Path(getattr(sys, "_MEIPASS", APP_ROOT))
    sys.path.insert(0, str(BASE_DIR))
    sys.path.insert(0, str(BASE_DIR / "src"))
else:
    APP_ROOT = Path(__file__).resolve().parent.parent
    BASE_DIR = APP_ROOT
    sys.path.insert(0, str(BASE_DIR))
    sys.path.insert(0, str(BASE_DIR / "src"))

from src.pipeline import run_pipeline  # noqa: E402
from src.utils.fs import ensure_dir  # noqa: E402
from src.utils.logging import setup_logger  # noqa: E402


logger = setup_logger()

# Paths: support PyInstaller (frozen) bundle
if IS_FROZEN:
    # Static assets are embedded via --add-data into _MEIPASS/webui/static
    MEIPASS = BASE_DIR
    STATIC_DIR = MEIPASS / "webui" / "static"
else:
    MEIPASS = BASE_DIR
    STATIC_DIR = BASE_DIR / "scripts" / "webui" / "static"

DATA_ROOT = ensure_dir(APP_ROOT / "data")
DATA_IN = ensure_dir(DATA_ROOT / "input")
DATA_OUT = ensure_dir(DATA_ROOT / "output")

# Serve SPA static files at root path
APP = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/")
APP.secret_key = os.environ.get("FACE_MVP_SECRET", "dev-secret")

# Optional CORS for cross-origin frontends (e.g., Vite dev server)
try:
    from flask_cors import CORS

    CORS(APP, resources={r"/api/*": {"origins": "*"}, r"/out/*": {"origins": "*"}})
except Exception:
    pass

# Allow large uploads (set via env MAX_UPLOAD_MB, default 512MB)
try:
    _max_mb = int(os.environ.get("MAX_UPLOAD_MB", "512"))
except Exception:
    _max_mb = 512
APP.config["MAX_CONTENT_LENGTH"] = max(1, _max_mb) * 1024 * 1024  # bytes
APP.config["MAX_FORM_MEMORY_SIZE"] = APP.config["MAX_CONTENT_LENGTH"]

from werkzeug.exceptions import RequestEntityTooLarge  # noqa: E402

@APP.errorhandler(RequestEntityTooLarge)
def _too_large(e):
    return jsonify({
        "error": "too_large",
        "message": "Upload too large",
        "limit_mb": int(APP.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)),
    }), 413

# In-memory job states (simple MVP)
JOBS: Dict[str, Dict[str, Any]] = {}


INDEX_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet"/>
    <title>Face MVP — Upload</title>
  </head>
  <body>
    <div class="container my-4" style="max-width: 920px;">
      <h3 class="mb-3">학원 사진 자동 분류·추천 — 웹 UI</h3>
      {% with messages = get_flashed_messages() %}
        {% if messages %}
          <div class="alert alert-warning">{{ messages[0] }}</div>
        {% endif %}
      {% endwith %}

      <form method="post" action="{{ url_for('run') }}" enctype="multipart/form-data" class="card p-3 mb-4">
        <div class="mb-3">
          <label class="form-label">사진 업로드 (여러 장 선택 가능)</label>
          <input class="form-control" type="file" name="images" accept="image/*" multiple required>
          <div class="form-text">로컬에서 파일을 업로드합니다. 대용량은 압축(zip) 후 업로드 권장</div>
        </div>
        <div class="row g-3">
          <div class="col">
            <label class="form-label">Top-K</label>
            <input class="form-control" type="number" name="topk" value="3" min="1" max="10">
          </div>
          <div class="col">
            <label class="form-label">min_cluster_size</label>
            <input class="form-control" type="number" name="mcs" value="5" min="2" max="50">
          </div>
        </div>
        <div class="mt-3 d-flex gap-2">
          <button class="btn btn-primary" type="submit">실행</button>
          <a class="btn btn-outline-secondary" href="{{ url_for('sessions') }}">세션 목록</a>
        </div>
      </form>

      <div class="card p-3">
        <div class="d-flex justify-content-between align-items-center">
          <h5 class="m-0">최근 세션</h5>
          <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('sessions') }}">전체 보기</a>
        </div>
        <ul class="mt-3">
          {% for sid in recent %}
            <li>
              <a href="{{ url_for('ui', sid=sid) }}">{{ sid }}</a>
              <small class="ms-2"><a href="{{ url_for('groups', sid=sid) }}">원본 그룹</a></small>
            </li>
          {% else %}
            <li class="text-muted">세션 없음</li>
          {% endfor %}
        </ul>
      </div>
    </div>
  </body>
  </html>
"""


# Interactive UI (progress + grouped originals)
UI_HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet"/>
    <title>진행 현황</title>
    <style>
      .thumb { object-fit: cover; width: 100%; height: 120px; }
    </style>
  </head>
  <body>
    <div class="container my-4">
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h3 class="m-0">처리 진행</h3>
        <div class="btn-group">
          <a class="btn btn-outline-secondary btn-sm" href="{{ url_for('index') }}">업로드</a>
          <a class="btn btn-outline-secondary btn-sm" href="{{ url_for('sessions') }}">세션 목록</a>
        </div>
      </div>
      <div id="progressArea" class="card p-3 mb-3">
        <div class="d-flex justify-content-between"><div>단계: <span id="stage">대기</span></div><div><span id="pct">0</span>%</div></div>
        <div class="progress mt-2" role="progressbar" aria-label="progress">
          <div id="bar" class="progress-bar progress-bar-striped progress-bar-animated" style="width: 0%"></div>
        </div>
      </div>

      <div id="resultArea" style="display:none">
        <h4 class="mb-3">원본 사진 — 인물별</h4>
        <div id="persons" class="row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3"></div>

        <div class="my-4">
          <h5 class="mb-2">분리되지 않은 사진</h5>
          <div class="mb-1"><span class="badge bg-secondary">Noise (얼굴은 있으나 미분류)</span></div>
          <div id="noise" class="row row-cols-4 g-2"></div>
          <div class="mt-3 mb-1"><span class="badge bg-secondary">No Face (얼굴 없음)</span></div>
          <div id="noface" class="row row-cols-4 g-2"></div>
        </div>
      </div>
    </div>

    <script>
      const sid = "{{ sid }}";
      const statusUrl = "/status/" + sid;
      const groupUrl = "/group-data/" + sid;

      async function poll() {
        try {
          const r = await fetch(statusUrl, {cache: 'no-store'});
          const s = await r.json();
          const pct = Math.max(0, Math.min(100, Math.round(s.percent || 0)));
          document.getElementById('pct').textContent = pct;
          document.getElementById('bar').style.width = pct + '%';
          document.getElementById('stage').textContent = s.stage || '진행중';
          if (s.done || s.stage === 'done') {
            await showResults();
            return;
          }
        } catch (e) {
          console.log('status error', e);
        }
        setTimeout(poll, 800);
      }

      function makeThumb(src) {
        return `<div class='col'><a href='${src}' target='_blank'><img class='img-fluid rounded thumb' src='${src}'/></a></div>`;
      }

      async function showResults() {
        try {
          const r = await fetch(groupUrl, {cache: 'no-store'});
          const g = await r.json();
          const base = '/out/' + sid + '/';
          // Persons
          const personsDiv = document.getElementById('persons');
          personsDiv.innerHTML = '';
          const keys = Object.keys(g.clusters_to_photos || {}).filter(k => /^\d+$/.test(k)).map(k => parseInt(k)).sort((a,b)=>a-b);
          for (const k of keys) {
            const rels = g.clusters_to_photos[String(k)] || [];
            const thumbs = rels.slice(0, 12).map(p => makeThumb(base + p)).join('');
            const fallback = "<div class='text-muted'>이미지 없음</div>";
            const body = thumbs || fallback;
            const card = `
              <div class='col'>
                <div class='card h-100'>
                  <div class='card-header'><strong>인물 ${k}</strong> <span class='badge bg-light text-dark'>${rels.length}</span></div>
                  <div class='card-body'><div class='row row-cols-4 g-2'>${body}</div></div>
                </div>
              </div>`;
            personsDiv.insertAdjacentHTML('beforeend', card);
          }
          if (keys.length === 0) {
            personsDiv.innerHTML = "<div class='text-muted'>클러스터가 없습니다.</div>";
          }

          // Noise and No Face
          const noiseDiv = document.getElementById('noise');
          const nfDiv = document.getElementById('noface');
          const noise = (g.clusters_to_photos && g.clusters_to_photos['noise']) || [];
          const noface = g.no_face || [];
          noiseDiv.innerHTML = noise.slice(0, 24).map(p => makeThumb(base + p)).join('') || "<div class='text-muted'>없음</div>";
          nfDiv.innerHTML = noface.slice(0, 24).map(p => makeThumb(base + p)).join('') || "<div class='text-muted'>없음</div>";

          // Toggle areas
          document.getElementById('progressArea').style.display = 'none';
          document.getElementById('resultArea').style.display = '';
        } catch (e) {
          console.log('group error', e);
          setTimeout(showResults, 1000);
        }
      }

      poll();
    </script>
  </body>
  </html>
"""


@APP.route("/")
def index() -> Response:
    # Serve SPA entrypoint
    return send_from_directory(STATIC_DIR, "index.html")


@APP.get("/api/health")
def api_health() -> Response:
    return jsonify({"ok": True, "service": "face-mvp", "version": 1})


@APP.post("/api/purge-all")
def api_purge_all() -> Response:
    """
    Danger: Remove all contents under data/input and data/output to free space.
    Keeps the directories themselves. Returns simple stats.
    """
    def _purge(root: Path) -> Dict[str, int]:
        files = 0
        dirs = 0
        errs = 0
        try:
            root.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        for p in list(root.iterdir()) if root.exists() else []:
            try:
                if p.is_symlink() or p.is_file():
                    try:
                        p.unlink()
                    except FileNotFoundError:
                        pass
                    files += 1
                elif p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                    dirs += 1
            except Exception:
                errs += 1
        return {"files": files, "dirs": dirs, "errors": errs}

    in_stats = _purge(DATA_IN)
    out_stats = _purge(DATA_OUT)
    try:
        JOBS.clear()
    except Exception:
        pass
    return jsonify({"ok": True, "input": in_stats, "output": out_stats})


@APP.route("/api/upload", methods=["POST"])
def api_upload() -> Response:
    # Support two modes:
    # 1) Whole-file mode: multiple image files per request (fields: files/images)
    # 2) Chunk mode: single binary chunk (field: chunk) + meta(file_name, chunk_index, chunk_total)
    files = request.files.getlist("files") or request.files.getlist("images")
    chunk = request.files.get("chunk")
    if not files and not chunk:
        return jsonify({"error": "no_files"}), 400
    # Fixed defaults (UI에서 항목 제거)
    topk = 3
    mcs = 5
    link_flag = (request.form.get("link") or "").lower() in {"1", "true", "yes", "y"}

    req_job = (request.form.get("job_id") or "").strip()
    final_flag = (request.form.get("final") or "").lower() in {"1", "true", "yes", "y"}
    started = False

    if req_job:
        job_id = req_job
    else:
        job_id = uuid.uuid4().hex

    in_dir = ensure_dir(DATA_IN / job_id)
    out_dir = ensure_dir(DATA_OUT / job_id)

    saved = 0
    received_info: Dict[str, Any] | None = None
    if chunk is not None:
        # Chunk mode
        file_name = (request.form.get("file_name") or chunk.filename or "blob.bin").strip()
        if not file_name:
            file_name = "blob.bin"
        # basic extension allowlist for chunked uploads (match whole-file mode)
        _ext = Path(file_name).suffix.lower()
        if _ext not in {".jpg", ".jpeg", ".png"}:
            return jsonify({"error": "unsupported_ext", "message": "Only .jpg/.jpeg/.png allowed"}), 400
        idx = int((request.form.get("chunk_index") or 0))
        total = int((request.form.get("chunk_total") or 1))
        target = in_dir / Path(file_name).name
        try:
            if idx == 0 and target.exists():
                target.unlink()
        except Exception:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        # Append chunk to target file
        try:
            try:
                chunk.stream.seek(0)
            except Exception:
                pass
            data_bytes = chunk.stream.read()
            if not data_bytes:
                data_bytes = chunk.read()
            with open(target, "ab") as w:
                w.write(data_bytes)
        except Exception:
            data_bytes = chunk.read()
            with open(target, "ab") as w:
                w.write(data_bytes)
        saved = 1
        received_info = {"file_name": Path(file_name).name, "chunk_index": idx, "chunk_total": total}
    else:
        # Whole-file mode
        for f in files:
            if not f.filename:
                continue
            fname = Path(f.filename).name
            if not any(fname.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
                continue
            try:
                f.save(str(in_dir / fname))
            except Exception:
                (in_dir / fname).write_bytes(f.read())
            saved += 1
        if saved == 0:
            return jsonify({"error": "no_supported_files"}), 400

    # Start policy:
    # - If client provided job_id: start only when final=1
    # - If job_id not provided: start immediately unless final=0 is explicitly given
    if req_job:
        if final_flag:
            t = Thread(target=_run_job, args=(job_id, in_dir, out_dir, topk, mcs, link_flag), daemon=True)
            t.start()
            started = True
    else:
        if not (request.form.get("final") or ""):  # backward compat: start on single-shot upload
            t = Thread(target=_run_job, args=(job_id, in_dir, out_dir, topk, mcs, link_flag), daemon=True)
            t.start()
            started = True
        elif final_flag:
            t = Thread(target=_run_job, args=(job_id, in_dir, out_dir, topk, mcs, link_flag), daemon=True)
            t.start()
            started = True

    resp = {"job_id": job_id, "started": started}
    if received_info:
        resp["received"] = received_info
    return jsonify(resp)


@APP.get("/api/progress")
def api_progress() -> Response:
    job_id = request.args.get("job_id", "").strip()
    if not job_id:
        return jsonify({"error": "missing_job_id"}), 400
    st = JOBS.get(job_id)
    if not st:
        cfg = DATA_OUT / job_id / "status.json"
        if cfg.exists():
            try:
                st = json.loads(cfg.read_text(encoding="utf-8"))
            except Exception:
                st = None
    if not st:
        return jsonify({"phase": "queued", "progress": 0.0, "counts": {}})

    percent = float(st.get("percent", 0.0))
    phase = st.get("stage") or st.get("phase") or "running"
    extra = st.get("extra") or {}
    counts = {
        "photos_done": int(extra.get("processed", extra.get("photos", 0)) or 0),
        "faces_done": int(extra.get("faces", 0) or 0),
        "faces_total_est": int(extra.get("faces", 0) or 0),
    }
    return jsonify({
        "phase": phase,
        "progress": max(0.0, min(1.0, percent / 100.0)),
        "counts": counts,
    })


@APP.get("/api/result")
def api_result() -> Response:
    job_id = request.args.get("job_id", "").strip()
    if not job_id:
        return jsonify({"error": "missing_job_id"}), 400
    cfg = DATA_OUT / job_id / "clusters.json"
    if not cfg.exists():
        return jsonify({"error": "not_found"}), 404
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"error": "invalid_json", "message": str(e)}), 500

    grouping = data.get("grouping", {})
    clusters_map: Dict[str, List[str]] = grouping.get("clusters_to_photos", {})
    labels_map: Dict[str, str] = grouping.get("labels", {}) or {}
    hidden_set = {str(h) for h in (grouping.get("hidden_clusters") or [])}
    numeric_ids = sorted([int(k) for k in clusters_map.keys() if str(k).isdigit()])
    clusters_out: List[Dict[str, Any]] = []
    def _mk_item(p: str) -> Dict[str, str]:
        try:
            from pathlib import Path as _P
            rp = _P(p)
            if len(rp.parts) and rp.parts[0] == 'grouped_photos':
                tail = _P(*rp.parts[1:])
                prev_rel = _P('previews') / tail
            else:
                prev_rel = _P('previews') / rp
            prev_rel = prev_rel.with_suffix('.webp')
            prev_url = f"/out/{job_id}/{prev_rel.as_posix()}"
        except Exception:
            prev_url = f"/out/{job_id}/{p}"
        base_url = f"/out/{job_id}/{p}"
        return {"photo": base_url, "thumb": prev_url, "preview": prev_url}

    person_idx = 0
    for cid in numeric_ids:
        key = str(cid)
        if key in hidden_set:
            continue
        rels = clusters_map.get(key, [])
        person_idx += 1
        originals = [_mk_item(p) for p in rels]
        default_label = f"인물 {person_idx}"
        custom_label = labels_map.get(key, "")
        clusters_out.append({
            "cluster_id": cid,
            "name": custom_label or default_label,
            "default_name": default_label,
            "custom_name": custom_label,
            "originals": originals,
            "is_noise": False,
            "count": len(rels),
        })

    noise = clusters_map.get("noise", [])
    no_face = grouping.get("no_face", [])
    unassigned = [_mk_item(p) for p in (noise + no_face)]

    meta = {
        "total_photos": len(data.get("photos", [])),
        "total_faces": len(data.get("faces", [])),
    }
    return jsonify({
        "meta": meta,
        "clusters": clusters_out,
        "unassigned": unassigned,
    })


@APP.post("/api/delete")
def api_delete() -> Response:
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}
    job_id = (payload.get("job_id") or "").strip()
    rel = (payload.get("path") or "").strip()
    if not job_id or not rel:
        return jsonify({"error": "missing_params"}), 400
    base = DATA_OUT / job_id
    cfg = base / "clusters.json"
    if not cfg.exists():
        return jsonify({"error": "not_found"}), 404
    # Normalize and validate path
    target = (base / rel).resolve()
    if not str(target).startswith(str(base.resolve())):
        return jsonify({"error": "invalid_path"}), 400
    # Update grouping JSON
    import json as _json
    data = _json.loads(cfg.read_text(encoding="utf-8"))
    grouping = data.get("grouping") or {}
    ctps = grouping.get("clusters_to_photos") or {}
    labels = grouping.get("labels") or {}
    hidden_list = grouping.get("hidden_clusters") or []
    hidden_norm: List[int] = []
    for item in hidden_list:
        try:
            hidden_norm.append(int(item))
        except Exception:
            continue
    grouping_changed = False
    data_changed = False
    emptied: List[int] = []
    # Remove from any cluster lists
    for k, lst in list(ctps.items()):
        if rel in lst:
            lst2 = [x for x in lst if x != rel]
            if lst2:
                ctps[k] = lst2
            else:
                ctps.pop(k, None)
                if str(k).isdigit():
                    cid_val = int(k)
                    emptied.append(cid_val)
                    labels.pop(str(cid_val), None)
            grouping_changed = True
    grouping["clusters_to_photos"] = ctps
    # Remove from no_face
    nf = grouping.get("no_face") or []
    if rel in nf:
        grouping["no_face"] = [x for x in nf if x != rel]
        grouping_changed = True
    if emptied:
        for cid_val in emptied:
            if cid_val not in hidden_norm:
                hidden_norm.append(cid_val)
        grouping["hidden_clusters"] = hidden_norm
    grouping["labels"] = labels
    # Remove photo metadata/original if identifiable
    photo_id = None
    try:
        rel_name = Path(rel).name
        prefix = rel_name.split("_", 1)[0]
        if prefix.isdigit():
            photo_id = int(prefix)
    except Exception:
        photo_id = None

    removed_face_ids: set[int] = set()
    if photo_id is not None:
        photos = data.get("photos", [])
        photo_entry = None
        for p in photos:
            if int(p.get("id")) == photo_id:
                photo_entry = p
                break
        if photo_entry:
            # Delete original file
            orig_rel = photo_entry.get("path")
            if isinstance(orig_rel, str):
                try:
                    orig_abs = (base / orig_rel).resolve()
                    if orig_abs.exists() and orig_abs.is_file():
                        orig_abs.unlink()
                except Exception:
                    pass
            data["photos"] = [p for p in photos if int(p.get("id")) != photo_id]
            data_changed = True
            # Remove faces referencing this photo
            faces = data.get("faces", [])
            removed_face_ids = {int(f.get("id")) for f in faces if int(f.get("photo_id", -1)) == photo_id}
            if removed_face_ids:
                data["faces"] = [f for f in faces if int(f.get("photo_id", -1)) != photo_id]
                data_changed = True
            # Update clusters to drop removed faces
            if removed_face_ids:
                new_clusters = []
                for c in data.get("clusters", []):
                    member_ids = c.get("member_face_ids", []) or []
                    filtered_members = [int(fid) for fid in member_ids if int(fid) not in removed_face_ids]
                    if len(filtered_members) != len(member_ids):
                        c["member_face_ids"] = filtered_members
                        c["size"] = len(filtered_members)
                        c["top"] = [t for t in c.get("top", []) if int(t.get("face_id", -1)) not in removed_face_ids]
                        data_changed = True
                        if c.get("cluster_id") is not None and not c.get("is_noise", False) and c["size"] == 0:
                            cid_val = int(c["cluster_id"])
                            if cid_val not in emptied:
                                emptied.append(cid_val)
                                labels.pop(str(cid_val), None)
                    new_clusters.append(c)
                data["clusters"] = new_clusters

    if emptied:
        # ensure hidden list updated after potential new empties
        hidden_norm = list({*hidden_norm, *emptied})
        grouping["hidden_clusters"] = hidden_norm
        grouping_changed = True
        # Drop empty clusters payload records
        clusters_list = data.get("clusters", [])
        kept_clusters = []
        for c in clusters_list:
            try:
                cid_val = int(c.get("cluster_id"))
            except Exception:
                cid_val = None
            if cid_val is not None and cid_val in emptied:
                data_changed = True
                continue
            kept_clusters.append(c)
        if len(kept_clusters) != len(clusters_list):
            data["clusters"] = kept_clusters

    if grouping_changed or data_changed:
        data["grouping"] = grouping
        cfg.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Delete physical file (best effort)
    try:
        if target.exists() or target.is_symlink():
            target.unlink()
    except Exception:
        pass

    # Remove associated preview (best effort)
    try:
        rel_path = Path(rel)
        if rel_path.parts and rel_path.parts[0] == "grouped_photos":
            tail = Path(*rel_path.parts[1:])
            prev_rel = Path("previews") / tail
            prev_rel = prev_rel.with_suffix('.webp')
            preview_target = (base / prev_rel).resolve()
            if str(preview_target).startswith(str(base.resolve())):
                if preview_target.exists() or preview_target.is_symlink():
                    preview_target.unlink()
    except Exception:
        pass

    # Remove cluster directories when emptied
    for cid_val in emptied:
        try:
            cluster_dir = base / "grouped_photos" / f"person_{cid_val:03d}"
            if cluster_dir.exists():
                shutil.rmtree(cluster_dir)
        except Exception:
            pass
        try:
            preview_dir = base / "previews" / f"person_{cid_val:03d}"
            if preview_dir.exists():
                shutil.rmtree(preview_dir)
        except Exception:
            pass
    return jsonify({"ok": True, "removed": rel})


@APP.post("/api/cluster/rename")
def api_cluster_rename() -> Response:
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}
    job_id = (payload.get("job_id") or "").strip()
    name_raw = (payload.get("name") or "").strip()
    cid = payload.get("cid")
    if not job_id or cid is None:
        return jsonify({"error": "missing_params"}), 400
    try:
        cid = int(cid)
    except Exception:
        return jsonify({"error": "invalid_cid"}), 400
    if cid < 0:
        return jsonify({"error": "unsupported_cid"}), 400

    cfg = DATA_OUT / job_id / "clusters.json"
    if not cfg.exists():
        return jsonify({"error": "not_found"}), 404
    import json as _json
    data = _json.loads(cfg.read_text(encoding="utf-8"))
    grouping = data.get("grouping") or {}
    labels = grouping.get("labels") or {}

    label = name_raw[:80] if name_raw else ""
    key = str(cid)
    if label:
        labels[key] = label
    else:
        labels.pop(key, None)

    grouping["labels"] = labels
    data["grouping"] = grouping
    cfg.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "cid": cid, "name": label})


@APP.post("/api/cluster/delete")
def api_cluster_delete() -> Response:
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}
    job_id = (payload.get("job_id") or "").strip()
    cid = payload.get("cid")
    if not job_id or cid is None:
        return jsonify({"error": "missing_params"}), 400
    try:
        cid = int(cid)
    except Exception:
        return jsonify({"error": "invalid_cid"}), 400
    if cid < 0:
        return jsonify({"error": "unsupported_cid"}), 400

    cfg = DATA_OUT / job_id / "clusters.json"
    if not cfg.exists():
        return jsonify({"error": "not_found"}), 404

    def _preview_rel(rel: str) -> Path:
        p = Path(rel)
        if p.parts and p.parts[0] == "grouped_photos":
            tail = Path(*p.parts[1:])
            base = Path("previews") / tail
        else:
            base = Path("previews") / p
        return base.with_suffix('.webp')

    import json as _json
    data = _json.loads(cfg.read_text(encoding="utf-8"))
    grouping = data.get("grouping") or {}
    ctps: Dict[str, List[str]] = grouping.get("clusters_to_photos") or {}
    labels = grouping.get("labels") or {}
    hidden_list = grouping.get("hidden_clusters") or []

    key = str(cid)
    rels = ctps.pop(key, [])
    grouping["clusters_to_photos"] = ctps
    labels.pop(key, None)
    grouping["labels"] = labels

    hidden_norm: List[int] = []
    for item in hidden_list:
        try:
            hidden_norm.append(int(item))
        except Exception:
            continue
    if cid not in hidden_norm:
        hidden_norm.append(cid)
    grouping["hidden_clusters"] = hidden_norm

    base = DATA_OUT / job_id
    # Delete files that belonged to the cluster
    removed = 0
    for rel in rels:
        target = (base / rel).resolve()
        if str(target).startswith(str(base.resolve())):
            try:
                if target.exists() or target.is_symlink():
                    target.unlink()
                    removed += 1
            except Exception:
                pass
        try:
            prev_rel = _preview_rel(rel)
            prev_abs = (base / prev_rel).resolve()
            if str(prev_abs).startswith(str(base.resolve())) and (prev_abs.exists() or prev_abs.is_symlink()):
                prev_abs.unlink()
        except Exception:
            pass

    # Remove cluster directories (best effort)
    cluster_dir = base / "grouped_photos" / f"person_{cid:03d}"
    preview_dir = base / "previews" / f"person_{cid:03d}"
    for folder in (cluster_dir, preview_dir):
        try:
            if folder.exists():
                shutil.rmtree(folder)
        except Exception:
            pass

    data["grouping"] = grouping
    cfg.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "cid": cid, "removed_files": removed})


@APP.post("/api/assign")
def api_assign() -> Response:
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}
    job_id = (payload.get("job_id") or "").strip()
    rel = (payload.get("path") or "").strip()
    target_cid = payload.get("target_cid")
    if not job_id or not rel or target_cid is None:
        return jsonify({"error": "missing_params"}), 400
    try:
        target_cid = int(target_cid)
    except Exception:
        return jsonify({"error": "invalid_cid"}), 400
    base = DATA_OUT / job_id
    cfg = base / "clusters.json"
    if not cfg.exists():
        return jsonify({"error": "not_found"}), 404
    import json as _json
    data = _json.loads(cfg.read_text(encoding="utf-8"))
    grouping = data.get("grouping") or {}
    ctps: Dict[str, List[str]] = grouping.get("clusters_to_photos") or {}

    # Compute destination rel path under grouped_photos/person_{cid:03d}
    src_rel = Path(rel)
    if src_rel.parts and src_rel.parts[0] == "previews":
        # convert preview path to grouped_photos
        tail = Path(*src_rel.parts[1:])
        src_rel = Path("grouped_photos") / tail
        src_rel = src_rel.with_suffix("")  # drop .webp
    if not (src_rel.parts and src_rel.parts[0] == "grouped_photos"):
        return jsonify({"error": "invalid_path"}), 400

    src_abs = (base / src_rel).resolve()
    if not str(src_abs).startswith(str(base.resolve())):
        return jsonify({"error": "invalid_path"}), 400
    if not src_abs.exists():
        return jsonify({"error": "missing_file"}), 404

    dst_rel = Path("grouped_photos") / (f"person_{target_cid:03d}") / src_abs.name
    dst_abs = (base / dst_rel).resolve()
    dst_abs.parent.mkdir(parents=True, exist_ok=True)

    # Remove from any existing entries
    changed = False
    for k, lst in list(ctps.items()):
        if str(src_rel) in lst:
            ctps[k] = [x for x in lst if x != str(src_rel)]
            changed = True
    nf = grouping.get("no_face") or []
    if str(src_rel) in nf:
        grouping["no_face"] = [x for x in nf if x != str(src_rel)]
        changed = True
    # Also noise bucket
    noise_list = ctps.get("noise") or []
    if str(src_rel) in noise_list:
        ctps["noise"] = [x for x in noise_list if x != str(src_rel)]
        changed = True

    # Physically move file
    try:
        if dst_abs.exists():
            dst_abs.unlink()
        src_abs.replace(dst_abs)
    except Exception:
        # fallback: copy then remove
        try:
            import shutil
            shutil.copy2(src_abs, dst_abs)
            src_abs.unlink(missing_ok=True)  # type: ignore
        except Exception:
            pass

    # Add to target cluster list
    key = str(target_cid)
    lst = ctps.get(key) or []
    if str(dst_rel) not in lst:
        lst.append(str(dst_rel))
    ctps[key] = lst
    grouping["clusters_to_photos"] = ctps

    hidden_list = grouping.get("hidden_clusters") or []
    hidden_norm: List[int] = []
    for item in hidden_list:
        try:
            hidden_norm.append(int(item))
        except Exception:
            continue
    if target_cid in hidden_norm:
        hidden_norm = [h for h in hidden_norm if h != target_cid]
    grouping["hidden_clusters"] = hidden_norm

    data["grouping"] = grouping
    cfg.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "moved": {"from": str(src_rel), "to": str(dst_rel)}})


@APP.post("/api/reorder")
def api_reorder() -> Response:
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}
    job_id = (payload.get("job_id") or "").strip()
    cid = payload.get("cid")
    order = payload.get("order")
    if not job_id or cid is None or not isinstance(order, list):
        return jsonify({"error": "missing_params"}), 400
    try:
        cid = int(cid)
    except Exception:
        return jsonify({"error": "invalid_cid"}), 400
    base = DATA_OUT / job_id
    cfg = base / "clusters.json"
    if not cfg.exists():
        return jsonify({"error": "not_found"}), 404
    import json as _json
    data = _json.loads(cfg.read_text(encoding="utf-8"))
    grouping = data.get("grouping") or {}
    ctps: Dict[str, List[str]] = grouping.get("clusters_to_photos") or {}
    key = str(cid)
    # sanitize: keep only items that point under grouped_photos
    clean = []
    for rel in order:
        try:
            p = Path(rel)
            if p.parts and p.parts[0] == "grouped_photos":
                clean.append(str(p))
        except Exception:
            continue
    ctps[key] = clean
    grouping["clusters_to_photos"] = ctps
    data["grouping"] = grouping
    cfg.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "cid": cid, "count": len(clean)})


@APP.post("/api/export")
def api_export() -> Response:
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}
    job_id = (payload.get("job_id") or "").strip()
    paths = payload.get("paths") or []
    if not job_id or not isinstance(paths, list) or not paths:
        return jsonify({"error": "missing_params"}), 400
    base = DATA_OUT / job_id
    from io import BytesIO
    import zipfile
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        used = set()
        for rel in paths:
            try:
                src = (base / rel).resolve()
                # security: must be under base
                if not str(src).startswith(str(base.resolve())):
                    continue
                if not src.exists():
                    continue
                # Compute arcname (cluster folder + filename)
                arcname = rel
                # Flatten previews path to original grouped_photos
                if arcname.startswith("previews/"):
                    arcname = "grouped_photos/" + arcname[len("previews/"):]
                    arcname = os.path.splitext(arcname)[0]  # drop .webp
                # If arcname duplicates, append index
                name = arcname
                i = 1
                while name in used:
                    stem, ext = os.path.splitext(arcname)
                    name = f"{stem}_{i}{ext}"
                    i += 1
                used.add(name)
                # Resolve symlink to actual file content
                try:
                    if src.is_symlink():
                        real = Path(os.path.realpath(src))
                    else:
                        real = src
                    zf.write(str(real), arcname=name)
                except Exception:
                    continue
            except Exception:
                continue
    buf.seek(0)
    ts = time.strftime("%Y%m%d-%H%M%S")
    return Response(buf.read(), mimetype="application/zip", headers={
        "Content-Disposition": f"attachment; filename=selected_{job_id}_{ts}.zip"
    })


def _save_status(sid: str, out_dir: Path, stage: str, percent: float, extra: Dict[str, Any] | None = None) -> None:
    import json
    st = {
        "sid": sid,
        "stage": stage,
        "percent": float(percent),
        "extra": extra or {},
        "done": stage == "done",
    }
    JOBS[sid] = st
    try:
        (out_dir / "status.json").write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass


def _run_job(sid: str, in_dir: Path, out_dir: Path, topk: int, mcs: int, link_originals: bool = False) -> None:
    def cb(stage: str, pct: float, extra: Dict):
        _save_status(sid, out_dir, stage, pct, extra)

    try:
        _save_status(sid, out_dir, "start", 0.0, {})
        # Adaptive min_cluster_size for small batches: make clusters appear with few photos
        try:
            num_imgs = sum(1 for p in in_dir.iterdir() if p.suffix.lower() in {'.jpg', '.jpeg', '.png'})
        except Exception:
            num_imgs = 0
        mcs_eff = int(mcs)
        if num_imgs <= 12:
            mcs_eff = min(mcs_eff, 2)
        elif num_imgs <= 30:
            mcs_eff = min(mcs_eff, 3)
        run_pipeline(str(in_dir), str(out_dir), topk=topk, min_cluster_size=mcs_eff, progress_cb=cb, link_originals=link_originals)
        _save_status(sid, out_dir, "done", 100.0, {})
    except Exception as e:
        _save_status(sid, out_dir, "error", 100.0, {"message": str(e)})


@APP.route("/run", methods=["POST"])
def run() -> Response:
    files = request.files.getlist("images")
    if not files:
        flash("업로드된 이미지가 없습니다.")
        return redirect(url_for("index"))

    topk = int(request.form.get("topk", 3))
    mcs = int(request.form.get("mcs", 5))

    sid = time.strftime("%Y%m%d-%H%M%S")
    in_dir = ensure_dir(DATA_IN / sid)
    out_dir = ensure_dir(DATA_OUT / sid)

    # Save uploaded files
    saved = 0
    for f in files:
        if not f.filename:
            continue
        fname = Path(f.filename).name
        if not any(fname.lower().endswith(ext) for ext in [".jpg", ".jpeg", ".png"]):
            continue
        (in_dir / fname).write_bytes(f.read())
        saved += 1

    if saved == 0:
        flash("지원되는 이미지가 없습니다. (jpg/jpeg/png)")
        return redirect(url_for("index"))

    # Run pipeline in background
    t = Thread(target=_run_job, args=(sid, in_dir, out_dir, topk, mcs), daemon=True)
    t.start()
    return redirect(url_for("ui", sid=sid))


@APP.route("/ui/<sid>")
def ui(sid: str):
    # Interactive page: shows progress, then grouped originals
    page = render_template_string(UI_HTML, sid=sid)
    return Response(page, mimetype="text/html")


@APP.route("/status/<sid>")
def status(sid: str):
    import json
    if sid in JOBS:
        return Response(json.dumps(JOBS[sid]), mimetype="application/json")
    cfg = DATA_OUT / sid / "status.json"
    if cfg.exists():
        return Response(cfg.read_text(encoding="utf-8"), mimetype="application/json")
    return Response(json.dumps({"sid": sid, "stage": "unknown", "percent": 0, "done": False}), mimetype="application/json")


@APP.route("/group-data/<sid>")
def group_data(sid: str):
    import json
    cfg = DATA_OUT / sid / "clusters.json"
    if not cfg.exists():
        return Response(json.dumps({"error": "not_found"}), status=404, mimetype="application/json")
    data = json.loads(cfg.read_text(encoding="utf-8"))
    grouping = data.get("grouping", {})
    return Response(json.dumps(grouping), mimetype="application/json")


@APP.route("/view/<sid>")
def view(sid: str):
    # Wrapper page with quick links + embedded report
    report_url = url_for('report', sid=sid)
    groups_url = url_for('groups', sid=sid)
    json_url = url_for('out_files', sid=sid, path='clusters.json')
    page = f"""
    <!doctype html><html><head>
      <meta charset='utf-8'>
      <meta name='viewport' content='width=device-width, initial-scale=1'>
      <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css' rel='stylesheet'>
      <title>세션 보기 — {sid}</title>
      <style>body,html,iframe{{height:100%}} .vh-80{{height:80vh}}</style>
    </head><body>
      <div class='container-fluid p-3'>
        <div class='d-flex gap-2 align-items-center mb-2'>
          <a class='btn btn-outline-secondary btn-sm' href='{url_for('sessions')}'>세션 목록</a>
          <a class='btn btn-primary btn-sm' href='{report_url}'>리포트</a>
          <a class='btn btn-outline-primary btn-sm' href='{groups_url}'>원본 그룹</a>
          <a class='btn btn-outline-secondary btn-sm' href='{json_url}' target='_blank'>clusters.json</a>
        </div>
        <iframe class='w-100 vh-80 border-0' src='{report_url}'></iframe>
      </div>
    </body></html>
    """
    return Response(page, mimetype="text/html")


@APP.route("/report/<sid>")
def report(sid: str):
    out_dir = DATA_OUT / sid
    report = out_dir / "report.html"
    if not report.exists():
        return Response("report.html not found for session", status=404)
    html = report.read_text(encoding="utf-8")
    injected = html.replace("<head>", f"<head>\n<base href=\"/out/{sid}/\">", 1)
    return Response(injected, mimetype="text/html")


@APP.route("/out/<sid>/<path:path>")
def out_files(sid: str, path: str):
    out_dir = DATA_OUT / sid
    # Symlink-safe: only serve files that resolve under out_dir
    try:
        target = (out_dir / path).resolve()
        base = out_dir.resolve()
        if not str(target).startswith(str(base)):
            return Response("forbidden", status=403)
        if not target.exists() or target.is_dir():
            return Response("not found", status=404)
        # Serve the validated resolved file path
        return send_file(str(target), max_age=3600)
    except Exception:
        return Response("error", status=500)


@APP.post("/api/delete-originals")
def api_delete_originals() -> Response:
    """
    Delete all original uploaded files for a session after materializing any
    grouped_photos symlinks into real files to avoid broken references.
    """
    try:
        payload = request.get_json(force=True, silent=True) or {}
    except Exception:
        payload = {}
    job_id = (payload.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "missing_job_id"}), 400

    out_dir = DATA_OUT / job_id
    in_dir = DATA_IN / job_id
    if not out_dir.exists():
        return jsonify({"error": "not_found"}), 404

    grouped = out_dir / "grouped_photos"
    converted = 0
    failed = 0
    if grouped.exists():
        for p in grouped.rglob("*"):
            try:
                if p.is_symlink():
                    try:
                        src_real = Path(os.path.realpath(p))
                        # Replace symlink with a real file copy if source exists
                        p.unlink(missing_ok=True)  # type: ignore[arg-type]
                        if src_real.exists() and src_real.is_file():
                            p.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_real, p)
                            converted += 1
                        else:
                            # Source missing; leave as removed
                            failed += 1
                    except Exception:
                        failed += 1
                        continue
            except Exception:
                # Some files may vanish during traversal; ignore
                continue

    removed = False
    try:
        if in_dir.exists():
            shutil.rmtree(in_dir)
            removed = True
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "converted_symlinks": converted,
        "convert_failures": failed,
        "deleted_input": removed,
    })


@APP.route("/sessions")
def sessions():
    sids = sorted([p.name for p in (DATA_OUT).glob("*") if p.is_dir()], reverse=True)
    rows = "".join(
        f"<tr><td><a href='{url_for('ui', sid=s)}'>{s}</a></td>"
        f"<td><a class='btn btn-sm btn-outline-primary' href='{url_for('out_files', sid=s, path='clusters.json')}'>JSON</a></td>"
        f"<td><a class='btn btn-sm btn-outline-secondary' href='{url_for('out_files', sid=s, path='faces/')}'>faces/</a></td>"
        f"<td><a class='btn btn-sm btn-outline-success' href='{url_for('groups', sid=s)}'>원본 그룹</a></td></tr>"
        for s in sids
    )
    page = f"""
    <!doctype html><html><head>
      <meta charset='utf-8'>
      <meta name='viewport' content='width=device-width, initial-scale=1'>
      <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css' rel='stylesheet'>
      <title>세션 목록</title>
    </head><body>
      <div class='container my-4'>
        <div class='d-flex justify-content-between align-items-center'>
          <h3 class='m-0'>세션 목록</h3>
          <a class='btn btn-outline-secondary' href='{url_for('index')}'>홈</a>
        </div>
        <table class='table table-sm table-striped mt-3'>
          <thead><tr><th>Session</th><th>JSON</th><th>Faces</th><th>Groups</th></tr></thead>
          <tbody>{rows if rows else "<tr><td colspan=4 class='text-muted'>없음</td></tr>"}</tbody>
        </table>
      </div>
    </body></html>
    """
    return Response(page, mimetype="text/html")


def _load_grouping(sid: str):
    import json
    cfg = DATA_OUT / sid / "clusters.json"
    if not cfg.exists():
        return None
    with cfg.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("grouping")


@APP.route("/groups/<sid>")
def groups(sid: str):
    grouping = _load_grouping(sid)
    if not grouping:
        return Response("No grouping found. Run pipeline first.", status=404)
    base = f"/out/{sid}/"

    persons = []
    clusters_to_photos = grouping.get("clusters_to_photos", {})
    # keys are strings (cluster ids and 'noise')
    num_keys = [int(x) for x in clusters_to_photos.keys() if x.isdigit()]
    for k in sorted(num_keys):
        rels = clusters_to_photos.get(str(k), [])
        # Top thumbnails (first 12) with uniform sizing
        top = rels[:12]
        rest = rels[12:]
        thumbs_top = "".join(
            (
                f"<a class='thumb-link' href='{base}{p}' target='_blank' title='인물 {k}'><img loading='lazy' alt='인물 {k}' src='{base}{p}'/></a>"
            )
            for p in top
        )
        # Remaining thumbnails collapsed under details
        if rest:
            more_count = len(rest)
            thumbs_rest = "".join(
                (
                    f"<a class='thumb-link' href='{base}{p}' target='_blank' title='인물 {k}'><img loading='lazy' alt='인물 {k}' src='{base}{p}'/></a>"
                )
                for p in rest
            )
            more_html = (
                f"<details class='mt-2 small'>"
                f"  <summary class='text-secondary'>더보기 (+{more_count})</summary>"
                f"  <div class='thumb-grid mt-2'>{thumbs_rest}</div>"
                f"</details>"
            )
        else:
            more_html = ""

        _fallback = "<div class='text-muted'>이미지 없음</div>"
        body_thumbs = thumbs_top if thumbs_top else _fallback

        report_anchor = f"{url_for('report', sid=sid)}#cluster-{k}"
        persons.append(
            (
                f"<div class='col'>"
                f"  <div class='card h-100 person-card shadow-sm border-0'>"
                f"    <div class='card-header bg-white d-flex justify-content-between align-items-center'>"
                f"      <div><strong>인물 {k}</strong> <span class='badge bg-light text-dark'>{len(rels)}</span></div>"
                f"      <a class='btn btn-sm btn-outline-primary' href='{report_anchor}' target='_blank'>리포트에서 보기</a>"
                f"    </div>"
                f"    <div class='card-body'>"
                f"      <div class='thumb-grid'>{body_thumbs}</div>"
                f"      {more_html}"
                f"    </div>"
                f"  </div>"
                f"</div>"
            )
        )
    persons_html = "".join(persons) or "<div class='text-muted'>클러스터가 없습니다.</div>"

    noise = clusters_to_photos.get("noise", [])
    no_face = grouping.get("no_face", [])
    noise_html = "".join(
        (
            f"<a class='thumb-link' href='{base}{p}' target='_blank' title='Noise'><img loading='lazy' alt='Noise' src='{base}{p}'/></a>"
        )
        for p in noise[:20]
    ) or "<div class='text-muted'>없음</div>"
    nf_html = "".join(
        (
            f"<a class='thumb-link' href='{base}{p}' target='_blank' title='No Face'><img loading='lazy' alt='No Face' src='{base}{p}'/></a>"
        )
        for p in no_face[:20]
    ) or "<div class='text-muted'>없음</div>"

    page = f"""
    <!doctype html><html><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css' rel='stylesheet'>
      <title>원본 그룹 — {sid}</title>
      <style>
        .thumb-grid { display: flex; flex-wrap: wrap; gap: 0.5rem; }
        .thumb-grid img { width: 120px; height: 120px; object-fit: cover; border-radius: 0.5rem; box-shadow: 0 0.25rem 0.5rem rgba(0,0,0,0.05); }
        .thumb-link { display: inline-flex; }
        .person-card { transition: box-shadow .15s ease; }
        .person-card:hover { box-shadow: 0 .5rem 1rem rgba(0,0,0,.1); }
      </style>
    </head><body>
      <div class='container my-4'>
        <div class='d-flex justify-content-between align-items-center'>
          <h3 class='m-0'>원본 그룹 — {sid}</h3>
          <div class='btn-group'>
            <a class='btn btn-outline-secondary btn-sm' href='{url_for('view', sid=sid)}'>리포트 보기</a>
            <a class='btn btn-outline-secondary btn-sm' href='{url_for('sessions')}'>세션 목록</a>
          </div>
        </div>
        <div class='my-3'>
          <h4 class='mb-2'>인물별</h4>
          <div class='row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3'>{persons_html}</div>
        </div>
        <div class='my-4'>
          <h5 class='mb-2'>분리되지 않은 항목</h5>
          <div class='mb-1'><span class='badge bg-secondary'>Noise (얼굴은 있으나 미분류)</span></div>
          <div class='thumb-grid'>{noise_html}</div>
          <div class='mt-3 mb-1'><span class='badge bg-secondary'>No Face (얼굴 없음)</span></div>
          <div class='thumb-grid'>{nf_html}</div>
        </div>
      </div>
    </body></html>
    """
    return Response(page, mimetype="text/html")


def main() -> None:
    port = int(os.environ.get("PORT", 8000))
    # Run without reloader in background usage
    APP.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
