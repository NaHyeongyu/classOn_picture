from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from tqdm import tqdm
from PIL import Image, ImageOps

from src.utils.fs import ensure_dir, iter_images, file_hash, write_json, link_or_copy
from src.utils.image import load_image, crop_with_margin
from src.utils.logging import setup_logger
from src.detectors.face_detector import InsightFaceDetector
from src.embeddings.face_embedder import FaceEmbedder
from src.quality.sharpness import sharpness_score
from src.quality.brisque import brisque_score
from src.quality.smile import SmileScorer
from src.clustering.hdbscan_cluster import cluster_embeddings
from src.viz.report import render_report


logger = setup_logger()


@dataclass
class Photo:
    id: int
    path: str
    shot_time: Optional[str]
    width: int
    height: int
    hash: str


@dataclass
class FaceRec:
    id: int
    photo_id: int
    bbox: Tuple[int, int, int, int]
    det_score: float
    embedding_idx: int
    smile_prob: float
    sharpness: float
    brisque: Optional[float]
    thumb_path: str
    cluster_id: int = -1


def _min_max_norm(values: List[float]) -> List[float]:
    if not values:
        return []
    vmin, vmax = float(min(values)), float(max(values))
    if vmax - vmin < 1e-8:
        return [0.5 for _ in values]
    return [(v - vmin) / (vmax - vmin) for v in values]


def run_pipeline(
    input_dir: str,
    output_dir: str,
    topk: int = 3,
    min_cluster_size: int = 5,
    link_originals: bool = False,
    progress_cb: Optional[Callable[[str, float, Dict], None]] = None,
) -> Dict:
    out_root = Path(output_dir)
    faces_dir = ensure_dir(out_root / "faces")
    cache_dir = ensure_dir(out_root / "cache")

    # Progress helper
    def _progress(stage: str, pct: float, extra: Dict = {}):
        try:
            if progress_cb:
                progress_cb(stage, float(max(0.0, min(100.0, pct))), extra)
        except Exception:
            pass

    # Scan inputs
    img_paths = iter_images(input_dir)
    _progress("scan", 5.0, {"total_images": len(img_paths)})
    if not img_paths:
        logger.warning("No input images found.")
        out = {
            "photos": [],
            "faces": [],
            "clusters": [],
            "params": {"topk": topk, "min_cluster_size": min_cluster_size},
            "grouping": {"grouped_dir": "grouped_photos", "clusters_to_photos": {}, "no_face": []},
        }
        write_json(out_root / "clusters.json", out)
        render_report(out_root, out)
        return out

    # Models
    detector = InsightFaceDetector()
    detector.load()
    embedder = FaceEmbedder()
    smile_scorer = SmileScorer()

    photos: List[Photo] = []
    faces: List[FaceRec] = []
    embeddings: List[np.ndarray] = []

    face_id = 0
    photo_id = 0
    total = len(img_paths)
    processed = 0
    for p in tqdm(img_paths, desc="Processing images"):
        try:
            li = load_image(p)
        except Exception as e:
            logger.warning(f"Failed to load {p}: {e}")
            continue
        ph = Photo(
            id=photo_id,
            path=str(p),
            shot_time=li.shot_time,
            width=li.width,
            height=li.height,
            hash=file_hash(p),
        )
        photos.append(ph)

        dets = detector.detect(li.bgr)
        for d in dets:
            x, y, w, h = d.bbox_xywh
            # Crop face area with margin for thumbnail & quality
            crop = crop_with_margin(li.bgr, d.bbox_xywh, margin=0.25)
            # Compute embedding
            emb = d.embedding
            if emb is None:
                emb = embedder.embed(crop)
            if emb is None:
                # skip faces with no embedding at all
                continue
            emb = emb.astype(np.float32)
            emb_idx = len(embeddings)
            embeddings.append(emb)

            # Quality & smile
            s_sharp = sharpness_score(crop)
            s_brisque = brisque_score(crop)
            s_smile = smile_scorer.score(crop)

            # Save thumbnail
            thumb_name = f"face_{face_id:06d}.jpg"
            thumb_path = faces_dir / thumb_name
            import cv2 as _cv2

            _cv2.imwrite(str(thumb_path), crop)

            faces.append(
                FaceRec(
                    id=face_id,
                    photo_id=photo_id,
                    bbox=(int(x), int(y), int(w), int(h)),
                    det_score=float(d.det_score),
                    embedding_idx=emb_idx,
                    smile_prob=float(s_smile),
                    sharpness=float(s_sharp),
                    brisque=float(s_brisque) if s_brisque is not None else None,
                    thumb_path=str(thumb_path),
                )
            )
            face_id += 1

        photo_id += 1
        processed += 1
        # Map image processing progress to 10% -> 70%
        pct = 10.0 + 60.0 * (processed / max(1, total))
        _progress("process_images", pct, {"processed": processed, "total": total})

    def _target_name(photo: Photo) -> str:
        src_name = Path(photo.path).name
        return f"{photo.id:06d}_{src_name}"

    def _preview_rel(rel: str) -> Path:
        rel_path = Path(rel)
        if rel_path.parts and rel_path.parts[0] == "grouped_photos":
            tail = Path(*rel_path.parts[1:])
            base = Path("previews") / tail
        else:
            base = Path("previews") / rel_path
        return base.with_suffix('.webp')

    def _make_preview(src_abs: Path, dst_abs: Path, max_side: int = 1200) -> None:
        try:
            dst_abs.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(str(src_abs)) as im:
                try:
                    im = ImageOps.exif_transpose(im)
                except Exception:
                    pass
                im = im.convert("RGB")
                w, h = im.size
                scale = 1.0
                if max(w, h) > max_side:
                    scale = max_side / float(max(w, h))
                    im = im.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                im.save(str(dst_abs), format="WEBP", quality=80, method=6)
        except Exception:
            pass

    if not embeddings:
        logger.warning("No faces detected with embeddings. Nothing to cluster.")
        photos_json = [
            {
                "id": p.id,
                "path": os.path.relpath(p.path, start=out_root),
                "shot_time": p.shot_time,
                "width": p.width,
                "height": p.height,
                "hash": p.hash,
            }
            for p in photos
        ]
        out = {
            "photos": photos_json,
            "faces": [],
            "clusters": [],
            "params": {"topk": topk, "min_cluster_size": min_cluster_size},
        }
        grouped_root = ensure_dir(out_root / "grouped_photos")
        noface_dir = ensure_dir(grouped_root / "no_face")
        noface_rels: List[str] = []
        noface_set: set[str] = set()
        for p in photos:
            dst = noface_dir / _target_name(p)
            rel = os.path.relpath(dst, start=out_root)
            if rel in noface_set:
                continue
            link_or_copy(p.path, dst, mode="symlink" if link_originals else "copy")
            noface_rels.append(rel)
            noface_set.add(rel)
        out["grouping"] = {
            "grouped_dir": os.path.relpath(grouped_root, start=out_root),
            "clusters_to_photos": {},
            "no_face": noface_rels,
            "labels": {},
            "hidden_clusters": [],
        }
        ensure_dir(out_root / "previews")
        for rel in noface_rels:
            try:
                prev_rel = _preview_rel(rel)
                src_abs = out_root / rel
                dst_abs = out_root / prev_rel
                if not dst_abs.exists():
                    _make_preview(src_abs, dst_abs)
            except Exception:
                continue
        write_json(out_root / "clusters.json", out)
        render_report(out_root, out)
        return out

    # Save embedding cache
    emb_arr = np.stack(embeddings, axis=0)
    np.save(cache_dir / "face_embeddings.npy", emb_arr)

    # Cluster
    _progress("clustering", 75.0, {"faces": len(faces)})
    labels, model = cluster_embeddings(emb_arr, min_cluster_size=min_cluster_size)
    pos = [lab for lab in set(labels.tolist()) if lab != -1]
    if len(pos) == 0:
        # Fallback for small batches: relax parameters
        new_mcs = 2 if emb_arr.shape[0] >= 2 else 1
        _progress("clustering_retry", 78.0, {"orig_mcs": min_cluster_size, "new_mcs": new_mcs})
        labels, model = cluster_embeddings(emb_arr, min_cluster_size=new_mcs, min_samples=1)
        logger.info(f"No clusters at mcs={min_cluster_size}; retried with mcs={new_mcs}, min_samples=1")
        pos = [lab for lab in set(labels.tolist()) if lab != -1]
        # Last resort: for very small batches, force a single cluster to avoid empty results
        if len(pos) == 0 and emb_arr.shape[0] <= 12:
            import numpy as _np
            labels = _np.zeros_like(labels)
            logger.info("Forced a single cluster for small batch (<=12 faces) to avoid all-noise result.")
    for f in faces:
        f.cluster_id = int(labels[f.embedding_idx])

    # Normalize sharpness
    sharp_vals = [f.sharpness for f in faces]
    sharp_norm = _min_max_norm(sharp_vals)
    # Final score: 0.6*smile + 0.4*sharpness_norm
    final_scores: List[float] = []
    for f, sn in zip(faces, sharp_norm):
        final_scores.append(0.6 * f.smile_prob + 0.4 * sn)

    _progress("scoring", 82.0, {})

    # Build clusters
    clusters: Dict[int, Dict] = {}
    by_cluster: Dict[int, List[int]] = defaultdict(list)
    for idx, f in enumerate(faces):
        by_cluster[f.cluster_id].append(idx)

    for cid, idxs in by_cluster.items():
        member_face_ids = [faces[i].id for i in idxs]
        size = len(idxs)
        avg_smile = float(np.mean([faces[i].smile_prob for i in idxs])) if size else 0.0
        avg_sharp = float(np.mean([faces[i].sharpness for i in idxs])) if size else 0.0
        scored = sorted(
            idxs,
            key=lambda i: final_scores[i],
            reverse=True,
        )
        top = []
        for i in scored[: topk if cid != -1 else 0]:  # do not pick top for noise by default
            f = faces[i]
            ph = next(p for p in photos if p.id == f.photo_id)
            top.append(
                {
                    "face_id": f.id,
                    "score": round(float(final_scores[i]), 4),
                    "smile": round(float(f.smile_prob), 4),
                    "sharpness": round(float(f.sharpness), 2),
                    "thumb_path": os.path.relpath(f.thumb_path, start=out_root),
                    "photo_path": os.path.relpath(ph.path, start=out_root),
                }
            )
        clusters[cid] = {
            "cluster_id": int(cid),
            "is_noise": cid == -1,
            "size": size,
            "member_face_ids": member_face_ids,
            "stats": {
                "avg_smile": round(avg_smile, 4),
                "avg_sharpness": round(avg_sharp, 2),
            },
            "top": top,
        }

    # Serialize minimal face info (exclude raw embeddings)
    faces_json = [
        {
            "id": f.id,
            "photo_id": f.photo_id,
            "bbox": list(f.bbox),
            "det_score": round(float(f.det_score), 4),
            "smile_prob": round(float(f.smile_prob), 4),
            "sharpness": round(float(f.sharpness), 2),
            "brisque": round(float(f.brisque), 4) if f.brisque is not None else None,
            "thumb_path": os.path.relpath(f.thumb_path, start=out_root),
            "cluster_id": int(f.cluster_id),
        }
        for f in faces
    ]

    photos_json = [
        {
            "id": p.id,
            "path": os.path.relpath(p.path, start=out_root),
            "shot_time": p.shot_time,
            "width": p.width,
            "height": p.height,
            "hash": p.hash,
        }
        for p in photos
    ]

    out = {
        "photos": photos_json,
        "faces": faces_json,
        "clusters": list(clusters.values()),
        "params": {
            "topk": topk,
            "min_cluster_size": min_cluster_size,
        },
    }
    write_json(out_root / "clusters.json", out)

    # Group original photos by cluster into grouped_photos/
    _progress("grouping", 90.0, {})
    # Group original photos by cluster into grouped_photos/
    grouped_root = ensure_dir(out_root / "grouped_photos")
    cluster_to_photos: Dict[str, List[str]] = {}

    # Build photo lookup and helper to relative name
    photos_by_id: Dict[int, Photo] = {p.id: p for p in photos}
    face_score_by_id: Dict[int, float] = {faces[i].id: float(final_scores[i]) for i in range(len(faces))}
    faces_by_photo: Dict[int, List[FaceRec]] = defaultdict(list)
    for f in faces:
        faces_by_photo[f.photo_id].append(f)

    # Assign each photo to a single representative cluster (largest face wins, ties -> higher score)
    photo_assignments: Dict[int, int] = {}
    for pid, face_list in faces_by_photo.items():
        best_positive: Optional[Tuple[float, float, int]] = None
        best_noise: Optional[Tuple[float, float, int]] = None
        for f in face_list:
            w = max(int(f.bbox[2]), 0)
            h = max(int(f.bbox[3]), 0)
            area = float(w * h)
            score = face_score_by_id.get(f.id, 0.0)
            candidate = (area, score, int(f.cluster_id))
            if f.cluster_id >= 0:
                if best_positive is None or (candidate[0], candidate[1]) > (best_positive[0], best_positive[1]):
                    best_positive = candidate
            else:
                if best_noise is None or (candidate[0], candidate[1]) > (best_noise[0], best_noise[1]):
                    best_noise = candidate
        if best_positive is not None:
            photo_assignments[pid] = best_positive[2]
        elif best_noise is not None:
            photo_assignments[pid] = -1

    assigned_by_cluster: Dict[int, List[int]] = defaultdict(list)
    for pid, cid in photo_assignments.items():
        assigned_by_cluster[int(cid)].append(pid)

    # Positive clusters
    for cid in sorted(c for c in clusters.keys() if c >= 0):
        rels: List[str] = []
        rels_set: set[str] = set()
        photo_ids = sorted(set(assigned_by_cluster.get(cid, [])))
        if photo_ids:
            folder = ensure_dir(grouped_root / f"person_{cid:03d}")
            for pid in photo_ids:
                ph = photos_by_id[pid]
                dst = folder / _target_name(ph)
                rel = os.path.relpath(dst, start=out_root)
                if rel in rels_set:
                    continue
                link_or_copy(ph.path, dst, mode="symlink" if link_originals else "copy")
                rels.append(rel)
                rels_set.add(rel)
        cluster_to_photos[str(cid)] = rels

    # Noise faces
    noise_ids = sorted(set(assigned_by_cluster.get(-1, [])))
    noise_rels: List[str] = []
    if noise_ids:
        noise_dir = ensure_dir(grouped_root / "noise")
        noise_set: set[str] = set()
        for pid in noise_ids:
            ph = photos_by_id[pid]
            dst = noise_dir / _target_name(ph)
            rel = os.path.relpath(dst, start=out_root)
            if rel in noise_set:
                continue
            link_or_copy(ph.path, dst, mode="symlink" if link_originals else "copy")
            noise_rels.append(rel)
            noise_set.add(rel)
    cluster_to_photos["noise"] = noise_rels

    # Photos with no detected faces
    all_face_photo_ids = set(f.photo_id for f in faces)
    noface_rels: List[str] = []
    noface_dir = ensure_dir(grouped_root / "no_face")
    noface_set: set[str] = set()
    for p in photos:
        if p.id not in all_face_photo_ids:
            dst = noface_dir / _target_name(p)
            rel = os.path.relpath(dst, start=out_root)
            if rel in noface_set:
                continue
            link_or_copy(p.path, dst, mode="symlink" if link_originals else "copy")
            noface_rels.append(rel)
            noface_set.add(rel)

    out["grouping"] = {
        "grouped_dir": os.path.relpath(grouped_root, start=out_root),
        "clusters_to_photos": cluster_to_photos,
        "no_face": noface_rels,
        "labels": {},
        "hidden_clusters": [],
    }

    # Generate previews (WEBP) for grouped photos to speed up modal rendering
    ensure_dir(out_root / "previews")

    # Build list of rel paths from grouping
    def _all_grouped_rels() -> List[str]:
        rels_all: List[str] = []
        for k, rels in cluster_to_photos.items():
            rels_all.extend(rels)
        rels_all.extend(noface_rels)
        return rels_all

    for rel in _all_grouped_rels():
        # rel is relative to out_root, starts with grouped_photos/
        try:
            prev_rel = _preview_rel(rel)
            src_abs = out_root / rel
            dst_abs = out_root / prev_rel
            if not dst_abs.exists():
                _make_preview(src_abs, dst_abs)
        except Exception:
            pass

    # Persist final JSON including grouping for API consumers
    write_json(out_root / "clusters.json", out)

    # Render report
    _progress("report", 96.0, {})
    render_report(out_root, out)

    _progress("done", 100.0, {"photos": len(photos), "faces": len(faces)})
    logger.info(f"Processed {len(photos)} photos, {len(faces)} faces → {len([c for c in clusters if c!=-1])} clusters.")
    return out
