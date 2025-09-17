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
          <div class=\"row row-cols-1 row-cols-md-2 row-cols-lg-3 g-3\">
            {cards}
          </div>
        </div>
      </body>
    </html>
    """
    out_path = out_root / "report.html"
    out_path.write_text(html_doc, encoding="utf-8")

