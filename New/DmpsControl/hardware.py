from GP8XXX_IIC import GP8403

import serial

class CPC:
    def __init__(self, port):
        self.ser = serial.Serial(port, 9600, timeout=1)
        self.parity = 7
        

    def read_instrument(self):
        self.ser.write(b'RB\n') # Reads the number of counts in the last second RA=6s and DC gives the count from last DC call
        response = self.ser.readline().decode('utf-8').strip()
        return response
    
class HaukeDMA:
    def __init__(self):
        self.r1 = 0.025
        self.r2 = 0.033
        self.L = 0.28
        
    
        

class BlowerDAC:
    def __init__(self):
        self.dac = GP8403(i2c_addr=0x5F, bus=1)
        self.voltage = 0.0

        # test connection
        self.dac.set_dac_out_voltage(voltage=0, channel=1)

        print("DAC connected")

    def set_voltage(self, voltage):
        self.voltage = float(voltage)

        # clamp 0–5 V
        self.voltage = max(0, min(5, self.voltage))

        self.dac.set_dac_out_voltage(
            voltage=self.voltage,
            channel=1
        )

    def get_parameter(self):
        return self.voltage


