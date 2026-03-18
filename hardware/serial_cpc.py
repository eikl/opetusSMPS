"""Serial CPC device adapter.

Supports TSI3010 and TSI3772 condensation particle counters.
The device type is selected via the CPC_TYPE setting in the .env file.

API (instance methods):
 - connect() -> None
 - disconnect() -> None
 - get_concentration() -> float
"""
from __future__ import annotations

import threading
import time
from typing import Optional
from config import get_float, get_str

# Device-specific serial configurations
_DEVICE_CONFIGS = {
    'TSI3010': {
        'default_baud': 9600,
        'parity': 'EVEN',
        'bytesize': 7,
        'stopbits': 1,
        'encoding': 'ascii',
        'read_mode': 'readline',
    },
    'TSI3772': {
        'default_baud': 115200,
        'parity': 'NONE',
        'bytesize': 8,
        'stopbits': 1,
        'encoding': 'utf-8',
        'read_mode': 'read_until_cr',
    },
}


class SerialCPCDevice:
    def __init__(self, port: Optional[str] = None, baud: int = None,
                 sample_interval: float = None, cpc_type: str = None):
        self.port = port
        self.cpc_type = (cpc_type or get_str('CPC_TYPE', 'TSI3010')).upper()
        if self.cpc_type not in _DEVICE_CONFIGS:
            raise ValueError(f"Unknown CPC_TYPE '{self.cpc_type}'. Supported: {list(_DEVICE_CONFIGS)}")
        self._cfg = _DEVICE_CONFIGS[self.cpc_type]
        self.baud = int(baud if baud is not None else self._cfg['default_baud'])
        self._lock = threading.Lock()
        self._conc = 0.0
        self._connected = False
        self._serial = None
        self.sample_interval = float(sample_interval if sample_interval is not None else get_float('CPC_SAMPLE_INTERVAL', 1.0))
        self._last_sample_time = 0.0
        self._last_value = float(self._conc)

    def connect(self) -> None:
        with self._lock:
            if self._connected:
                return
            try:
                import serial  # type: ignore
                parity_map = {
                    'EVEN': serial.PARITY_EVEN,
                    'ODD': serial.PARITY_ODD,
                    'NONE': serial.PARITY_NONE,
                }
                bytesize_map = {
                    7: serial.SEVENBITS,
                    8: serial.EIGHTBITS,
                }
                self._serial = serial.Serial(
                    self.port,
                    self.baud,
                    timeout=1,
                    parity=parity_map[self._cfg['parity']],
                    bytesize=bytesize_map[self._cfg['bytesize']],
                    stopbits=serial.STOPBITS_ONE,
                )
                self._connected = True
                print(f"SerialCPCDevice ({self.cpc_type}): connected to {self.port} at {self.baud} baud")
            except Exception:
                self._connected = False
                raise

    def disconnect(self) -> None:
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
            self._connected = False

    def _format_query(self) -> bytes:
        """Format a query to request a concentration measurement."""
        return b'RD\r'

    def _read_response(self) -> bytes:
        """Read a response from the device using the appropriate method."""
        if self._cfg['read_mode'] == 'read_until_cr':
            return self._serial.read_until(b'\r')  # type: ignore
        return self._serial.readline()  # type: ignore

    def _parse_conc_response(self, data: bytes) -> Optional[float]:
        """Parse a response from the device and return concentration as float."""
        try:
            s = data.decode(self._cfg['encoding']).strip()
        except Exception as e:
            print('decode error', e)
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def get_concentration(self) -> float:
        with self._lock:
            now = time.time()
            if now - self._last_sample_time < self.sample_interval:
                return float(self._last_value)

            if not self._connected:
                raise RuntimeError("SerialCPCDevice: not connected")
            try:
                self._serial.reset_input_buffer()  # type: ignore
                self._serial.write(self._format_query())  # type: ignore
                data = self._read_response()
                print(data)
                v = self._parse_conc_response(data)
                print('cpc response', v)
                if v is not None:
                    self._conc = v
                val = float(self._conc)
            except Exception:
                val = float(self._conc)

            self._last_value = val
            self._last_sample_time = now
            return float(val)


# default device instance
device = SerialCPCDevice()
