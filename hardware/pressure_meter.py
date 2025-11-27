"""Hardware adapter for Sensirion SDP816 differential pressure sensor (I2C).

This module provides an interface for the Sensirion SDP816 differential pressure sensor
using the official Sensirion python-i2c-sdp library:
https://github.com/Sensirion/python-i2c-sdp

Install with: pip install sensirion-i2c-sdp

Configuration via .env keys:

- USE_I2C_PRESSURE: 'true' to request real I2C hardware
- PRESSURE_I2C_BUS: I2C bus number (default 1)

If USE_I2C_PRESSURE is true but the I2C device cannot be accessed,
this module will raise a RuntimeError so the caller knows the connection failed.

SDP816 specifications:
- I2C address: 0x25 (default, handled by library)
- Pressure range: ±500 Pa
- Resolution: 16-bit (1/120 Pa)
- Temperature compensation: Built-in
"""
from config import get_str, get_int
import logging


def _str_to_bool(s: str) -> bool:
    return str(s).lower() in ('1', 'true', 'yes', 'on')


class SDP816Adapter:
    """Adapter for Sensirion SDP816 using python-i2c-sdp library.
    
    Wraps the official Sensirion library and provides a simplified interface.
    
    Interface:
    - get_pressure() -> float: Returns differential pressure in Pa
    - get_temperature() -> float: Returns temperature in °C
    """

    def __init__(self, sdp_device):
        """Initialize with an SDP8xx device instance from sensirion-i2c-sdp library."""
        self.device = sdp_device
        self._last_measurement = None

    def connect(self):
        """Start continuous measurement mode."""
        try:
            # Stop any existing measurement
            try:
                self.device.stop_continuous_measurement()
            except Exception:
                pass
            
            # Start continuous measurement with mass flow temperature compensation
            # averaging=False for fastest response
            self.device.start_continuous_measurement_with_mass_flow_t_comp(averaging=False)
            
            # Read once to verify it's working
            import time
            time.sleep(0.010)  # Wait for first measurement
            self.device.read_measurement()
        except Exception as e:
            raise RuntimeError(f"Failed to start SDP816 measurement: {e}")

    def disconnect(self):
        """Stop continuous measurement mode."""
        try:
            self.device.stop_continuous_measurement()
        except Exception:
            pass

    def _read_measurement(self):
        """Internal method to read and cache the latest measurement."""
        try:
            self._last_measurement = self.device.read_measurement()
        except Exception as e:
            logging.exception('Error reading measurement from SDP816')
            raise RuntimeError(f'Failed to read measurement: {e}')

    def get_pressure(self) -> float:
        """Read differential pressure in Pascals.
        
        Returns the differential pressure value from the SDP816.
        The library returns pressure already converted to Pa.
        """
        try:
            self._read_measurement()
            # The library's read_measurement() returns a tuple-like object
            # with differential_pressure attribute
            return float(self._last_measurement.differential_pressure)
        except Exception as e:
            logging.exception('Error reading pressure from SDP816')
            raise RuntimeError(f'Failed to read pressure: {e}')

    def get_temperature(self) -> float:
        """Read temperature in degrees Celsius.
        
        Returns the temperature value from the SDP816.
        The library returns temperature already converted to °C.
        """
        try:
            self._read_measurement()
            # The library's read_measurement() returns a tuple-like object
            # with temperature attribute
            return float(self._last_measurement.temperature)
        except Exception as e:
            logging.exception('Error reading temperature from SDP816')
            raise RuntimeError(f'Failed to read temperature: {e}')


# Decide which implementation to expose as `device`
_use_i2c = _str_to_bool(get_str('USE_I2C_PRESSURE', 'false'))

if _use_i2c:
    # Get I2C configuration for SDP816
    bus_number = get_int('PRESSURE_I2C_BUS', 1)
    
    try:
        # Import Sensirion I2C SDP library
        from sensirion_i2c_driver import LinuxI2cTransceiver, I2cConnection
        from sensirion_i2c_sdp import Sdp8xxI2cDevice
        
        # Create I2C connection using Linux I2C device
        # The device path is /dev/i2c-<bus_number>
        i2c_transceiver = LinuxI2cTransceiver(f'/dev/i2c-{bus_number}')
        i2c_connection = I2cConnection(i2c_transceiver)
        
        # Create SDP8xx device instance (library handles default address 0x25)
        sdp_device = Sdp8xxI2cDevice(i2c_connection)
        
        # Wrap in our adapter
        device = SDP816Adapter(sdp_device)
        
        # Attempt connection and start measurements
        try:
            device.connect()
        except Exception as e:
            raise RuntimeError(f"Failed to connect to SDP816 sensor: {e}")
            
    except ImportError as e:
        raise RuntimeError(
            f"Sensirion I2C SDP library not found: {e}\n"
            "Install with: pip install sensirion-i2c-sdp"
        )
    except Exception as e:
        raise RuntimeError(f"Failed to initialize SDP816: {e}")
else:
    # Use dummy device when I2C not requested
    from hardware.dummy_devices import DummyPressureMeterDevice
    device = DummyPressureMeterDevice()
