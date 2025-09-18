from __future__ import annotations

import html
from pathlib import Path
from typing import Dict, List

from src.utils.fs import write_json


BOOTSTRAP_CSS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css"


def _cluster_card(cluster: Dict, faces_by_id: Dict[int, Dict], photos_by_id: Dict[int, Dict]) -> str:
    cid = cluster["cluster_id"]
    is_noise = cluster.get("is_noise", False)
    size = cluster.get("size", 0)
    stats = cluster.get("stats", {})
    top = cluster.get("top", [])

    title = f"Cluster {cid} ({size})" if not is_noise else f"Noise ({size})"
    badge = "<span class=\"badge bg-secondary\">Noise</span>" if is_noise else ""
    tops = "".join(
        f"<div class=\"col\"><div class=\"card\"><img class=\"card-img-top\" src=\"{html.escape(t['thumb_path'])}\"/><div class=\"card-body p-2\"><span class=\"badge bg-success\">Top</span> <small>score {t['score']}</small></div></div></div>"
        for t in top
    )
    if not tops:
        tops = "<div class=\"text-muted\"><small>No Top images (noise)</small></div>"

    body = f"""
    <div class=\"card h-100\">
      <div class=\"card-header d-flex justify-content-between align-items-center\">
        <strong>{html.escape(title)}</strong>
        {badge}
      </div>
      <div class=\"card-body\">
        <div class=\"mb-2\"><small>avg_smile: {stats.get('avg_smile', 0)}, avg_sharpness: {stats.get('avg_sharpness', 0)}</small></div>
        <div class=\"row row-cols-3 g-2\">
          {tops}
        </div>
      </div>
    </div>
    """
    return body


def render_report(out_root: Path, result: Dict) -> None:
    clusters = result.get("clusters", [])
    faces = result.get("faces", [])
    photos = result.get("photos", [])

    faces_by_id = {f["id"]: f for f in faces}
    photos_by_id = {p["id"]: p for p in photos}

    cards = "".join(
        f"<div class=\"col\">{_cluster_card(c, faces_by_id, photos_by_id)}</div>"
        for c in sorted(clusters, key=lambda c: (c.get("is_noise", False), -c.get("size", 0)))
    )

    # Originals grouped view
    grouping = result.get("grouping", {})
    grouped_dir = grouping.get("grouped_dir")
    clusters_to_photos: Dict[str, List[str]] = grouping.get("clusters_to_photos", {})
    no_face_list: List[str] = grouping.get("no_face", [])

    originals_sections = []
    if grouped_dir:
        # Person clusters
        items = []
        for k in sorted([int(x) for x in clusters_to_photos.keys() if x.isdigit()]):
            rels = clusters_to_photos.get(str(k), [])
            if not rels:
                continue
            imgs = "".join(
                f"<div class='col'><a href='{p}' target='_blank'><img class='img-fluid rounded' src='{p}'/></a></div>"
                for p in rels[:6]
            )
            fallback = "<div class='text-muted'>이미지 없음</div>"
            body_imgs = imgs if imgs else fallback
            items.append(
                f"<div class='col'><div class='card h-100'><div class='card-header'><strong>인물 {k}</strong></div>"
                f"<div class='card-body'><div class='row row-cols-3 g-2'>{body_imgs}</div></div></div></div>"
            )
        persons_html = "".join(items) or "<div class='text-muted'>클러스터가 없습니다.</div>"

        # Noise and no_face
        noise_imgs = "".join(
            f"<div class='col'><a href='{p}' target='_blank'><img class='img-fluid rounded' src='{p}'/></a></div>"
            for p in clusters_to_photos.get("noise", [])[:12]
        ) or "<div class='text-muted'>없음</div>"
        nf_imgs = "".join(
            f"<div class='col'><a href='{p}' target='_blank'><img class='img-fluid rounded' src='{p}'/></a></div>"
            for p in no_face_list[:12]
        ) or "<div class='text-muted'>없음</div>"

        originals_sections.append(
            f"""
            <div class='my-4'>
              <h4 class='mb-2'>원본 사진 — 클러스터별</h4>
              <div class='row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3'>
                {persons_html}
              </div>
            </div>
            <div class='my-4'>
              <h5 class='mb-2'>분리되지 않은 항목</h5>
              <div class='mb-1'><span class='badge bg-secondary'>Noise (얼굴은 있으나 미분류)</span></div>
              <div class='row row-cols-3 g-2'>{noise_imgs}</div>
              <div class='mt-3 mb-1'><span class='badge bg-secondary'>No Face (얼굴 없음)</span></div>
              <div class='row row-cols-3 g-2'>{nf_imgs}</div>
            </div>
            """
        )

    originals_html = "".join(originals_sections)

    html_doc = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset=\"utf-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
        <title>Face Clusters Report</title>
        <link href=\"{BOOTSTRAP_CSS}\" rel=\"stylesheet\"/>
      </head>
      <body>
        <div class=\"container my-4\">
          <h3 class=\"mb-3\">Face Clusters Report</h3>
          <div class=\"row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3\">{cards}</div>
          {originals_html}
        </div>
      </body>
    </html>
    """
    out_path = out_root / "report.html"
    out_path.write_text(html_doc, encoding="utf-8")
