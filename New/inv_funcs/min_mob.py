import numpy as np
import scipy.constants as sc

from .cunn import cunn
from .visc import visc


def min_mob(mob, t, press):
    # Calculate diameter from mobility
    
    dp = np.ones(len(mob)) * 1e-9
    dpt = np.ones(len(mob))
    
    while np.max(np.abs(dp - dpt) / dpt) > 1e-6:
        dp = dpt.copy()
        dpt = sc.elementary_charge * cunn(dp, t, press) / (3 * np.pi * visc(t) * mob)
    
    return dpt