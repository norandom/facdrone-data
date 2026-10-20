# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Kenneth French daily Fama-French 3-factor transport (R2.1, R2.3).

This module is the LIVE fetch layer for the keyless ``fama_french_factors``
family. Dartmouth Tuck ships the daily F-F 3-factor series as a ZIP archive
holding a single CSV: a short preamble, a header row (``,Mkt-RF,SMB,HML,RF``),
then rows ``YYYYMMDD,<mkt_rf>,<smb>,<hml>,<rf>`` in *percent* units, followed by
an annual block we must stop before.

``fetch`` returns the PARSED daily block as a frame carrying the original Ken
French header columns ``Date``/``Mkt-RF``/``SMB``/``HML``/``RF`` — exactly what
``FamaFrenchNormalizer`` reads. The normalizer owns the percent→decimal divide
and the ``-99.99``/``-999`` sentinel→NULL conversion, so ``fetch`` keeps the raw
percent tokens and only does the *structural* parsing: skip to the header, read
date rows, stop at the first non-eight-digit token (the blank line / annual block).

The network call is an injected ``KenFrenchZip`` transport (the zip bytes), so the adapter
is offline-testable; ``live_kenfrench`` GETs through ``policy.fetch_url``.
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.providers.policy import fetch_url
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.ports import FetchRequest

log = logging.getLogger(__name__)

# The Dartmouth daily 3-factor archive.
_FAMA_FRENCH_URL = (
    "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
    "F-F_Research_Data_Factors_daily_CSV.zip"
)

# () -> the raw zip archive bytes. May raise; the adapter catches and fails open
# (an empty frame leaves the existing table intact: the fetch fails open).
KenFrenchZip = Callable[[], bytes]

# The raw header columns ``FamaFrenchNormalizer`` reads (percent units, parsed but
# unconverted; the normalizer owns the divide + sentinel handling).
_RAW_COLUMNS = ("Date", "Mkt-RF", "SMB", "HML", "RF")


def _parse_daily_block(raw_csv: str) -> pd.DataFrame:
    """Parse the CSV *structure* into the raw header frame.

    Skip to the header line (the one carrying ``Mkt-RF``/``SMB``/``HML``), then
    read ``YYYYMMDD,...`` rows, stopping at the first row whose leading token is
    not an eight-digit date (the blank separator before the annual block). Values
    are kept as raw percent tokens under the original Ken French headers; the
    normalizer performs percent→decimal and sentinel→NULL."""
    lines = raw_csv.splitlines()

    header_idx: int | None = None
    for i, line in enumerate(lines):
        if "Mkt-RF" in line and "SMB" in line and "HML" in line:
            header_idx = i
            break
    if header_idx is None:
        return pd.DataFrame(columns=list(_RAW_COLUMNS))

    rows: list[dict[str, str]] = []
    for line in lines[header_idx + 1 :]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        date_tok = parts[0]
        if not (len(date_tok) == 8 and date_tok.isdigit()):
            # Reached the annual block (or a blank separator): stop.
            break
        rows.append(
            {
                "Date": date_tok,
                "Mkt-RF": parts[1],
                "SMB": parts[2],
                "HML": parts[3],
                "RF": parts[4],
            }
        )

    return pd.DataFrame(rows, columns=list(_RAW_COLUMNS))


def _unzip_first_member(payload: bytes) -> str:
    """Read the single CSV member of the F-F zip archive (the first member, ``names[0]``)."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if not names:
            log.warning("kenfrench: ZIP archive is empty")
            return ""
        csv_bytes = archive.read(names[0])
    return csv_bytes.decode("utf-8", errors="replace")


def live_kenfrench() -> KenFrenchZip:
    """The live transport (wired only at the composition root): the Dartmouth daily 3-factor
    zip's bytes. Keyless."""
    return lambda: fetch_url(Provider.KEN_FRENCH, _FAMA_FRENCH_URL)


@dataclass(frozen=True)
class KenFrenchClient:
    """``MarketDataClient`` for the keyless Ken French ``fama_french_factors`` family.

    ``fetch`` downloads (via the injected transport) the daily 3-factor zip,
    unzips + parses the daily block, and returns the raw ``Date``/``Mkt-RF``/
    ``SMB``/``HML``/``RF`` frame that ``FamaFrenchNormalizer`` reads, windowed to
    ``[req.start, req.end]``. A non-``fama_french_factors`` dataset returns empty →
    the refresher fails open and skips it; any transport/zip/parse error likewise
    fails open with an empty frame."""

    transport: KenFrenchZip

    @property
    def provider(self) -> Provider:
        return Provider.KEN_FRENCH

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset != "fama_french_factors":
            return pd.DataFrame()  # Ken French serves only the daily 3-factor family
        try:
            payload = self.transport()
            raw_csv = _unzip_first_member(payload)
            frame = _parse_daily_block(raw_csv)
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning("kenfrench: fetch failed: %s", exc)
            return pd.DataFrame(columns=list(_RAW_COLUMNS))
        if frame.empty:
            return frame
        # Window on the YYYYMMDD ``Date`` token (inclusive), honoring FetchRequest.
        start_tok = req.start.strftime("%Y%m%d")
        end_tok = req.end.strftime("%Y%m%d")
        windowed = frame[(frame["Date"] >= start_tok) & (frame["Date"] <= end_tok)]
        return windowed.reset_index(drop=True)

    def datasets(self) -> tuple[str, ...]:
        return ("fama_french_factors",)
