from __future__ import annotations

from typing import Optional

import numpy as np


def brisque_score(img_bgr: np.ndarray) -> Optional[float]:
    """
    Optional BRISQUE score in [0, 1], where 1=best.
    Uses pybrisque if installed. If unavailable or errors, returns None.
    """
    try:
        from pybrisque import BRISQUE
        import cv2

        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        # pybrisque expects file path or cv2 image; we'll pass gray image
        raw = BRISQUE().score(gray)
        # BRISQUE raw is roughly 0~100 (lower is sharper/better). Map to 0~1 (higher better)
        raw = float(raw)
        mapped = 1.0 - max(0.0, min(1.0, raw / 100.0))
        return mapped
    except Exception:
        return None

