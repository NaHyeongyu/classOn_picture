from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from tqdm import tqdm

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
        return {}

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

    if not embeddings:
        logger.warning("No faces detected with embeddings. Nothing to cluster.")
        return {}

    # Save embedding cache
    emb_arr = np.stack(embeddings, axis=0)
    np.save(cache_dir / "face_embeddings.npy", emb_arr)

    # Cluster
    _progress("clustering", 75.0, {"faces": len(faces)})
    labels, model = cluster_embeddings(emb_arr, min_cluster_size=min_cluster_size)
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

    # Map: cluster id -> set of photo_ids
    cid_to_photo_ids: Dict[int, set] = defaultdict(set)
    for f in faces:
        cid_to_photo_ids[f.cluster_id].add(f.photo_id)

    # Build photo lookup and helper to relative name
    photos_by_id: Dict[int, Photo] = {p.id: p for p in photos}

    # Positive clusters
    for cid, photo_ids in cid_to_photo_ids.items():
        if cid < 0:
            continue
        folder = ensure_dir(grouped_root / f"person_{cid:03d}")
        rels: List[str] = []
        for pid in sorted(photo_ids):
            ph = photos_by_id[pid]
            dst = folder / Path(ph.path).name
            link_or_copy(ph.path, dst, mode="symlink" if link_originals else "copy")
            rels.append(os.path.relpath(dst, start=out_root))
        cluster_to_photos[str(cid)] = rels

    # Noise faces
    noise_ids = cid_to_photo_ids.get(-1, set())
    noise_rels: List[str] = []
    if noise_ids:
        noise_dir = ensure_dir(grouped_root / "noise")
        for pid in sorted(noise_ids):
            ph = photos_by_id[pid]
            dst = noise_dir / Path(ph.path).name
            link_or_copy(ph.path, dst, mode="symlink" if link_originals else "copy")
            noise_rels.append(os.path.relpath(dst, start=out_root))
    cluster_to_photos["noise"] = noise_rels

    # Photos with no detected faces
    all_face_photo_ids = set(f.photo_id for f in faces)
    noface_rels: List[str] = []
    noface_dir = ensure_dir(grouped_root / "no_face")
    for p in photos:
        if p.id not in all_face_photo_ids:
            dst = noface_dir / Path(p.path).name
            link_or_copy(p.path, dst, mode="symlink" if link_originals else "copy")
            noface_rels.append(os.path.relpath(dst, start=out_root))

    out["grouping"] = {
        "grouped_dir": os.path.relpath(grouped_root, start=out_root),
        "clusters_to_photos": cluster_to_photos,
        "no_face": noface_rels,
    }

    # Render report
    _progress("report", 96.0, {})
    render_report(out_root, out)

    _progress("done", 100.0, {"photos": len(photos), "faces": len(faces)})
    logger.info(f"Processed {len(photos)} photos, {len(faces)} faces → {len([c for c in clusters if c!=-1])} clusters.")
    return out
