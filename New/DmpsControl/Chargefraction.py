import numpy as np
import scipy.constants as const

def ionRatio(Cpos, Cneg, use_mod=False):
    if use_mod:
        return 1
    Cpos = np.asarray(Cpos, dtype=float)
    Cneg = np.asarray(Cneg, dtype=float)
    return np.divide(Cpos, Cneg, out=np.full_like(Cpos, np.inf), where=Cneg != 0)

def alpha(q,dp, use_mod=True):
    if use_mod:
        return 1
    if q == 1:
        return 0.9939 + 1.2128 * np.exp(-0.064*dp)
    elif q == 2:
        return 0.9826 + 0.9435 * np.exp(-0.0478*dp)
    else:
        return 1
    


def mobilityratio():
    return 1.5

def gunnWosner(q, dp, Npos, Nneg, T=293.15, use_mod=True):
    dp_nm = np.asarray(dp, dtype=float)
    dp_m = dp_nm * 1e-9

    a = alpha(q, dp_nm, use_mod=use_mod)

    sigma2 = (
        2 * np.pi * const.epsilon_0 * a * dp_m * const.Boltzmann * T
        / const.elementary_charge**2
    )

    mean = sigma2 * np.log(
        ionRatio(Npos, Nneg, use_mod=use_mod) * mobilityratio()
    )

    f = 1 / np.sqrt(2 * np.pi * sigma2)
    f *= np.exp(-((q - mean)**2) / (2 * sigma2))

    return f