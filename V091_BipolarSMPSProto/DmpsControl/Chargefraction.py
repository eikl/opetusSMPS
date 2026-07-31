import numpy as np
import scipy.constants as const

def ionRatio(Cpos, Cneg, use_mod=False):
    if use_mod:
        return 1

    Cpos = np.asarray(Cpos, dtype=float)
    Cneg = np.asarray(Cneg, dtype=float)

    return np.divide(
        Cpos,
        Cneg,
        out=np.full_like(Cpos, np.nan, dtype=float),
        where=Cneg > 0,
    )

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

def gunnWosner(q, dp, Npos, Nneg, T=293.15, use_mod=True, test=False):
    dp_nm = np.asarray(dp, dtype=float)
    dp_m = dp_nm * 1e-9

    a = alpha(q, dp_nm, use_mod=use_mod)

    sigma2 = (
        2 * np.pi * const.epsilon_0 * a * dp_m * const.Boltzmann * T
        / const.elementary_charge**2
    )
    
    ratio = ionRatio(Npos, Nneg, use_mod=use_mod) * mobilityratio()
    ratio = np.where(ratio > 0, ratio, np.nan)

    mean = sigma2 * np.log(ratio)

    f = 1 / np.sqrt(2 * np.pi * sigma2)
    f *= np.exp(-((q - mean)**2) / (2 * sigma2))

    return f

def wiedensohlerAi(q):
    if q == 0:
        return [-0.0003, -0.1014, 0.3072, -0.3372, 0.1023, -0.0105]
    elif q == 1:
        return [-2.3484, 0.6044, 0.4800, 0.0013, -0.1544, 0.0320]
    elif q == 2:
        return [-44.4756, 79.3772, -62.8900, 26.4492, -5.7480, 0.5059]
    elif q == -1:
        return [-2.3197, 0.6175, 0.6201, -0.1105, -0.1260, 0.0297]
    elif q == -2:
        return [-26.3328, 35.9044, -21.4608, 7.0867, -1.3088, 0.1051]

def wiedensohler(q, dp):
    dp_nm = np.asarray(dp, dtype=float)
    ai = np.asarray(wiedensohlerAi(q), dtype=float)

    x = np.log10(dp_nm)

    log10_f = np.zeros_like(dp_nm)
    for i, a in enumerate(ai):
        log10_f += a * x**i

    return 10**log10_f