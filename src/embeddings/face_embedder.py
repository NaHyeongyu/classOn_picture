from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from src.utils.logging import setup_logger


logger = setup_logger()


class FaceEmbedder:
    """
    Wraps InsightFace embedding if available; otherwise computes a simple
    deterministic 512D fallback embedding from the face crop (not for production).
    """

    def __init__(self) -> None:
        self._model = None
        self._insight_embedding = False
        try:
            from insightface.app import FaceAnalysis  # noqa: F401
            # If detector was loaded, we may already get embeddings from faces.
            # This embedder is for safety if only face crops are available.
            from insightface.model_zoo import get_model

            # Use arcface_r100 or similar; but FaceAnalysis already yields embeddings.
            # We keep a lightweight placeholder here. Prefer using detector's embeddings.
            self._insight_embedding = False
        except Exception:
            self._insight_embedding = False

    def embed(self, face_bgr: np.ndarray) -> Optional[np.ndarray]:
        if self._insight_embedding and self._model is not None:
            # Not used in this MVP; embeddings come from FaceAnalysis.
            pass

        # Fallback: simple handcrafted 512D vector (normalized), ensures pipeline runs offline.
        # DO NOT use for production identity clustering.
        gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (32, 16), interpolation=cv2.INTER_AREA)  # 512 dims
        feat = resized.astype(np.float32).flatten()
        norm = np.linalg.norm(feat) + 1e-8
        return feat / norm

