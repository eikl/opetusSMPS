import numpy as np
import scipy.constants as const


def ionRatio(Cpos, Cneg):
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

def gunnWosner(q, dp, T=293.15):
    f = const.elementary_charge / np.sqrt(4*np.pi**2*const.epsilon_0*alpha(q, dp, use_mod=True)*dp*const.Boltzmann*T)
    f = f * np.exp(-(q - (2*np.pi*const.epsilon_0*alpha(q, dp, use_mod=True)*dp*const.Boltzmann*T)/(const.elementary_charge**2)*np.log(mobilityratio()))/(2*(2*np.pi*const.epsilon_0*alpha(q, dp, use_mod=True)*dp*const.Boltzmann*T)/(const.elementary_charge**2)))
    return f