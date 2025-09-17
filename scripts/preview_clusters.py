#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.utils.fs import read_json
from src.viz.report import render_report


def main() -> None:
    ap = argparse.ArgumentParser(description="Preview clusters report (re-render HTML)")
    ap.add_argument("--out", required=True, help="Output directory where clusters.json lives")
    args = ap.parse_args()

    out_root = Path(args.out)
    data = read_json(out_root / "clusters.json")
    if not data:
        raise SystemExit("clusters.json not found. Run scripts/run_pipeline.py first.")
    render_report(out_root, data)
    print(f"Report written to {out_root / 'report.html'}")


if __name__ == "__main__":
    main()

