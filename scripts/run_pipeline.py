#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from src.pipeline import run_pipeline
from src.utils.fs import ensure_dir


def main() -> None:
    ap = argparse.ArgumentParser(description="Run face clustering and recommendation pipeline")
    ap.add_argument("--input", required=True, help="Input images directory (e.g., data/input)")
    ap.add_argument("--out", required=True, help="Output directory (e.g., data/output)")
    ap.add_argument("--topk", type=int, default=3, help="Top-K recommendations per cluster")
    ap.add_argument("--min-cluster-size", type=int, default=5, help="HDBSCAN min_cluster_size")
    args = ap.parse_args()

    ensure_dir(args.out)
    run_pipeline(args.input, args.out, topk=args.topk, min_cluster_size=args.min_cluster_size)


if __name__ == "__main__":
    main()

