#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

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
    args = ap.parse_args()

    ensure_dir(args.out)
    run_pipeline(args.input, args.out, topk=args.topk, min_cluster_size=args.min_cluster_size)


if __name__ == "__main__":
    main()
