import numpy as np
from .cunn import cunn
from .visc import visc


def diffuus(dpp, temp, press):
    # Particle diffusivity 
    K = 1.38e-23
    
    res = (K * temp * cunn(dpp, temp, press)) / (3 * np.pi * visc(temp) * dpp)
    
    return res