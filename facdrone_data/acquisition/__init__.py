# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Data pipeline (task 11.2): acquire → concat → validate → stamp → project →
merge, as explicit, inspectable Kedro nodes.

``create_pipeline`` is a zero-arg static factory (so ``register_pipelines`` and
``kedro pipeline describe data`` keep working). The injected boundaries —
``acquire_deps`` (provider client + registry), ``panel_engine`` (DB), the target
``*_table``, the ``quality_validator``, and the ``quality_policy`` — are named
inputs ABSENT from the catalog; a composition root writes them onto the live
``DataCatalog`` before the run (sim/live inject their own, tests inject fakes).
The pure scalars arrive via ``params:``. The pit.* view (task 11.1) is read
downstream of the merge, so the data graph terminates at ``data.merge``.

Kedro is the optional ``[kedro]`` extra: it is imported inside :func:`create_pipeline`, so
importing any ``facdrone_data.acquisition`` module (the command line does) never needs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from facdrone_data.acquisition.nodes import (
    acquire_node,
    apply_policy_node,
    concat_frames_node,
    merge_node,
    project_to_table_node,
    stamp_node,
    validate_detect_node,
)

if TYPE_CHECKING:
    from kedro.pipeline import Pipeline


def create_pipeline() -> Pipeline:
    from kedro.pipeline import Pipeline, node

    return Pipeline(
        [
            node(
                acquire_node,
                inputs=[
                    "daily_prices",
                    "acquire_deps",
                    "symbol",
                    "params:dataset",
                    "params:want_range",
                ],
                outputs="acquire_result",
                name="data.acquire",
            ),
            node(
                concat_frames_node,
                inputs=["acquire_result", "params:dataset"],
                outputs="acquired_frame",
                name="data.concat",
            ),
            node(
                validate_detect_node,
                inputs=["acquired_frame", "quality_validator", "symbol", "params:dataset"],
                outputs="quality_report",
                name="data.validate_detect",
            ),
            node(
                apply_policy_node,
                inputs=["acquired_frame", "quality_report", "quality_policy"],
                outputs="validated_frame",
                name="data.apply_policy",
            ),
            node(
                stamp_node,
                inputs=["validated_frame", "params:availability_column", "params:event_column"],
                outputs="stamped_frame",
                name="data.stamp",
            ),
            node(
                project_to_table_node,
                inputs=["stamped_frame", "daily_prices_table"],
                outputs="projected_frame",
                name="data.project",
            ),
            node(
                merge_node,
                inputs=["panel_engine", "daily_prices_table", "projected_frame"],
                outputs="merge_receipt",
                name="data.merge",
            ),
        ]
    )
