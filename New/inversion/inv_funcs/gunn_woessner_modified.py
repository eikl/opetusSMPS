import numpy as np
from .. import constants


def gunn_woessner_modified(q, dp, t, Zp, Zn, Mrp, Mrn, Np, Nn, summed):
    """
    Modified Gunn-Woessner charging-fraction formula.
    Based on Chen, Xiaotong (2018).

    q  : scalar charge number (integer, positive or negative)
    dp : scalar or 1-D array of particle diameters [m]
    Returns a 1-D array of charge fractions, same length as dp.
    """
    kB = constants.boltz
    eps_0 = constants.eo
    e = constants.e

    dp = np.atleast_1d(np.asarray(dp, dtype=float))

    q_abs = abs(int(round(q)))
    if q_abs == 1:
        alpha = 0.9630 * np.exp(7.6019 / (dp * 1e9 + 2.2476))
    elif q_abs == 2:
        alpha = 0.9826 + 0.9435 * np.exp(-0.0478 * dp * 1e9)
    else:
        alpha = np.ones_like(dp)

    tmp = 2 * np.pi * eps_0 * alpha * dp * kB * t / (e ** 2)

    log_ratio = np.log(Np / Nn * Zp / Zn)
    prefactor = e / np.sqrt(4 * np.pi ** 2 * eps_0 * alpha * dp * kB * t)

    if summed == 1:
        charge_fraction = (
            prefactor * np.exp(-(q - tmp * log_ratio) ** 2 / (2 * tmp)) +
            prefactor * np.exp(-(-q - tmp * log_ratio) ** 2 / (2 * tmp))
        )
    else:
        charge_fraction = prefactor * np.exp(-(q - tmp * log_ratio) ** 2 / (2 * tmp))

    return charge_fraction
