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
from threading import Thread

from flask import Flask, Response, flash, redirect, render_template_string, request, send_from_directory, url_for, jsonify

# Ensure src is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.pipeline import run_pipeline  # noqa: E402
from src.utils.fs import ensure_dir  # noqa: E402
from src.utils.logging import setup_logger  # noqa: E402


logger = setup_logger()

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "scripts" / "webui" / "static"
DATA_IN = BASE_DIR / "data" / "input"
DATA_OUT = BASE_DIR / "data" / "output"

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
            with open(target, "ab") as w:
                chunk.save(w)
        except Exception:
            data = chunk.stream.read()
            with open(target, "ab") as w:
                w.write(data)
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
    numeric_ids = sorted([int(k) for k in clusters_map.keys() if str(k).isdigit()])
    clusters_out: List[Dict[str, Any]] = []
    for idx, cid in enumerate(numeric_ids, start=1):
        rels = clusters_map.get(str(cid), [])
        originals = [{
            "photo": f"/out/{job_id}/{p}",
            "thumb": f"/out/{job_id}/{p}",
        } for p in rels]
        clusters_out.append({
            "cluster_id": cid,
            "name": f"인물 {idx}",
            "originals": originals,
        })

    noise = clusters_map.get("noise", [])
    no_face = grouping.get("no_face", [])
    unassigned = [{
        "photo": f"/out/{job_id}/{p}",
        "thumb": f"/out/{job_id}/{p}",
    } for p in (noise + no_face)]

    meta = {
        "total_photos": len(data.get("photos", [])),
        "total_faces": len(data.get("faces", [])),
    }
    return jsonify({
        "meta": meta,
        "clusters": clusters_out,
        "unassigned": unassigned,
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
        run_pipeline(str(in_dir), str(out_dir), topk=topk, min_cluster_size=mcs, progress_cb=cb, link_originals=link_originals)
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
    return send_from_directory(out_dir, path)


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
        thumbs = "".join(
            f"<div class='col'><a href='{base}{p}' target='_blank'><img class='img-fluid rounded' src='{base}{p}'/></a></div>"
            for p in rels[:12]
        )
        _fallback = "<div class='text-muted'>이미지 없음</div>"
        body_thumbs = thumbs if thumbs else _fallback
        persons.append(
            f"<div class='col'><div class='card h-100'><div class='card-header'><strong>인물 {k}</strong> <span class='badge bg-light text-dark'>{len(rels)}</span></div>"
            f"<div class='card-body'><div class='row row-cols-4 g-2'>{body_thumbs}</div></div></div></div>"
        )
    persons_html = "".join(persons) or "<div class='text-muted'>클러스터가 없습니다.</div>"

    noise = clusters_to_photos.get("noise", [])
    no_face = grouping.get("no_face", [])
    noise_html = "".join(
        f"<div class='col'><a href='{base}{p}' target='_blank'><img class='img-fluid rounded' src='{base}{p}'/></a></div>"
        for p in noise[:20]
    ) or "<div class='text-muted'>없음</div>"
    nf_html = "".join(
        f"<div class='col'><a href='{base}{p}' target='_blank'><img class='img-fluid rounded' src='{base}{p}'/></a></div>"
        for p in no_face[:20]
    ) or "<div class='text-muted'>없음</div>"

    page = f"""
    <!doctype html><html><head>
      <meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
      <link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css' rel='stylesheet'>
      <title>원본 그룹 — {sid}</title>
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
          <div class='row row-cols-4 g-2'>{noise_html}</div>
          <div class='mt-3 mb-1'><span class='badge bg-secondary'>No Face (얼굴 없음)</span></div>
          <div class='row row-cols-4 g-2'>{nf_html}</div>
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
