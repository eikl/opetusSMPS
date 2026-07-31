from pathlib import Path

import pandas as pd


scan_file = Path("SMEARIII/DMPS007_20260629.scan")

rows = []
columns = None
scan_number = None
scan_time = None

with scan_file.open() as file:
    for line in file:
        line = line.strip()

        if not line:
            continue

        if line.startswith("SCAN "):
            scan_number = int(line.split()[1])
            continue

        if line.startswith("T20"):
            # Example: T2026-06-29 00:02:13 +02+0200
            scan_time = pd.to_datetime(line[1:].rsplit(" ", 1)[0])
            continue

        if line.startswith("time_s "):
            columns = line.split()
            continue

        if columns is None or scan_number is None or scan_time is None:
            continue

        parts = line.split()
        if len(parts) != len(columns):
            continue

        rows.append([scan_number, scan_time, *map(float, parts)])

smear_iii = pd.DataFrame(rows, columns=["scan", "scan_time", *columns])

filepath = Path("logs/scans/20260629/")


df = pd.DataFrame()
for file in filepath.glob("*.csv"):
    df = pd.concat([df, pd.read_csv(file)], ignore_index=True)

df["abs_size_nm"] = pd.to_numeric(df["size_nm"], errors="coerce").abs()
df["cpc_count"] = pd.to_numeric(df["cpc_count"], errors="coerce")

our_bins = (
    df.dropna(subset=["abs_size_nm", "cpc_count"])
    .groupby("abs_size_nm", as_index=False)
    .agg(
        our_concentration=("cpc_count", "mean"),
        our_std=("cpc_count", "std"),
        our_n=("cpc_count", "size"),
    )
    .sort_values("abs_size_nm")
)
our_bins["abs_size_nm"] = our_bins["abs_size_nm"].astype(float)

smear_iii["diameter_nm"] = pd.to_numeric(smear_iii["diameter_nm"], errors="coerce")
smear_iii["screen_conc"] = pd.to_numeric(smear_iii["screen_conc"], errors="coerce")
smear_bins = smear_iii.dropna(subset=["diameter_nm", "screen_conc"]).copy()
smear_bins["smear_size_nm"] = smear_bins["diameter_nm"].round()

smear_mean_bins = (
    smear_bins.groupby("smear_size_nm", as_index=False)
    .agg(
        smear_concentration=("screen_conc", "mean"),
        smear_std=("screen_conc", "std"),
        smear_n=("screen_conc", "size"),
    )
    .sort_values("smear_size_nm")
)

comparison = pd.merge_asof(
    our_bins,
    smear_mean_bins,
    left_on="abs_size_nm",
    right_on="smear_size_nm",
    direction="nearest",
)
comparison["size_difference_nm"] = comparison["abs_size_nm"] - comparison["smear_size_nm"]
comparison["concentration_difference"] = (
    comparison["our_concentration"] - comparison["smear_concentration"]
)
comparison["percent_difference"] = (
    comparison["concentration_difference"] / comparison["smear_concentration"] * 100
)
comparison.loc[comparison["smear_concentration"] == 0, "percent_difference"] = pd.NA

smear_min_size = smear_mean_bins["smear_size_nm"].min()
comparison.loc[
    comparison["abs_size_nm"] < smear_min_size,
    [
        "smear_size_nm",
        "smear_concentration",
        "smear_std",
        "smear_n",
        "size_difference_nm",
        "concentration_difference",
        "percent_difference",
    ],
] = pd.NA

print(comparison)
        
