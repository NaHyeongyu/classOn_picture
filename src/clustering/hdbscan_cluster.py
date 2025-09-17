from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import hdbscan


def l2_normalize(x: np.ndarray, axis: int = 1, eps: float = 1e-8) -> np.ndarray:
    n = np.linalg.norm(x, axis=axis, keepdims=True)
    return x / np.maximum(n, eps)


def cluster_embeddings(
    embeddings: np.ndarray,
    min_cluster_size: int = 5,
    min_samples: Optional[int] = None,
) -> Tuple[np.ndarray, hdbscan.HDBSCAN]:
    """
    Returns labels (-1 for noise) and the fitted hdbscan model.
    Use L2-normalized embeddings; Euclidean distance on unit vectors ≈ cosine distance.
    """
    if embeddings.size == 0:
        return np.empty((0,), dtype=int), None  # type: ignore
    X = l2_normalize(embeddings.astype(np.float32), axis=1)
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",
        prediction_data=False,
        core_dist_n_jobs=1,
    )
    labels = clusterer.fit_predict(X)
    return labels.astype(int), clusterer

