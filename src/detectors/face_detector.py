from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from src.utils.logging import setup_logger


logger = setup_logger()


@dataclass
class DetectedFace:
    bbox_xywh: Tuple[int, int, int, int]
    det_score: float
    embedding: Optional[np.ndarray]
    landmarks: Optional[np.ndarray]


class InsightFaceDetector:
    def __init__(self, model_name: str = "buffalo_l") -> None:
        self.model_name = model_name
        self._app = None

    def load(self) -> None:
        try:
            from insightface.app import FaceAnalysis

            self._app = FaceAnalysis(name=self.model_name, providers=["CPUExecutionProvider"])
            # det_size can be adjusted for speed/accuracy tradeoff
            self._app.prepare(ctx_id=0, det_size=(640, 640))
            logger.info(f"Loaded InsightFace FaceAnalysis: {self.model_name}")
        except Exception as e:
            logger.warning(f"Failed to load InsightFace ({e}). Falling back to Haar cascades (no real embeddings).")
            self._app = None

    def detect(self, img_bgr: np.ndarray) -> List[DetectedFace]:
        if self._app is not None:
            faces = self._app.get(img_bgr)
            results: List[DetectedFace] = []
            for f in faces:
                # bbox as [x1, y1, x2, y2]
                b = f.bbox.astype(int)
                x1, y1, x2, y2 = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                w, h = max(0, x2 - x1), max(0, y2 - y1)
                bbox_xywh = (x1, y1, w, h)
                emb = None
                if hasattr(f, "embedding") and f.embedding is not None:
                    emb = np.array(f.embedding, dtype=np.float32)
                lm = getattr(f, "landmark_2d_106", None)
                results.append(DetectedFace(bbox_xywh=bbox_xywh, det_score=float(getattr(f, "det_score", 1.0)), embedding=emb, landmarks=lm))
            return results

        # Fallback: Haar cascade face detection. Embeddings are dummy.
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        rects = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        res: List[DetectedFace] = []
        for (x, y, w, h) in rects:
            res.append(DetectedFace(bbox_xywh=(int(x), int(y), int(w), int(h)), det_score=0.5, embedding=None, landmarks=None))
        return res

