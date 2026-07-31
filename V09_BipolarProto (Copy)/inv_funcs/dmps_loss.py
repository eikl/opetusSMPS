import numpy as np
from .ltubefl import ltubefl


def dmps_loss1(dp, aeroflow, temp, press):
    # Loss function for the DMPS system
    # Loss is modelled as laminar flow tube losses
    # You have to specify the aerosol flow of the DMPS and
    # the fitted length parameter from your detection efficiency calibrations
    # This is actually unknown
    plength = 5.5
    
    res = ltubefl(dp, plength, aeroflow, temp, press)
    
    return res


def dmps_loss2(dp, aeroflow, temp, press):
    # Stub for dmps_loss2 - same as dmps_loss1 for now
    return dmps_loss1(dp, aeroflow, temp, press)