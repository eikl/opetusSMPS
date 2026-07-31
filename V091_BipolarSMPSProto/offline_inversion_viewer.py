import sys

import panel as pn

from DMPS_inversion_gui.offline_app import layout as app_layout
from DMPS_inversion_gui.offline_app import run_auto_worker, start_app


if __name__ == "__main__" and "--auto-worker" in sys.argv:
    run_auto_worker()
else:
    layout = start_app() if pn.state.curdoc is not None else app_layout
    layout.servable()
