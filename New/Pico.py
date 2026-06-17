from machine import Pin

class InletSwitchMosfet:
    def __init__(
        self,
        pin=17,
    ):
        self.valve = Pin(17, Pin.OUT)

    def valveon(self):
        self.valve.on()

    def valveoff(self):
        self.valve.off()

    def cleanup(self):
        self.valveoff()
        self.valve.close()
        
        
def 