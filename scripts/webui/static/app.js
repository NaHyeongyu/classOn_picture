let selectedFiles = [];
let jobId = null;
let pollTimer = null;

const el = (id) => document.getElementById(id);

function notify(message, type = "info") {
  const t = el("toast");
  t.textContent = message;
  t.className = `toast ${type}`;
  t.style.display = "block";
  setTimeout(() => { t.style.display = "none"; }, 3500);
}

function setBusy(busy) {
  el("uploadBtn").disabled = busy || !selectedFiles.length;
  el("chooseBtn").disabled = busy;
  el("fileInput").disabled = busy;
}

function updateProgress(phase, progress, counts) {
  const pct = Math.max(0, Math.min(100, Math.round((progress || 0) * 100)));
  el("phaseLabel").textContent = phase || "처리중";
  el("percentLabel").textContent = `${pct}%`;
  el("progressBar").style.width = `${pct}%`;
  if (counts && (counts.photos_done || counts.faces_done)) {
    const pd = counts.photos_done ?? 0;
    const fd = counts.faces_done ?? 0;
    const ft = counts.faces_total_est ?? 0;
    el("countsLabel").textContent = `사진 ${pd} / 얼굴 ${fd}${ft ? ` (예상 ${ft})` : ""}`;
  } else {
    el("countsLabel").textContent = "";
  }
}

async function startUpload(files) {
  if (!files || !files.length) {
    notify("업로드할 파일을 선택하세요.", "error");
    return;
  }
  setBusy(true);
  updateProgress("업로드", 0.0);
  try {
    const topk = Math.max(1, parseInt(el("optTopk").value || "3", 10));
    const mcs = Math.max(2, parseInt(el("optMcs").value || "5", 10));
    const link = el("optLink").checked;
    const chunkMb = Math.max(0.25, parseFloat(el("optChunk").value || "1"));
    const CHUNK = Math.floor(chunkMb * 1024 * 1024);

    let localJobId = null;
    const totalBytes = files.reduce((s, f) => s + (f.size || 0), 0);
    let sent = 0;

    for (let fi = 0; fi < files.length; fi++) {
      const f = files[fi];
      const parts = Math.max(1, Math.ceil(f.size / CHUNK));
      for (let pi = 0; pi < parts; pi++) {
        const start = pi * CHUNK;
        const end = Math.min(f.size, start + CHUNK);
        const blob = f.slice(start, end);
        const isLastChunkOfFile = pi === parts - 1;
        const isLastOverall = (fi === files.length - 1) && isLastChunkOfFile;

        const fd = new FormData();
        fd.append("chunk", blob, `${f.name}.part-${pi}`);
        fd.append("file_name", f.name);
        fd.append("chunk_index", String(pi));
        fd.append("chunk_total", String(parts));
        if (localJobId) fd.append("job_id", localJobId);
        if (isLastOverall) {
          fd.append("final", "1");
          fd.append("topk", String(topk));
          fd.append("mcs", String(mcs));
          if (link) fd.append("link", "1");
        }
        const res = await fetch("/api/upload", { method: "POST", body: fd });
        if (!res.ok) throw new Error(`업로드 실패 (${res.status})`);
        const data = await res.json();
        localJobId = data.job_id || localJobId;
        sent += blob.size;
        const up = Math.min(99, Math.round((sent / Math.max(1, totalBytes)) * 100));
        el("phaseLabel").textContent = "업로드";
        el("percentLabel").textContent = `${up}%`;
        el("progressBar").style.width = `${up}%`;
      }
    }
    if (!localJobId) throw new Error("job_id 없음");
    jobId = localJobId;
    startPolling(jobId);
  } catch (e) {
    console.error(e);
    notify(`업로드 오류: ${e.message || e}`, "error");
    setBusy(false);
  }
}

function startPolling(id) {
  if (pollTimer) clearInterval(pollTimer);
  let lastTick = Date.now();
  pollTimer = setInterval(async () => {
    try {
      const url = `/api/progress?job_id=${encodeURIComponent(id)}`;
      const res = await fetch(url, { cache: "no-store" });
      if (!res.ok) throw new Error(`progress 상태 ${res.status}`);
      const s = await res.json();
      lastTick = Date.now();
      const progress = typeof s.progress === "number" ? s.progress : (s.progress || 0);
      updateProgress(s.phase || "진행중", progress, s.counts || {});
      if (progress >= 1.0 || s.phase === "done") {
        clearInterval(pollTimer);
        pollTimer = null;
        loadResult(id);
      }
    } catch (e) {
      console.warn("progress 오류", e);
      if (Date.now() - lastTick > 15000) {
        clearInterval(pollTimer);
        pollTimer = null;
        notify("진행률 확인에 실패했습니다. 잠시 후 새로고침 해주세요.", "error");
        setBusy(false);
      }
    }
  }, 800);
}

async function loadResult(id) {
  try {
    updateProgress("결과 로딩", 1.0);
    const res = await fetch(`/api/result?job_id=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`결과 상태 ${res.status}`);
    const data = await res.json();
    renderClusters(data.clusters || []);
    renderUnassigned(data.unassigned || []);
    el("result").hidden = false;
    setBusy(false);
    notify("완료되었습니다.", "success");
  } catch (e) {
    console.error(e);
    notify(`결과 로딩 오류: ${e.message || e}`, "error");
    setBusy(false);
  }
}

function renderClusters(clusters) {
  const root = el("clusters");
  root.innerHTML = "";
  clusters.forEach((c, idx) => {
    const n = c.name || `인물 ${idx + 1}`;
    const list = c.originals || [];
    const card = document.createElement("div");
    card.className = "cluster-card";
    const header = document.createElement("div");
    header.className = "header";
    header.innerHTML = `<div>${n}</div><span class="badge">${list.length}</span>`;
    const body = document.createElement("div");
    body.className = "body";
    const grid = document.createElement("div");
    grid.className = "grid";
    list.forEach((it, i) => {
      const a = document.createElement("a");
      a.href = it.photo;
      a.target = "_blank";
      a.rel = "noopener";
      a.title = `${n} 원본 열기`;
      a.tabIndex = 0;
      const img = document.createElement("img");
      img.src = it.thumb || it.photo;
      img.alt = `${n} 원본 썸네일 ${i + 1}`;
      img.className = "thumb";
      a.appendChild(img);
      grid.appendChild(a);
    });
    body.appendChild(grid);
    card.appendChild(header);
    card.appendChild(body);
    root.appendChild(card);
  });
  if (!clusters.length) {
    const empty = document.createElement("div");
    empty.className = "muted";
    empty.textContent = "클러스터가 없습니다.";
    root.appendChild(empty);
  }
}

function renderUnassigned(items) {
  const root = el("unassigned");
  root.innerHTML = "";
  items.forEach((it, i) => {
    const a = document.createElement("a");
    a.href = it.photo;
    a.target = "_blank";
    a.rel = "noopener";
    a.title = `원본 열기`;
    a.tabIndex = 0;
    const img = document.createElement("img");
    img.src = it.thumb || it.photo;
    img.alt = `분리되지 않은 원본 썸네일 ${i + 1}`;
    img.className = "thumb";
    a.appendChild(img);
    root.appendChild(a);
  });
  if (!items.length) {
    root.innerHTML = "<div class=\"muted\">없음</div>";
  }
}

function bindUI() {
  const drop = el("dropzone");
  const fileInput = el("fileInput");
  const chooseBtn = el("chooseBtn");
  const uploadBtn = el("uploadBtn");

  const updateCount = () => {
    el("fileCount").textContent = selectedFiles.length ? `${selectedFiles.length}개 선택됨` : "";
    setBusy(false);
  };

  // Drag & drop
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, (e) => {
    e.preventDefault(); e.stopPropagation(); drop.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, (e) => {
    e.preventDefault(); e.stopPropagation(); drop.classList.remove("drag");
    if (ev === "drop") {
      const files = Array.from(e.dataTransfer.files || []).filter(f => f.type.startsWith("image/"));
      selectedFiles = files;
      updateCount();
    }
  }));
  drop.addEventListener("click", () => fileInput.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });

  // File chooser
  fileInput.addEventListener("change", (e) => {
    const files = Array.from(e.target.files || []).filter(f => f.type.startsWith("image/"));
    selectedFiles = files;
    updateCount();
  });
  chooseBtn.addEventListener("click", () => fileInput.click());
  uploadBtn.addEventListener("click", () => startUpload(selectedFiles));
}

window.addEventListener("DOMContentLoaded", bindUI);
