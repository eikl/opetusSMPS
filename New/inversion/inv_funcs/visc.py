import numpy as np
from .. import constants


def visc(t):
    # Viscosity
    vv = (174.0 + 0.433 * (t - 273.0)) * 1.0e-7
    return vv