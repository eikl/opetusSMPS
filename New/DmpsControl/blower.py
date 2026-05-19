from .hardware import BlowerDAC, Flowmeter
from simple_pid import PID
import threading
import time
from hardware import flowmeter, blower

def set_flow(flow_lpm = 10):
    pid = PID(0.005, 0.03, 0, setpoint=flow_lpm)
    pid.output_limits = (0, 5)


    def loop():
        while True:
            pv = Flowmeter.get_flow()
            out = pid(pv)

            BlowerDAC.set_voltage(out)

            time.sleep(0.05)

    threading.Thread(target=loop, daemon=True).start()