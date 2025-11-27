"""Dummy device implementations for development and testing.

These classes provide the same interfaces as real hardware but return
placeholder/constant values. They allow the application to run without
hardware and without requiring serial configuration.

Dummy devices are NOT simulators - they don't model dynamic behavior.
They simply provide static responses to allow testing UI and control logic.
"""
import threading
import time
from typing import Optional


class DummyHVDevice:
    """Dummy high-voltage device that accepts voltage commands and returns
    the last set value.
    
    Interface matches SerialHVDevice:
    - connect() -> None
    - disconnect() -> None
    - set_voltage(voltage: float) -> None
    - get_voltage() -> float
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._voltage = 0.0
        self._connected = False
    
    def connect(self) -> None:
        """No-op for dummy device."""
        with self._lock:
            self._connected = True
    
    def disconnect(self) -> None:
        """No-op for dummy device."""
        with self._lock:
            self._connected = False
    
    def set_voltage(self, voltage: float) -> None:
        """Store the requested voltage."""
        with self._lock:
            self._voltage = float(voltage)
    
    def get_voltage(self) -> float:
        """Return the last set voltage."""
        with self._lock:
            return float(self._voltage)


class DummyCPCDevice:
    """Dummy CPC device that returns a constant concentration value.
    
    Interface matches SerialCPCDevice:
    - connect() -> None
    - disconnect() -> None
    - get_concentration() -> float
    """
    
    def __init__(self, sample_interval: float = 1.0):
        self._lock = threading.Lock()
        self._conc = 1000.0  # constant placeholder concentration
        self._connected = False
        self.sample_interval = float(sample_interval)
        self._last_sample_time = 0.0
    
    def connect(self) -> None:
        """No-op for dummy device."""
        with self._lock:
            self._connected = True
    
    def disconnect(self) -> None:
        """No-op for dummy device."""
        with self._lock:
            self._connected = False
    
    def get_concentration(self) -> float:
        """Return a constant concentration value."""
        with self._lock:
            now = time.time()
            # respect sample interval for consistency
            if now - self._last_sample_time >= self.sample_interval:
                self._last_sample_time = now
            return float(self._conc)


class DummyBlowerDevice:
    """Dummy blower device that accepts voltage and returns a proportional parameter.
    
    Interface:
    - set_voltage(voltage: float) -> None
    - get_parameter() -> float
    """
    
    def __init__(self, gain: float = 2.0):
        self._lock = threading.Lock()
        self._voltage = 0.0
        self._gain = float(gain)
    
    def set_voltage(self, voltage: float) -> None:
        """Store the requested voltage."""
        with self._lock:
            self._voltage = float(voltage)
    
    def get_parameter(self) -> float:
        """Return a simple proportional response (gain * voltage)."""
        with self._lock:
            return float(self._gain * self._voltage)


class DummyFlowmeterDevice:
    """Dummy flowmeter device that returns a constant flow value.
    
    Interface:
    - step(dt: Optional[float] = None) -> None
    - get_flow() -> float
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._flow = 1.5  # constant placeholder flow in L/min
    
    def step(self, dt: Optional[float] = None) -> None:
        """No-op for dummy device."""
        pass
    
    def get_flow(self) -> float:
        """Return a constant flow value."""
        with self._lock:
            return float(self._flow)


class DummyPressureMeterDevice:
    """Dummy SDP816 differential pressure meter device that returns constant values.
    
    Interface matches SDP816Adapter:
    - connect() -> None
    - disconnect() -> None
    - get_pressure() -> float (returns pressure in Pascals)
    - get_temperature() -> float (returns temperature in °C)
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._pressure = 50.0  # constant placeholder pressure in Pa
        self._temperature = 22.0  # constant placeholder temperature in °C
        self._connected = False
    
    def connect(self) -> None:
        """No-op for dummy device."""
        with self._lock:
            self._connected = True
    
    def disconnect(self) -> None:
        """No-op for dummy device."""
        with self._lock:
            self._connected = False
    
    def get_pressure(self) -> float:
        """Return a constant pressure value in Pascals."""
        with self._lock:
            return float(self._pressure)
    
    def get_temperature(self) -> float:
        """Return a constant temperature value in °C."""
        with self._lock:
            return float(self._temperature)
