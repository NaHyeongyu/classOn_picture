from __future__ import annotations

import cv2
import numpy as np


class SmileScorer:
    def __init__(self) -> None:
        # Use OpenCV Haar cascade for smiles
        self.smile_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_smile.xml")

    def score(self, face_bgr: np.ndarray) -> float:
        gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
        # Parameters tuned modestly for typical face crops
        smiles = self.smile_cascade.detectMultiScale(gray, scaleFactor=1.7, minNeighbors=22)
        if len(smiles) == 0:
            # Heuristic: no detection ≈ low smile prob
            return 0.1
        # Heuristic: more/larger smile regions → higher probability
        H, W = gray.shape[:2]
        areas = [(w * h) / float(W * H) for (x, y, w, h) in smiles]
        prob = min(1.0, 0.3 + 1.5 * float(sum(areas)))
        return float(prob)

