from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
try:
    import hdbscan as _hdbscan
except Exception:  # pragma: no cover - optional at runtime/packaging
    _hdbscan = None


def l2_normalize(x: np.ndarray, axis: int = 1, eps: float = 1e-8) -> np.ndarray:
    n = np.linalg.norm(x, axis=axis, keepdims=True)
    return x / np.maximum(n, eps)


def cluster_embeddings(
    embeddings: np.ndarray,
    min_cluster_size: int = 5,
    min_samples: Optional[int] = None,
) -> Tuple[np.ndarray, object]:
    """
    Returns labels (-1 for noise) and the fitted clustering model.
    Primary: HDBSCAN (if available). Fallback: sklearn DBSCAN.
    Use L2-normalized embeddings; Euclidean distance on unit vectors ≈ cosine distance.
    """
    if embeddings.size == 0:
        return np.empty((0,), dtype=int), None

    X = l2_normalize(embeddings.astype(np.float32), axis=1)

    if _hdbscan is not None:
        clusterer = _hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            metric="euclidean",
            prediction_data=False,
            core_dist_n_jobs=1,
        )
        labels = clusterer.fit_predict(X)
        return labels.astype(int), clusterer

    # Fallback: DBSCAN
    try:
        from sklearn.cluster import DBSCAN
    except Exception:
        # As a last resort: single cluster
        return np.zeros((X.shape[0],), dtype=int), None

    eps = 0.35  # tuned mildly for unit vectors
    ms = int(min_cluster_size) if min_cluster_size is not None else 5
    try:
        model = DBSCAN(eps=eps, min_samples=ms, metric="euclidean")
    except TypeError:
        model = DBSCAN(eps=eps, min_samples=ms)
    labels = model.fit_predict(X)
    return labels.astype(int), model
