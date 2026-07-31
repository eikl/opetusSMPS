import serial
import time

class PicoValve:
    def __init__(self, port="/dev/ttyACM0", baudrate=115200):
        self.ser = serial.Serial(port, baudrate, timeout=2)
        time.sleep(2)
        self.ser.reset_input_buffer()

    def command(self, cmd):
        self.ser.write((cmd + "\n").encode())
        return self.ser.readline().decode().strip()

    def on(self):
        return self.command("ON")

    def off(self):
        return self.command("OFF")

    def status(self):
        return self.command("STATUS")

    def close(self):
        self.off()
        self.ser.close()
        
if __name__ == "__main__":
    valve = PicoValve()
    print("Turning valve ON...")
    print(valve.on())
    time.sleep(20)
    print("Valve status:", valve.status())
    print("Turning valve OFF...")
    print(valve.off())
    time.sleep(2)
    print("Valve status:", valve.status())
    valve.close()