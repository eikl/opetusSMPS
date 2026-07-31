if __name__ == "__main__":
    from hardware import BlowerDAC, Flowmeter
else:
    from .hardware import BlowerDAC, Flowmeter
from simple_pid import PID
import threading
import time


class FlowController:
    def __init__(self, flowmeter, blower, flow_lpm=10):
        self.flowmeter = flowmeter
        self.blower = blower
        self.pid = PID(0.008, 0.015, 0, setpoint=flow_lpm)
        self.pid.output_limits = (0, 5)
        self.running = False
        self.out = 2.5
        self.blower.set_voltage(self.out)

    def setpoint(self, flow_lpm):
        self.pid.setpoint = flow_lpm

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while self.running:
            try:
                self.flowmeter.step()
                raw = self.flowmeter.get_flow()
                self.out = self.pid(raw)
                self.blower.set_voltage(self.out)
            except OSError as e:
                print(f"Flowmeter I2C error: {e}", flush=True)
                time.sleep(0.5)

            time.sleep(0.1)


if __name__ == "__main__":
    flowmeter = Flowmeter()
    blower = BlowerDAC()

    controller = FlowController(flowmeter, blower, 10)
    controller.start()

    while True:
        time.sleep(1)

