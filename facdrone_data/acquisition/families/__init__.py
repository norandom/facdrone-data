# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The family-acquisition abstraction.

This package owns the acquisition orchestration of one provider data family:
the typed ports (``FamilyPort`` and the ``MergeStrategy``/``IncrementalStrategy``/
``Normalizer`` Protocols), the ``FamilyRegistry`` enumerated against
``research/data_acquisition_inventory.md``, the incremental cursors, the
``FamilyRefresher``, the per-family normalizers, and the ``ProviderCredentials``
loader.

It depends only on the domain ports and the pipelines/data primitives — never on
``shells`` (the live transports). Concrete provider transports live in
``shells/providers`` and meet this package only at the CLI data composition root.
"""

from __future__ import annotations
