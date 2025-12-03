"""Flowmeter hardware adapter.

Provides `device` which is either an I2C-backed Sensirion SFM3000 sensor or a dummy
implementation. Selection is controlled by the environment variable `USE_I2C_FLOWMETER`.

Interface:
- step(dt: Optional[float] = None) -> None
- get_flow() -> float
"""
import threading
from typing import Optional

from config import get_str, get_int, get_float


class SFM3000FlowmeterDevice:
    """Sensirion SFM3000 I2C flowmeter device.
    
    Interface:
    - step(dt: Optional[float] = None) -> None
    - get_flow() -> float
    """
    
    def __init__(
        self,
        i2c_bus: int = 1,
        i2c_address: int = 0x40,
        scale_factor: float = 140.0,
        offset: float = 32000.0
    ):
        """Initialize SFM3000 flowmeter.
        
        Args:
            i2c_bus: I2C bus number (default 1 for Raspberry Pi)
            i2c_address: I2C address (default 0x40)
            scale_factor: Flow scale factor (default 140.0 for SFM3000)
            offset: Flow offset (default 32000.0 for SFM3000)
        """
        from hardware.sensirionsfm3000 import (
            SensirionI2CSfm3000,
            SensirionI2CError,
            NackError,
            CrcError
        )
        
        self._lock = threading.Lock()
        self._sensor = SensirionI2CSfm3000()
        self._i2c_bus = i2c_bus
        self._i2c_address = i2c_address
        self._scale_factor = scale_factor
        self._offset = offset
        self._flow = 0.0
        self._connected = False
        self._error_count = 0
        self._max_errors = 5  # Hard reset after this many consecutive errors
        
        # Store exception classes for error handling
        self._SensirionI2CError = SensirionI2CError
        self._NackError = NackError
        self._CrcError = CrcError
    
    def connect(self) -> bool:
        """Connect to the sensor and start measurements.
        
        Returns:
            True if connection successful, False otherwise
        """
        with self._lock:
            try:
                self._sensor.begin(
                    i2c_bus=self._i2c_bus,
                    i2c_address=self._i2c_address
                )
                
                # Try to read serial number to verify communication
                serial = self._sensor.read_serial_number()
                print(f"SFM3000 connected - Serial: {serial} (0x{serial:08X})")
                
                # Try to read scale factor and offset from sensor
                try:
                    sf = self._sensor.read_scale_factor()
                    off = self._sensor.read_offset()
                    if sf != 0:
                        self._scale_factor = float(sf)
                        self._offset = float(off)
                        print(f"SFM3000 using sensor calibration: scale={sf}, offset={off}")
                except Exception:
                    print(f"SFM3000 using default calibration: scale={self._scale_factor}, offset={self._offset}")
                
                # Start continuous measurement
                self._sensor.start_continuous_measurement()
                self._connected = True
                self._error_count = 0
                return True
                
            except Exception as e:
                print(f"SFM3000 connection failed: {e}")
                self._connected = False
                return False
    
    def disconnect(self) -> None:
        """Disconnect from the sensor."""
        with self._lock:
            try:
                self._sensor.close()
            except Exception:
                pass
            self._connected = False
    
    def step(self, dt: Optional[float] = None) -> None:
        """Update flow reading from sensor.
        
        This method reads the latest flow value from the sensor.
        Should be called periodically (e.g., every 100ms).
        
        Args:
            dt: Time step (unused, for interface compatibility)
        """
        with self._lock:
            if not self._connected:
                return
            
            try:
                self._flow = self._sensor.read_measurement(
                    scaling_factor=self._scale_factor,
                    offset=self._offset
                )
                self._error_count = 0  # Reset error count on success
                
            except self._NackError:
                # No valid measurement ready yet, keep last value
                pass
                
            except (self._CrcError, self._SensirionI2CError) as e:
                self._error_count += 1
                print(f"SFM3000 read error ({self._error_count}/{self._max_errors}): {e}")
                
                if self._error_count >= self._max_errors:
                    print("SFM3000: Too many errors, attempting reconnect...")
                    self._connected = False
                    # Try to reconnect
                    try:
                        self._sensor.soft_reset()
                        self._sensor.start_continuous_measurement()
                        self._connected = True
                        self._error_count = 0
                    except Exception as e2:
                        print(f"SFM3000 reconnect failed: {e2}")
                        
            except Exception as e:
                self._error_count += 1
                print(f"SFM3000 unexpected error: {e}")
    
    def get_flow(self) -> float:
        """Return the last measured flow value in slm (standard liters per minute).
        
        Returns:
            Flow rate in slm
        """
        with self._lock:
            return float(self._flow)
    
    def is_connected(self) -> bool:
        """Check if sensor is connected."""
        with self._lock:
            return self._connected


class DummyFlowmeterDevice:
    """Dummy flowmeter device that returns a constant flow value.
    
    Interface:
    - step(dt: Optional[float] = None) -> None
    - get_flow() -> float
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._flow = 1.5  # constant placeholder flow in L/min
    
    def connect(self) -> bool:
        """No-op for dummy device."""
        return True
    
    def disconnect(self) -> None:
        """No-op for dummy device."""
        pass
    
    def step(self, dt: Optional[float] = None) -> None:
        """No-op for dummy device."""
        pass
    
    def get_flow(self) -> float:
        """Return a constant flow value."""
        with self._lock:
            return float(self._flow)
    
    def is_connected(self) -> bool:
        """Always returns True for dummy device."""
        return True


# -----------------------------------------------------------------------------
# Device selection based on configuration
# -----------------------------------------------------------------------------

def _str_to_bool(s: Optional[str]) -> bool:
    """Convert string to boolean."""
    if s is None:
        return False
    return str(s).strip().lower() in ('1', 'true', 'yes', 'on')


_use_i2c = _str_to_bool(get_str('USE_I2C_FLOWMETER', 'false'))
_i2c_bus = get_int('FLOWMETER_I2C_BUS', 1)
_i2c_address = int(get_str('FLOWMETER_I2C_ADDRESS', '0x40'), 0)  # Support hex
_scale_factor = get_float('FLOWMETER_SCALE_FACTOR', 140.0)
_offset = get_float('FLOWMETER_OFFSET', 32000.0)

if _use_i2c:
    try:
        device = SFM3000FlowmeterDevice(
            i2c_bus=_i2c_bus,
            i2c_address=_i2c_address,
            scale_factor=_scale_factor,
            offset=_offset
        )
        if device.connect():
            print(f"Flowmeter: SFM3000 on I2C bus {_i2c_bus}, address 0x{_i2c_address:02X}")
        else:
            print("Flowmeter: SFM3000 connection failed, falling back to dummy")
            device = DummyFlowmeterDevice()
            device.connect()
    except Exception as e:
        print(f"Flowmeter: SFM3000 init failed ({e}), using dummy")
        device = DummyFlowmeterDevice()
        device.connect()
else:
    device = DummyFlowmeterDevice()
    device.connect()
    print("Flowmeter: Using dummy device")
