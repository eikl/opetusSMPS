"""Hardware adapter for Sensirion SDP8xx differential pressure sensor (I2C).

This module provides an interface for the Sensirion SDP8xx differential pressure sensor
using the official Sensirion python-i2c-sdp library:
https://sensirion.github.io/python-i2c-sdp/

Install with: pip install sensirion-i2c-sdp

Configuration via config.ini [pressure] section:

- use_i2c_pressure: '1' or 'true' to request real I2C hardware
- pressure_i2c_bus: I2C bus number (default 1)
- pressure_i2c_address: I2C address (default 0x25, use 0x26 for alternate address)
- aerosol_flow_calibration: Calibration factor to convert differential pressure to L/min

SDP8xx I2C addresses:
- SDP8xx supports 0x25 (default) and 0x26
- SDP3x supports 0x21, 0x22, and 0x23

Aerosol Flow Calculation:
The differential pressure is multiplied by the calibration factor to get the flow rate:
    flow [L/min] = calibration_factor * differential_pressure [Pa]
The calibration factor should be determined empirically for your specific setup.
"""
from config import get_str, get_int, get_float
import threading
import logging
import time


def _str_to_bool(s: str) -> bool:
    return str(s).lower() in ('1', 'true', 'yes', 'on')


class SDP8xxAdapter:
    """Adapter for Sensirion SDP8xx using python-i2c-sdp library.
    
    Wraps the official Sensirion library and provides a simplified interface.
    
    Interface:
    - connect() -> None: Start continuous measurement mode
    - disconnect() -> None: Stop measurement mode
    - get_pressure() -> float: Returns differential pressure in Pa
    - get_temperature() -> float: Returns temperature in °C
    - get_aerosol_flow() -> float: Returns calculated aerosol flow in L/min
    - step() -> None: Update readings from sensor
    """

    def __init__(self, sdp_device, calibration_factor: float = 1.0):
        """Initialize with an SdpI2cDevice instance from sensirion-i2c-sdp library.
        
        Args:
            sdp_device: SdpI2cDevice instance from sensirion-i2c-sdp
            calibration_factor: Factor to multiply pressure (Pa) to get flow (L/min)
        """
        self._lock = threading.Lock()
        self.device = sdp_device
        self._calibration_factor = calibration_factor
        self._last_pressure = 0.0
        self._last_temperature = 0.0
        self._connected = False

    def connect(self):
        """Start continuous measurement mode."""
        with self._lock:
            try:
                # Stop any existing measurement
                try:
                    self.device.stop_continuous_measurement()
                except Exception:
                    pass
                
                # Start continuous measurement with mass flow temperature compensation
                self.device.start_continuous_measurement_with_mass_flow_t_comp()
                
                # Wait for first measurement to be ready
                time.sleep(0.010)
                
                # Read once to verify it's working
                differential_pressure, temperature = self.device.read_measurement()
                self._last_pressure = float(differential_pressure.pascal)
                self._last_temperature = float(temperature.degrees_celsius)
                self._connected = True
                print(f"SDP8xx: Connected, initial pressure={self._last_pressure:.2f} Pa, temp={self._last_temperature:.1f} °C")
            except Exception as e:
                self._connected = False
                raise RuntimeError(f"Failed to start SDP8xx measurement: {e}")

    def disconnect(self):
        """Stop continuous measurement mode."""
        with self._lock:
            try:
                self.device.stop_continuous_measurement()
            except Exception:
                pass
            self._connected = False

    def step(self):
        """Update readings from the sensor.
        
        Call this periodically to refresh the cached pressure and temperature values.
        """
        with self._lock:
            if not self._connected:
                return
            try:
                differential_pressure, temperature = self.device.read_measurement()
                self._last_pressure = float(differential_pressure.pascal)
                self._last_temperature = float(temperature.degrees_celsius)
            except Exception as e:
                logging.exception('Error reading measurement from SDP8xx')

    def get_pressure(self) -> float:
        """Read differential pressure in Pascals.
        
        Returns the cached differential pressure value from the SDP8xx.
        Call step() first to update the reading.
        """
        with self._lock:
            return float(self._last_pressure)

    def get_temperature(self) -> float:
        """Read temperature in degrees Celsius.
        
        Returns the cached temperature value from the SDP8xx.
        Call step() first to update the reading.
        """
        with self._lock:
            return float(self._last_temperature)

    def get_aerosol_flow(self) -> float:
        """Calculate aerosol flow rate from differential pressure.
        
        Uses the calibration factor to convert differential pressure to flow rate.
        Formula: flow [L/min] = calibration_factor * pressure [Pa]
        
        Returns:
            Flow rate in L/min
        """
        with self._lock:
            return float(self._calibration_factor * self._last_pressure)
    
    def is_connected(self) -> bool:
        """Check if sensor is connected."""
        with self._lock:
            return self._connected

    def set_calibration_factor(self, factor: float):
        """Set the calibration factor for flow calculation.
        
        Args:
            factor: New calibration factor (L/min per Pa)
        """
        with self._lock:
            self._calibration_factor = factor

    def get_calibration_factor(self) -> float:
        """Get the current calibration factor."""
        with self._lock:
            return self._calibration_factor


def calibrate_for_1lpm(dev) -> float:
    """Calculate calibration factor assuming current flow is 1 L/min.
    
    The user should set the actual flow to exactly 1 L/min using an external
    reference, then call this function to compute the calibration factor.
    
    Formula: calibration_factor = 1.0 / pressure [Pa]
    So that: flow = calibration_factor * pressure = 1 L/min
    
    Args:
        dev: The pressure meter device (SDP8xxAdapter or compatible)
    
    Returns:
        The new calibration factor
    
    Raises:
        ValueError: If pressure reading is zero or too small
    """
    dev.step()  # Update reading
    pressure = dev.get_pressure()
    if abs(pressure) < 0.001:
        raise ValueError("Pressure reading is too small for calibration (near zero)")
    
    # calibration_factor = target_flow / pressure = 1.0 / pressure
    factor = 1.0 / pressure
    return factor


def save_calibration_to_config(factor: float, config_path: str = None):
    """Save the calibration factor to config.ini.
    
    Args:
        factor: The calibration factor to save
        config_path: Path to config.ini (defaults to config.ini next to this package)
    """
    import configparser
    from pathlib import Path
    
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / 'config.ini'
    else:
        config_path = Path(config_path)
    
    cp = configparser.ConfigParser()
    cp.read(str(config_path))
    
    if not cp.has_section('pressure'):
        cp.add_section('pressure')
    
    cp.set('pressure', 'aerosol_flow_calibration', str(factor))
    
    with open(config_path, 'w') as f:
        cp.write(f)
    
    # Refresh the in-memory config cache
    from config import reload
    reload()
    
    print(f"Saved aerosol_flow_calibration = {factor} to {config_path}")


# Decide which implementation to expose as `device`
_use_i2c = _str_to_bool(get_str('USE_I2C_PRESSURE', 'false'))
_calibration_factor = float(get_float('AEROSOL_FLOW_CALIBRATION', 1.0))

if _use_i2c:
    # Get I2C configuration for SDP8xx
    bus_number = get_int('PRESSURE_I2C_BUS', 1)
    # SDP8xx supports 0x25 (default) and 0x26
    i2c_address = get_int('PRESSURE_I2C_ADDRESS', 0x25)
    
    try:
        # Import Sensirion I2C SDP library
        from sensirion_i2c_driver import LinuxI2cTransceiver, I2cConnection
        from sensirion_i2c_sdp import SdpI2cDevice
        
        # Create I2C connection using Linux I2C device
        # The device path is /dev/i2c-<bus_number>
        i2c_transceiver = LinuxI2cTransceiver(f'/dev/i2c-{bus_number}')
        i2c_connection = I2cConnection(i2c_transceiver)
        
        # Create SDP device instance with specified I2C address
        sdp_device = SdpI2cDevice(i2c_connection, slave_address=i2c_address)
        
        # Wrap in our adapter with calibration factor
        device = SDP8xxAdapter(sdp_device, calibration_factor=_calibration_factor)
        
        # Attempt connection and start measurements
        try:
            device.connect()
            print(f"SDP8xx: Aerosol flow calibration factor = {_calibration_factor}")
        except Exception as e:
            raise RuntimeError(f"Failed to connect to SDP8xx sensor: {e}")
            
    except ImportError as e:
        raise RuntimeError(
            f"Sensirion I2C SDP library not found: {e}\n"
            "Install with: pip install sensirion-i2c-sdp"
        )
    except Exception as e:
        raise RuntimeError(f"Failed to initialize SDP8xx: {e}")
else:
    # Use dummy device when I2C not requested
    from hardware.dummy_devices import DummyPressureMeterDevice
    
    class DummyPressureMeterWithFlow(DummyPressureMeterDevice):
        """Extended dummy device with aerosol flow calculation."""
        
        def __init__(self, calibration_factor: float = 1.0):
            super().__init__()
            self._calibration_factor = calibration_factor
        
        def step(self):
            """No-op for dummy device."""
            pass
        
        def get_aerosol_flow(self) -> float:
            """Return calculated flow from dummy pressure."""
            with self._lock:
                return self._calibration_factor * self._pressure
        
        def is_connected(self) -> bool:
            """Always returns True for dummy device."""
            return True
        
        def set_calibration_factor(self, factor: float):
            """Set the calibration factor for flow calculation."""
            with self._lock:
                self._calibration_factor = factor
        
        def get_calibration_factor(self) -> float:
            """Get the current calibration factor."""
            with self._lock:
                return self._calibration_factor
    
    device = DummyPressureMeterWithFlow(calibration_factor=_calibration_factor)
    device.connect()
    print(f"SDP8xx: Using dummy device (calibration factor = {_calibration_factor})")
