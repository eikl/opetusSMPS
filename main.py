from blower import start_blower
import blower
from high_voltage import start_hv
import high_voltage
import ui
from cpc_controller import start_cpc


if __name__ == "__main__":
    # start controller services
    start_blower()
    start_hv()
    # start CPC reader
    start_cpc()

    # launch Tkinter UI (this call blocks until window is closed)
    ui.run_ui(high_voltage, blower)