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
                    timeout=0.5,
                    write_timeout=1.0,  # Add write timeout
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
        with self._lock:

            try:
                st=chr(2)+'0106EN=0'+'{:X}'.format(self.chk_sum('0106EN=0'))+chr(10)
                print(st)
                self.write_cmd(st)

            except Exception:
                raise Exception("Failed to disable HV output")

    def write_cmd(self, cmd):
        cmd_e=cmd.encode('utf-8')
        self._serial.write(cmd_e)

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
        """Format the serial command to set voltage for your HV supply.

        Replace this with device-specific formatting.
        Example placeholder: b'SETV:1234.56\n'
        """
        #    st=chr(2)+'0106V1='+"{:07.1f}".format(voltage)+'{:X}'.format(chk_sum('0106V1='+"{:07.1f}".format(voltage)))+chr(10)
        cmd = '0106V1=' + "{:07.1f}".format(voltage) + '{:X}'.format(self.chk_sum('0106V1=' + "{:07.1f}".format(voltage))) + '\n'

        return cmd

    def get_voltage(self) -> float:
        #get voltage, but not implemented yet
        return 10
    
    def set_voltage(self, voltage: float) -> None:
        """Set the HV output voltage. Connects, sends command, then disconnects."""
        with self._lock:
            v = int(voltage)
            cmd = self._format_set_command(v)
            
            # Always open fresh connection for each command
            serial_obj = None
            try:
                import serial  # type: ignore
                print(f"Opening serial port for command: {self.port}")
                serial_obj = serial.Serial(
                    self.port, 
                    self.baud, 
                    timeout=0.5,
                    write_timeout=1.0
                )
                
                # Send command
                bytes_written = serial_obj.write(cmd.encode('utf-8'))
                serial_obj.flush()  # Ensure data is sent
                print(f"HV command written: {cmd} ({bytes_written} bytes)")
                self._voltage = v  # Update stored voltage
                time.sleep(0.1)
                # Send disable command
                disable_cmd = '0106EN=0' + '{:X}'.format(self.chk_sum('0106EN=0')) + '\n'
                print(f"Sending disable command: {disable_cmd}")
                serial_obj.write(disable_cmd.encode('utf-8'))
                serial_obj.flush()
                
            except Exception as e:
                print(f"Error in HV command: {e}")
                import traceback
                traceback.print_exc()
                raise
            finally:
                # Always close the port
                if serial_obj is not None:
                    time.sleep(0.1)
                    try:
                        serial_obj.close()
                        print(f"Serial port closed: {self.port}")
                    except Exception as e:
                        print(f"Error closing serial port: {e}")



# default device instance: instantiate real serial-backed device by default
device = SerialHVDevice()
