import Chargefraction as cf
import pandas as pd 
import numpy as np
import matplotlib.pyplot as plt 

df  = pd.read_csv("logs/scans/20260522/20260522_scan_0021.csv")

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

charge_fraction = cf.gunnWosner(
    q=1,
    dp=merged["abs_size_nm"].to_numpy(),
    Npos=merged["cpc_pos"].to_numpy(),
    Nneg=merged["cpc_neg"].to_numpy(),
)
charge_fraction_og = cf.gunnWosner(
    q=1,
    dp=merged["abs_size_nm"].to_numpy(),
    Npos=merged["cpc_pos"].to_numpy(),
    Nneg=merged["cpc_neg"].to_numpy(),
    use_mod=False,
    test=True
)
charge_fraction_wiedensohler = cf.wiedensohler(
    q=1,
    dp=merged["abs_size_nm"].to_numpy(),
)

import matplotlib.pyplot as plt

plt.plot(merged["abs_size_nm"], charge_fraction, ".", label="Gunn-Wosner")
plt.plot(merged["abs_size_nm"], charge_fraction_og, ".", label="Gunn-Wosner no mod")
plt.plot(merged["abs_size_nm"], charge_fraction_wiedensohler, ".", label="Wiedensohler")
plt.xlabel("particle diameter / nm")
plt.ylabel("charge fraction")
plt.legend()
plt.show()