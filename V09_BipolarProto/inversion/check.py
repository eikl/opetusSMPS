import numpy as np
import pandas as pd
from pathlib import Path
import Chargefraction as cf

folder = Path("logs/scans/20260521")
csv_files = sorted(folder.glob("*.csv"))

df = pd.concat([pd.read_csv(f) for f in csv_files], ignore_index=True)

df2 = df.copy()

df2["time"] = pd.to_datetime(df2["time"])
df2["cpc_float"] = pd.to_numeric(df2["cpc_count"], errors="coerce")
df2["abs_size_nm"] = df2["size_nm"].abs()
df2["polarity"] = np.where(df2["size_nm"] > 0, "pos", "neg")

df2 = df2.sort_values("time")

pos = df2[df2["polarity"] == "pos"].copy()
neg = df2[df2["polarity"] == "neg"].copy()

pos = pos.rename(columns={"cpc_float": "cpc_pos", "time": "time_pos"})
neg = neg.rename(columns={"cpc_float": "cpc_neg", "time": "time_neg"})

merged = pd.merge_asof(
    pos.sort_values("time_pos"),
    neg.sort_values("time_neg"),
    left_on="time_pos",
    right_on="time_neg",
    by="abs_size_nm",
    direction="nearest",
    tolerance=pd.Timedelta("10min"),
)

merged = merged.dropna(subset=["cpc_pos", "cpc_neg"])

merged["ion_ratio"] = cf.ionRatio(
    merged["cpc_pos"].to_numpy(),
    merged["cpc_neg"].to_numpy(),
)

merged = merged.sort_values("time_pos")

import matplotlib.pyplot as plt

plt.plot(merged["time_pos"], merged["ion_ratio"], ".-")
plt.xlabel("time")
plt.ylabel("ion ratio")
plt.xticks(rotation=45)
plt.tight_layout()
plt.show()