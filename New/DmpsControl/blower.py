from .hardware import BlowerDAC, Flowmeter
from simple_pid import PID
import threading
import time


def set_flow(flowmeter, blower, flow_lpm=10):
    pid = PID(0.005, 0.03, 0, setpoint=flow_lpm)
    pid.output_limits = (0, 5)

    def loop():
        while True:
            flowmeter.step()          # if your Flowmeter needs this
            pv = flowmeter.get_flow()
            out = pid(pv)

            blower.set_voltage(out)

            print(f"Flow: {pv:.2f} L/min | DAC: {out:.3f} V")
            time.sleep(0.05)

    threading.Thread(target=loop, daemon=True).start()


if __name__ == "__main__":
    flowmeter = Flowmeter()
    blower = BlowerDAC()

    set_flow(flowmeter, blower, 10)

    while True:
        time.sleep(1)