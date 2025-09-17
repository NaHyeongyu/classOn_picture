#!/usr/bin/env python
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path
from typing import List

from flask import Flask, Response, flash, redirect, render_template_string, request, send_from_directory, url_for

# Ensure src is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.pipeline import run_pipeline  # noqa: E402
from src.utils.fs import ensure_dir  # noqa: E402
from src.utils.logging import setup_logger  # noqa: E402


logger = setup_logger()

APP = Flask(__name__)
APP.secret_key = os.environ.get("FACE_MVP_SECRET", "dev-secret")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_IN = BASE_DIR / "data" / "input"
DATA_OUT = BASE_DIR / "data" / "output"


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
            <li><a href="{{ url_for('view', sid=sid) }}">{{ sid }}</a></li>
          {% else %}
            <li class="text-muted">세션 없음</li>
          {% endfor %}
        </ul>
      </div>
    </div>
  </body>
  </html>
"""


@APP.route("/")
def index() -> str:
    recent = sorted([p.name for p in (DATA_OUT).glob("*") if p.is_dir()])[-5:][::-1]
    return render_template_string(INDEX_HTML, recent=recent)


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

    # Run pipeline (blocking for MVP)
    run_pipeline(str(in_dir), str(out_dir), topk=topk, min_cluster_size=mcs)

    return redirect(url_for("view", sid=sid))


@APP.route("/view/<sid>")
def view(sid: str):
    out_dir = DATA_OUT / sid
    report = out_dir / "report.html"
    if not report.exists():
        return Response("report.html not found for session", status=404)
    # Inject base href so relative assets resolve via /out/<sid>/
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
        f"<tr><td><a href='{url_for('view', sid=s)}'>{s}</a></td>"
        f"<td><a class='btn btn-sm btn-outline-primary' href='{url_for('out_files', sid=s, path='clusters.json')}'>clusters.json</a></td>"
        f"<td><a class='btn btn-sm btn-outline-secondary' href='{url_for('out_files', sid=s, path='faces/')}'>faces/</a></td></tr>"
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
          <thead><tr><th>Session</th><th>JSON</th><th>Faces</th></tr></thead>
          <tbody>{rows if rows else "<tr><td colspan=3 class='text-muted'>없음</td></tr>"}</tbody>
        </table>
      </div>
    </body></html>
    """
    return Response(page, mimetype="text/html")


def main() -> None:
    port = int(os.environ.get("PORT", 8000))
    APP.run(host="127.0.0.1", port=port, debug=True, threaded=True)


if __name__ == "__main__":
    main()

