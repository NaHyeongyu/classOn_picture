from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageOps, ExifTags


MAX_SIDE = 1600


@dataclass
class LoadedImage:
    path: str
    rgb: np.ndarray  # HWC, uint8
    bgr: np.ndarray  # HWC, uint8
    width: int
    height: int
    shot_time: Optional[str]


def _exif_transpose(pil_img: Image.Image) -> Image.Image:
    try:
        pil_img = ImageOps.exif_transpose(pil_img)
    except Exception:
        pass
    return pil_img


def _read_shot_time(pil_img: Image.Image) -> Optional[str]:
    try:
        exif = pil_img.getexif()
        if not exif:
            return None
        # 36867: DateTimeOriginal
        return exif.get(36867) or exif.get(306)  # fallback: DateTime
    except Exception:
        return None


def load_image(path: str | Path, max_side: int = MAX_SIDE) -> LoadedImage:
    p = str(path)
    pil = Image.open(p).convert("RGB")
    pil = _exif_transpose(pil)
    w, h = pil.size
    shot_time = _read_shot_time(pil)

    # resize by max side
    scale = 1.0
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        new_w, new_h = int(w * scale), int(h * scale)
        pil = pil.resize((new_w, new_h), Image.LANCZOS)
        w, h = new_w, new_h

    rgb = np.array(pil, dtype=np.uint8)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return LoadedImage(path=p, rgb=rgb, bgr=bgr, width=w, height=h, shot_time=shot_time)


def crop_with_margin(img: np.ndarray, bbox_xywh: Tuple[int, int, int, int], margin: float = 0.2) -> np.ndarray:
    x, y, w, h = bbox_xywh
    H, W = img.shape[:2]
    cx, cy = x + w / 2.0, y + h / 2.0
    mw, mh = int(w * (1 + margin)), int(h * (1 + margin))
    x1 = max(0, int(cx - mw / 2))
    y1 = max(0, int(cy - mh / 2))
    x2 = min(W, int(cx + mw / 2))
    y2 = min(H, int(cy + mh / 2))
    return img[y1:y2, x1:x2]

