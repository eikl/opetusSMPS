import numpy as np
from .rlambda import rlambda


def cunn(dp, t, press):
    # Cunningham correction
    
    c = 1.0 + rlambda(t, press) / dp * (2.514 + 0.800 * np.exp(-0.55 * dp / rlambda(t, press)))
    
    return c