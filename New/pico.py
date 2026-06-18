from machine import Pin
import sys
import select

class InletSwitchMosfet:
    def __init__(self, pin=17):
        self.valve = Pin(pin, Pin.OUT)
        self.valve.off()

    def valveon(self):
        self.valve.on()

    def valveoff(self):
        self.valve.off()

    def status(self):
        return self.valve.value()

    def cleanup(self):
        self.valveoff()


valve = InletSwitchMosfet(pin=17)

poll = select.poll()
poll.register(sys.stdin, select.POLLIN)

print("Pico valve controller ready")

while True:
    if poll.poll(100):
        cmd = sys.stdin.readline().strip().upper()

        if cmd == "ON":
            valve.valveon()
            print("OK ON")

        elif cmd == "OFF":
            valve.valveoff()
            print("OK OFF")

        elif cmd == "STATUS":
            print("ON" if valve.status() else "OFF")

        elif cmd == "CLEANUP":
            valve.cleanup()
            print("OK CLEANUP")

        else:
            print("ERR UNKNOWN CMD")