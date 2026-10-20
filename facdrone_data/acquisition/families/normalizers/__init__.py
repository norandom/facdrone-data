# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Per-family normalizers (R2.3, R6.1, R6.5).

One explicit typed normalizer per family (D7) — never a config-driven mapper —
each reproducing the source's exact dict-key set and quirks verbatim (the ``netPostion``
typo, the ``key_metrics ⨝ ratios`` bvps fallback, CFTC zero-fill, Fama-French
%→decimal, ...). Shared, quirk-free mechanics (column projection, date coercion,
``_updated_at`` injection) live in :mod:`._base`.
"""

from __future__ import annotations
