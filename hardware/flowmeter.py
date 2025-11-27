"""Hardware adapter for flowmeter devices.

This module selects between a simulator (`flowmeter.MassFlowMeter`) and a
serial-backed implementation using an external library such as
`sensirion_uart_sfx6xxx` when configured via the .env keys:

- USE_SERIAL_MASSFLOW: 'true' to request real hardware (no silent fallback)
- MASSFLOW_PYLIB_MODULE: python module name for the vendor library
- MASSFLOW_PYLIB_CLASS: class name to instantiate from that module
- MASSFLOW_SERIAL_PORT, MASSFLOW_SERIAL_BAUD: connection params

If `USE_SERIAL_MASSFLOW` is true but the requested library cannot be
imported/instantiated, this module will raise a RuntimeError so the caller
knows the connection failed (no silent fallback to simulation).
"""
from config import get_str, get_float
import importlib
import logging

from flowmeter import MassFlowMeter


def _str_to_bool(s: str) -> bool:
    return str(s).lower() in ('1', 'true', 'yes', 'on')


class SerialMassFlowAdapter:
    """Adapter that wraps a vendor Python library device instance and
    provides the `get_flow()` and optional `step()` interface expected by
    controllers.

    This wrapper attempts to call common API names exposed by vendor libs,
    such as `get_mass_flow`, `read_mass_flow`, or `read`.
    """

    def __init__(self, lib_module, lib_device):
        self.lib_module = lib_module
        self.lib_device = lib_device

    def step(self, dt=None):
        # real device: nothing to advance; simulator libs may provide polling
        try:
            if hasattr(self.lib_device, 'poll'):
                try:
                    self.lib_device.poll()
                except TypeError:
                    # maybe poll takes a timeout
                    try:
                        self.lib_device.poll(dt)
                    except Exception:
                        pass
        except Exception:
            logging.exception('Error in SerialMassFlowAdapter.step')

    def get_flow(self) -> float:
        # try several common method names
        candidates = ['get_mass_flow', 'read_mass_flow', 'read_flow', 'get_flow', 'read']
        for name in candidates:
            fn = getattr(self.lib_device, name, None)
            if callable(fn):
                try:
                    val = fn()
                    # some libs return a tuple (value, unit)
                    if isinstance(val, (list, tuple)) and val:
                        return float(val[0])
                    return float(val)
                except Exception:
                    # try next candidate
                    logging.debug('SerialMassFlowAdapter: method %s exists but raised', name)
                    continue
        # last resort: try reading attributes
        for attr in ('mass_flow', 'flow', 'value'):
            v = getattr(self.lib_device, attr, None)
            if v is not None:
                try:
                    return float(v)
                except Exception:
                    pass
        raise RuntimeError('Serial massflow device does not expose a readable flow API')


# Decide which implementation to expose as `device`
_use_serial = _str_to_bool(get_str('USE_SERIAL_MASSFLOW', 'false'))
port = get_str('MASSFLOW_SERIAL_PORT', None) or None

if _use_serial and port:
    # Use serial adapter only when port is configured
    pass  # will be handled in else block below
else:
    # Use dummy device when serial not requested or no port configured
    from hardware.dummy_devices import DummyFlowmeterDevice
    device = DummyFlowmeterDevice()

if _use_serial and port:
    # attempt to instantiate vendor library
    module_name = get_str('MASSFLOW_PYLIB_MODULE', 'sensirion_uart_sfx6xxx')
    class_name = get_str('MASSFLOW_PYLIB_CLASS', '')
    port = get_str('MASSFLOW_SERIAL_PORT', '/dev/ttyUSB0')
    baud = int(get_str('MASSFLOW_SERIAL_BAUD', '9600') or 9600)
    try:
        lib = importlib.import_module(module_name)
    except Exception as e:
        raise RuntimeError(f"Requested serial massflow library '{module_name}' not found: {e}\nPlease install it or set USE_SERIAL_MASSFLOW=false in .env")

    # try to find a class to instantiate
    lib_device = None
    if class_name:
        cls = getattr(lib, class_name, None)
        if cls is None:
            raise RuntimeError(f"Library '{module_name}' does not expose class '{class_name}'")
        try:
            # try common constructor signatures
            try:
                lib_device = cls(port, baud)
            except TypeError:
                try:
                    lib_device = cls(port)
                except TypeError:
                    lib_device = cls()
        except Exception as e:
            raise RuntimeError(f"Failed to instantiate {class_name} from {module_name}: {e}")
    else:
        # try some common factory points in the module
        # user can set MASSFLOW_PYLIB_CLASS in .env to guide us
        possible = []
        for name in dir(lib):
            if 'sfx' in name.lower() or 'mass' in name.lower() or 'flow' in name.lower():
                possible.append(name)
        # try to instantiate any callable candidate
        inst = None
        for name in possible:
            candidate = getattr(lib, name)
            if callable(candidate):
                try:
                    try:
                        inst = candidate(port, baud)
                    except TypeError:
                        try:
                            inst = candidate(port)
                        except TypeError:
                            inst = candidate()
                    break
                except Exception:
                    continue
        if inst is None:
            raise RuntimeError(f"Could not find an instantiable device class in module '{module_name}'. Please set MASSFLOW_PYLIB_CLASS in .env to the correct class name.")
        lib_device = inst

    # wrap and expose
    device = SerialMassFlowAdapter(lib, lib_device)
