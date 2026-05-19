from hardware import BlowerDAC, flowmeter
from simple_pid import PID
import threading
import time
from hardware import flowmeter, blower

pid = PID(0.005, 0.03, 0, setpoint=4.0)
pid.output_limits = (0, 5)

def loop():
    while True:
        pv = flowmeter.get_flow()
        out = pid(pv)

        BlowerDAC.set_voltage(out)

        time.sleep(0.05)

threading.Thread(target=loop, daemon=True).start()