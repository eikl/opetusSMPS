from math import pi
import numpy as np
import pandas as pd
from original_constants import constants
from scipy.optimize import fixed_point

particle_diffusivity = 1.38e-23
dm=3.7e-10 # origin not explained, probably some estimate of air molecule diameter

def calculate_air_mean_free_path(temperature, pressure):
    return constants.R*temperature/(np.sqrt(2.)*constants.Avogadro*pressure*pi*dm**2)

def cunningham_correction(diameter, temperature, pressure):
    mean_free_path = calculate_air_mean_free_path(temperature, pressure)
    if np.isscalar(mean_free_path):
        return 1.0+(mean_free_path/diameter)*(2.514+0.800*np.exp(-0.55*diameter/mean_free_path))
    else:
        return 1.0+(mean_free_path[None,:]/diameter)*(2.514+0.800*np.exp(-0.55*diameter/mean_free_path[None,:]))

def air_viscosity(temperature):
    return (174+0.433*(temperature-273))*1.0e-7

def diffusion(diameter,temperature,pressure):
    #Test, does it matter that there was 3.14 instead of pi.  
    return (particle_diffusivity*temperature*cunningham_correction(diameter,temperature,pressure))/(3*3.14*air_viscosity(temperature)*diameter)

def tube_loss(diameter, temperature, pressure, pipe_length, pipe_flow):
    
    rmuu=pi*diffusion(diameter,temperature,pressure)*pipe_length/pipe_flow

    region1 = rmuu < 0.02
    region2 = ~region1

    loss_factor = np.zeros_like(rmuu)

    loss_factor[region1] = 1-2.56*rmuu[region1]**(2/3)+1.2*rmuu[region1]+0.177*rmuu[region1]**(4/3)

    loss_factor[region2] = 0.819*np.exp(-3.657*rmuu[region2])+0.097*np.exp(-22.3*rmuu[region2])+0.032*np.exp(-57*rmuu[region2])

    return loss_factor

def diameters_from_mobility(mobility,temperature,pressure):
    #Calculate diameter from mobility
    #Numerical solution since it is not analytically solvable
    #Assumes singly charged stuff

    actual_shape = mobility.shape

    def diameter(d_vector, mobility, temperature, pressure):
        d = d_vector.reshape(actual_shape)
        difference = constants.e*cunningham_correction(d,temperature,pressure)[None,:]/(3*pi*air_viscosity(temperature)[None,:]*mobility)
        result = difference.ravel()

        return result

    #initial guess
    dp= constants.e*cunningham_correction(1E-8,temperature,pressure)/(3*pi*air_viscosity(temperature)[None,:]*mobility)
    dp_in = dp.ravel()   

    #So fixed_point only accepts 1d initial values & output
    vector = fixed_point(diameter, dp_in, xtol=1e-11, maxiter=1000, args=(mobility, temperature, pressure))

    return vector.reshape(actual_shape)

def original_diameters_from_mobility(mobility, temperature, pressure):

    dpt = np.ones_like(mobility)
    dp = 1E-9 * dpt

    while np.max(np.abs(dp-dpt)/dpt) > 1E-6:
        dp = dpt
        dpt = constants.e * cunningham_correction(dp, temperature, pressure)/ (3*pi*air_viscosity(temperature)*mobility)

    return dpt