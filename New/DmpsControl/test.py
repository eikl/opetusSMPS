import spidev
import time

spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 100000
spi.mode = 0b01

while True:
    print("send")
    spi.xfer2([0x00, 0x80, 0x00])  # midscale
    time.sleep(1)
    spi.xfer2([0x00, 0xFF, 0xFF])  # fullscale
    time.sleep(1)
    spi.xfer2([0x00, 0x00, 0x00])  # zero
    time.sleep(1)