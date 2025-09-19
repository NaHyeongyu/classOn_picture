from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, List

from src.utils.fs import write_json


BOOTSTRAP_CSS = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css"


def _format_float(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def _cluster_card(cluster: Dict, anchor_id: str) -> str:
    cid = cluster["cluster_id"]
    is_noise = cluster.get("is_noise", False)
    size = cluster.get("size", 0)
    stats = cluster.get("stats", {})
    top = cluster.get("top", [])

    label = f"인물 {cid}" if not is_noise else "Noise"
    badge_class = "bg-secondary" if is_noise else "bg-primary"
    avg_smile = _format_float(stats.get("avg_smile"))
    avg_sharp = _format_float(stats.get("avg_sharpness"))
    metrics = " · ".join(filter(None, [f"Smile {avg_smile}" if avg_smile else "", f"Sharp {avg_sharp}" if avg_sharp else ""]))
    metrics_html = html.escape(metrics or "No metrics available")

    top_items = []
    for idx, t in enumerate(top, start=1):
        thumb = html.escape(t.get("thumb_path", ""))
        photo = html.escape(t.get("photo_path", t.get("thumb_path", "")))
        score_val = _format_float(t.get("score"))
        if not score_val:
            raw_score = t.get("score")
            score_val = f"{raw_score}" if raw_score not in (None, "") else "--"
        score_html = html.escape(str(score_val))
        top_items.append(
            f"<a class='thumb-link text-decoration-none text-reset' href='{photo}' target='_blank'>"
            f"<img src='{thumb}' alt='cluster {cid} top {idx}' class='shadow-sm'/>"
            f"<div class='small text-muted text-center mt-1'>score {score_html}</div>"
            "</a>"
        )
    if not top_items:
        top_items.append("<div class='text-muted small'>대표 얼굴이 없습니다.</div>")

    top_html = "".join(top_items)
    top_count = len(top)

    return f"""
    <section id=\"{html.escape(anchor_id)}\" class=\"card shadow-sm border-0 mb-4 cluster-card\">
      <div class=\"card-header bg-white\">
        <div class=\"d-flex flex-wrap justify-content-between align-items-center gap-2\">
          <div>
            <strong>{html.escape(label)}</strong>
            <span class=\"badge {badge_class} rounded-pill ms-2\">faces {size}</span>
          </div>
          <div class=\"text-muted small\">{metrics_html}</div>
        </div>
      </div>
      <div class=\"card-body\">
        <div class=\"thumb-strip mb-3\">{top_html}</div>
        <div class=\"small text-muted\">대표 얼굴 {top_count}장 · 총 얼굴 {size}장</div>
      </div>
    </section>
    """


def render_report(out_root: Path, result: Dict) -> None:
    clusters = result.get("clusters", [])
    faces = result.get("faces", [])
    photos = result.get("photos", [])

    sorted_clusters = sorted(clusters, key=lambda c: (c.get("is_noise", False), -c.get("size", 0)))
    cluster_sections: List[str] = []
    nav_entries: List[str] = []
    person_count = 0
    noise_faces = 0

    for c in sorted_clusters:
        cid = c.get("cluster_id", -1)
        is_noise = c.get("is_noise", False)
        size = c.get("size", 0)
        anchor = f"cluster-{cid}" if not is_noise else "cluster-noise"
        cluster_sections.append(_cluster_card(c, anchor))

        stats = c.get("stats", {}) or {}
        smile = _format_float(stats.get("avg_smile"))
        sharp = _format_float(stats.get("avg_sharpness"))
        summary_bits: List[str] = []
        if smile:
            summary_bits.append(f"Smile {smile}")
        if sharp:
            summary_bits.append(f"Sharp {sharp}")
        summary_text = " · ".join(summary_bits) or ("Noise faces" if is_noise else "No metrics")

        label = "Noise" if is_noise else f"인물 {cid}"
        badge_class = "bg-secondary" if is_noise else "bg-primary"
        nav_entries.append(
            f"<a class='list-group-item list-group-item-action cluster-nav-item' href='#{anchor}'>"
            f"<div class='d-flex justify-content-between align-items-center'>"
            f"<span>{html.escape(label)}</span>"
            f"<span class='badge {badge_class} rounded-pill'>faces {size}</span>"
            "</div>"
            f"<div class='small text-muted mt-1'>{html.escape(summary_text)}</div>"
            "</a>"
        )

        if is_noise:
            noise_faces += size
        else:
            person_count += 1

    nav_html = "".join(nav_entries) if nav_entries else "<div class='list-group-item text-muted'>클러스터가 없습니다.</div>"
    clusters_html = "".join(cluster_sections) if cluster_sections else "<div class='alert alert-warning'>클러스터 결과가 없습니다.</div>"

    cluster_layout = (
        f"""
        <div class='row g-4 align-items-start mb-5'>
          <div class='col-12 col-lg-4 col-xl-3'>
            <div class='card sticky-top cluster-nav-card' style='top: 1rem;'>
              <div class='card-header d-flex justify-content-between align-items-center'>
                <span>인물 목록</span>
                <span class='badge bg-primary rounded-pill'>{person_count}</span>
              </div>
              <div class='list-group list-group-flush'>{nav_html}</div>
            </div>
          </div>
          <div class='col-12 col-lg-8 col-xl-9'>
            {clusters_html}
          </div>
        </div>
        """
        if cluster_sections
        else "<div class='alert alert-info'>표시할 클러스터가 없습니다.</div>"
    )

    # Originals grouped view
    grouping = result.get("grouping", {})
    grouped_dir = grouping.get("grouped_dir")
    clusters_to_photos: Dict[str, List[str]] = grouping.get("clusters_to_photos", {})
    no_face_list: List[str] = grouping.get("no_face", [])

    originals_sections = []
    if grouped_dir:
        # Person clusters
        person_panels = []
        for k in sorted([int(x) for x in clusters_to_photos.keys() if x.isdigit()]):
            rels = clusters_to_photos.get(str(k), [])
            thumbs = "".join(
                f"<a class='thumb-link' href='{html.escape(p)}' target='_blank'><img src='{html.escape(p)}' alt='인물 {k}'/></a>"
                for p in rels
            )
            fallback = "<div class='text-muted small'>이미지 없음</div>"
            person_panels.append(
                f"<details class='mb-3 rounded border bg-body-tertiary p-3'>"
                f"<summary class='d-flex justify-content-between align-items-center'>"
                f"<span>인물 {k}</span>"
                f"<span class='badge bg-primary rounded-pill'>{len(rels)}</span>"
                "</summary>"
                f"<div class='thumb-grid mt-3'>{thumbs if thumbs else fallback}</div>"
                "</details>"
            )
        persons_html = "".join(person_panels) or "<div class='text-muted'>클러스터가 없습니다.</div>"

        # Noise and no_face
        noise_items = "".join(
            f"<a class='thumb-link' href='{html.escape(p)}' target='_blank'><img src='{html.escape(p)}' alt='Noise face'/></a>"
            for p in clusters_to_photos.get("noise", [])
        )
        nf_items = "".join(
            f"<a class='thumb-link' href='{html.escape(p)}' target='_blank'><img src='{html.escape(p)}' alt='No face'/></a>"
            for p in no_face_list
        )

        originals_sections.append(
            f"""
            <div class='my-5'>
              <h4 class='mb-3'>원본 사진 모음</h4>
              <div class='mb-4'>
                <h5 class='mb-2'>인물별</h5>
                {persons_html}
              </div>
              <div class='mb-4'>
                <details class='rounded border p-3 bg-body-tertiary'>
                  <summary class='d-flex justify-content-between align-items-center'>
                    <span>Noise (얼굴은 있으나 미분류)</span>
                    <span class='badge bg-secondary rounded-pill'>{len(clusters_to_photos.get("noise", []))}</span>
                  </summary>
                  <div class='thumb-grid mt-3'>{noise_items or "<div class='text-muted small'>없음</div>"}</div>
                </details>
              </div>
              <div>
                <details class='rounded border p-3 bg-body-tertiary'>
                  <summary class='d-flex justify-content-between align-items-center'>
                    <span>No Face (얼굴 없음)</span>
                    <span class='badge bg-secondary rounded-pill'>{len(no_face_list)}</span>
                  </summary>
                  <div class='thumb-grid mt-3'>{nf_items or "<div class='text-muted small'>없음</div>"}</div>
                </details>
              </div>
            </div>
            """
        )

    originals_html = "".join(originals_sections)

    summary_html = f"""
      <div class='row g-3 mb-4 summary-row'>
        <div class='col-6 col-md-3'>
          <div class='stat-card h-100'>
            <div class='label'>인물 수</div>
            <div class='value'>{person_count}</div>
          </div>
        </div>
        <div class='col-6 col-md-3'>
          <div class='stat-card h-100'>
            <div class='label'>얼굴 수</div>
            <div class='value'>{len(faces)}</div>
          </div>
        </div>
        <div class='col-6 col-md-3'>
          <div class='stat-card h-100'>
            <div class='label'>원본 사진</div>
            <div class='value'>{len(photos)}</div>
          </div>
        </div>
        <div class='col-6 col-md-3'>
          <div class='stat-card h-100'>
            <div class='label'>Noise 얼굴</div>
            <div class='value'>{noise_faces}</div>
          </div>
        </div>
      </div>
    """

    html_doc = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset=\"utf-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
        <title>Face Clusters Report</title>
        <link href=\"{BOOTSTRAP_CSS}\" rel=\"stylesheet\"/>
        <style>
          body {{ background-color: #f8f9fa; }}
          .cluster-card {{ background-color: #ffffff; }}
          .cluster-nav-card {{ max-height: 80vh; overflow-y: auto; }}
          .cluster-nav-item {{ font-size: 0.95rem; }}
          .thumb-strip {{ display: flex; gap: 0.75rem; overflow-x: auto; padding-bottom: 0.5rem; }}
          .thumb-strip img {{ width: 110px; height: 110px; object-fit: cover; border-radius: 0.75rem; }}
          .thumb-strip::-webkit-scrollbar {{ height: 6px; }}
          .thumb-strip::-webkit-scrollbar-thumb {{ background: #ced4da; border-radius: 3px; }}
          .thumb-grid {{ display: flex; flex-wrap: wrap; gap: 0.75rem; }}
          .thumb-grid img {{ width: 140px; height: 140px; object-fit: cover; border-radius: 0.75rem; box-shadow: 0 0.25rem 0.5rem rgba(0,0,0,0.08); }}
          details > summary {{ cursor: pointer; list-style: none; }}
          details > summary::marker {{ display: none; }}
          details > summary::after {{ content: '\25BC'; font-size: 0.75rem; margin-left: 0.5rem; transition: transform 0.2s ease; }}
          details[open] > summary::after {{ transform: rotate(180deg); }}
          details[open] > summary {{ margin-bottom: 0.5rem; }}
          .thumb-link {{ display: inline-flex; }}
          .stat-card {{ background: #ffffff; border-radius: 1rem; padding: 1rem; box-shadow: 0 0.25rem 0.5rem rgba(0,0,0,0.05); }}
          .stat-card .label {{ font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #6c757d; }}
          .stat-card .value {{ font-size: 1.6rem; font-weight: 600; color: #212529; }}
        </style>
      </head>
      <body>
        <div class=\"container my-4\">
          <h3 class=\"mb-3\">Face Clusters Report</h3>
          {summary_html}
          {cluster_layout}
          {originals_html}
        </div>
      </body>
    </html>
    """
    out_path = out_root / "report.html"
    out_path.write_text(html_doc, encoding="utf-8")
