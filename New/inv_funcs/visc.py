import numpy as np
import scipy.constants as sc



def visc(t):
    # Viscosity
    vv = (174.0 + 0.433 * (t - 273.0)) * 1.0e-7
    return vv