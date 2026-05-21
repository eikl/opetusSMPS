import panel as pn
import pandas as pd
import time
import DmpsControl as ctl
import numpy as np
import csv
import json
from pathlib import Path
from datetime import datetime
from plotly.subplots import make_subplots

SETTINGS_FILE = Path("settings.json")

DEFAULT_SETTINGS = {
    "cpc_com_port": "/dev/ttyAMA0",
    "range1": [1, 40],
    "range1_sheath": 20,
    "range1_steps": 20,
    "range2": [20, 400],
    "range2_sheath": 5,
    "range2_steps": 20,
    "meas_time": 15,
    "sleep_time": 5,
}

# Run with systemd service, or manually:
# source ./venv/bin/activate
# python gui.py

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
stop_button = pn.widgets.Button(name="Stop and zero HV", button_type="danger")

n_scans_plot = pn.widgets.IntInput(name="Number of completed scans to plot",value=3,step=1)

range1 = pn.widgets.ArrayInput(
    name="Range 1 [min,max] nm",
    value=np.array([1, 40]),
    max_array_size=2,
)
sheath1 = pn.widgets.IntInput(name="Sheath 1 (L/min)", value=20, step=1)
steps1 = pn.widgets.IntInput(name="Steps 1", value=20, step=1)

range2 = pn.widgets.ArrayInput(
    name="Range 2 [min,max] nm",
    value=np.array([20, 400]),
    max_array_size=2,
)
sheath2 = pn.widgets.IntInput(name="Sheath 2 (L/min)", value=5, step=1)
steps2 = pn.widgets.IntInput(name="Steps 2", value=20, step=1)

meas_time = pn.widgets.IntInput(name="Measurement time per size (s)", value=15, step=1)
sleep_time = pn.widgets.IntInput(name="Sleep time between measurements (s)", value=5, step=1)

status_text = pn.pane.Markdown("Status: idle")
last_row_pane = pn.pane.Str("Last measurement: -")
scan_pane = pn.pane.Str("Scan program: -")

table_pane = pn.widgets.DataFrame(
    pd.DataFrame(
        columns=[
            "time",
            "scan_range",
            "size_nm",
            "cpc_count",
            "sheath_flow",
            "sheath_setpoint",
        ]
    ),
    height=220,
    width=900,
)

rows = []
current_size_index = 0
phase = "idle"
phase_start_time = time.time()
scan_rows = []
completed_scans = []
scan_number = 0

def ensure_settings_file():
    if not SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)


def _int_list(value):
    return [int(x) for x in np.array(value).ravel()]


def save_settings():
    settings = {
        "cpc_com_port": str(cpc_com_port.value),
        "range1": _int_list(range1.value),
        "range1_sheath": int(sheath1.value),
        "range1_steps": int(steps1.value),
        "range2": _int_list(range2.value),
        "range2_sheath": int(sheath2.value),
        "range2_steps": int(steps2.value),
        "meas_time": int(meas_time.value),
        "sleep_time": int(sleep_time.value),
    }

    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def load_settings():
    try:
        with open(SETTINGS_FILE, "r") as f:
            settings = json.load(f)
    except json.JSONDecodeError:
        broken = SETTINGS_FILE.with_name("settings_broken.json")
        SETTINGS_FILE.rename(broken)
        ensure_settings_file()
        with open(SETTINGS_FILE, "r") as f:
            settings = json.load(f)

    cpc_com_port.value = settings.get("cpc_com_port", DEFAULT_SETTINGS["cpc_com_port"])

    # Supports both new settings and a few old keys harmlessly.
    range1.value = np.array(settings.get("range1", DEFAULT_SETTINGS["range1"]))
    sheath1.value = settings.get("range1_sheath", DEFAULT_SETTINGS["range1_sheath"])
    steps1.value = settings.get("range1_steps", DEFAULT_SETTINGS["range1_steps"])

    range2.value = np.array(settings.get("range2", DEFAULT_SETTINGS["range2"]))
    sheath2.value = settings.get("range2_sheath", DEFAULT_SETTINGS["range2_sheath"])
    steps2.value = settings.get("range2_steps", DEFAULT_SETTINGS["range2_steps"])

    meas_time.value = settings.get("meas_time", DEFAULT_SETTINGS["meas_time"])
    sleep_time.value = settings.get("sleep_time", DEFAULT_SETTINGS["sleep_time"])

ensure_settings_file()
load_settings()

def bipolar_log_sizes(size_range_value, n, order="negative_then_positive"):
    lo, hi = np.array(size_range_value).ravel().astype(float)
    lo, hi = abs(lo), abs(hi)

    if lo <= 0 or hi <= 0:
        raise ValueError("Use positive nonzero limits, e.g. [20, 400]")
    if hi < lo:
        lo, hi = hi, lo
    if int(n) < 2:
        raise ValueError("steps must be >= 2")

    pos = np.round(np.logspace(np.log10(lo), np.log10(hi), int(n))).astype(int)
    pos = list(dict.fromkeys([int(x) for x in pos if int(x) != 0]))
    neg = [-x for x in pos]

    if order == "positive_then_negative":
        return pos + neg
    return neg + pos

def save_completed_scan(scan_rows, scan_number):
    if not scan_rows:
        return

    run_day = datetime.now().strftime("%Y%m%d")
    scan_id = f"{run_day}_scan_{scan_number:04d}"

    path = Path("logs/scans") / run_day / f"{scan_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(scan_rows).to_csv(path, index=False)
    print(f"Saved completed scan: {path}", flush=True)
    
def get_recent_completed_scans(n=None):
    if n is None:
        n = int(n_scans_plot.value)

    if not completed_scans:
        return pd.DataFrame()

    dfs = completed_scans[-n:]
    return pd.concat(dfs, ignore_index=True)

def get_scan_program():
    scan = []

    for dp in bipolar_log_sizes(range1.value, int(steps1.value)):
        scan.append({"scan_range": 1, "dp": int(dp), "sheath": float(sheath1.value)})

    for dp in bipolar_log_sizes(range2.value, int(steps2.value)):
        scan.append({"scan_range": 2, "dp": int(dp), "sheath": float(sheath2.value)})

    return scan

def update_scan_preview():
    try:
        scan = get_scan_program()
        sizes = [p["dp"] for p in scan]
        scan_pane.object = f"Scan points ({len(sizes)}): {sizes}"
    except Exception as e:
        scan_pane.object = f"Scan parse error: {e}"

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
        flow_lpm=float(sheath1.value),
    )
    flow_controller.start()
    status_text.object = "Status: hardware initialized"

def measurement_step(debug=True):
    global current_size_index, phase, phase_start_time, scan_number

    if not start_button.value:
        return

    if flow_controller is None:
        init()

    scan = get_scan_program()
    if not scan:
        status_text.object = "Status: no scan points defined"
        return

    now = time.time()
    meas_sec = float(meas_time.value)

    try:
        if phase == "idle":
            phase = "measuring"
            phase_start_time = now
            current_size_index = 0

            point = scan[current_size_index]
            flow_controller.setpoint(point["sheath"])
            ctl.HV.voltage_set(point["dp"], Q_sh_lpm=point["sheath"])

        if phase == "measuring":
            point = scan[current_size_index]
            dp = point["dp"]
            q_sheath = point["sheath"]
            scan_range = point["scan_range"]

            flow_controller.setpoint(q_sheath)
            ctl.HV.voltage_set(dp, Q_sh_lpm=q_sheath)

            cpc_count = cpc.read_instrument()
            flow = flowmeter.get_flow()

            local_log = Path("logs") / f"measurement_{datetime.now().strftime('%Y%m%d')}.csv"

            row = {
                "time": datetime.now().isoformat(),
                "scan_range": scan_range,
                "size_nm": dp,
                "cpc_count": cpc_count,
                "sheath_flow": flow,
                "sheath_setpoint": q_sheath,
                "scan_number": scan_number,
            }

            if debug:
                print(row, flush=True)

            rows.append(row)
            scan_rows.append(row)
            last_row_pane.object = str(row)
            log_row(row, local_log=local_log, cloud_log=None)
            
            

            if now - phase_start_time >= meas_sec:
                phase_start_time = now
                current_size_index += 1
                if current_size_index >= len(scan):
                    current_size_index = 0
      
                    current_size_index = 0
                    scan_number += 1

                    save_completed_scan(scan_rows, scan_number)
                    completed_scans.append(pd.DataFrame(scan_rows.copy()))

                    scan_rows.clear()
                    ctl.HV.zero()

        if rows:
            table_pane.value = pd.DataFrame(rows[-100:])

    except Exception as e:
        status_text.object = f"Measurement error: {e}"
        print(f"Measurement error: {e}", flush=True)


def append_row_csv(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()

    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def log_row(row, local_log, cloud_log=None):
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

def on_start_change(event):
    global phase, phase_start_time, current_size_index, callback

    if event.new:
        init()
        update_scan_preview()

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

def make_plot(df):
    if df is None or df.empty:
        return pn.pane.Markdown("No data")

    df = df.copy()
    df["cpc_float"] = pd.to_numeric(df["cpc_count"], errors="coerce")

    hover_strings = df["size_nm"].astype(str).to_list()

    fig = make_subplots(
        rows=5,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.20, 0.80, 0.25, 0.80, 0.8],
        vertical_spacing=0.05,
    )

    fig.add_scatter(
        x=df["time"],
        y=df["size_nm"],
        mode="lines+markers",
        name="Particle size (nm)",
        customdata=hover_strings,
        hovertemplate="Size: %{y}<br>dp: %{customdata} nm<extra></extra>",
        row=1,
        col=1,
    )

    fig.add_scatter(
        x=df["time"],
        y=df["cpc_float"],
        mode="lines+markers",
        name="CPC (#/cm³)",
        customdata=hover_strings,
        hovertemplate="Conc: %{y}<br>dp: %{customdata} nm<extra></extra>",
        row=2,
        col=1,
    )

    fig.add_scatter(
        x=df["time"],
        y=df["sheath_setpoint"],
        mode="lines",
        name="Sheath setpoint (L/min)",
        customdata=hover_strings,
        hovertemplate="Sheath setpoint: %{y}<br>dp: %{customdata} nm<extra></extra>",
        row=3,
        col=1,
    )

    fig.add_scatter(
        x=df["time"],
        y=df["sheath_flow"],
        mode="lines",
        name="Sheath flow (L/min)",
        customdata=hover_strings,
        hovertemplate="Sheath flow: %{y}<br>dp: %{customdata} nm<extra></extra>",
        row=3,
        col=1,
    )

    df2 = get_recent_completed_scans(int(n_scans_plot.value))

    if df2 is not None and not df2.empty:
        df2 = df2.copy()
        df2["cpc_float"] = pd.to_numeric(df2["cpc_count"], errors="coerce")
        df2["abs_size_nm"] = df2["size_nm"].abs()
        df2["polarity"] = np.where(df2["size_nm"] > 0, "pos", "neg")

        grouped = (
            df2.groupby(["scan_number", "abs_size_nm", "polarity"])["cpc_float"]
            .mean()
            .reset_index()
        )

        for sn, g in grouped.groupby("scan_number"):
            pos = g[g["polarity"] == "pos"].rename(columns={"cpc_float": "cpc_pos"})
            neg = g[g["polarity"] == "neg"].rename(columns={"cpc_float": "cpc_neg"})

            merged = pd.merge(
                pos[["abs_size_nm", "cpc_pos"]],
                neg[["abs_size_nm", "cpc_neg"]],
                on="abs_size_nm",
                how="inner",
            ).sort_values("abs_size_nm")

            if merged.empty:
                continue

            ratio = ctl.Chargefraction.ionRatio(
                merged["cpc_pos"].to_numpy(),
                merged["cpc_neg"].to_numpy(),
            )

            fig.add_scatter(
                x=merged["abs_size_nm"],
                y=ratio,
                mode="lines+markers",
                name=f"Scan {sn}: CPC + / -",
                row=4,
                col=1,
            )
            
            charge_fraction_modified = ctl.Chargefraction.gunnWosner(1, merged["abs_size_nm"].to_numpy(), merged["cpc_pos"].to_numpy(), merged["cpc_neg"].to_numpy(), use_mod=True)
            charge_fraction_og = ctl.Chargefraction.gunnWosner(1, merged["abs_size_nm"].to_numpy(), merged["cpc_pos"].to_numpy(), merged["cpc_neg"].to_numpy(), use_mod=False)
            
            fig.add_scatter(
                x=merged["abs_size_nm"],
                y=charge_fraction_modified,
                mode="lines+markers",
                name=f"Scan {sn}: charge fraction GW modified",
                row=5,
                col=1,
            )
            
            fig.add_scatter(
                x=merged["abs_size_nm"],
                y=charge_fraction_og,
                mode="lines+markers",
                name=f"Scan {sn}: charge fraction GW",
                row=5,
                col=1,
            )

    fig.update_xaxes(title_text="|dp| (nm)", row=4, col=1)

    fig.update_yaxes(title_text="Charge fraction", row=5, col=1)
    fig.update_xaxes(title_text="|dp| (nm)", row=5, col=1)
    fig.update_yaxes(title_text="Size (nm)", row=1, col=1)
    fig.update_yaxes(title_text="CPC", row=2, col=1)
    fig.update_yaxes(title_text="Sheath", row=3, col=1)
    fig.update_yaxes(title_text="+ / - ratio", row=4, col=1)
    fig.update_xaxes(title_text="Time", row=3, col=1)

    fig.update_layout(
        title="Live DMPS scan",
        margin=dict(l=20, r=20, t=40, b=20),
        height=700,
        width=1500,
        showlegend=True,
    )

    return pn.pane.Plotly(fig, config={"responsive": True})

plot_pane = pn.bind(make_plot, table_pane.param.value)

def on_scan_setting_change(event):
    save_settings()
    update_scan_preview()

for widget in [
    cpc_com_port,
    range1,
    sheath1,
    steps1,
    range2,
    sheath2,
    steps2,
    meas_time,
    sleep_time,
]:
    widget.param.watch(on_scan_setting_change, "value")

sleep_time.param.watch(on_sleep_change, "value")
start_button.param.watch(on_start_change, "value")
init_button.on_click(lambda event: init())
stop_button.on_click(lambda event: stop_and_zero())

update_scan_preview()

#### Layout ####
layout = pn.Column(
    "# DMA / CPC Control GUI",
    pn.Row(cpc_com_port),
    "# CPC / DMA control panel",
    pn.Row(start_button, status_text, init_button, stop_button),
    "### Scan range 1",
    pn.Row(range1, sheath1, steps1),
    "### Scan range 2",
    pn.Row(range2, sheath2, steps2),
    scan_pane,
    pn.Row(meas_time, sleep_time, n_scans_plot),
    "### Live data",
    last_row_pane,
    table_pane,
    "### Live plot",
    plot_pane,
)

layout.servable()

# To host via Tailscale. Update websocket_origin IPs if your Tailscale IPs change.
pn.serve(
    layout,
    address="0.0.0.0",
    port=5006,
    show=False,
    websocket_origin=["100.77.46.12:5006", "100.104.173.10:5006", "100.104.216.3:5006", "localhost:5006"],
)
