import numpy as np
from .diffuus import diffuus


def ltubefl(dpp, plength, pflow, temp, press):
    # Tube losses for laminar flow
    dpp = np.atleast_1d(np.asarray(dpp, dtype=float))
    rmuu = np.pi * diffuus(dpp, temp, press) * plength / pflow
    res = np.where(
        rmuu < 0.02,
        1 - 2.56 * rmuu ** (2 / 3) + 1.2 * rmuu + 0.177 * rmuu ** (4 / 3),
        0.819 * np.exp(-3.657 * rmuu) + 0.097 * np.exp(-22.3 * rmuu) + 0.032 * np.exp(-57 * rmuu),
    )
    return res
