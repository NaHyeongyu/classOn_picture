from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List
import shutil


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def iter_images(root: str | Path, exts: Iterable[str] = (".jpg", ".jpeg", ".png")) -> List[Path]:
    """Recursively list images under root with case-insensitive extension match."""
    root_p = Path(root)
    exts_l = {e.lower() for e in exts}
    files: List[Path] = []
    for p in root_p.rglob("*"):
        try:
            if p.is_file() and p.suffix.lower() in exts_l:
                files.append(p)
        except Exception:
            continue
    return sorted(files)


def file_hash(path: str | Path) -> str:
    p = Path(path)
    stat = p.stat()
    h = hashlib.sha256()
    h.update(str(p).encode())
    h.update(str(stat.st_mtime_ns).encode())
    h.update(str(stat.st_size).encode())
    return h.hexdigest()[:16]


def read_json(path: str | Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def link_or_copy(src: str | Path, dst: str | Path, mode: str = "copy") -> None:
    """
    Create `dst` from `src` using:
    - mode="symlink": try symlink, fallback to copy2
    - mode="copy": always copy2
    """
    src_p, dst_p = Path(src), Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    if mode == "symlink":
        try:
            if dst_p.exists() or dst_p.is_symlink():
                return
            os.symlink(src_p, dst_p)
            return
        except Exception:
            pass
    # default: copy
    if not dst_p.exists():
        shutil.copy2(src_p, dst_p)
