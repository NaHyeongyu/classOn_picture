#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict

# Add project root to sys.path for "src" package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.pipeline import run_pipeline  # noqa: E402
from src.utils.fs import ensure_dir  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Run face clustering and recommendation pipeline")
    ap.add_argument("--input", required=True, help="Input images directory (e.g., data/input)")
    ap.add_argument("--out", required=True, help="Output directory (e.g., data/output)")
    ap.add_argument("--topk", type=int, default=3, help="Top-K recommendations per cluster")
    ap.add_argument("--min-cluster-size", type=int, default=5, help="HDBSCAN min_cluster_size")
    ap.add_argument("--link-originals", action="store_true", help="Use symlinks instead of copying originals into grouped folders")
    ap.add_argument("--status-json", help="Optional status.json path to write progress")
    args = ap.parse_args()

    ensure_dir(args.out)

    def write_status(stage: str, pct: float, extra: Dict):
        if not args.status_json:
            return
        try:
            data = {
                "stage": stage,
                "percent": float(pct),
                "extra": extra or {},
                "done": stage == "done",
                "timestamp": time.time(),
            }
            p = Path(args.status_json)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    run_pipeline(
        args.input,
        args.out,
        topk=args.topk,
        min_cluster_size=args.min_cluster_size,
        link_originals=args.link_originals,
        progress_cb=write_status if args.status_json else None,
    )


if __name__ == "__main__":
    main()
