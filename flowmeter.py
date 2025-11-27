"""Flowmeter placeholders used as inputs to the blower PID controller.

This module provides small placeholder interfaces for real flowmeter drivers
and intentionally does not contain any simulation logic. Real drivers should
subclass these placeholders and implement the low-level I/O.

Provided classes:
- FlowMeter: minimal base placeholder exposing `get_flow()` and optional
  `step()` (no-op by default).
- MassFlowMeter: placeholder for sensors that return mass flow directly.
- DifferentialPressureMeter: placeholder for differential pressure sensors
  where a driver may provide raw dp; this class exposes `get_flow()` which
  should be implemented by a concrete driver (e.g. converting sqrt(dp)->flow).

These classes purposely do not simulate behavior. If you want a simulator, a
separate module or test fixture should provide that.
"""
from __future__ import annotations

from typing import Any


class FlowMeter:
    """Minimal placeholder interface for a flow meter.

    Concrete driver implementations should subclass this and implement
    `get_flow()` and any low-level I/O. `step()` is provided as a no-op hook
    that drivers may implement if they require periodic polling.
    """

    def step(self, dt: float | None = None) -> None:
        """Optional hook to advance internal driver state. No-op by default."""
        return

    def get_flow(self) -> float:
        """Return the current flow reading (float).

        Drivers must override this method to return a numeric flow value.
        """
        raise NotImplementedError("get_flow() must be implemented by concrete flowmeter drivers")


class MassFlowMeter(FlowMeter):
    """Placeholder for mass-flow sensors.

    Real drivers should implement `get_flow()` to return mass flow in the
    chosen units.
    """

    def get_flow(self) -> float:  # pragma: no cover - placeholder
        raise NotImplementedError("MassFlowMeter driver must implement get_flow()")


class DifferentialPressureMeter(FlowMeter):
    """Placeholder for differential-pressure sensors.

    Drivers may choose to expose raw DP via a `read_dp()` method and convert
    it to flow; concrete subclasses should implement `get_flow()`.
    """

    def get_flow(self) -> float:  # pragma: no cover - placeholder
        raise NotImplementedError("DifferentialPressureMeter driver must implement get_flow()")
