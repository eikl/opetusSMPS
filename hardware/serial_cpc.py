"""Serial CPC device adapter boilerplate.

Provides `SerialCPCDevice` which can run in `simulate=True` mode for development
or `simulate=False` to attempt to use a real serial-connected CPC.

API (instance methods):
 - connect() -> None
 - disconnect() -> None
 - get_concentration() -> float

Replace `_format_query` and `_parse_conc_response` with device-specific logic
when integrating a real CPC.
"""
from __future__ import annotations

import threading
import time
from typing import Optional
from config import get_float


class SerialCPCDevice:
    def __init__(self, port: Optional[str] = None, baud: int = 9600, sample_interval: float = None):
        self.port = port
        self.baud = int(baud)
        self._lock = threading.Lock()
        self._conc = 0.0
        self._connected = False
        self._serial = None
        # sampling interval (use config default if not provided)
        self.sample_interval = float(sample_interval if sample_interval is not None else get_float('CPC_SAMPLE_INTERVAL', 1.0))
        self._last_sample_time = 0.0
        self._last_value = float(self._conc)

    def connect(self) -> None:
        with self._lock:
            if self._connected:
                return
            try:
                import serial  # type: ignore
                self._serial = serial.Serial(
                    self.port,
                    self.baud,
                    timeout=0.5,
                    parity=serial.PARITY_EVEN,
                    bytesize=serial.SEVENBITS,
                    stopbits=serial.STOPBITS_ONE,
                )
                self._connected = True
                print(f"SerialCPCDevice: connected to {self.port} at {self.baud} baud")
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
        """Format a query to request a concentration measurement from the device.

        Replace with device-specific command if needed.
        Example placeholder: b'READ?\n'
        """
        return b"RD\r"

    def _parse_conc_response(self, data: bytes) -> Optional[float]:
        """Parse a response from the device and return concentration as float.

        Replace with device-specific parsing. Example expects b'C:12.34\n'.
        """
        try:
            s = data.decode('ascii').strip()
            #print('decoded')
            #print(s)
        except Exception as e:
            print('decode error', e)
        return float(s)

    def get_concentration(self) -> float:
        with self._lock:
            now = time.time()
            if now - self._last_sample_time < self.sample_interval:
                return float(self._last_value)

            if not self._connected:
                raise RuntimeError("SerialCPCDevice: not connected")
            try:
                # flush any stale data before sending query
                self._serial.reset_input_buffer()  # type: ignore
                # send a query and parse response
                self._serial.write(self._format_query())  # type: ignore
                data = self._serial.readline()  # type: ignore
                v = self._parse_conc_response(data)
                if v is not None:
                    self._conc = v
                val = float(self._conc)
            except Exception:
                val = float(self._conc)

            self._last_value = val
            self._last_sample_time = now
            return float(val)


# default device instance: instantiate real serial-backed device by default
device = SerialCPCDevice()
