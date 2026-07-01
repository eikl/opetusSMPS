from datetime import date
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
    "save_root": "~/OneDrive/DMPS_inversions",
    "n_scans_plot": 200,
    "auto_interval_min": 30,
    "auto_file_age_sec": 120,
    "daily_overwrite": True,
    "dma_L": 0.28,
    "dma_r1": 0.025,
    "dma_r2": 0.033,
    "qa_lpm": 1.0,
    "qs_lpm": 1.0,
    "temp_K": 293.15,
    "press_Pa": 101325,
    "zratio": 1.35e-4 / 1.60e-4,
    "heatmap_clip": 20000,
    "smallest_size": 6.5,
}

pn.extension("plotly")

inversion_executor = ThreadPoolExecutor(max_workers=1)
inversion_lock = threading.Lock()
inversion_running = False
latest_inversion = None
auto_pending_signature = None
AUTO_STATE_FILE = Path("auto_inversion_state.json")


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
        "save_root": save_root.value,
        "n_scans_plot": int(n_scans_plot.value),
        "auto_interval_min": int(auto_interval_min.value),
        "auto_file_age_sec": int(auto_file_age_sec.value),
        "daily_overwrite": bool(daily_overwrite_checkbox.value),
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


def _json_safe(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value

def save_data(event=None):
    if latest_inversion is None:
        status.object = "No inversion data to save yet."
        return
    outdir = Path(save_root.value).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    if daily_overwrite_checkbox.value:
        stamp = pd.Timestamp.now().strftime("%Y%m%d")
    else:
        stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    if raw_plot.object is not None:
        raw_plot.object.write_html(outdir / f"raw_plot_{stamp}.html")
    if inversion_plot.object is not None:
        inversion_plot.object.write_html(outdir / f"inversion_plot_{stamp}.html")
    ntot_tables = []
    for tr in latest_inversion:
        if tr["kind"] == "heatmap":
            z = np.asarray(tr["Z"], dtype=float)
            heatmap_df = pd.DataFrame(
                z.T,
                index=pd.to_datetime(tr["x"]),
                columns=np.asarray(tr["y"], dtype=float),
            )
            heatmap_df.index.name = "time"
            heatmap_df.columns.name = "size_nm"
            heatmap_df.to_csv(
                outdir / f"heatmap_{tr['polarity']}_{stamp}.csv"
            )
        elif tr["kind"] == "ntot":
            polarity = tr["polarity"]
            d = pd.DataFrame({
                "time": pd.to_datetime(tr["x"]),
                f"Ntot_{polarity}_inverted": tr["y"],
            })
            if polarity == "positive" and "y_measured" in tr:
                d["Ntot_measured"] = tr["y_measured"]
            ntot_tables.append(d)
            
        elif tr["kind"] == "ion_ratio":
            ion_ratio_df = pd.DataFrame({
                "time": pd.to_datetime(tr["x"]),
                "Zp_Zn": tr["y"],
                "selected_dp_nm": tr["selected_dp"],
            })
            ion_ratio_df = ion_ratio_df.set_index("time").sort_index()
            ion_ratio_df.to_csv(outdir / f"estimated_z_ratio_{stamp}.csv")
    if ntot_tables:
        ntot_df = ntot_tables[0]
        for d in ntot_tables[1:]:
            ntot_df = pd.merge(ntot_df, d, on="time", how="outer")
        ntot_df = ntot_df.sort_values("time")
        try:
            t0 = ntot_df["time"].min()
            t1 = ntot_df["time"].max()

            smear_cpc = load_smeariii_cpc_concentration(
                t0 - pd.Timedelta(hours=1),
                t1 - pd.Timedelta(hours=1),
            )
            smear_cpc["time"] = smear_cpc["time"] + pd.Timedelta(hours=1)
            smear_cpc = smear_cpc[smear_cpc["time"].between(t0, t1)].copy()

            if not smear_cpc.empty:
                ntot_df = pd.merge_asof(
                    ntot_df.sort_values("time"),
                    smear_cpc[["time", "SMEARIII_CPC"]].sort_values("time"),
                    on="time",
                    direction="nearest",
                    tolerance=pd.Timedelta(minutes=15),
                )
        except Exception as e:
            print(f"Could not save SMEAR III CPC: {e}", flush=True)
        ntot_df = ntot_df.set_index("time")
        ntot_df.to_csv(outdir / f"ntot_{stamp}.csv")
    status.object = f"Saved plots and data to `{outdir}`."

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

import requests

SMEAR_API = "https://smear-backend-avaa-smear-prod.2.rahtiapp.fi"
def load_smeariii_cpc_concentration(start, end):
    url = f"{SMEAR_API}/search/timeseries"
    start = pd.to_datetime(start)
    end = pd.to_datetime(end)
    params = {
        "from": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "to": end.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "tablevariable": "KUM_AERO.cn",
        "quality": "ANY",
        "aggregation": "NONE",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()
    df = pd.DataFrame(payload["data"])
    if df.empty:
        print(
            f"No SMEAR III CPC API data for {params['from']} to {params['to']}",
            flush=True,
        )
        return pd.DataFrame(columns=["time", "SMEARIII_CPC"])
    df = df.rename(columns={
        "samptime": "time",
        "KUM_AERO.cn": "SMEARIII_CPC",
    })
    df["time"] = pd.to_datetime(df["time"])
    df["SMEARIII_CPC"] = pd.to_numeric(df["SMEARIII_CPC"], errors="coerce")
    return df[["time", "SMEARIII_CPC"]]


def list_scan_files(min_age_sec=0):
    root = Path(scan_root.value).expanduser()
    files = root.glob("*/*.csv")

    if min_age_sec > 0:
        now = pd.Timestamp.now().timestamp()
        files = [p for p in files if now - p.stat().st_mtime >= min_age_sec]

    return sorted(files, key=lambda p: (p.parent.name, p.stem))


def load_auto_state():
    if not AUTO_STATE_FILE.exists():
        return {"last_saved_signature": None}

    try:
        return json.loads(AUTO_STATE_FILE.read_text())
    except json.JSONDecodeError:
        return {"last_saved_signature": None}


def save_auto_state(state):
    AUTO_STATE_FILE.write_text(json.dumps(state, indent=2))


def selected_files_signature():
    parts = []

    for f in scan_files.value:
        p = Path(f)
        try:
            stat = p.stat()
            parts.append(f"{p}:{stat.st_mtime_ns}:{stat.st_size}")
        except FileNotFoundError:
            parts.append(str(p))

    return "|".join(parts)
    
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

save_root = pn.widgets.TextInput(
    name="Save folder",
    value=settings.get("save_root", DEFAULT_SETTINGS["save_root"]),
    width=700,
)

n_scans_plot = pn.widgets.IntInput(
    name="Auto-select last N",
    value=int(settings.get("n_scans_plot", DEFAULT_SETTINGS["n_scans_plot"])),
    step=1,
    width=160,
)

auto_interval_min = pn.widgets.IntInput(
    name="Auto interval min",
    value=int(settings.get("auto_interval_min", DEFAULT_SETTINGS["auto_interval_min"])),
    step=1,
    width=160,
)

auto_file_age_sec = pn.widgets.IntInput(
    name="Min file age sec",
    value=int(settings.get("auto_file_age_sec", DEFAULT_SETTINGS["auto_file_age_sec"])),
    step=10,
    width=160,
)

scan_files = pn.widgets.MultiChoice(
    name="Select scan CSVs",
    options=[],
    value=[],
    width=900,
)

save_button = pn.widgets.Button(name="Save plots/data", button_type="primary")
auto_checkbox = pn.widgets.Checkbox(name="Auto-run", value=False)
daily_overwrite_checkbox = pn.widgets.Checkbox(
    name="Daily overwrite files",
    value=bool(settings.get("daily_overwrite", DEFAULT_SETTINGS["daily_overwrite"])),
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

smallest_size = pn.widgets.FloatInput(name="Smallest size (nm)", value=6.5, step=0.1)
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
    files = list_scan_files()

    print("cwd:", Path.cwd(), flush=True)
    print("scan root:", root.resolve(), flush=True)
    print("found files:", len(files), flush=True)

    scan_files.options = [str(p) for p in files]

    if files and not scan_files.value:
        n = max(1, int(n_scans_plot.value))
        scan_files.value = [str(p) for p in files[-n:]]

    status.object = f"Found **{len(files)}** scan CSV files."


def select_last_n(event=None):
    files = list_scan_files()
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
    d = d[d["cpc_float"] > 0]
    d = d[d["abs_size_nm"] > smallest_size.value]
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
            1.93, qa, 1,
            zp, zn,
            140, 101,
            1e13, 1e13,
            "gunn woessner mod",
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
            ntot_rows["cpc_float"] = pd.to_numeric(ntot_rows["cpc_count"], errors="coerce")
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

                ntot_scan += np.trapezoid(n_inv)

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
            
            
            
            

            if "y_measured" in tr and tr['polarity'] == "positive":
                from inv_funcs.ltubefl import ltubefl
                y=tr["y_measured"] / ltubefl(20e-9, 3, 6.5/60000, 297.15, 101325)
                fig.add_scatter(
                    x=tr["x"],
                    y=tr["y_measured"],
                    mode="markers",
                    marker_symbol="x",
                    marker_size=10,
                    name=f"Measured Ntot",
                    row=3,
                    col=1,
                )
                
                t0 = pd.to_datetime(tr["x"][0])
                t1 = pd.to_datetime(tr["x"][-1])
                totalconc = load_smeariii_cpc_concentration(t0 - pd.Timedelta(hours=1), t1 - pd.Timedelta(hours=1))
                totalconc["time"] = totalconc["time"] + pd.Timedelta(hours=1)
                totalconc = totalconc[
                    totalconc["time"].between(t0, t1)
                ].copy()
                
                totalconc["SMEARIII_CPC"] = totalconc["SMEARIII_CPC"]/2
                
                fig.add_scatter(
                    x=totalconc["time"],
                    y=totalconc["SMEARIII_CPC"],
                    mode="lines+markers",
                    name="SMEAR III CPC",
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
        global inversion_running, latest_inversion, auto_pending_signature
        try:
            result = future.result()
            latest_inversion = result
            def finish_success():
                global auto_pending_signature

                plot_inversion_result(result)

                if auto_checkbox.value:
                    save_data()
                    if auto_pending_signature is not None:
                        save_auto_state({"last_saved_signature": auto_pending_signature})
                        auto_pending_signature = None
                    status.object = "Auto-run: inversion finished and saved."
                else:
                    status.object = "Inversion finished."

            if doc is not None:
                doc.add_next_tick_callback(finish_success)
            else:
                finish_success()
        except Exception:
            auto_pending_signature = None
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


def auto_refresh_invert_save():
    global auto_pending_signature

    if not auto_checkbox.value:
        return

    min_age = max(0, int(auto_file_age_sec.value))
    files = list_scan_files(min_age_sec=min_age)

    scan_files.options = [str(p) for p in files]

    if not files:
        status.object = "Auto-run: no completed scan files found."
        return

    n = max(1, int(n_scans_plot.value))
    scan_files.value = [str(p) for p in files[-n:]]

    signature = selected_files_signature()
    state = load_auto_state()

    if signature == state.get("last_saved_signature"):
        status.object = "Auto-run: no new selected scans."
        return

    with inversion_lock:
        running = inversion_running

    if running:
        status.object = "Auto-run: inversion already running."
        return

    auto_pending_signature = signature
    status.object = "Auto-run: new scans detected, running inversion."
    run_inversion()

save_button.on_click(save_data)
invert_button.on_click(run_inversion)


for w in [
    scan_root,
    save_root,
    n_scans_plot,
    auto_interval_min,
    auto_file_age_sec,
    daily_overwrite_checkbox,
    dma_L,
    dma_r1,
    dma_r2,
    qa_lpm,
    qs_lpm,
    temp_K,
    press_Pa,
    zratio_widget,
    heatmap_clip,
    smallest_size,
]:
    w.param.watch(lambda event: save_settings(), "value")


layout = pn.Column(
    "# Offline DMPS inversion / scan viewer",
    pn.Row(scan_root, refresh_button, select_last_button, n_scans_plot),
    pn.Row(save_root),
    pn.Row(auto_checkbox, daily_overwrite_checkbox, auto_interval_min, auto_file_age_sec),
    scan_files,
    "### DMA / inversion settings",
    pn.Row(dma_L, dma_r1, dma_r2),
    pn.Row(qa_lpm, qs_lpm, temp_K, press_Pa),
    pn.Row(zratio_widget, use_zratio_checkbox, heatmap_clip, smallest_size),
    pn.Row(plot_button, invert_button, save_button, status),
    "## Raw scans",
    raw_plot,
    "## Inversion",
    inversion_plot,
    width=1400,
)

refresh_scan_files()
auto_callback = pn.state.add_periodic_callback(
    auto_refresh_invert_save,
    period=max(1, int(auto_interval_min.value)) * 60 * 1000,
    start=True,
)


def update_auto_period(event):
    auto_callback.period = max(1, int(auto_interval_min.value)) * 60 * 1000


auto_interval_min.param.watch(update_auto_period, "value")
layout.servable()
