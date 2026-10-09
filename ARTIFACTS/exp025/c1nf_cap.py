"""EXP-025 C1-NF: the one added line of the rule, as code.

Rule (EXP-025 section 2): after stage 2 (prediction > 0.02) and BEFORE the one-position-per-mint book, drop the row if h_top1 > 0.5
or h_top1 is NaN.

h_top1 is computed by the pinned scripts/11_passA.py (the `hold = np.bincount(...)` block): the largest positive cumulative PumpSwap net-token position
among wallets that traded the canonical pool before the decision slot, divided by the sum of all positive positions. This module only applies the
threshold. It computes no outcome and reads no file.
"""
from __future__ import annotations

import numpy as np

H_TOP1_MAX = 0.5  # pinned; caps 0.3 and 0.7 are report-only (EXP-025 section 8)


def keep_mask(h_top1, cap: float = H_TOP1_MAX) -> np.ndarray:
    """True where the row stays: h_top1 is a number and h_top1 <= cap. NaN is dropped. The comparison is `<=`, so exactly 0.5 stays."""
    h = np.asarray(h_top1, dtype=np.float64)
    return np.isfinite(h) & (h <= cap)


def apply_cap_before_book(h_top1, selected, cap: float = H_TOP1_MAX) -> np.ndarray:
    """selected: boolean stage-2 selection (prediction > threshold). Returns the selection that enters the per-mint book."""
    sel = np.asarray(selected, dtype=bool)
    if sel.shape != np.asarray(h_top1).shape:
        raise ValueError("shape mismatch between selection and h_top1")
    return sel & keep_mask(h_top1, cap)
