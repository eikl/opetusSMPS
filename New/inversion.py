import panel as pn
import pandas as pd
import time
import inv_funcs as inv
import numpy as np
import json
from pathlib import Path
from datetime import datetime
from plotly.subplots import make_subplots
import threading
import traceback
from scipy.integrate import quad, trapezoid
from scipy.optimize import nnls
from concurrent.futures import ThreadPoolExecutor

SETTINGS_FILE = Path("settings_inversion.json")

DEFAULT_SETTINGS = {

}

inversion_executor = ThreadPoolExecutor(max_workers=1)
latest_inversion = None
latest_inversion_signature = None
inversion_running = False
inversion_lock = threading.Lock()

# Run with systemd service, or manually:
# source ./venv/bin/activate
# python gui.py

pn.extension("plotly")

#### Widgets ####


def ensure_settings_file():
    if not SETTINGS_FILE.exists():
        with open(SETTINGS_FILE, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=2)


def save_settings():
    settings = {

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


ensure_settings_file()
load_settings()

    
def get_recent_completed_scans(n=None):

    if n is None:
        n = int(n_scans_plot.value)

def save_completed_scan(scan_rows, scan_number):
    if not scan_rows:
        return

    t0 = pd.to_datetime(scan_rows[0]["time"])
    scan_id = t0.strftime("%Y%m%d_%H%M%S")

    run_day = t0.strftime("%Y%m%d")
    path = Path("logs/scans") / run_day / f"{scan_id}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    pd.DataFrame(scan_rows).to_csv(path, index=False)
    print(f"Saved completed scan: {path}", flush=True)
    root = Path("logs/scans")
    if not root.exists():
        print(f"No scan root found: {root}", flush=True)
        return pd.DataFrame()

    csv_files = sorted(
    root.glob("*/*.csv"),
    key=lambda p: p.stem,
    )[-n:]

    dfs = []
    for f in csv_files:
        try:
            d = pd.read_csv(f)
            d["scan_id"] = f.stem
            dfs.append(d)
        except Exception as e:
            print(f"Could not read scan file {f}: {e}", flush=True)

    if dfs:
        return pd.concat(dfs, ignore_index=True)

    return pd.DataFrame()


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


def invert_one_scan(d, polarity, scan_range, zratio = None, temp=293.15, press=101325):
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

    if polarity == "positive":
        p = np.arange(-1, -6, -1, dtype=float)
    else:
        p = np.arange(1, 6, 1, dtype=float)

    for i, dp_nm in enumerate(dp_meas_nm):
        voltage = ctl.HV.voltage_from_size(
            dp_nm if polarity == "positive" else -dp_nm,
            Q_sh_lpm=q_sheath,
        )
        
        if zratio is None or not np.isfinite(zratio):
            zratio = 1.35e-4 / 1.60e-4

        
        Zn = 1e-4
        Zp = zratio * Zn
        
        args = (
            temp, press, p, voltage,
            dma.L, dma.r2, dma.r1,
            qa, qc, qm, qs,
            1.0, qa, 1,
            Zp, Zn,
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
        "N_GWalpha": x,
    })


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
        rows=9,
        cols=1,
        shared_xaxes=False,
        row_heights=[0.20, 0.80, 0.25, 0.80, 0.8, 0.8, 0.8, 0.45, 0.8],
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
            
            
            sizes = merged["abs_size_nm"].to_numpy()
            cpc_pos = merged["cpc_pos"].to_numpy()
            cpc_neg = merged["cpc_neg"].to_numpy()

            charge_fractions = {}

            for q in [1]:
                charge_fractions[f"GW mod q{q}"] = ctl.Chargefraction.gunnWosner(
                    q, sizes, cpc_pos, cpc_neg, use_mod=True
                )

                charge_fractions[f"GW og q{q}"] = ctl.Chargefraction.gunnWosner(
                    q, sizes, cpc_pos, cpc_neg, use_mod=False
                )

                charge_fractions[f"Wiedensohler q{q}"] =ctl.Chargefraction.wiedensohler(q, sizes)

            for label, charge_fraction in charge_fractions.items():
                fig.add_scatter(
                    x=merged["abs_size_nm"],
                    y=charge_fraction,
                    mode="lines+markers",
                    name=f"Scan {sn}: charge fraction {label}",
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
    fig.update_xaxes(title_text="Time", row=1, col=1)
    fig.update_xaxes(title_text="Time", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=3, col=1)

    fig.update_layout(
        title="Live DMPS scan",
        margin=dict(l=20, r=260, t=40, b=20),
        height=1100,
        width=1500,
        showlegend=True,
        legend=dict(x=1.18, y=1.0),
        autosize=False,
        uirevision="dmps",
    )

    return pn.pane.Plotly(
        fig,
        config={"responsive": False},
        height=1100,
        width=1500,
        sizing_mode="fixed",
    )


def _scan_signature(df2):
    if df2 is None or df2.empty:
        return None

    tmax = str(pd.to_datetime(df2["time"], errors="coerce").max())
    scan_ids = tuple(sorted(df2["scan_id"].dropna().astype(str).unique()))
    nrows = int(len(df2))
    nplot = int(n_scans_plot.value)

    return (nrows, scan_ids, tmax, nplot)

def estimate_ion_mobility_ratio_for_scan(g_scan, temp=293.15, press=101325):
    d = g_scan.copy()
    d["cpc_float"] = pd.to_numeric(d["cpc_count"], errors="coerce")
    d["abs_size_nm"] = d["size_nm"].abs()
    d["polarity"] = np.where(d["size_nm"] > 0, "pos", "neg")

    grouped = (
        d.groupby(["abs_size_nm", "polarity"])["cpc_float"]
        .mean()
        .reset_index()
    )

    pos = grouped[grouped["polarity"] == "pos"].rename(columns={"cpc_float": "R_pos"})
    neg = grouped[grouped["polarity"] == "neg"].rename(columns={"cpc_float": "R_neg"})

    m = pd.merge(
        pos[["abs_size_nm", "R_pos"]],
        neg[["abs_size_nm", "R_neg"]],
        on="abs_size_nm",
        how="inner",
    ).sort_values("abs_size_nm")

    if len(m) < 3:
        return np.nan, np.nan

    dp = m["abs_size_nm"].to_numpy(dtype=float)
    Rp = m["R_pos"].to_numpy(dtype=float)
    Rn = m["R_neg"].to_numpy(dtype=float)

    # start from largest-size peak
    start = np.argmax(Rp + Rn)

    for i in range(start, len(dp)):
        if Rp[i] <= 0 or Rn[i] <= 0:
            continue

        dp_i_m = dp[i] * 1e-9

        # singly charged mobility at dp_i
        mob_i = (
            1.602176634e-19
            * ctl.HV.cunningham_correction(dp_i_m, T=temp, P=press)
            / (3 * np.pi * 1.81e-5 * dp_i_m)
        )

        # doubly charged contaminant: same mobility => particle mobility is half
        dp_g_m = inv.min_mob(np.array([0.5 * mob_i]), temp, press)[0]
        dp_g_nm = dp_g_m * 1e9

        if dp_g_nm > np.nanmax(dp):
            return np.sqrt(Rp[i] / Rn[i]), dp[i]

        Rg_pos = np.interp(dp_g_nm, dp, Rp)
        Rg_neg = np.interp(dp_g_nm, dp, Rn)

        fw_pos = inv.wiedensohler(dp_g_m, "+")
        fw_neg = inv.wiedensohler(dp_g_m, "-")

        double_pos = Rg_pos * fw_pos[1] / fw_pos[0]
        double_neg = Rg_neg * fw_neg[1] / fw_neg[0]

        ok_pos = double_pos < 0.10 * Rp[i]
        ok_neg = double_neg < 0.10 * Rn[i]

        if ok_pos and ok_neg:
            return np.sqrt(Rp[i] / Rn[i]), dp[i]

    return np.nan, np.nan

def compute_inversion_heatmap(df2):
    df2 = df2.copy()
    df2["abs_size_nm"] = df2["size_nm"].abs()
    df2["polarity"] = np.where(df2["size_nm"] > 0, "positive", "negative")
    df2["time"] = pd.to_datetime(df2["time"], errors="coerce")

    traces = []
    ntot_traces = []
    
    ion_x = []
    ion_y = []
    ion_dp = []
    scan_zratios = {}

    group_key = "scan_id" if "scan_id" in df2.columns else "scan_number"
    size_axis = sorted(set(abs(x["dp"]) for x in get_scan_program()))
    size_axis = np.asarray(size_axis, dtype=float)
    
    for sn, g_scan in df2.groupby(group_key):
        zratio, selected_dp = estimate_ion_mobility_ratio_for_scan(g_scan)

        scan_zratios[sn] = zratio

        if np.isfinite(zratio):
            ion_x.append(g_scan["time"].median())
            ion_y.append(zratio)
            ion_dp.append(selected_dp)

    for polarity, row in [("positive", 6), ("negative", 7)]:
        dd_pol = df2[df2["polarity"] == polarity].copy()

        heat_cols = []
        heat_times = []
        ntot_vals = []


        for sn, g_scan in dd_pol.groupby(group_key):
            zratio = scan_zratios.get(sn, np.nan)
            
            scan_parts = []
            ntot_scan = 0.0


            for scan_range, g in g_scan.groupby("scan_range"):
                invdf = invert_one_scan(g, polarity, scan_range, zratio=zratio)

                dp_inv = invdf["abs_size_nm"].to_numpy(dtype=float)
                n_inv = invdf["N_GWalpha"].to_numpy(dtype=float)


                ntot_scan += trapezoid(n_inv, np.log(dp_inv))

                order = np.argsort(dp_inv)
                scan_parts.append((dp_inv[order], n_inv[order]))

            full_col = np.full(len(size_axis), np.nan)

            for dp_inv, n_inv in scan_parts:
                mask = (size_axis >= np.nanmin(dp_inv)) & (size_axis <= np.nanmax(dp_inv))
                full_col[mask] = np.interp(
                    np.log10(size_axis[mask]),
                    np.log10(dp_inv),
                    n_inv,
                )
                

            heat_cols.append(full_col)
            heat_times.append(g_scan["time"].median())
            ntot_vals.append(ntot_scan)


        if not heat_cols:
            continue

        
        
        Z = np.column_stack(heat_cols)

        traces.append({
            "kind": "heatmap",
            "polarity": polarity,
            "scan_range": "all",
            "row": row,
            "Z": Z,
            "x": heat_times,
            "y": size_axis,
            "name": f"{polarity} inverted",
        })

        ntot_traces.append({
            "kind": "ntot",
            "polarity": polarity,
            "row": 9,
            "x": heat_times,
            "y": ntot_vals,
            "name": f"Ntot GW {polarity}",
        })

    traces.append({
            "kind": "ion_ratio",
            "row": 8,
            "x": ion_x,
            "y": ion_y,
            "selected_dp": ion_dp,
            "name": "Ion mobility ratio Z+/Z-",
        })

    return traces + ntot_traces


def start_inversion_job(df2):
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
    with inversion_lock:
        cached = latest_inversion

    if not cached:
        return

    for tr in cached:
        if tr["kind"] == "heatmap":
            Z = np.clip(tr["Z"], 0, 20000)

            fig.add_heatmap(
                z=Z,
                x=tr["x"],
                y=tr["y"],
                zmin=0,
                zmax=20000,
                colorbar=dict(title=f'{tr["polarity"]}'),
                name=tr["name"],
                row=tr["row"],
                col=1,
            )

            fig.update_yaxes(type="log", title_text="dp (nm)", row=tr["row"], col=1)
            fig.update_xaxes(title_text="Time", tickformat="%H:%M", row=tr["row"], col=1)

        elif tr["kind"] == "ntot":
            
            fig.add_scatter(
                x=tr["x"],
                y=tr["y"],
                mode="lines+markers",
                name=tr["name"],
                row=9,
                col=1,
            )
            
        

            fig.update_yaxes(title_text="Ntot", row=9, col=1)
            fig.update_xaxes(title_text="Time", tickformat="%H:%M", row=9, col=1)
            
        elif tr["kind"] == "ion_ratio":
            y = np.clip(tr["y"], 0.5, 2.0)
            fig.add_scatter(
                x=tr["x"],
                y=y,
                mode="lines+markers",
                name=tr["name"],
                row=8,
                col=1,
            )

            fig.update_yaxes(title_text="Z+ / Z-", row=8, col=1)
            fig.update_xaxes(title_text="Time", tickformat="%H:%M", row=8, col=1)


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

        group_key = "scan_id" if "scan_id" in df0.columns else "scan_number"

        for sn, g in df0.groupby(group_key):
            completed_scans.append(g.copy())

    else:
        print("No startup scans found", flush=True)

pn.state.onload(startup_load)

def on_scan_setting_change(event):
    save_settings()
    update_scan_preview()

for widget in [
 
]:
    widget.param.watch(on_scan_setting_change, "value")


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
    pn.Row(meas_time, sleep_time, n_scans_plot, settling_time, polarity_switch_time),
    "### Live data",
    last_row_pane,
    table_pane,
    "### Live plot",
    plot_box,
    sizing_mode="fixed",
    width=1600,
)

layout.servable()


# To host via Tailscale. Update the websocket_origin list with your Tailscale IPs to get access.
pn.serve(
    layout,
    autoreload=True,
    address="0.0.0.0",
    port=5006,
    show=True,
    websocket_origin=["100.77.46.12:5006", "100.104.173.10:5006", "100.104.216.3:5006", "localhost:5006"],
)
