"""Hardware abstraction package.

Provides adapters so the rest of the code can use `hardware.hv.device` and
`hardware.blower.device`. The default adapters wrap the existing simulators.
"""

__all__ = ["hv", "blower", "cpc"]
