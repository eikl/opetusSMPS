from hardware import blower
from simple_pid import PID
import threading
import time

pid = PID(0.005, 0.03, 0, setpoint=4.0)
pid.output_limits = (0, 5)

def loop():
    while True:
        pv = blower.get_parameter()
        out = pid(pv)

        blower.set_voltage(out)

        time.sleep(0.05)

threading.Thread(target=loop, daemon=True).start()