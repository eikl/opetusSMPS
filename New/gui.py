import panel as pn
import pandas as pd
import time
import DmpsControl as ctl
import inv_funcs as inv
import numpy as np
import csv
import json
from pathlib import Path
from datetime import datetime
from plotly.subplots import make_subplots
import threading
import traceback
from scipy.integrate import quad
from scipy.optimize import nnls
from concurrent.futures import ThreadPoolExecutor

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

inversion_executor = ThreadPoolExecutor(max_workers=1)
latest_inversion = None
latest_inversion_signature = None
inversion_running = False
inversion_lock = threading.Lock()

# Run with systemd service, or manually:
# source ./venv/bin/activate
# python gui.py

flowmeter = None
blower = None
flow_controller = None
cpc = None

measurement_running = threading.Event()
measurement_thread = None

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

    root = Path("logs/scans")
    if not root.exists():
        print(f"No scan root found: {root}", flush=True)
        return pd.DataFrame()

    csv_files = sorted(root.glob("*/*.csv"))[-n:]

    dfs = []
    for f in csv_files:
        try:
            dfs.append(pd.read_csv(f))
        except Exception as e:
            print(f"Could not read scan file {f}: {e}", flush=True)

    if dfs:
        return pd.concat(dfs, ignore_index=True)

    return pd.DataFrame()

def load_initial_scans_to_table():
    df0 = get_recent_completed_scans(int(n_scans_plot.value))
    if df0 is not None and not df0.empty:
        table_pane.value = df0.tail(100)
    else:
        print("No completed scans found on startup", flush=True)

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
    global phase, current_size_index, phase_start_time

    measurement_running.clear()

    if start_button.value:
        start_button.value = False

    phase = "idle"
    current_size_index = 0
    phase_start_time = time.time()

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

def measurement_loop():
    while True:
        if measurement_running.is_set():
            measurement_step()
        time.sleep(float(sleep_time.value))

def ensure_measurement_thread():
    global measurement_thread
    if measurement_thread is None or not measurement_thread.is_alive():
        measurement_thread = threading.Thread(target=measurement_loop, daemon=True)
        measurement_thread.start()

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

            log_row(row, local_log=local_log, cloud_log=None)

            doc = pn.state.curdoc

            if doc is not None:
                doc.add_next_tick_callback(
                    lambda row=row: setattr(last_row_pane, "object", str(row))
                )
            else:
                last_row_pane.object = str(row)
                        

            if now - phase_start_time >= meas_sec:
                phase_start_time = now
                current_size_index += 1
                if current_size_index >= len(scan):
                    current_size_index = 0
    
                    
                    scan_number += 1

                    save_completed_scan(scan_rows, scan_number)
                    completed_scans.append(pd.DataFrame(scan_rows.copy()))

                    scan_rows.clear()
                    ctl.HV.zero()

        if rows:
            latest_df = pd.DataFrame(rows[-100:])

            doc = pn.state.curdoc

            if doc is not None:
                doc.add_next_tick_callback(
                    lambda df=latest_df: setattr(table_pane, "value", df)
                )
            else:
                table_pane.value = latest_df

    except Exception as e:
        traceback.print_exc()
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



def on_start_change(event):
    global phase, phase_start_time, current_size_index

    if event.new:
        init()
        ensure_measurement_thread()

        phase = "idle"
        phase_start_time = time.time()
        current_size_index = 0

        measurement_running.set()
        status_text.object = "Status: running"

    else:
        measurement_running.clear()
        status_text.object = "Status: stopped"

def invert_one_scan(d, polarity, scan_range, temp=293.15, press=101325):
    d = d.copy()
    d["cpc_float"] = pd.to_numeric(d["cpc_count"], errors="coerce")
    d["abs_size_nm"] = d["size_nm"].abs()
    d = d.sort_values("abs_size_nm")

    y = d.groupby("abs_size_nm")["cpc_float"].mean()
    dp_meas_nm = y.index.to_numpy(dtype=float)
    y = y.to_numpy(dtype=float)

    dp_grid_nm = dp_meas_nm.copy()
    dp_grid_m = dp_grid_nm * 1e-9
    ldp = np.log10(dp_grid_m)

    limits = np.empty(len(ldp) + 1)
    limits[0] = ldp[0] - (ldp[1] - ldp[0]) / 2
    limits[1:-1] = 0.5 * (ldp[1:] + ldp[:-1])
    limits[-1] = ldp[-1] + (ldp[-1] - ldp[-2]) / 2

    dma = ctl.HaukeDMA()
    A = np.zeros((len(dp_meas_nm), len(dp_grid_nm)))

    qa = 1.0 / 60000.0
    qs = 1.0 / 60000.0
    q_sheath = float(d["sheath_setpoint"].median())
    qc = q_sheath / 60000.0
    qm = qc + qa - qs

    # match old sign convention:
    # pol=+1 uses negative charges, pol=-1 uses positive charges
    if polarity == "positive":
        p = np.arange(-1, -6, -1, dtype=float)
    else:
        p = np.arange(1, 6, 1, dtype=float)

    for i, dp_nm in enumerate(dp_meas_nm):
        voltage = ctl.HV.voltage_from_size(
            dp_nm if polarity == "positive" else -dp_nm,
            Q_sh_lpm=q_sheath,
        )

        args = (
            temp, press, p, voltage,
            dma.L, dma.r2, dma.r1,
            qa, qc, qm, qs,
            1.0, qa, 1,
            1.35e-4, 1.60e-4,
            140, 101,
            1e13, 1e13,
            "gunn woessner mod",
            0,
        )

        for j in range(len(dp_grid_nm)):
            a = limits[j]
            b = limits[j + 1]
            val, _ = quad(inv.intfun, a, b, args=args, limit=50)
            A[i, j] = val / (b - a)

    x, rnorm = nnls(A, y)

    return pd.DataFrame({
        "abs_size_nm": dp_grid_nm,
        "N_inverted": x,
    })

'''def corrected_scan_df(df, temp=293.15, press=101325):
    df = df.copy()
    df["cpc_float"] = pd.to_numeric(df["cpc_count"], errors="coerce")
    df["abs_size_nm"] = df["size_nm"].abs()
    df["dp_m"] = df["abs_size_nm"] * 1e-9
    df["polarity"] = np.where(df["size_nm"] > 0, "positive", "negative")

    response = np.zeros(len(df))
    dma = ctl.HaukeDMA()

    for i, row in df.iterrows():
        voltage = ctl.HV.voltage_from_size(
            row["size_nm"],
            Q_sh_lpm=row["sheath_setpoint"],
        )

        p = np.array([1]) if row["size_nm"] > 0 else np.array([-1])

        qa = 1.0 / 60000.0
        qs = 1.0 / 60000.0
        qc = row["sheath_setpoint"] / 60000.0
        qm = qc + qa - qs

        dp0 = row["dp_m"]

        a = np.log10(dp0 / 1.05)
        b = np.log10(dp0 * 1.05)

        args = (
            temp, press, p, voltage,
            dma.L, dma.r2, dma.r1,
            qa, qc, qm, qs,
            1.0, qa, 1,
            1.35e-4, 1.60e-4,
            140, 101,
            1e13, 1e13,
            "gunn woessner mod",
            0,
        )

        val, err = quad(inv.intfun, a, b, args=args, limit=50)
        response[i] = val / (b - a)

    df["kernel_response"] = response
    df["N_corrected"] = df["cpc_float"] / response

    return df'''

def make_plot(df):
    df2 = get_recent_completed_scans(int(n_scans_plot.value))

    if (df is None or df.empty) and (df2 is None or df2.empty):
        return pn.pane.Markdown("No data")

    if df is None or df.empty:
        df = pd.DataFrame(columns=["time", "size_nm", "cpc_count", "sheath_setpoint", "sheath_flow"])
    else:
        df = df.copy()

    df = df.copy()
    df["cpc_float"] = pd.to_numeric(df["cpc_count"], errors="coerce")

    hover_strings = df["size_nm"].astype(str).to_list()

    fig = make_subplots(
        rows=7,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.20, 0.80, 0.25, 0.80, 0.8, 0.8, 0.8],
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

    if df2 is not None and not df2.empty:
        start_inversion_job(df2)
        add_cached_heatmaps(fig)

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

            ratio = np.clip(ratio, 0, 4)

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
            charge_fraction_wiedensohler = ctl.Chargefraction.wiedensohler(1, merged["abs_size_nm"].to_numpy())
            
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
            
            fig.add_scatter(
                x=merged["abs_size_nm"],
                y=charge_fraction_wiedensohler,
                mode="lines+markers",
                name=f"Scan {sn}: charge fraction Wiedensohler",
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
        margin=dict(l=20, r=260, t=40, b=20),
        height=900,
        width=1500,
        showlegend=True,
        legend=dict(x=1.18, y=1.0),
        autosize=False,
        uirevision="dmps",
    )

    return pn.pane.Plotly(
        fig,
        config={"responsive": False},
        height=900,
        width=1500,
        sizing_mode="fixed",
    )




def _scan_signature(df2):
    if df2 is None or df2.empty:
        return None

    tmax = str(pd.to_datetime(df2["time"], errors="coerce").max())
    scan_numbers = tuple(sorted(pd.to_numeric(df2["scan_number"], errors="coerce").dropna().astype(int).unique()))
    nrows = int(len(df2))
    nplot = int(n_scans_plot.value)

    return (nrows, scan_numbers, tmax, nplot)


def _size_axis_for_range(scan_range):
    sizes = sorted(
        set(
            abs(x["dp"])
            for x in get_scan_program()
            if int(x["scan_range"]) == int(scan_range)
        )
    )
    return np.asarray(sizes, dtype=float)


def compute_inversion_heatmap(df2):
    """Slow worker-thread calculation. Does not touch Panel/Bokeh objects."""
    df2 = df2.copy()
    df2["abs_size_nm"] = df2["size_nm"].abs()
    df2["polarity"] = np.where(df2["size_nm"] > 0, "positive", "negative")
    df2["time"] = pd.to_datetime(df2["time"], errors="coerce")

    traces = []

    for polarity, row in [("positive", 6), ("negative", 7)]:
        for scan_range in sorted(df2["scan_range"].dropna().unique()):
            size_axis = _size_axis_for_range(scan_range)

            if len(size_axis) < 2:
                continue

            dd = df2[
                (df2["polarity"] == polarity)
                & (df2["scan_range"] == scan_range)
            ].copy()

            if dd.empty:
                continue

            heat_cols = []
            heat_times = []

            for sn, g in dd.groupby("scan_number"):
                if g.empty:
                    continue

                invdf = invert_one_scan(g, polarity, scan_range)

                dp_inv = invdf["abs_size_nm"].to_numpy(dtype=float)
                n_inv = invdf["N_inverted"].to_numpy(dtype=float)

                order = np.argsort(dp_inv)
                dp_inv = dp_inv[order]
                n_inv = n_inv[order]

                x_interp = np.interp(
                    np.log10(size_axis),
                    np.log10(dp_inv),
                    n_inv,
                    left=np.nan,
                    right=np.nan,
                )

                heat_cols.append(x_interp)
                heat_times.append(g["time"].median())

            if not heat_cols:
                continue

            Z = np.column_stack(heat_cols)

            traces.append(
                {
                    "polarity": polarity,
                    "scan_range": int(scan_range),
                    "row": row,
                    "Z": Z,
                    "x": heat_times,
                    "y": size_axis,
                    "name": f"{polarity} inverted R{int(scan_range)}",
                }
            )

    return traces


def start_inversion_job(df2):
    """Start one background inversion job if needed."""
    global inversion_running, latest_inversion_signature

    signature = _scan_signature(df2)

    with inversion_lock:
        if inversion_running:
            return
        if signature is not None and signature == latest_inversion_signature:
            return

        inversion_running = True

    print("Starting background inversion", flush=True)

    fut = inversion_executor.submit(compute_inversion_heatmap, df2.copy())

    def done_callback(fut):
        global latest_inversion, latest_inversion_signature, inversion_running

        try:
            result = fut.result()
            with inversion_lock:
                latest_inversion = result
                latest_inversion_signature = signature
                inversion_running = False
            print(f"Background inversion finished: {len(result)} heatmaps", flush=True)

        except Exception:
            traceback.print_exc()
            with inversion_lock:
                inversion_running = False

    fut.add_done_callback(done_callback)


def add_cached_heatmaps(fig):
    """Fast UI function. Only draws cached inversion output."""
    with inversion_lock:
        cached = latest_inversion

    if not cached:
        return

    for tr in cached:

        Z = np.clip(tr["Z"], 0, 20000)

        fig.add_heatmap(
            z=Z,
            x=tr["x"],
            y=tr["y"],
            zmin=0,
            zmax=20000,
            colorbar=dict(
                title=f'{tr["polarity"]} R{tr["scan_range"]}',
                x=1.02 if tr["polarity"] == "positive" else 1.09,
                len=0.28,
                y=0.22 if tr["polarity"] == "positive" else 0.08,
            ),
            name=tr["name"],
            row=tr["row"],
            col=1,
        )

        fig.update_yaxes(
            type="log",
            title_text="dp (nm)",
            row=tr["row"],
            col=1,
        )

        fig.update_xaxes(
            title_text="Time",
            tickformat="%H:%M",
            row=tr["row"],
            col=1,
        )


plot_pane = pn.bind(make_plot, table_pane.param.value)

plot_box = pn.Column(
    plot_pane,
    height=950,
    width=1550,
    sizing_mode="fixed",
    scroll=False,
)

def startup_load():
    df0 = get_recent_completed_scans(int(n_scans_plot.value))

    if df0 is not None and not df0.empty:
        print(f"Startup loaded {len(df0)} rows", flush=True)

        table_pane.value = df0.tail(100)

        # also populate memory cache
        completed_scans.clear()

        for sn, g in df0.groupby("scan_number"):
            completed_scans.append(g.copy())

    else:
        print("No startup scans found", flush=True)

pn.state.onload(startup_load)

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
    plot_box,
    sizing_mode="fixed",
    width=1600,
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
