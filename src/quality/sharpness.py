from __future__ import annotations

import cv2
import numpy as np


def variance_of_laplacian(gray: np.ndarray) -> float:
    # Expect gray uint8
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def sharpness_score(img_bgr: np.ndarray) -> float:
    # Return an unbounded score; caller will normalize across dataset
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return variance_of_laplacian(gray)

