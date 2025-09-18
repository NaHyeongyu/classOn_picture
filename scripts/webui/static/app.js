let selectedFiles = [];
let jobId = null;
let pollTimer = null;
let modalEl = null; let modalImg = null; let modalOpenNew = null; let modalCloseBtn = null;

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

async function loadResult(id, attempt = 0) {
  try {
    updateProgress("결과 준비 중", 1.0);
    const res = await fetch(`/api/result?job_id=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (res.status === 404) {
      if (attempt < 20) { // 최대 약 16초 대기 (800ms × 20)
        setTimeout(() => loadResult(id, attempt + 1), 800);
        return;
      }
      notify("결과 준비 지연: 잠시 후 다시 시도하세요.", "error");
      setBusy(false);
      return;
    }
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
      const wrap = document.createElement("div");
      wrap.className = "thumb-wrap";
      const a = document.createElement("a");
      a.href = it.photo;
      a.title = `${n} 원본 미리보기`;
      a.tabIndex = 0;
      const img = document.createElement("img");
      img.src = it.thumb || it.photo;
      img.alt = `${n} 원본 썸네일 ${i + 1}`;
      img.className = "thumb";
      a.appendChild(img);
      a.addEventListener("click", (e) => { e.preventDefault(); window.__openModal(it.photo); });
      const rel = it.photo.replace(new RegExp(`^/out/${jobId}/`), "");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "del-btn";
      btn.innerHTML = "×";
      btn.title = "삭제";
      btn.addEventListener("click", async (e) => {
        e.preventDefault(); e.stopPropagation();
        const ok = await window.__confirm("이 썸네일을 삭제할까요? (원본 그룹에서 제거)");
        if (!ok) return;
        try {
          const res = await fetch("/api/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: jobId, path: rel }) });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          wrap.remove();
          // If grid becomes empty, show '이미지 없음'
          if (grid.querySelectorAll(".thumb-wrap").length === 0) {
            const empty = document.createElement("div");
            empty.className = "muted";
            empty.textContent = "이미지 없음";
            grid.appendChild(empty);
          }
        } catch (err) {
          notify(`삭제 실패: ${err.message || err}`, "error");
        }
      });
      wrap.appendChild(a);
      wrap.appendChild(btn);
      grid.appendChild(wrap);
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
    const wrap = document.createElement("div");
    wrap.className = "thumb-wrap";
    const a = document.createElement("a");
    a.href = it.photo;
    a.title = `원본 미리보기`;
    a.tabIndex = 0;
    const img = document.createElement("img");
    img.src = it.thumb || it.photo;
    img.alt = `분리되지 않은 원본 썸네일 ${i + 1}`;
    img.className = "thumb";
    a.appendChild(img);
    a.addEventListener("click", (e) => { e.preventDefault(); window.__openModal(it.photo); });
    const rel = it.photo.replace(new RegExp(`^/out/${jobId}/`), "");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "del-btn";
    btn.innerHTML = "×";
    btn.title = "삭제";
    btn.addEventListener("click", async (e) => {
      e.preventDefault(); e.stopPropagation();
      const ok = await window.__confirm("이 썸네일을 삭제할까요? (원본 그룹에서 제거)");
      if (!ok) return;
      try {
        const res = await fetch("/api/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: jobId, path: rel }) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        wrap.remove();
        if (root.querySelectorAll(".thumb-wrap").length === 0) {
          root.innerHTML = "<div class='muted'>없음</div>";
        }
      } catch (err) {
        notify(`삭제 실패: ${err.message || err}`, "error");
      }
    });
    wrap.appendChild(a);
    wrap.appendChild(btn);
    root.appendChild(wrap);
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

  // Modal refs
  modalEl = el("imgModal");
  modalImg = el("modalImg");
  modalOpenNew = el("modalOpenNew");
  modalCloseBtn = el("modalClose");
  const closeModal = () => { modalEl.classList.remove("show"); modalEl.setAttribute("aria-hidden", "true"); document.body.style.overflow = ""; };
  const openModal = (src) => { modalImg.src = src; modalOpenNew.href = src; modalEl.classList.add("show"); modalEl.setAttribute("aria-hidden", "false"); document.body.style.overflow = "hidden"; };
  modalCloseBtn.addEventListener("click", closeModal);
  modalEl.addEventListener("click", (e) => { if (e.target === modalEl) closeModal(); });
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && modalEl.classList.contains("show")) closeModal(); });

  // Confirm modal
  confirmEl = el("confirmModal");
  confirmYes = el("confirmYes");
  confirmNo = el("confirmNo");
  confirmMsg = el("confirmMsg");
  const closeConfirm = () => { confirmEl.classList.remove("show"); confirmEl.setAttribute("aria-hidden", "true"); document.body.style.overflow = ""; };
  const openConfirm = (message) => {
    confirmMsg.textContent = message || "이 작업을 진행할까요?";
    confirmEl.classList.add("show");
    confirmEl.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    return new Promise((resolve) => { __confirmResolver = resolve; });
  };
  confirmYes.addEventListener("click", () => { if (__confirmResolver) __confirmResolver(true); closeConfirm(); });
  confirmNo.addEventListener("click", () => { if (__confirmResolver) __confirmResolver(false); closeConfirm(); });
  confirmEl.addEventListener("click", (e) => { if (e.target === confirmEl) { if (__confirmResolver) __confirmResolver(false); closeConfirm(); } });

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

  // Expose modal handlers to render functions
  window.__openModal = openModal;
  window.__confirm = openConfirm;
}

window.addEventListener("DOMContentLoaded", bindUI);
