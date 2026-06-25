"""Backward-compatible shim for the renamed CPC controller module.

This file re-exports the public API from `cpc_controller.py` and emits a
DeprecationWarning so callers can migrate to the new name.
"""
import warnings
from cpc_controller import start_cpc, get_concentration, UPDATE_INTERVAL  # re-export

warnings.warn(
    "Module `cpc` is deprecated; import from `cpc_controller` instead.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["start_cpc", "get_concentration", "UPDATE_INTERVAL"]
