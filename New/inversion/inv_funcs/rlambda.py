import numpy as np
from .. import constants


def rlambda(t, press):
    # mean free path
    
    dm = 3.7e-10
    avoc = 6.022e23
    
    r = constants.kaasuv * t / (np.sqrt(2.0) * avoc * press * np.pi * dm * dm)
    
    return r