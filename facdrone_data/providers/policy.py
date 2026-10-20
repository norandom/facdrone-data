# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The retrieval policy every provider adapter shares.

Five adapters bound the same 30-second HTTP timeout and two bound the same
exchange timezone. Each was one choice copied into a module rather than
shared: raising the timeout for slow sources meant finding all five. An adapter
whose source needs its own value still declares it (the AQR workbook download
keeps its 120 seconds), so a deliberate difference stays visible next to the
shared default.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

# The default HTTP read timeout for a provider request, in seconds.
TIMEOUT_SECONDS = 30

# The exchange timezone US index sources publish and stamp in.
NEW_YORK = ZoneInfo("America/New_York")

__all__ = ["NEW_YORK", "TIMEOUT_SECONDS"]
