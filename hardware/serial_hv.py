"""Serial HV device adapter boilerplate.

This module provides `SerialHVDevice`, a small class that abstracts control of an
HV power supply over a serial port. It supports a `simulate=True` mode so you
can develop without hardware, and a `simulate=False` mode that will attempt to
use `pyserial` to talk to a real device.

API (instance methods):
 - connect() -> None
 - disconnect() -> None
 - set_voltage(voltage: float) -> None
 - get_voltage() -> float

The exact serial command format depends on your HV supply. Replace the
`_format_set_command` and `_parse_voltage_response` methods with device-specific
logic when integrating real hardware.
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class SerialHVDevice:
    def __init__(self, port: Optional[str] = None, baud: int = 9600):
        self.port = port
        self.baud = int(baud)
        self._lock = threading.Lock()
        self._voltage = 0.0
        self._connected = False
        self._serial = None

    def connect(self) -> None:
        """Open serial port if not simulating. Safe to call multiple times."""
        with self._lock:
            if self._connected:
                return
            try:
                import serial  # type: ignore

                self._serial = serial.Serial(
                    self.port, 
                    self.baud, 
                    timeout=1,
                    write_timeout=1,  # Add write timeout
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE
                
                )
                self._connected = True
                print(f"Serial port opened: {self.port}")
            except Exception as e:
                self._connected = False
                raise

    def disable(self) -> None:
        try:
            st = chr(2) + '0106EN=0' + '{:X}'.format(self.chk_sum('0106EN=0')) + chr(10)
            self.write_cmd(st)
        except Exception:
            raise Exception("Failed to disable HV output")

    def write_cmd(self, cmd):
        """Open serial port, send *cmd*, read response, close port.

        Returns the raw response bytes (from ``readline``).
        """
        import serial  # type: ignore
        ser = None
        try:
            ser = serial.Serial(self.port, self.baud, timeout=1, write_timeout=1)
            cmd_e = cmd.encode()
            ser.write(cmd_e)
            print('write')
            print(cmd_e)
            ser.flush()
            res = ser.readline()
            print('response')
            print(res)
            ser.flush()
            return res
        finally:
            if ser is not None:
                time.sleep(0.1)
                try:
                    ser.close()
                except Exception as e:
                    print(f"Error closing serial port: {e}")

    def chk_sum(self, a):
        sum_c=0
        for j in a:
            #print(hex(ord(j)))
            sum_c+=ord(j)
            
        sum_c=0x200-sum_c
        sum_c=sum_c | 0x40
        sum_c=sum_c & 0x7F
        return sum_c

    def _format_set_command(self, voltage: float) -> bytes:
        #M0? 
        #    st=chr(2)+'0106V1='+"{:07.1f}".format(voltage)+'{:X}'.format(chk_sum('0106V1='+"{:07.1f}".format(voltage)))+chr(10)
        cmd = chr(2) + '0106V1=' + "{:07.1f}".format(voltage) + '{:X}'.format(self.chk_sum('0106V1=' + "{:07.1f}".format(voltage))) + chr(10)
        #cmd = chr(2) + '0106CF=1' + "{:07.1f}".format(voltage) + '{:X}'.format(self.chk_sum('0106CF=1' + "{:07.1f}".format(voltage))) + chr(10)
        #cmd = chr(2) + '0106M0?' + "{:07.1f}".format(voltage) + '{:X}'.format(self.chk_sum('0106M0?' + "{:07.1f}".format(voltage))) + chr(10)

        print('voltage set cmd')
        return cmd


    # Status register bit definitions
    STATUS_BITS = {
        0: 'Enabled',
        1: 'Fault',
        2: 'Over voltage',
        3: 'Over current',
        4: 'Over temperature',
        5: 'Supply rail out of range',
        6: 'HW enable',
        7: 'SW enable',
    }

    def get_status(self):
        """Query the status register and return a parsed dict.

        Returns
        -------
        dict  with keys:
            'raw'   – the raw hex string (e.g. '00C1')
            'value' – integer value of the register
            'bits'  – dict  {bit_name: bool, …}  for each defined bit
        """
        try:
            st = chr(2) + '0106SR?' + '{:X}'.format(self.chk_sum('0106SR?')) + chr(10)
            res = self.write_cmd(st)
            return self._parse_status(res)
        except Exception as e:
            print(f'get_status error: {e}')
            return None

    def _parse_status(self, raw_response):
        """Parse a Spellman SR? response into a status dict.

        The response format is ``SR=XXXX`` where XXXX is a 4-character
        ASCII hex number (16-bit).  The least significant byte (lower 8
        bits) is the status byte with the defined flag bits.
        """
        try:
            text = raw_response.decode('ascii', errors='ignore') if isinstance(raw_response, bytes) else str(raw_response)
            idx = text.find('SR=')
            if idx < 0:
                return None
            # Extract first 4 hex chars after SR=
            hex_str = text[idx + 3: idx + 3 + 4]
            if len(hex_str) < 4:
                return None
            full_val = int(hex_str, 16)
            # Use only the least significant byte as the status byte
            status_byte = full_val & 0xFF
            bits = {}
            for bit, name in self.STATUS_BITS.items():
                bits[name] = bool(status_byte & (1 << bit))
            return {'raw': hex_str, 'value': status_byte, 'bits': bits}
        except Exception as e:
            print(f'_parse_status error: {e}')
            return None

    def get_voltage(self) -> float:
        """Query the voltage monitor value (M0?).

        Response format: ``M0=xxxxx.x``  – extract the float after ``M0=``.
        """
        try:
            cmd = chr(2) + '0106M0?' + '{:X}'.format(self.chk_sum('0106M0?')) + chr(10)
            res = self.write_cmd(cmd)
            text = res.decode('ascii', errors='ignore') if isinstance(res, bytes) else str(res)
            idx = text.find('M0=')
            if idx < 0:
                return self._voltage  # fallback to last setpoint
            # extract numeric chars (digits, dot, minus) after M0=
            num_str = ''
            for ch in text[idx + 3:]:
                if ch in '0123456789.-':
                    num_str += ch
                else:
                    break
            if num_str:
                return float(num_str)
            return self._voltage
        except Exception as e:
            print(f'get_voltage error: {e}')
            return self._voltage

    def clear_faults(self):
        st = chr(2) + '0106CF=1' + '{:X}'.format(self.chk_sum('0106CF=1')) + chr(10)
        print('clear fault cmd')
        self.write_cmd(st)

    def set_enable(self):
        st = chr(2) + '0106EN=1' + '{:X}'.format(self.chk_sum('0106EN=1')) + chr(10)
        print('enable cmd')
        self.write_cmd(st)

    def set_voltage(self, voltage: float) -> None:
        """Set the HV output voltage."""
        with self._lock:
            v = int(voltage)
            try:
                cmd = self._format_set_command(v)
                self.write_cmd(cmd)
                self._voltage = v
            except Exception as e:
                print(f"Error in HV command: {e}")
                import traceback
                traceback.print_exc()
                raise



# default device instance: instantiate real serial-backed device by default
device = SerialHVDevice()

