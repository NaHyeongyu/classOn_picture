"use strict";

let selectedFiles = [];
let jobId = null;
let pollTimer = null;
let modalEl = null; let modalImg = null; let modalOpenNew = null; let modalCloseBtn = null; let modalPrevBtn = null; let modalNextBtn = null; let modalCaption = null;
let modalItems = []; let modalIndex = 0;
const selectedPaths = new Set();

// Globals for confirm modal state (explicit to avoid implicit globals)
let __confirmResolver = null;
let confirmEl = null, confirmYes = null, confirmNo = null, confirmMsg = null;

let clusterData = [];
let unassignedData = [];
let currentMeta = { total_photos: 0, total_faces: 0 };
let clusterFilters = { search: "", sort: "faces_desc", minCount: 0 };

// Fallback no-op handlers to avoid runtime errors before UI bindings are ready
window.__openModalList = window.__openModalList || (() => {});
window.__confirm = window.__confirm || (() => Promise.resolve(false));
window.__toggleSelect = window.__toggleSelect || (() => {});

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
    el("countsLabel").textContent = `사진 ${pd} ${ft ? ` (예상 ${ft})` : ""}`;
  } else {
    el("countsLabel").textContent = "";
  }
}

function normalizeClusterName(cluster) {
  return (cluster.custom_name || cluster.name || cluster.default_name || "").toLowerCase();
}

function getClusterCount(cluster) {
  if (typeof cluster.count === "number") return cluster.count;
  if (Array.isArray(cluster.originals)) return cluster.originals.length;
  return 0;
}

function updateSummary(shownCount, shownFaces) {
  const card = el("summaryCard");
  if (!card) return;
  if (!clusterData.length && !unassignedData.length) {
    card.hidden = true;
    return;
  }
  const totalClusters = clusterData.length;
  const totalFaces = typeof currentMeta.total_faces === "number" && currentMeta.total_faces > 0
    ? currentMeta.total_faces
    : clusterData.reduce((acc, c) => acc + getClusterCount(c), 0);
  const totalPhotos = typeof currentMeta.total_photos === "number" && currentMeta.total_photos > 0
    ? currentMeta.total_photos
    : totalFaces;

  el("summaryClustersValue").textContent = String(totalClusters);
  const hint = totalClusters > 0
    ? (shownCount < totalClusters
      ? `총 ${totalClusters}명 중 ${shownCount}명 표시`
      : `전체 ${totalClusters}명 표시`)
    : "표시할 인물이 없습니다";
  el("summaryClustersHint").textContent = hint;
  el("summaryFacesValue").textContent = String(totalFaces);
  el("summaryPhotosValue").textContent = String(totalPhotos);
  el("summaryUnassignedValue").textContent = String(unassignedData.length);
  const summaryUpdated = el("summaryUpdated");
  if (summaryUpdated) {
    summaryUpdated.textContent = `업데이트 ${new Date().toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}`;
  }
  card.hidden = false;
}

function updateClusterHeader(shownCount, shownFaces) {
  const title = el("clustersTitle");
  const sub = el("clustersSubhead");
  if (!title || !sub) return;
  const total = clusterData.length;
  if (total === 0) {
    sub.textContent = "";
    return;
  }
  const phrase = `총 ${total}명 · ${shownCount}명 표시 · ${shownFaces}장`;
  sub.textContent = phrase;
}

function applyFilters() {
  if (!clusterData.length) {
    renderClusters([]);
    updateSummary(0, 0);
    updateClusterHeader(0, 0);
    return;
  }
  const search = clusterFilters.search.trim().toLowerCase();
  const minCount = Number.isFinite(clusterFilters.minCount) ? clusterFilters.minCount : 0;

  let filtered = clusterData.slice();
  if (search) {
    filtered = filtered.filter((cluster) => {
      const name = normalizeClusterName(cluster);
      return name.includes(search);
    });
  }
  if (minCount > 0) {
    filtered = filtered.filter((cluster) => getClusterCount(cluster) >= minCount);
  }

  filtered.sort((a, b) => {
    const countA = getClusterCount(a);
    const countB = getClusterCount(b);
    switch (clusterFilters.sort) {
      case "faces_asc":
        return countA - countB || normalizeClusterName(a).localeCompare(normalizeClusterName(b));
      case "name_asc":
        return normalizeClusterName(a).localeCompare(normalizeClusterName(b));
      case "name_desc":
        return normalizeClusterName(b).localeCompare(normalizeClusterName(a));
      case "faces_desc":
      default:
        return countB - countA || normalizeClusterName(a).localeCompare(normalizeClusterName(b));
    }
  });

  const shownFaces = filtered.reduce((acc, cluster) => acc + getClusterCount(cluster), 0);
  renderClusters(filtered);
  updateSummary(filtered.length, shownFaces);
  updateClusterHeader(filtered.length, shownFaces);
}

function resetFilters() {
  clusterFilters = { search: "", sort: "faces_desc", minCount: 0 };
  const searchInput = el("clusterSearch");
  const sortSelect = el("clusterSort");
  const minSelect = el("clusterMin");
  if (searchInput) searchInput.value = "";
  if (sortSelect) sortSelect.value = "faces_desc";
  if (minSelect) minSelect.value = "0";
  applyFilters();
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
    try { localStorage.setItem('lastJobId', jobId); } catch {}
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

const MAX_RESULT_RETRY = 180; // roughly 2.5 minutes (800ms interval)

async function loadResult(id, attempt = 0, options = {}) {
  try {
    updateProgress("결과 준비 중", 1.0);
    const res = await fetch(`/api/result?job_id=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (res.status === 404) {
      if (attempt < MAX_RESULT_RETRY) {
        setTimeout(() => loadResult(id, attempt + 1), 800);
        return;
      }
      notify("결과 준비 지연: 잠시 후 다시 시도하세요.", "error");
      setBusy(false);
      return;
    }
    if (!res.ok) throw new Error(`결과 상태 ${res.status}`);
    const data = await res.json();
    currentMeta = data.meta || { total_photos: 0, total_faces: 0 };
    clusterData = (data.clusters || []).map((cluster) => ({ ...cluster }));
    unassignedData = data.unassigned || [];

    const controlsCard = el("clusterControls");
    if (controlsCard) controlsCard.hidden = clusterData.length === 0;
    renderUnassigned(unassignedData);
    applyFilters();

    const resultSection = el("result");
    if (resultSection) resultSection.hidden = false;
    const summaryCard = el("summaryCard");
    if (summaryCard) summaryCard.hidden = clusterData.length === 0 && unassignedData.length === 0;
    setBusy(false);
  } catch (e) {
    console.error(e);
    notify(`결과 로딩 오류: ${e.message || e}`, "error");
    setBusy(false);
  }
}

function renderClusters(clusters) {
  const root = el("clusters");
  if (!root) return;
  const emptyState = el("clustersEmpty");
  root.innerHTML = "";

  if (!clusters.length) {
    const total = clusterData.length;
    if (emptyState) {
      emptyState.textContent = total
        ? "조건에 맞는 인물이 없습니다. 검색어나 필터를 확인해 주세요."
        : "표시할 인물이 아직 없습니다.";
      emptyState.hidden = false;
    }
    root.hidden = true;
    return;
  }

  root.hidden = false;
  if (emptyState) emptyState.hidden = true;

  clusters.forEach((c, idx) => {
    const defaultName = c.default_name || `인물 ${idx + 1}`;
    const currentCustom = c.custom_name || "";
    const n = c.name || defaultName;
    const list = Array.isArray(c.originals) ? c.originals : [];
    const card = document.createElement("div");
    card.className = "cluster-card";

    const header = document.createElement("div");
    header.className = "header";
    // Allow dropping onto header to assign images even when no previews exist
    const cidStrHeader = String(c.cluster_id ?? (c.cid ?? ""));
    header.addEventListener('dragover', (e) => { e.preventDefault(); });
    header.addEventListener('drop', async (e) => {
      e.preventDefault();
      const rel = e.dataTransfer.getData('text/plain');
      if (!rel) return;
      try {
        const res = await fetch('/api/assign', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_id: jobId, path: rel, target_cid: parseInt(cidStrHeader, 10) }) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadResult(jobId, 0, { silent: true });
      } catch (err) { notify(`분류 실패: ${err.message || err}`, 'error'); }
    });
    const hLeft = document.createElement("div");
    hLeft.className = "title";
    const nameEl = document.createElement("span");
    nameEl.className = "name";
    nameEl.textContent = n;
    hLeft.appendChild(nameEl);
    const hRight = document.createElement("div");
    hRight.className = "toolbar";

    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = `사진 ${getClusterCount(c)}`;
    hLeft.appendChild(badge);

    // Inline rename UI
    let editing = false; let nameInput = null; let cancelBtn = null;
    const editBtn = document.createElement('button');
    editBtn.type = 'button'; editBtn.className = 'icon-btn'; editBtn.title = '이름 변경'; editBtn.textContent = '✎';
    editBtn.disabled = !!c.is_noise;
    const startEdit = () => {
      if (editing) return; editing = true; editBtn.textContent = '✓'; editBtn.title = '저장';
      nameInput = document.createElement('input');
      nameInput.type = 'text'; nameInput.className = 'name-input';
      nameInput.value = currentCustom || n; nameInput.maxLength = 80;
      nameEl.replaceWith(nameInput);
      nameInput.focus(); nameInput.select();
      cancelBtn.style.display = '';
      nameInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); saveEdit(); }
        if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
      });
    };
    const saveEdit = async () => {
      if (!editing) return; if (!jobId) { notify('작업 ID가 없습니다.', 'error'); return; }
      const trimmed = (nameInput.value || '').trim();
      try {
        const res = await fetch('/api/cluster/rename', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_id: jobId, cid: c.cluster_id, name: trimmed }) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadResult(jobId, 0, { silent: true });
      } catch (err) {
        notify(`이름 변경 실패: ${err.message || err}`, 'error');
        cancelEdit();
      }
    };
    const cancelEdit = () => {
      if (!editing) return; editing = false; editBtn.textContent = '✎'; editBtn.title = '이름 변경';
      const span = document.createElement('span'); span.className = 'name'; span.textContent = n; nameInput.replaceWith(span); nameInput = null; cancelBtn.style.display = 'none';
    };
    editBtn.addEventListener('click', () => { if (!editing) startEdit(); else saveEdit(); });

    cancelBtn = document.createElement('button');
    cancelBtn.type = 'button'; cancelBtn.className = 'icon-btn'; cancelBtn.title = '취소'; cancelBtn.textContent = '×';
    cancelBtn.style.display = 'none';
    cancelBtn.addEventListener('click', cancelEdit);

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button"; deleteBtn.className = "icon-btn danger"; deleteBtn.title = "인물 삭제"; deleteBtn.textContent = "🗑";
    deleteBtn.disabled = !!c.is_noise;
    deleteBtn.addEventListener("click", async () => {
      if (!jobId) { notify("작업 ID가 없습니다.", "error"); return; }
      const ok = await window.__confirm(`'${n}' 인물과 연결된 사진을 삭제할까요? (원본 클러스터에서 제거)`);
      if (!ok) return;
      try {
        const res = await fetch('/api/cluster/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_id: jobId, cid: c.cluster_id }) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        await loadResult(jobId, 0, { silent: true });
      } catch (err) {
        notify(`인물 삭제 실패: ${err.message || err}`, 'error');
      }
    });

    const zipBtn = document.createElement("button");
    zipBtn.type = "button"; zipBtn.className = "icon-btn"; zipBtn.title = "ZIP 다운로드"; zipBtn.textContent = "⤓";
    zipBtn.addEventListener("click", () => {
      if (!jobId) { notify("작업 ID가 없습니다.", "error"); return; }
      const paths = list.map(it => it.photo.replace(new RegExp(`^/out/${jobId}/`), ""));
      if (!paths.length) { notify("다운로드할 항목이 없습니다.", "error"); return; }
      const safeName = (n || `cluster_${idx+1}`).replace(/\s+/g, "_");
      downloadZip(paths, `${safeName}_${jobId}.zip`);
    });

    hRight.appendChild(editBtn);
    hRight.appendChild(cancelBtn);
    hRight.appendChild(zipBtn);
    hRight.appendChild(deleteBtn);
    header.appendChild(hLeft);
    header.appendChild(hRight);

    const body = document.createElement("div");
    body.className = "body";
    // Preview strip (top few items)
    if (list.length) {
      const preview = document.createElement("div");
      preview.className = "preview-strip";
      const previewItems = list.slice(0, 8);
      const cidStr = String(c.cluster_id ?? (c.cid ?? ""));
      preview.addEventListener("dragover", (e) => { e.preventDefault(); });
      preview.addEventListener("drop", async (e) => {
        e.preventDefault();
        const rel = e.dataTransfer.getData("text/plain");
        const fromCidStr = e.dataTransfer.getData("application/x-cid");
        if (!rel) return;
        const targetWrap = e.target.closest && e.target.closest('.thumb-mini-wrap');
        // Same-cluster reorder
        if (fromCidStr && fromCidStr === cidStr) {
          const dragged = Array.from(preview.querySelectorAll('.thumb-mini-wrap')).find(w => w.dataset.rel === rel);
          if (dragged) {
            if (targetWrap && targetWrap !== dragged) {
              preview.insertBefore(dragged, targetWrap);
            } else if (!targetWrap) {
              preview.appendChild(dragged);
            }
            const orderPreview = Array.from(preview.querySelectorAll('.thumb-mini-wrap')).map(w => w.dataset.rel);
            const allRel = list.map(it => it.photo.replace(new RegExp(`^/out/${jobId}/`), ""));
            const rest = allRel.filter(x => !orderPreview.includes(x));
            const order = orderPreview.concat(rest);
            try { await fetch('/api/reorder', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ job_id: jobId, cid: parseInt(cidStr, 10), order }) }); } catch {}
          }
          return;
        }
        // Assign from unassigned or other cluster
        try {
          const res = await fetch('/api/assign', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_id: jobId, path: rel, target_cid: parseInt(cidStr, 10) }) });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          await loadResult(jobId, 0, { silent: true });
        } catch (err) { notify(`분류 실패: ${err.message || err}`, 'error'); }
      });
      previewItems.forEach((it, i) => {
        const wrap = document.createElement("div");
        wrap.className = "thumb-mini-wrap";
        wrap.draggable = true;
        const rel = it.photo.replace(new RegExp(`^/out/${jobId}/`), "");
        wrap.dataset.rel = rel;
        wrap.dataset.cid = cidStr;
        wrap.addEventListener("dragstart", (e) => {
          e.dataTransfer.setData("text/plain", rel);
          e.dataTransfer.setData("application/x-cid", cidStr);
        });

        const a = document.createElement("a");
        a.href = it.photo;
        a.title = `${n} 미리보기 ${i + 1}`;
        const img = document.createElement("img");
        img.src = it.thumb || it.photo;
        img.alt = `${n} 미리보기 ${i + 1}`;
        img.loading = "lazy";
        img.decoding = "async";
        img.onerror = () => { if (img.src !== it.photo) img.src = it.photo; };
        a.appendChild(img);
        a.addEventListener("click", (e) => { e.preventDefault(); window.__openModalList(list, i); });

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
            await loadResult(jobId, 0, { silent: true });
          } catch (err) {
            notify(`삭제 실패: ${err.message || err}`, "error");
          }
        });

        wrap.appendChild(a);
        wrap.appendChild(btn);
        preview.appendChild(wrap);
      });
      body.appendChild(preview);
    }

    if (!list.length) {
      const muted = document.createElement("div");
      muted.className = "muted";
      muted.textContent = "이미지가 없습니다.";
      body.appendChild(muted);
    } else {
      // No additional grid; previews only
    }

    card.appendChild(header);
    card.appendChild(body);
    root.appendChild(card);
  });
}

function renderUnassigned(items) {
  const root = el("unassigned");
  if (!root) return;
  const emptyState = el("unassignedEmpty");
  const title = el("unassignedTitle");
  const sub = el("unassignedSubhead");
  const count = items.length;

  if (title) title.textContent = "분리되지 않은 사진";
  if (sub) sub.textContent = count ? `${count}장` : "";

  root.innerHTML = "";
  if (!count) {
    root.hidden = true;
    if (emptyState) emptyState.hidden = false;
    return;
  }

  root.hidden = false;
  if (emptyState) emptyState.hidden = true;

  items.forEach((it, i) => {
    const wrap = document.createElement("div");
    wrap.className = "thumb-wrap";
    wrap.draggable = true;
    wrap.addEventListener("dragstart", (e) => {
      const rel = it.photo.replace(new RegExp(`^/out/${jobId}/`), "");
      e.dataTransfer.setData("text/plain", rel);
    });

    const a = document.createElement("a");
    a.href = it.photo;
    a.title = `원본 미리보기`;
    a.tabIndex = 0;
    const img = document.createElement("img");
    img.src = it.thumb || it.photo;
    img.alt = `분리되지 않은 원본 썸네일 ${i + 1}`;
    img.className = "thumb";
    img.loading = "lazy";
    img.decoding = "async";
    img.onerror = () => { if (img.src !== it.photo) img.src = it.photo; };
    a.appendChild(img);
    a.addEventListener("click", (e) => { e.preventDefault(); window.__openModalList(items, i); });

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
        await loadResult(jobId, 0, { silent: true });
      } catch (err) {
        notify(`삭제 실패: ${err.message || err}`, "error");
      }
    });

    wrap.appendChild(a);
    const sbtn = document.createElement("button");
    sbtn.type = "button";
    sbtn.className = "sel-btn";
    sbtn.innerHTML = "✓";
    sbtn.title = "선택/해제";
    sbtn.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); window.__toggleSelect(rel, wrap); });
    wrap.appendChild(sbtn);
    wrap.appendChild(btn);
    root.appendChild(wrap);
  });
}

function bindUI() {
  const drop = el("dropzone");
  const fileInput = el("fileInput");
  const chooseBtn = el("chooseBtn");
  const uploadBtn = el("uploadBtn");
  const toolbar = el("toolbar");
  const selCount = el("selCount");
  const downloadBtn = el("downloadBtn");
  const jumpUpload = el("jumpUpload");
  const clusterSearch = el("clusterSearch");
  const clusterSort = el("clusterSort");
  const clusterMin = el("clusterMin");
  const resetFiltersBtn = el("resetFilters");
  const deleteOriginalsBtn = el("deleteOriginalsBtn");
  const purgeAllBtn = el("purgeAllBtn");

  // Modal refs
  modalEl = el("imgModal");
  modalImg = el("modalImg");
  modalOpenNew = el("modalOpenNew");
  modalCloseBtn = el("modalClose"); modalPrevBtn = el("modalPrev"); modalNextBtn = el("modalNext");
  modalCaption = el("modalCaption");
  const closeModal = () => { modalEl.classList.remove("show"); modalEl.setAttribute("aria-hidden", "true"); document.body.style.overflow = ""; };
  const showModalAt = (idx) => {
    if (!modalItems.length) return;
    modalIndex = (idx + modalItems.length) % modalItems.length;
    const item = modalItems[modalIndex];
    const src = item.preview || item.thumb || item.photo;
    modalImg.src = src; modalImg.alt = `미리보기 ${modalIndex + 1}`;
    modalOpenNew.href = item.photo || src;
    // Caption: index / total · filename
    try {
      const name = decodeURIComponent((item.photo || src).split('/').pop() || '');
      if (modalCaption) modalCaption.textContent = `${modalIndex + 1} / ${modalItems.length} · ${name}`;
    } catch (_) {
      if (modalCaption) modalCaption.textContent = `${modalIndex + 1} / ${modalItems.length}`;
    }
    // Toggle nav visibility on single item
    const single = modalItems.length <= 1;
    if (modalPrevBtn) modalPrevBtn.toggleAttribute('disabled', single);
    if (modalNextBtn) modalNextBtn.toggleAttribute('disabled', single);
  };
  const openModalList = (items, idx) => { modalItems = items || []; showModalAt(idx || 0); modalEl.classList.add("show"); modalEl.setAttribute("aria-hidden", "false"); document.body.style.overflow = "hidden"; };
  modalCloseBtn.addEventListener("click", closeModal);
  modalEl.addEventListener("click", (e) => { if (e.target === modalEl) closeModal(); });
  window.addEventListener("keydown", (e) => { if (!modalEl.classList.contains("show")) return; if (e.key === "Escape") closeModal(); if (e.key === "ArrowLeft") showModalAt(modalIndex - 1); if (e.key === "ArrowRight") showModalAt(modalIndex + 1); });
  modalPrevBtn.addEventListener("click", () => showModalAt(modalIndex - 1));
  modalNextBtn.addEventListener("click", () => showModalAt(modalIndex + 1));

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

  if (jumpUpload && drop) {
    jumpUpload.addEventListener("click", () => {
      drop.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  if (clusterSearch) {
    clusterSearch.addEventListener("input", (e) => {
      clusterFilters.search = (e.target.value || "").trim().toLowerCase();
      applyFilters();
    });
  }
  if (clusterSort) {
    clusterSort.addEventListener("change", (e) => {
      clusterFilters.sort = e.target.value || "faces_desc";
      applyFilters();
    });
  }
  if (clusterMin) {
    clusterMin.addEventListener("change", (e) => {
      clusterFilters.minCount = parseInt(e.target.value || "0", 10) || 0;
      applyFilters();
    });
  }
  if (resetFiltersBtn) {
    resetFiltersBtn.addEventListener("click", () => {
      resetFilters();
    });
  }

  if (deleteOriginalsBtn) {
    deleteOriginalsBtn.addEventListener("click", async () => {
      if (!jobId) { notify("작업 ID가 없습니다.", "error"); return; }
      const ok = await window.__confirm("업로드한 원본 전체를 삭제할까요? (그룹 폴더의 링크는 실제 파일로 변환됩니다)");
      if (!ok) return;
      try {
        const res = await fetch('/api/delete-originals', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ job_id: jobId }) });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        notify(`원본 삭제 완료: 변환 ${data.converted_symlinks}, 실패 ${data.convert_failures}`, 'success');
      } catch (err) {
        notify(`원본 삭제 실패: ${err.message || err}`, 'error');
      }
    });
  }

  if (purgeAllBtn) {
    purgeAllBtn.addEventListener("click", async () => {
      const ok = await window.__confirm("정말로 이전 데이터를 모두 삭제할까요?\n\n주의: data/input 과 data/output 폴더 내의 모든 파일이 삭제됩니다.");
      if (!ok) return;
      try {
        const res = await fetch('/api/purge-all', { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        notify(`삭제 완료: input ${data.input.files + data.input.dirs}개, output ${data.output.files + data.output.dirs}개 항목`, 'success');
        try { localStorage.removeItem('lastJobId'); } catch {}
        setTimeout(() => window.location.reload(), 600);
      } catch (err) {
        notify(`삭제 실패: ${err.message || err}`, 'error');
      }
    });
  }

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
  if (downloadBtn) {
    downloadBtn.addEventListener("click", () => downloadZip(Array.from(selectedPaths), `selected_${jobId}.zip`));
  }

  // Expose modal handlers to render functions
  window.__openModalList = openModalList;
  window.__confirm = openConfirm;
  window.__toggleSelect = (rel, wrapEl) => {
    if (selectedPaths.has(rel)) {
      selectedPaths.delete(rel);
      wrapEl.classList.remove("selected");
    } else {
      selectedPaths.add(rel);
      wrapEl.classList.add("selected");
    }
    const n = selectedPaths.size;
    if (selCount) selCount.textContent = String(n);
    if (toolbar) toolbar.hidden = n === 0;
    if (downloadBtn) downloadBtn.disabled = n === 0;
  };

  // Auto-load only when job_id is in the URL (no localStorage fallback)
  try {
    const params = new URLSearchParams(window.location.search);
    const j = params.get('job_id');
    if (j && !jobId) {
      jobId = j;
      loadResult(j);
    }
  } catch {}
}

window.addEventListener("DOMContentLoaded", bindUI);

async function downloadZip(paths, filename) {
  try {
    if (!paths || !paths.length) { notify("다운로드할 항목이 없습니다.", "error"); return; }
    if (!jobId) { notify("작업 ID가 없습니다.", "error"); return; }
    const res = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: jobId, paths }) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename || `selected_${jobId}.zip`;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  } catch (e) {
    notify(`다운로드 실패: ${e.message || e}`, "error");
  }
}
