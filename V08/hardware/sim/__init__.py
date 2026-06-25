"""Simulator package removed.

This project no longer includes simulator implementations. Importing
the `hardware.sim` package will raise ImportError to make the removal
explicit and prevent silent fallbacks to simulated devices.
"""

raise ImportError("hardware.sim package removed: simulation code is not available")
