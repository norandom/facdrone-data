# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The shared vocabulary below the kernel and the data layer (facdrone-data-manager 1.1).

One definition per type: the kernel and the data layer import these names, and neither
redefines, subclasses or re-exports them. The package imports only the standard library
(pandas under ``TYPE_CHECKING`` for the port signatures).
"""
