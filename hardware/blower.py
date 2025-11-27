"""Blower hardware adapter.

Provides a dummy blower device for development. Replace with real
hardware adapter when available.
"""
from hardware.dummy_devices import DummyBlowerDevice

device = DummyBlowerDevice()
