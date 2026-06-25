import numpy as np
import scipy.constants as sc



def rlambda(t, press):
    # mean free path
    
    dm = 3.7e-10
    avoc = 6.022e23
    
    r = sc.R * t / (np.sqrt(2.0) * avoc * press * np.pi * dm * dm)
    
    return r