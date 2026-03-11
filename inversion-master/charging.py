import numpy as np
import pandas as pd
from original_constants import constants
from math import pi

alpha_positive = np.zeros((2,6))

alpha_positive[0,:] = [-2.3484,0.6044,0.4800,0.0013,-0.1553,0.0320]
alpha_positive[1,:] = [-44.4756,79.3772,-62.8900,26.4492,-5.7480,0.5049]

alpha_negative = np.zeros((2,6))

alpha_negative[0,:] = [-2.3197,0.6175,0.6201,-0.1105,-0.1260,0.0297]
alpha_negative[1,:] = [-26.3328,35.9044,-21.4608,7.0867,-1.3088,0.1051]

def charging_efficiency(diameters_in, max_charge, polarity, temperature):

    charges = np.arange(1,max_charge+1)

    diameters = np.atleast_1d(diameters_in)

    diameter_grid,charge_grid = np.broadcast_arrays(diameters[:,None], charges[None,:])
    efficiencies = np.zeros_like(diameter_grid)

    wiedensohler_range = (diameter_grid < 1E-6) & (charge_grid < 3) 

    gunn_range = ~wiedensohler_range

    efficiencies[wiedensohler_range] = Wiedensohler_charge(diameter_grid[wiedensohler_range],
                                                           charge_grid[wiedensohler_range],
                                                           polarity)



    efficiencies[gunn_range] = Gunn_charge(diameter_grid[gunn_range],
                                           charge_grid[gunn_range], polarity, temperature)


    return efficiencies

def Wiedensohler_charge(diameters, charges, polarity):

    nano_diameters = np.log10(diameters / 1E-9)

    #If high voltage polarity is positive, charge is negative.
    if polarity > 0:
        alfa = alpha_negative
    else:
        alfa = alpha_positive

    result = np.zeros_like(diameters)

    for i in range(alfa.shape[1]):
        result = result + alfa[charges-1, i] * nano_diameters**(i)
     
    return 10**result

def Gunn_charge(diameters, charges, polarity, temperature):

    fraction = np.zeros_like(diameters)

    constant=(2.0*pi*constants.epsilon_0*diameters*constants.Boltzmann*temperature)/(constants.e**2)

    fraction = (1.0/np.sqrt(constant*2.0*pi))*np.exp(-((polarity*charges-constant*0.1335)**2)/(2.0*constant))
    
    return fraction


