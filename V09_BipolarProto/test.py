import requests
import pandas as pd
SMEAR_API = "https://smear-backend-avaa-smear-prod.2.rahtiapp.fi"
def load_smeariii_total_concentration(start, end):
    url = f"{SMEAR_API}/search/timeseries"
    params = {
        "from": pd.to_datetime(start).strftime("%Y-%m-%dT%H:%M:%S.000"),
        "to": pd.to_datetime(end).strftime("%Y-%m-%dT%H:%M:%S.000"),
        "tablevariable": "KUM_DMPS.tconc",
        "quality": "ANY",
        "aggregation": "NONE",
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    payload = r.json()
    df = pd.DataFrame(payload["data"])
    if df.empty:
        return pd.DataFrame(columns=["time", "SMEARIII_Ntot"])
    df = df.rename(columns={
        "samptime": "time",
        "KUM_DMPS.tconc": "SMEARIII_Ntot",
    })
    df["time"] = pd.to_datetime(df["time"])
    df["SMEARIII_Ntot"] = pd.to_numeric(df["SMEARIII_Ntot"], errors="coerce")
    return df[["time", "SMEARIII_Ntot"]]
totalconc = load_smeariii_total_concentration(
    "2026-06-16 00:00:00",
    "2026-06-30 00:00:00",
)
print(totalconc)