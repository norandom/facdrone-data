# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Causal data-quality detectors (task 6.1; R12.1, R12.5).

A bank of pure outlier detectors over an acquired frame. All detectors EXCEPT
``isolation_forest_outliers`` are strictly causal — every flag at row T uses
only rows at or before T (trailing/expanding windows, never centered or forward
windows), so appending future rows cannot change a past verdict (R12.5).

``isolation_forest_outliers`` is the one exception: it fits the forest on the
WHOLE provided frame, so a past row's verdict depends on the full frame. This
is causal at the NODE level under the design's "fit at ingest on the stored
archive, never future rows" rule (design.md) — i.e. the validation node must
call the ensemble on the archive truncated to the as-of date, so the frame
never contains future rows. The structural no-lookahead guarantee is enforced
there (and proven by the task-14.3 causal-validation property test), not by
this detector in isolation. Do NOT call it on a frame that mixes past and
future rows and expect append-invariance.

This is the facdrone requirement the reference price/volume stack lacked — the
tick-sequence checks below (monotonicity, gaps, bid/ask sanity) exist because
bar construction is order-dependent and latency artifacts silently bias the
dollar/info bars the HMM trains on.

Each detector takes the frame plus :class:`DetectorThresholds` and returns a
boolean ``Series`` aligned to the frame index (array-in/array-out, no IO). The
ensemble (task 6.2) combines them by weighted voting. Thresholds are
asset-class-aware via :func:`thresholds_for`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

# Imported as `from sklearn import ensemble` (not `from sklearn.ensemble import
# IsolationForest`) so the structural scanner's suffix-based import resolver does
# not match the module path `sklearn.ensemble` to this package's own
# `quality/ensemble.py` and report a phantom detectors↔ensemble import cycle.
from sklearn import ensemble as sk_ensemble

# Modified z-score constant (Iglewicz & Hoaglin): 0.6745 = Φ⁻¹(0.75), so that
# 0.6745·(x−median)/MAD is on a standard-normal scale. 1/0.6745 ≈ 1.4826 is the
# Hampel MAD→σ consistency factor.
_MZ_CONST = 0.6745
_MAD_TO_SIGMA = 1.4826
_IFOREST_SEED = 42  # pinned: deterministic fits (R12.5 reproducibility)


@dataclass(frozen=True)
class DetectorThresholds:
    """Asset-class-aware detector thresholds (R12.1, R12.5).

    Defaults mirror the reference quality stack; :func:`thresholds_for`
    overrides per asset class (e.g. crypto tolerates larger moves than forex).
    """

    rolling_window: int = 20
    mad_z: float = 3.0  # robust modified-z |z| threshold
    hampel_k: float = 3.0  # Hampel σ multiplier
    iforest_contamination: float = 0.02
    iforest_n_estimators: int = 200
    fat_finger_return: float = 0.05  # |ΔP| jump threshold
    fat_finger_vol_z: float = 3.0  # accompanying volume z-score
    flash_crash_drawdown: float = -0.05  # peak→trough drop
    flash_crash_recovery: float = 0.5  # fraction of the drop recovered
    max_gap_seconds: float = 120.0  # tick inter-arrival gap
    iat_variance_z: float = 4.0  # inter-arrival-time z-score


# Values are validated by the dataclass field types at construction; typed Any
# so ``replace(**overrides)`` accepts the mixed int/float field set.
_BY_ASSET_CLASS: dict[str, dict[str, Any]] = {
    "crypto": {"fat_finger_return": 0.12, "mad_z": 4.0, "flash_crash_drawdown": -0.10},
    "forex": {"fat_finger_return": 0.02, "flash_crash_drawdown": -0.03},
    "indices": {"fat_finger_return": 0.04},
    "commodities": {"fat_finger_return": 0.06},
    "precious metals": {"fat_finger_return": 0.05},
}


def thresholds_for(asset_class: str) -> DetectorThresholds:
    """Return thresholds tuned for ``asset_class`` (R12.5), defaults otherwise."""
    overrides = _BY_ASSET_CLASS.get(asset_class, {})
    return replace(DetectorThresholds(), **overrides)


def _bool_series(mask: pd.Series, index: pd.Index) -> pd.Series:
    """Coerce a (possibly NaN-bearing) mask to a clean bool Series on ``index``."""
    return mask.reindex(index).fillna(False).astype(bool)


def robust_zscore_outliers(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """Modified z-score vs the EXPANDING (causal, whole-history) median/MAD.

    Catches values far from the overall distribution. Expanding windows use
    only rows up to and including the current one — never future rows."""
    close = frame["close"]
    mp = thr.rolling_window
    med = close.expanding(min_periods=mp).median()
    mad = (close - med).abs().expanding(min_periods=mp).median()
    mz = _MZ_CONST * (close - med) / mad.replace(0.0, np.nan)
    return _bool_series(mz.abs() > thr.mad_z, frame.index)


def hampel_outliers(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """Hampel filter on a TRAILING rolling window — catches local spikes the
    expanding robust-z would average away."""
    close = frame["close"]
    mp = thr.rolling_window
    med = close.rolling(mp, min_periods=mp).median()
    mad = (close - med).abs().rolling(mp, min_periods=mp).median()
    deviation = (close - med).abs()
    threshold = thr.hampel_k * _MAD_TO_SIGMA * mad
    return _bool_series((mad > 0.0) & (deviation > threshold), frame.index)


def isolation_forest_outliers(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """IsolationForest over (return, volume) features, fit on the provided
    archive with a pinned seed (deterministic, R12.5). Too-small frames yield
    no flags."""
    returns = frame["close"].pct_change().fillna(0.0)
    columns = [returns.to_numpy(dtype=float)]
    if "volume" in frame.columns:
        columns.append(frame["volume"].fillna(0.0).to_numpy(dtype=float))
    features = np.column_stack(columns)
    if len(features) < max(thr.rolling_window, 10):
        return pd.Series(False, index=frame.index)
    model = sk_ensemble.IsolationForest(
        contamination=thr.iforest_contamination,
        n_estimators=thr.iforest_n_estimators,
        random_state=_IFOREST_SEED,
    )
    predictions = model.fit_predict(features)
    return pd.Series(predictions == -1, index=frame.index)


def fat_finger_outliers(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """A large price jump ACCOMPANIED by a volume spike (a keying error), not
    either alone. Needs a volume column; otherwise no flags."""
    if "volume" not in frame.columns:
        return pd.Series(False, index=frame.index)
    mp = thr.rolling_window
    returns = frame["close"].pct_change().abs()
    volume = frame["volume"]
    vol_mean = volume.rolling(mp, min_periods=mp).mean()
    vol_std = volume.rolling(mp, min_periods=mp).std()
    vol_z = (volume - vol_mean) / vol_std.replace(0.0, np.nan)
    jumped = returns > thr.fat_finger_return
    spiked = vol_z > thr.fat_finger_vol_z
    return _bool_series(jumped & spiked, frame.index)


def flash_crash_outliers(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """A sharp drawdown from a recent peak followed by a recovery (a transient
    V), flagged on the recovery bar so only past/current rows are read."""
    close = frame["close"]
    mp = thr.rolling_window
    prior_peak = close.shift().rolling(mp, min_periods=mp).max()
    trough = close.rolling(mp, min_periods=mp).min()
    drop = (trough - prior_peak) / prior_peak
    span = (prior_peak - trough).replace(0.0, np.nan)
    recovered = (close - trough) / span
    crashed = drop < thr.flash_crash_drawdown
    recovering = recovered >= thr.flash_crash_recovery
    return _bool_series(crashed & recovering, frame.index)


def nonmonotonic_timestamps(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """Flag rows whose timestamp goes backwards vs the previous row (R12.1)."""
    del thr
    ts = pd.to_datetime(frame["timestamp"])
    return _bool_series(ts < ts.shift(), frame.index)


def tick_gap_outliers(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """Flag rows arriving after an inter-arrival gap exceeding the threshold."""
    ts = pd.to_datetime(frame["timestamp"])
    gap_seconds = ts.diff().dt.total_seconds()
    return _bool_series(gap_seconds > thr.max_gap_seconds, frame.index)


def iat_variance_outliers(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """Flag inter-arrival times that are statistical outliers vs the trailing
    inter-arrival distribution (batching/latency artifacts, R12.1)."""
    mp = thr.rolling_window
    ts = pd.to_datetime(frame["timestamp"])
    gap_seconds = ts.diff().dt.total_seconds()
    mean = gap_seconds.rolling(mp, min_periods=mp).mean()
    std = gap_seconds.rolling(mp, min_periods=mp).std()
    z = (gap_seconds - mean) / std.replace(0.0, np.nan)
    return _bool_series(z.abs() > thr.iat_variance_z, frame.index)


def bid_ask_outliers(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """Flag crossed/locked or non-positive quotes (ask < bid, or ≤ 0)."""
    del thr
    if not {"bid", "ask"}.issubset(frame.columns):
        return pd.Series(False, index=frame.index)
    bid = frame["bid"]
    ask = frame["ask"]
    insane = (ask < bid) | (bid <= 0.0) | (ask <= 0.0)
    return _bool_series(insane, frame.index)


# ── structural integrity: not statistical opinions ──────────────────


def nonpositive_prices(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """Rows whose close is not strictly positive.

    This is not an outlier judgement but a structural fact: a non-positive
    price cannot normalise a return or a price feature, so every log-based
    consumer downstream must treat the row as unmeasurable. WTI genuinely
    closed at -37.63 on 2020-04-20, so a flag here does NOT mean the datum is
    wrong — it means the row needs human review rather than silent use.
    """
    del thr  # a price is positive or it is not; no threshold applies
    return _bool_series(frame["close"] <= 0.0, frame.index)


def price_sign_changes(frame: pd.DataFrame, thr: DetectorThresholds) -> pd.Series:
    """Rows where the close crosses zero from the previous bar.

    A sign change is the signature that separates corruption from a real
    excursion. Air France-KLM carries 257 consecutive negative closes ending in
    a -229.94 to +61.65 jump overnight — an artifact of naive back-adjustment
    through a capital action, not a market event. Both edges of such a run are
    flagged, so the operator sees where the fabricated segment begins and ends.
    """
    del thr
    close = frame["close"]
    previous = close.shift(1)
    crossed = ((close > 0.0) & (previous < 0.0)) | ((close < 0.0) & (previous > 0.0))
    return _bool_series(crossed, frame.index)
