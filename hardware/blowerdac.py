"""Blower hardware adapter.

Provides a GP8403 DAC-based blower controller with fallback to dummy device
if the hardware fails to connect.
"""
from hardware.dummy_devices import DummyBlowerDevice
import threading


class AnalogBlowerDevice:
    """GP8403 DAC-based blower controller.
    
    Interface:
    - set_voltage(voltage: float) -> None
    - get_parameter() -> float
    """
    
    def __init__(self, i2c_addr: int = 0x5F, bus: int = 1):
        """Initialize the DAC-based blower controller.
        
        Args:
            i2c_addr: I2C address of GP8403 DAC (default 0x5F)
            bus: I2C bus number (default 1)
        """
        self._lock = threading.Lock()
        self._voltage = 0.0
        self._i2c_addr = i2c_addr
        self._bus = bus
        self._dac = None
        self._connected = False
    
    def connect(self) -> bool:
        """Connect to the GP8403 DAC.
        
        Returns:
            True if connection successful, False otherwise
        """
        with self._lock:
            try:
                from GP8XXX_IIC import GP8403
                self._dac = GP8403(i2c_addr=self._i2c_addr, bus=self._bus)
                # Test actual I2C communication by setting voltage to 0
                self._dac.set_dac_out_voltage(voltage=0, channel=1)
                self._connected = True
                print(f"BlowerDAC: Connected to GP8403 on bus {self._bus}, address 0x{self._i2c_addr:02X}")
                return True
            except Exception as e:
                print(f"BlowerDAC: Connection failed: {e}")
                self._dac = None
                self._connected = False
                return False

    def set_voltage(self, v: float) -> None:
        """Set voltage on the DAC.
        
        Args:
            v: Voltage to set
        """
        with self._lock:
            if self._dac is not None and self._connected:
                try:
                    self._dac.set_dac_out_voltage(voltage=v, channel=1)
                    self._voltage = v
                    #print(f'Set blower voltage to {v}')
                except Exception as e:
                    print(f"BlowerDAC: Error setting voltage: {e}")
            else:
                self._voltage = v

    def get_parameter(self) -> float:
        """Get the current voltage parameter.
        
        Returns:
            Current voltage setting
        """
        with self._lock:
            return self._voltage
    
    def is_connected(self) -> bool:
        """Check if DAC is connected."""
        with self._lock:
            return self._connected


# -----------------------------------------------------------------------------
# Device initialization with fallback
# -----------------------------------------------------------------------------

def _create_blower_device():
    """Create blower device with fallback to dummy if hardware fails."""
    try:
        blower = AnalogBlowerDevice(i2c_addr=0x5F, bus=1)
        if blower.connect():
            return blower
        else:
            print("BlowerDAC: Falling back to dummy device")
            return DummyBlowerDevice()
    except Exception as e:
        print(f"BlowerDAC: Init failed ({e}), using dummy device")
        return DummyBlowerDevice()


device = _create_blower_device()

    