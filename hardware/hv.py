"""HV hardware adapter.

Provides `device` which is either a serial-backed adapter or a simulator
implementation under `hardware.sim.hv_sim`. Selection is controlled by
the environment variable `USE_SERIAL_HV` (same convention as other modules).
"""
from config import get_str, get_float

# Read USE_SERIAL_HV from .env (via config) so .env controls serial selection
_use_serial = str(get_str('USE_SERIAL_HV', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
port = get_str('HV_SERIAL_PORT', None) or None

if _use_serial and port:
    # instantiate serial adapter class with configured port/baud
    try:
        from hardware.serial_hv import SerialHVDevice
        baud = int(get_float('HV_SERIAL_BAUD', 9600))
        device = SerialHVDevice(port=port, baud=baud)
        # Don't connect at startup - will connect on-demand when sending commands
        print(f'HV serial device configured for {port} at {baud} baud')
    except Exception:
        # re-raise as import-time error so the caller knows serial mode failed
        raise
else:
    # Use dummy device when no serial port configured
    from hardware.dummy_devices import DummyHVDevice
    device = DummyHVDevice()
    print('using dummy hv device')
    device.connect()


def reconnect():
    """Re-read config and reconnect the HV device.

    Replaces the module-level ``device`` with a freshly configured instance.
    """
    global device
    try:
        device.disconnect()
    except Exception:
        pass

    use_serial = str(get_str('USE_SERIAL_HV', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
    port = get_str('HV_SERIAL_PORT', None) or None

    if use_serial and port:
        from hardware.serial_hv import SerialHVDevice
        baud = int(get_float('HV_SERIAL_BAUD', 9600))
        device = SerialHVDevice(port=port, baud=baud)
        print(f'HV serial device reconfigured for {port} at {baud} baud')
    else:
        from hardware.dummy_devices import DummyHVDevice
        device = DummyHVDevice()
        device.connect()
