import panel as pn
import pandas as pd
import time
import DmpsControl as ctl
import numpy as np
import os
import csv
import plotly.express as px
from pathlib import Path
from datetime import datetime
import json
from pathlib import Path

SETTINGS_FILE = Path("settings.json")

DEFAULT_SETTINGS = {
    "cpc_com_port": "/dev/ttyAMA0",
    "sheath_flow": 10,
    "size_range": [-200, 200],
    "size_steps": 20,
    "meas_time": 15,
    "sleep_time": 5,
}


#Run with (in terminal) for local and comment the last part with pn.serve:
#Set-ExecutionPolicy Unrestricted -Scope Process
#.\.venv\Scripts\activate
#panel serve gui.py --autoreload --show


flowmeter = None
blower = None
flow_controller = None
cpc = None
callback = None

pn.extension("plotly")

#### Widgets ####
cpc_com_port = pn.widgets.TextInput(name="CPC COM port", value="/dev/ttyAMA0")

start_button = pn.widgets.Toggle(name="Start measurement", button_type="success")
init_button = pn.widgets.Button(name="Initialize hardware", button_type="primary")
Stop_button = pn.widgets.Button(name="Stop and zero HV", button_type="danger")
sheath_slider = pn.widgets.IntInput(name="Sheath flow setpoint (L/min)", value=int(10), step=1)
size_range = pn.widgets.ArrayInput(
    name="Size range [min_nm, max_nm]",
    value=np.array([-200, 200]),
    max_array_size=2,
)

size_steps = pn.widgets.IntInput(
    name="Log steps per polarity",
    value=20,
    step=1,
)

meas_time = pn.widgets.IntInput(name="Measurement time per size (s)", value=int(15), step=1)
sleep_time = pn.widgets.IntInput(name="Sleep time between measurements (s)", value=int(5), step=1)



status_text = pn.pane.Markdown("Status: idle")
last_row_pane = pn.pane.Str("Last measurement: -")
table_pane = pn.widgets.DataFrame(pd.DataFrame(columns=["time","size_nm","analog_voltage","cpc_count","sheath_flow"]),
                                  height=200, width=800)

rows = []
current_size_index = 0
phase = "idle"  
phase_start_time = time.time()

def ensure_settings_file():
    if not SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)

def save_settings():
    settings = {
        "cpc_com_port": str(cpc_com_port.value),
        "sheath_flow": int(sheath_slider.value),

        "size_range": [int(x) for x in size_range.value],
        "size_steps": int(size_steps.value),

        "meas_time": int(meas_time.value),
        "sleep_time": int(sleep_time.value),
    }

    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)
        
def load_settings():
    with open(SETTINGS_FILE, "r") as f:
        settings = json.load(f)

    cpc_com_port.value = settings.get(
        "cpc_com_port",
        DEFAULT_SETTINGS["cpc_com_port"],
    )

    sheath_slider.value = settings.get(
        "sheath_flow",
        DEFAULT_SETTINGS["sheath_flow"],
    )

    size_range.value = np.array(settings.get("size_range", DEFAULT_SETTINGS["size_range"]))
    size_steps.value = settings.get("size_steps", DEFAULT_SETTINGS["size_steps"])

    meas_time.value = settings.get(
        "meas_time",
        DEFAULT_SETTINGS["meas_time"],
    )

    sleep_time.value = settings.get(
        "sleep_time",
        DEFAULT_SETTINGS["sleep_time"],
    )

ensure_settings_file()
load_settings()

def stop_and_zero():
    global phase, current_size_index, phase_start_time, callback

    if start_button.value:
        start_button.value = False
    phase = "idle"
    current_size_index = 0
    phase_start_time = time.time()

    if callback is not None and callback.running:
        callback.stop()

    try:
        ctl.HV.zero()
    except OSError:
        ctl.setup()
        ctl.HV.zero()

    status_text.object = "Status: stopped, HV zeroed"

def init():
    global flowmeter, blower, flow_controller, cpc
    

    if flow_controller is not None:
        return

    flowmeter = ctl.Flowmeter()
    blower = ctl.BlowerDAC()
    cpc = ctl.CPC(cpc_com_port.value)

    ctl.setup()
    
    ctl.HV.zero()

    flow_controller = ctl.blower.FlowController(
        flowmeter,
        blower,
        flow_lpm=float(sheath_slider.value),
    )

    flow_controller.start()
    

def get_sizes():
    try:
        limits = np.array(size_range.value).ravel()
        if len(limits) != 2:
            raise ValueError("Give [min_nm, max_nm], e.g. [-200, 200]")

        start, end = float(limits[0]), float(limits[1])
        n = int(size_steps.value)

        if n < 2:
            raise ValueError("steps must be >= 2")

        sizes = []

        if start < 0:
            neg_max = abs(start)
            neg = -np.logspace(0, np.log10(neg_max), n)
            sizes.extend(neg)

        if end > 0:
            pos = np.logspace(0, np.log10(end), n)
            sizes.extend(pos)

        sizes = np.round(sizes).astype(int)

        # remove possible duplicates from rounding, keep order
        sizes = list(dict.fromkeys(sizes))

        return sizes

    except Exception as e:
        status_text.object = f"Size parse error: {e}"
        return []


def measurement_step(debug=True):
    global current_size_index, phase, phase_start_time

    if not start_button.value:
        return  # not running

    if flow_controller is not None:
        flow_controller.setpoint(float(sheath_slider.value))
    else:
        init()

    sizes = get_sizes()
    if not sizes:
        status_text.object = "Status: no sizes defined"
        return

    now = time.time()
    meas_sec = float(meas_time.value)
    sleep_sec = float(sleep_time.value)

    try:
        # --- start state ---
        if phase == "idle":
            # move to first size
            phase = "measuring"
            phase_start_time = now
            current_size_index = 0

            dp = sizes[current_size_index]
            flow = flowmeter.get_flow()
            ctl.HV.voltage_set(dp, Q_sh_lpm=float(sheath_slider.value))

        # --- measuring phase ---
        if phase == "measuring":
            dp = sizes[current_size_index]

            # do one measurement sample
            cpc_count = cpc.read_instrument()
            flow = flowmeter.get_flow()

            ctl.HV.voltage_set(dp, Q_sh_lpm=float(sheath_slider.value))


            RUN_ID = datetime.now().strftime("%Y%m%d")

            LOCAL_LOG = Path("logs") / f"measurement_{RUN_ID}.csv"
            CLOUD_LOG = None
            
            row = {
                "time": datetime.now().isoformat(),
                "size_nm": dp,
                "cpc_count": cpc_count,
                "sheath_flow": flow,
                "sheath_setpoint": float(sheath_slider.value),
            }
            if debug:
                print(row)

            rows.append(row)
            last_row_pane.object = str(row)

            log_row(row, local_log=LOCAL_LOG, cloud_log=CLOUD_LOG)


            if now - phase_start_time >= meas_sec:
                phase_start_time = now
                current_size_index += 1
                if current_size_index >= len(sizes):
                    current_size_index = 0

        if rows:
            df = pd.DataFrame(rows[-100:])
            table_pane.value = df
    except Exception as e:
        status_text.object = f"Measurement error: {e}"
        print(f"Measurement error: {e}")


def append_row_csv(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    file_exists = path.exists()

    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def log_row(row, local_log, cloud_log):
    append_row_csv(local_log, row)
    if cloud_log is not None:
        try:
            append_row_csv(cloud_log, row)
        except Exception as e:
            status_text.object = f"Cloud log failed, local OK: {e}"

def on_sleep_change(event):
    global callback
    if callback is not None:
        callback.period = max(100, int(event.new) * 1000)

sleep_time.param.watch(on_sleep_change, "value")

def on_start_change(event):
    global phase, phase_start_time, current_size_index, callback

    if event.new:
        init()

        status_text.object = "Status: running"
        phase = "idle"
        phase_start_time = time.time()
        current_size_index = 0

        if callback is None or not callback.running:
            callback = pn.state.add_periodic_callback(
                measurement_step,
                period=int(sleep_time.value) * 1000,
                start=True,
            )
        else:
            callback.start()

    else:
        status_text.object = "Status: stopped"
        if callback is not None and callback.running:
            callback.stop()


        
from plotly.subplots import make_subplots

def make_plot(df):
    if df is None or df.empty:
        return pn.pane.Markdown("No data")
    
    hover_strings = [
    " ".join(t.split(" ")[-4:-1])
    for t in df["time"]
    ]   


    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.20, 0.80],  # top size, bottom cpc
        vertical_spacing=0.05
    )

    fig.add_scatter(
        x=df["time"],
        y=df["size_nm"],
        mode="lines",
        name="Particle Size (nm)",
        customdata=hover_strings,
        hovertemplate="Size: %{y}<br>Time: %{customdata}<extra></extra>",
        row=1, col=1
    )

    fig.add_scatter(
        x=df["time"],
        y=df["cpc_count"].astype(float),
        mode="lines",
        name="CPC Concentration (#/cm³)",
        customdata=hover_strings,
        hovertemplate="Conc: %{y}<br>Time: %{customdata}<extra></extra>",
        row=2, col=1
    )

    fig.update_yaxes(title_text="Size (nm)", row=1, col=1)
    fig.update_yaxes(title_text="CPC (#/cm³)", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=2, col=1)

    fig.update_layout(
        title="Live CPC & Particle Size",
        margin=dict(l=20, r=20, t=40, b=20),
        height=500,
        width=1500,
        showlegend=True,
    )

    return pn.pane.Plotly(fig, config={"responsive": True})

plot_pane = pn.bind(make_plot, table_pane.param.value)


for widget in [
    cpc_com_port,
    sheath_slider,
    size_range,
    size_steps,
    meas_time,
    sleep_time,
]:
    widget.param.watch(lambda event: save_settings(), "value")

start_button.param.watch(on_start_change, 'value')
init_button.on_click(lambda event: init())
Stop_button.on_click(lambda event: stop_and_zero())
#### Layout ####
layout = pn.Column(
    "# DMA / CPC Control GUI",
    pn.Row(cpc_com_port),
    "# CPC / DMA control panel",
    pn.Row(start_button, status_text, init_button, Stop_button),
    pn.Row(sheath_slider, size_range, size_steps),
    pn.Row(meas_time, sleep_time),
    "### Live data",
    last_row_pane,
    table_pane,
    "### Live plot",
    plot_pane,
)


layout.servable()

# To host it via tailscale. Should be at http://100.77.46.12:5006 then since rPI is pi@100.77.46.12:5006
# Also add your tailscale ip to the websocket_origin list below if you want to access it from there. 
pn.serve(
    layout,
    address="0.0.0.0",
    port=5006,
    show=False,
    websocket_origin=["100.77.46.12:5006", "100.104.173.10:5006", "localhost:5006"],
)