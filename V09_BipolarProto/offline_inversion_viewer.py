import json
import traceback
import threading
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import panel as pn
from plotly.subplots import make_subplots
from scipy.integrate import trapezoid
from scipy.optimize import nnls
from numpy.polynomial.legendre import leggauss

_GL_NODES, _GL_WEIGHTS = leggauss(5)

import inv_funcs as inv


# ---------------------------------------------------------------------
# Settings / constants
# ---------------------------------------------------------------------

SETTINGS_FILE = Path("settings_inversion.json")

DEFAULT_SETTINGS = {
    "scan_root": "logs/scans",
    "n_scans_plot": 5,
    "dma_L": 0.28,
    "dma_r1": 0.025,
    "dma_r2": 0.033,
    "qa_lpm": 1.0,
    "qs_lpm": 1.0,
    "temp_K": 293.15,
    "press_Pa": 101325,
    "zratio": 1.35e-4 / 1.60e-4,
    "heatmap_clip": 20000,
}

pn.extension("plotly")

inversion_executor = ThreadPoolExecutor(max_workers=1)
inversion_lock = threading.Lock()
inversion_running = False
latest_inversion = None


# ---------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------

def ensure_settings_file():
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.write_text(json.dumps(DEFAULT_SETTINGS, indent=2))


def load_settings():
    ensure_settings_file()
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except json.JSONDecodeError:
        broken = SETTINGS_FILE.with_name("settings_inversion_broken.json")
        SETTINGS_FILE.rename(broken)
        ensure_settings_file()
        return json.loads(SETTINGS_FILE.read_text())


def save_settings():
    settings = {
        "scan_root": scan_root.value,
        "n_scans_plot": int(n_scans_plot.value),
        "dma_L": float(dma_L.value),
        "dma_r1": float(dma_r1.value),
        "dma_r2": float(dma_r2.value),
        "qa_lpm": float(qa_lpm.value),
        "qs_lpm": float(qs_lpm.value),
        "temp_K": float(temp_K.value),
        "press_Pa": float(press_Pa.value),
        "zratio": float(zratio_widget.value),
        "heatmap_clip": float(heatmap_clip.value),
    }
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


def cunningham_correction(dp, T=293.15, P=101325, a=1.142, b=0.558, c=0.999):
    lambda_0 = 67.3e-9
    T0 = 273.15
    P0 = 101325
    lambda_air = lambda_0 * (T / T0) * (P0 / P)
    return 1 + (2 * lambda_air / dp) * (a + b * np.exp(-c * dp / (2 * lambda_air)))


def voltage_from_size(dp_nm, q_sh_lpm, dma, temp_K=293.15, press_Pa=101325):
    mu = 1.81e-5
    e = 1.602176634e-19

    sign = -1 if dp_nm < 0 else 1
    dp = abs(float(dp_nm)) * 1e-9
    q_sh = float(q_sh_lpm) / 60000.0
    ln_r = np.log(dma.r2 / dma.r1)
    cc = cunningham_correction(dp, T=temp_K, P=press_Pa)

    v = (3 * mu * q_sh * ln_r * dp) / (2 * dma.L * e * cc)
    return sign * v


def get_dma():
    return SimpleNamespace(
        L=float(dma_L.value),
        r1=float(dma_r1.value),
        r2=float(dma_r2.value),
    )


def get_scan_size_axis(df):
    sizes = sorted(pd.to_numeric(df["size_nm"], errors="coerce").abs().dropna().unique())
    return np.asarray(sizes, dtype=float)


def load_selected_scans():
    dfs = []

    for f in scan_files.value:
        p = Path(f)
        try:
            d = pd.read_csv(p)
            d["scan_id"] = p.stem
            dfs.append(d)
        except Exception as e:
            status.object = f"Could not read {p}: {e}"
            print(f"Could not read {p}: {e}", flush=True)

    if not dfs:
        return pd.DataFrame()

    df = pd.concat(dfs, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["cpc_float"] = pd.to_numeric(df["cpc_count"], errors="coerce")
    df["abs_size_nm"] = pd.to_numeric(df["size_nm"], errors="coerce").abs()
    df["polarity"] = np.where(df["size_nm"] > 0, "positive", "negative")
    return df


# ---------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------

settings = load_settings()

scan_root = pn.widgets.TextInput(
    name="Scan folder",
    value=settings.get("scan_root", DEFAULT_SETTINGS["scan_root"]),
    width=700,
)

n_scans_plot = pn.widgets.IntInput(
    name="Auto-select last N",
    value=int(settings.get("n_scans_plot", DEFAULT_SETTINGS["n_scans_plot"])),
    step=1,
    width=160,
)

scan_files = pn.widgets.MultiChoice(
    name="Select scan CSVs",
    options=[],
    value=[],
    width=900,
)

refresh_button = pn.widgets.Button(name="Refresh scan list", button_type="primary")
select_last_button = pn.widgets.Button(name="Select last N", button_type="primary")
plot_button = pn.widgets.Button(name="Plot raw selected scans", button_type="success")
invert_button = pn.widgets.Button(name="Run inversion", button_type="danger")

dma_L = pn.widgets.FloatInput(name="DMA L (m)", value=float(settings.get("dma_L", 0.28)), step=0.01)
dma_r1 = pn.widgets.FloatInput(name="DMA r1 (m)", value=float(settings.get("dma_r1", 0.025)), step=0.001)
dma_r2 = pn.widgets.FloatInput(name="DMA r2 (m)", value=float(settings.get("dma_r2", 0.033)), step=0.001)

qa_lpm = pn.widgets.FloatInput(name="Aerosol flow qa (L/min)", value=float(settings.get("qa_lpm", 1.0)), step=0.1)
qs_lpm = pn.widgets.FloatInput(name="Sample flow qs (L/min)", value=float(settings.get("qs_lpm", 1.0)), step=0.1)

temp_K = pn.widgets.FloatInput(name="T (K)", value=float(settings.get("temp_K", 293.15)), step=1)
press_Pa = pn.widgets.FloatInput(name="P (Pa)", value=float(settings.get("press_Pa", 101325)), step=100)

zratio_widget = pn.widgets.FloatInput(
    name="Zp/Zn",
    value=float(settings.get("zratio", DEFAULT_SETTINGS["zratio"])),
    step=0.01,
)
use_zratio_checkbox = pn.widgets.Checkbox(name="Use Zp/Zn from settings", value=False)

heatmap_clip = pn.widgets.FloatInput(
    name="Heatmap clip",
    value=float(settings.get("heatmap_clip", 20000)),
    step=1000,
)

status = pn.pane.Markdown("Status: idle")

raw_plot = pn.pane.Plotly(height=750, width=1300)
inversion_plot = pn.pane.Plotly(height=850, width=1300)


# ---------------------------------------------------------------------
# Scan file browser
# ---------------------------------------------------------------------

def refresh_scan_files(event=None):
    root = Path(scan_root.value).expanduser()
    files = sorted(root.glob("*/*.csv"), key=lambda p: (p.parent.name, p.stem))

    print("cwd:", Path.cwd(), flush=True)
    print("scan root:", root.resolve(), flush=True)
    print("found files:", len(files), flush=True)

    scan_files.options = [str(p) for p in files]

    if files and not scan_files.value:
        n = max(1, int(n_scans_plot.value))
        scan_files.value = [str(p) for p in files[-n:]]

    status.object = f"Found **{len(files)}** scan CSV files."


def select_last_n(event=None):
    root = Path(scan_root.value).expanduser()
    files = sorted(root.glob("*/*.csv"), key=lambda p: (p.parent.name, p.stem))
    n = max(1, int(n_scans_plot.value))
    scan_files.options = [str(p) for p in files]
    scan_files.value = [str(p) for p in files[-n:]]
    status.object = f"Selected last **{len(scan_files.value)}** scan files."


refresh_button.on_click(refresh_scan_files)
select_last_button.on_click(select_last_n)


# ---------------------------------------------------------------------
# Raw scan plot
# ---------------------------------------------------------------------

def plot_selected_scans(event=None):
    df = load_selected_scans()
    df = df[df["Ntot"] == False]  
    if df.empty:
        status.object = "No selected scan data."
        return

    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.08,
        subplot_titles=[
            "CPC concentration vs selected size",
            "CPC concentration vs time",
            "Sheath flow / setpoint",
            "Positive / negative CPC ratio",
        ],
    )

    for (scan_id, polarity), g in df.groupby(["scan_id", "polarity"]):
        g = g.sort_values("abs_size_nm")

        fig.add_scatter(
            x=g["abs_size_nm"],
            y=g["cpc_float"],
            mode="lines+markers",
            name=f"{scan_id} {polarity}",
            row=1,
            col=1,
        )

        fig.add_scatter(
            x=g["time"],
            y=g["cpc_float"],
            mode="lines+markers",
            name=f"{scan_id} {polarity} time",
            row=2,
            col=1,
            showlegend=False,
        )

    for scan_id, g in df.groupby("scan_id"):
        g = g.sort_values("time")

        fig.add_scatter(
            x=g["time"],
            y=g["sheath_flow"],
            mode="lines",
            name=f"{scan_id} sheath",
            row=3,
            col=1,
        )

        fig.add_scatter(
            x=g["time"],
            y=g["sheath_setpoint"],
            mode="lines",
            name=f"{scan_id} setpoint",
            row=3,
            col=1,
        )

        grouped = (
            g.groupby(["abs_size_nm", "polarity"])["cpc_float"]
            .mean()
            .reset_index()
        )

        pos = grouped[grouped["polarity"] == "positive"].rename(columns={"cpc_float": "cpc_pos"})
        neg = grouped[grouped["polarity"] == "negative"].rename(columns={"cpc_float": "cpc_neg"})

        m = pd.merge(
            pos[["abs_size_nm", "cpc_pos"]],
            neg[["abs_size_nm", "cpc_neg"]],
            on="abs_size_nm",
            how="inner",
        ).sort_values("abs_size_nm")

        if not m.empty:
            ratio = np.divide(
                m["cpc_pos"].to_numpy(dtype=float),
                m["cpc_neg"].to_numpy(dtype=float),
                out=np.full(len(m), np.nan),
                where=m["cpc_neg"].to_numpy(dtype=float) > 0,
            )

            fig.add_scatter(
                x=m["abs_size_nm"],
                y=np.clip(ratio, 0, 4),
                mode="lines+markers",
                name=f"{scan_id} CPC + / -",
                row=4,
                col=1,
            )

    fig.update_xaxes(type="log", title_text="|dp| (nm)", row=1, col=1)
    fig.update_xaxes(title_text="Time", row=2, col=1)
    fig.update_xaxes(title_text="Time", row=3, col=1)
    fig.update_xaxes(type="log", title_text="|dp| (nm)", row=4, col=1)

    fig.update_yaxes(title_text="CPC", row=1, col=1)
    fig.update_yaxes(title_text="CPC", row=2, col=1)
    fig.update_yaxes(title_text="Flow L/min", row=3, col=1)
    fig.update_yaxes(title_text="+ / -", row=4, col=1)

    fig.update_layout(
        height=750,
        width=1300,
        title="Selected DMPS scans",
        showlegend=True,
        margin=dict(l=50, r=260, t=60, b=30),
        legend=dict(x=1.02, y=1.0),
    )

    raw_plot.object = fig
    status.object = f"Plotted **{df['scan_id'].nunique()}** scan(s)."


plot_button.on_click(plot_selected_scans)


# ---------------------------------------------------------------------
# Ion ratio + inversion
# ---------------------------------------------------------------------

def estimate_ion_mobility_ratio_for_scan(g_scan, temp=293.15, press=101325):
    d = g_scan.copy()
    d["cpc_float"] = pd.to_numeric(d["cpc_count"], errors="coerce")
    d["abs_size_nm"] = d["size_nm"].abs()
    d["polarity"] = np.where(d["size_nm"].astype(float) > 0, "pos", "neg")
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
            * cunningham_correction(dp_i_m, T=temp, P=press)
            / (3 * np.pi * 1.81e-5 * dp_i_m)
        )

        # doubly charged contaminant: same mobility => particle mobility is half
        dp_g_m = inv.min_mob(np.array([0.5 * mob_i]), temp, press)[0]
        dp_g_nm = dp_g_m * 1e9

        if dp_g_nm > np.nanmax(dp):
            return np.sqrt(Rp[i] / Rn[i]), dp[i]

        Rg_pos = np.interp(dp_g_nm, dp, Rp)
        Rg_neg = np.interp(dp_g_nm, dp, Rn)

        zratio_default = float(zratio_widget.value)
        Zn = 1e-4
        Zp = zratio_default * Zn

        fw_pos_1 = inv.gunn_woessner_modified(
            1,
            np.array([dp_g_m]),
            temp,
            Zp,
            Zn,
            140,
            101,
            1e13,
            1e13,
            0,
        )
        
        fw_pos_2 = inv.gunn_woessner_modified(
            2,
            np.array([dp_g_m]),
            temp,
            Zp,
            Zn,
            140,
            101,
            1e13,
            1e13,
            0,
        )

        fw_neg_1 = inv.gunn_woessner_modified(
            -1,
            np.array([dp_g_m]),
            temp,
            Zp,
            Zn,
            140,
            101,
            1e13,
            1e13,
            0,
        )
        
        fw_neg_2 = inv.gunn_woessner_modified(
            -2,
            np.array([dp_g_m]),
            temp,
            Zp,
            Zn,
            140,
            101,
            1e13,
            1e13,
            0,
        )

        double_pos = Rg_pos * fw_pos_2 / fw_pos_1
        double_neg = Rg_neg * fw_neg_2 / fw_neg_1

        ok_pos = double_pos < 0.10 * Rp[i]
        ok_neg = double_neg < 0.10 * Rn[i]

        if ok_pos and ok_neg:
            return np.sqrt(Rp[i] / Rn[i]), dp[i]

    return np.nan, np.nan


def invert_one_scan(d, polarity, zratio=None, temp=293.15, press=101325):
    d = d.copy()
    d = d[d["Ntot"] == False]
    d["cpc_float"] = pd.to_numeric(d["cpc_count"], errors="coerce")
    d["abs_size_nm"] = pd.to_numeric(d["size_nm"], errors="coerce").abs()
    d = d.dropna(subset=["abs_size_nm", "cpc_float"])
    d = d.sort_values("abs_size_nm")

    y_series = d.groupby("abs_size_nm")["cpc_float"].mean()
    dp_meas_nm = y_series.index.to_numpy(dtype=float)
    y = y_series.to_numpy(dtype=float)

    if len(dp_meas_nm) < 2:
        return pd.DataFrame(columns=["abs_size_nm", "N_GWalpha"])

    dp_grid_nm = dp_meas_nm.copy()
    dp_grid_m = dp_grid_nm * 1e-9
    ldp = np.log10(dp_grid_m)

    limits = np.empty(len(ldp) + 1)
    limits[0] = ldp[0] - (ldp[1] - ldp[0]) / 2
    limits[1:-1] = 0.5 * (ldp[1:] + ldp[:-1])
    limits[-1] = ldp[-1] + (ldp[-1] - ldp[-2]) / 2

    mids = 0.5 * (limits[:-1] + limits[1:])
    halfs = 0.5 * (limits[1:] - limits[:-1])
    gl_pts = (mids[:, None] + halfs[:, None] * _GL_NODES[None, :]).ravel()

    dma = get_dma()
    A = np.zeros((len(dp_meas_nm), len(dp_grid_nm)))

    qa = float(qa_lpm.value) / 60000.0
    qs = float(qs_lpm.value) / 60000.0
    q_sheath_lpm = float(d["sheath_setpoint"].median())
    qc = q_sheath_lpm / 60000.0
    qm = qc + qa - qs

    if polarity == "positive":
        p = np.arange(-1, -6, -1, dtype=float)
    else:
        p = np.arange(1, 6, 1, dtype=float)

    if zratio is None or not np.isfinite(zratio) or use_zratio_checkbox.value:
        zratio = float(zratio_widget.value)

    zn = 1e-4
    zp = zratio * zn

    for i, dp_nm in enumerate(dp_meas_nm):
        voltage = voltage_from_size(
            dp_nm if polarity == "positive" else -dp_nm,
            q_sh_lpm=q_sheath_lpm,
            dma=dma,
            temp_K=temp,
            press_Pa=press,
        )

        args = (
            temp, press, p, voltage,
            dma.L, dma.r2, dma.r1,
            qa, qc, qm, qs,
            2.55, qa, 1,
            zp, zn,
            140, 101,
            1e13, 1e13,
            "fuchs",
            0,
        )

        vals = inv.intfun(gl_pts, *args).reshape(len(dp_grid_nm), len(_GL_NODES))
        A[i, :] = 0.5 * vals @ _GL_WEIGHTS

    x, _ = nnls(A, y)

    return pd.DataFrame({
        "abs_size_nm": dp_grid_nm,
        "N_GWalpha": x,
    })


def run_inversion_calculation(df):
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["abs_size_nm"] = pd.to_numeric(df["size_nm"], errors="coerce").abs()
    df["polarity"] = np.where(df["size_nm"] > 0, "positive", "negative")

    size_axis = get_scan_size_axis(df[df["Ntot"] == False])
    output = []
    ion_points = []

    group_key = "scan_id" if "scan_id" in df.columns else "scan_number"

    zratios = {}
    for scan_id, g_scan in df.groupby(group_key):
        zratio, selected_dp = estimate_ion_mobility_ratio_for_scan(
            g_scan,
            temp=float(temp_K.value),
            press=float(press_Pa.value),
        )
        zratios[scan_id] = 1/zratio
        if np.isfinite(zratio):
            ion_points.append((g_scan["time"].median(), zratio, selected_dp, scan_id))

    for polarity in ["positive", "negative"]:
        dd = df[df["polarity"] == polarity].copy()

        heat_cols = []
        heat_times = []
        ntot_vals = []
        ntot_measured = []

        for scan_id, g_scan in dd.groupby(group_key):
            zratio = zratios.get(scan_id, np.nan)
            scan_parts = []
            ntot_scan = 0.0

            ntot_rows = g_scan[g_scan["Ntot"] == True].copy()
            ntot_rows["cpc_float"] = pd.to_numeric(ntot_rows["cpc_count"], errors="coerce")/2
            measured_ntot = ntot_rows["cpc_float"].mean()

            for _, g_range in g_scan.groupby("scan_range"):
                
                invdf = invert_one_scan(
                    g_range,
                    polarity=polarity,
                    zratio=zratio,
                    temp=float(temp_K.value),
                    press=float(press_Pa.value),
                )

                if invdf.empty:
                    continue

                dp_inv = invdf["abs_size_nm"].to_numpy(dtype=float)
                n_inv = invdf["N_GWalpha"].to_numpy(dtype=float)

                ntot_scan += trapezoid(n_inv, np.log(dp_inv))

                order = np.argsort(dp_inv)
                scan_parts.append((dp_inv[order], n_inv[order]))

            if not scan_parts:
                continue

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
            ntot_measured.append(measured_ntot)

        if heat_cols:
            output.append({
                "kind": "heatmap",
                "polarity": polarity,
                "Z": np.column_stack(heat_cols),
                "x": heat_times,
                "y": size_axis,
            })

            output.append({
                "kind": "ntot",
                "polarity": polarity,
                "x": heat_times,
                "y": ntot_vals,
                "y_measured": ntot_measured,
            })
            
            

    output.append({
        "kind": "ion_ratio",
        "x": [x[0] for x in ion_points],
        "y": [x[1] for x in ion_points],
        "selected_dp": [x[2] for x in ion_points],
        "scan_id": [x[3] for x in ion_points],
    })

    return output


def plot_inversion_result(result):
    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.08,
        subplot_titles=[
            "Positive inverted heatmap",
            "Negative inverted heatmap",
            "Ntot",
            "Estimated Zp/Zn",
        ],
    )

    for tr in result:
        if tr["kind"] == "heatmap":
            row = 1 if tr["polarity"] == "positive" else 2
            z = np.clip(tr["Z"], 0, float(heatmap_clip.value))

            fig.add_heatmap(
                z=z,
                x=tr["x"],
                y=tr["y"],
                zmin=0,
                zmax=float(heatmap_clip.value),
                name=f"{tr['polarity']} heatmap",
                colorbar=dict(title=tr["polarity"], len=0.35),
                row=row,
                col=1,
            )

            fig.update_yaxes(type="log", title_text="dp (nm)", row=row, col=1)
            fig.update_xaxes(title_text="Time", tickformat="%H:%M", row=row, col=1)

        elif tr["kind"] == "ntot":
            fig.add_scatter(
                x=tr["x"],
                y=tr["y"],
                mode="lines+markers",
                name=f"Inverted Ntot {tr['polarity']}",
                row=3,
                col=1,
            )

            if "y_measured" in tr:
                fig.add_scatter(
                    x=tr["x"],
                    y=tr["y_measured"],
                    mode="markers",
                    marker_symbol="x",
                    marker_size=10,
                    name=f"Measured Ntot {tr['polarity']}",
                    row=3,
                    col=1,
                )
            

        elif tr["kind"] == "ion_ratio":
            fig.add_scatter(
                x=tr["x"],
                y=np.clip(tr["y"], 0.3, 3.0),
                mode="lines+markers",
                name="Zp/Zn",
                customdata=np.array(tr["selected_dp"]),
                hovertemplate="Zp/Zn=%{y:.3f}<br>dp=%{customdata:.1f} nm<extra></extra>",
                row=4,
                col=1,
            )

    fig.update_yaxes(title_text="Ntot", row=3, col=1)
    fig.update_xaxes(title_text="Time", tickformat="%H:%M", row=3, col=1)
    fig.update_yaxes(title_text="Zp/Zn", row=4, col=1)
    fig.update_xaxes(title_text="Time", tickformat="%H:%M", row=4, col=1)

    fig.update_layout(
        height=850,
        width=1300,
        title="Offline inversion result",
        showlegend=True,
        margin=dict(l=50, r=260, t=60, b=30),
        legend=dict(x=1.02, y=1.0),
    )

    inversion_plot.object = fig


def run_inversion(event=None):
    global inversion_running

    df = load_selected_scans()
    if df.empty:
        status.object = "No selected scan data for inversion."
        return

    with inversion_lock:
        if inversion_running:
            status.object = "Inversion already running."
            return
        inversion_running = True

    status.object = "Running inversion..."

    fut = inversion_executor.submit(run_inversion_calculation, df)

    doc = pn.state.curdoc
    def done_callback(future):
        global inversion_running, latest_inversion
        try:
            result = future.result()
            latest_inversion = result
            if doc is not None:
                doc.add_next_tick_callback(lambda: plot_inversion_result(result))
                doc.add_next_tick_callback(lambda: setattr(status, "object", "Inversion finished."))
            else:
                plot_inversion_result(result)
                status.object = "Inversion finished."
        except Exception:
            traceback.print_exc()
            if doc is not None:
                doc.add_next_tick_callback(
                    lambda: setattr(status, "object", "Inversion failed. Check terminal.")
                )
            else:
                status.object = "Inversion failed. Check terminal."
        finally:
            with inversion_lock:
                inversion_running = False

    fut.add_done_callback(done_callback)


invert_button.on_click(run_inversion)


for w in [
    scan_root,
    n_scans_plot,
    dma_L,
    dma_r1,
    dma_r2,
    qa_lpm,
    qs_lpm,
    temp_K,
    press_Pa,
    zratio_widget,
    heatmap_clip,
]:
    w.param.watch(lambda event: save_settings(), "value")


layout = pn.Column(
    "# Offline DMPS inversion / scan viewer",
    pn.Row(scan_root, refresh_button, select_last_button, n_scans_plot),
    scan_files,
    "### DMA / inversion settings",
    pn.Row(dma_L, dma_r1, dma_r2),
    pn.Row(qa_lpm, qs_lpm, temp_K, press_Pa),
    pn.Row(zratio_widget, use_zratio_checkbox, heatmap_clip),
    pn.Row(plot_button, invert_button, status),
    "## Raw scans",
    raw_plot,
    "## Inversion",
    inversion_plot,
    width=1400,
)

refresh_scan_files()
layout.servable()
