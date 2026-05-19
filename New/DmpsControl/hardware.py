from GP8XXX_IIC import GP8403
from GP8XXX_IIC import GP8403
import serial
import time
import smbus2
from smbus2 import i2c_msg
class CPC:
    def __init__(self, port):
        self.ser = serial.Serial(
            port=port,
            baudrate=9600,
            bytesize=serial.SEVENBITS,
            parity=serial.PARITY_EVEN,
            stopbits=serial.STOPBITS_ONE,
            timeout=1
        )

    def read_instrument(self):
        self.ser.write(b"RB\r")
        return self.ser.readline().decode("utf-8").strip()
class HaukeDMA:
    def __init__(self):
        self.r1 = 0.025
        self.r2 = 0.033
        self.L = 0.28
        
I2C_BUS = 1
I2C_ADDRESS = 0x40

SCALE_FACTOR = 140.0
OFFSET = 32000.0

def crc8(data):
    crc = 0x00

    for byte in data:
        crc ^= byte

        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF

    return crc


class Flowmeter:
    def __init__(self):
        self.bus = smbus2.SMBus(I2C_BUS)
        self.flow = 0.0

        self.start_measurement()

        print("SFM3000 connected")

    def write_command(self, command):
        msb = (command >> 8) & 0xFF
        lsb = command & 0xFF

        msg = i2c_msg.write(I2C_ADDRESS, [msb, lsb])
        self.bus.i2c_rdwr(msg)

    def read_word(self):
        msg = i2c_msg.read(I2C_ADDRESS, 3)
        self.bus.i2c_rdwr(msg)

        data = list(msg)

        msb = data[0]
        lsb = data[1]
        crc = data[2]

        if crc8(bytes([msb, lsb])) != crc:
            raise RuntimeError("SFM3000 CRC error")

        return (msb << 8) | lsb

    def start_measurement(self):
        self.write_command(0x1000)
        time.sleep(0.01)

    def step(self):
        self.start_measurement()
        raw = self.read_word()

        self.flow = (raw - OFFSET) / SCALE_FACTOR

    def get_flow(self):
        return self.flow





class BlowerDAC:
    def __init__(self):
        self.dac = GP8403(
            i2c_addr=0x5F,
            bus=1
        )

        self.voltage = 0.0

        # test communication
        self.dac.set_dac_out_voltage(
            voltage=0,
            channel=1
        )

        print("GP8403 connected")

    def set_voltage(self, voltage):
        self.voltage = float(voltage)

        # limit 0–5V
        self.voltage = max(0, min(5, self.voltage))

        self.dac.set_dac_out_voltage(
            voltage=self.voltage,
            channel=1
        )

    def get_parameter(self):
        return self.voltage

