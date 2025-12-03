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

    def disable(self, ser) -> None:
        try:
            st=chr(2)+'0106EN=0'+'{:X}'.format(self.chk_sum('0106EN=0'))+chr(10)

            self.write_cmd(st, ser)

        except Exception:
            raise Exception("Failed to disable HV output")

    def write_cmd(self, cmd, ser):
        cmd_e=cmd.encode()
        ser.write(cmd_e)
        print(cmd_e)
        ser.flush()
        #time.sleep(0.1)

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

        #    st=chr(2)+'0106V1='+"{:07.1f}".format(voltage)+'{:X}'.format(chk_sum('0106V1='+"{:07.1f}".format(voltage)))+chr(10)
        cmd = chr(2) + '0106V1=' + "{:07.1f}".format(voltage) + '{:X}'.format(self.chk_sum('0106V1=' + "{:07.1f}".format(voltage))) + chr(10)
        print('voltage set cmd')
        return cmd

    def get_voltage(self) -> float:
        #get voltage, but not implemented yet
        return 10

    def read_spellman(self, ser):
        res = ser.readline()
        print('response')
        print(res)
        ser.flush()
        return res
    
    def set_enable(self, ser):
        st=chr(2)+'0106EN=1'+'{:X}'.format(self.chk_sum('0106EN=1'))+chr(10)
        #print(st)
        print('enable cmd')
        self.write_cmd(st, ser)
        #print(self.read_spellman(ser))

    def set_voltage(self, voltage: float) -> None:
        """Set the HV output voltage. Connects, sends command, then disconnects."""
        with self._lock:
            v = int(voltage)
            # Always open fresh connection for each command
            serial_obj = None
            try:
                import serial  # type: ignore
                print(f"Opening serial port for command: {self.port}")
                serial_obj = serial.Serial(
                    self.port, 
                    self.baud, 
                    timeout=10,
                    write_timeout=10
                )
                #send enable cmd
                #self.set_enable(serial_obj)

                # Send command
                cmd = self._format_set_command(v)
                self.write_cmd(cmd, serial_obj)
                #self.disable(serial_obj)
                res = self.read_spellman(serial_obj)
                self._voltage = v  # Update stored voltage
                

                
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

