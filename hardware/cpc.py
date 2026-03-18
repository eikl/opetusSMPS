"""CPC hardware adapter.

Selects the device implementation to use:
 - serial adapter: `hardware.serial_cpc.device` when USE_SERIAL_CPC=1
 - simulated adapter: `hardware.sim.cpc_sim.CPCDeviceSim` otherwise

This keeps simulated code in `hardware.sim` so production adapters live
separately from simulation helpers.
"""
from config import get_str, get_float

# Use the serial adapter when the .env flag USE_SERIAL_CPC is set to true
_use_serial = str(get_str('USE_SERIAL_CPC', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
port = get_str('CPC_SERIAL_PORT', None) or None

if _use_serial and port:
    try:
        from hardware.serial_cpc import SerialCPCDevice
        cpc_type = get_str('CPC_TYPE', 'TSI3010')
        baud_override = get_str('CPC_SERIAL_BAUD', None)
        baud = int(baud_override) if baud_override else None
        device = SerialCPCDevice(port=port, baud=baud, sample_interval=get_float('CPC_SAMPLE_INTERVAL', 1.0), cpc_type=cpc_type)
        try:
            device.connect()
        except Exception as e:
            raise RuntimeError(f"Failed to connect to serial CPC device on port {port}: {e}")
    except Exception:
        # re-raise so import fails when serial is requested but not available
        raise
else:
    # Use dummy device when no serial port configured
    from hardware.dummy_devices import DummyCPCDevice
    device = DummyCPCDevice(sample_interval=get_float('CPC_SAMPLE_INTERVAL', 1.0))
    device.connect()


def reconnect():
    """Re-read config and reconnect the CPC device.

    Replaces the module-level ``device`` with a freshly configured instance.
    """
    global device
    # disconnect old device
    try:
        device.disconnect()
    except Exception:
        pass

    use_serial = str(get_str('USE_SERIAL_CPC', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
    port = get_str('CPC_SERIAL_PORT', None) or None

    if use_serial and port:
        from hardware.serial_cpc import SerialCPCDevice
        cpc_type = get_str('CPC_TYPE', 'TSI3010')
        baud_override = get_str('CPC_SERIAL_BAUD', None)
        baud = int(baud_override) if baud_override else None
        device = SerialCPCDevice(port=port, baud=baud,
                                 sample_interval=get_float('CPC_SAMPLE_INTERVAL', 1.0),
                                 cpc_type=cpc_type)
        device.connect()
    else:
        from hardware.dummy_devices import DummyCPCDevice
        device = DummyCPCDevice(sample_interval=get_float('CPC_SAMPLE_INTERVAL', 1.0))
        device.connect()
