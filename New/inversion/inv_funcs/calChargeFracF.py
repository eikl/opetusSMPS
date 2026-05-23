"""
Pure Python implementation of calChargeFracF from SteadyStateChargeFractionSolver.jl
Fuchs charging theory - all variables are in SI units
Based on XiaoTong Chen's doctoral dissertation
Runlong Cai, Sep. 17 2019 at Helsinki
Ported from Julia to Python by GitHub Copilot
"""

import numpy as np
from scipy.integrate import solve_ivp


# Physical constants
NA = 6.02214129e23  # Avogadro constant, in 1/mol
kB = 1.3806505e-23  # Boltzmann constant, in J/K
Rg = NA * kB  # Ideal gas constant, in J/(mol·K)
Mr = 28.959  # Relative molecular mass of air, dimensionless
amu = 1.66054e-27  # Atomic mass unit, in kg
e = 1.602176565e-19  # Elementary charge, in C
eps0 = 8.854187817e-12  # Vacuum Permittivity, in F/m


def cal_delta(a, lam):
    """Calculate delta parameter"""
    term1 = (1 + lam / a) ** 5 / 5
    term2 = (1 + lam**2 / a**2) * (1 + lam / a) ** 3 / 3
    term3 = 2 / 15 * (1 + lam**2 / a**2) ** 2.5
    delta = a**3 / lam**2 * (term1 - term2 + term3)
    return delta


def cal_U(r, q, a, a_i, epsp):
    """Electric potential"""
    term1 = (epsp - 1) / (epsp + 2)
    term2 = 2 * r**2 * (r**2 - a**2)
    U = e**2 / (4 * np.pi * eps0) * (q / r - term1 * a**3 / term2)
    return U


def cal_b(r, delta, q, a, a_i, epsp, T):
    """Impact parameter and collision probability"""
    term = 1 + 2 / (3 * kB * T) * (cal_U(delta, q, a, a_i, epsp) - cal_U(r, q, a, a_i, epsp))
    if term <= 0:
        b = 0
    else:
        b = r * np.sqrt(term)
    return b


def cal_bmin(delta, q, a, a_i, epsp, T):
    """Calculate minimum impact parameter"""
    r = np.linspace(a, delta, 1000)
    b = np.array([cal_b(r_i, delta, q, a, a_i, epsp, T) for r_i in r])
    bmin = np.min(b)
    return bmin


def cal_beta(delta, q, a, a_i, D, c, alpha, epsp, T):
    """Collision coefficient"""
    temp1 = np.exp(-cal_U(delta, q, a, a_i, epsp) / (kB * T))
    term2 = np.pi * alpha * c * delta**2 * temp1
    term3 = temp1 * alpha * c * delta**2 / (4 * D * a)
    x = np.linspace(0, a / delta, 10000)
    dx = x[1] - x[0]
    term4 = np.sum(np.exp(cal_U(a / x, q, a, a_i, epsp) / (kB * T))) * dx
    beta = term2 / (1 + term3 * term4)
    return beta


def cal_char_frac(t, y, params):
    """ODE function for charge fraction"""
    beta_p_tuple, beta_n_tuple, num_char_tuple, Np, Nn = params
    dydt = np.zeros_like(y)
    
    for jj in range(len(num_char_tuple)):
        if jj > 0:
            term1 = beta_p_tuple[jj - 1] * y[jj - 1] * Np  # catching a positive ion
        else:
            term1 = 0
        term2 = -beta_p_tuple[jj] * y[jj] * Np  # catching an additional positive ion
        if jj < len(num_char_tuple) - 1:
            term3 = beta_n_tuple[jj + 1] * y[jj + 1] * Nn  # catching a negative ion
        else:
            term3 = 0
        term4 = -beta_n_tuple[jj] * y[jj] * Nn  # catching an additional negative ion
        dydt[jj] = term1 + term2 + term3 + term4
    
    return dydt


def calChargeFracF(dp, Zp, Zn, Mrp, Mrn, Np=1e13, Nn=1e13, epsp=1000, T=293, P=101325):
    """
    Calculate charge fraction using Fuchs charging theory
    
    Parameters:
    -----------
    dp : float
        Particle diameter in meters
    Zp : float
        Positive ion mobility in m²/(V·s)
    Zn : float
        Negative ion mobility in m²/(V·s)
    Mrp : float
        Positive ion molecular weight in g/mol
    Mrn : float
        Negative ion molecular weight in g/mol
    Np : float, optional
        Positive ion concentration in m⁻³ (default: 1e13)
    Nn : float, optional
        Negative ion concentration in m⁻³ (default: 1e13)
    epsp : float, optional
        Relative permittivity (default: 1000)
    T : float, optional
        Temperature in K (default: 293)
    P : float, optional
        Pressure in Pa (default: 101325)
    
    Returns:
    --------
    tuple
        (char_fracF, beta_p_tuple, beta_n_tuple) where:
        - char_fracF: array of charge fractions for charges from -5 to +5
        - beta_p_tuple: array of positive collision coefficients
        - beta_n_tuple: array of negative collision coefficients
    """
    
    # basic inputs
    ma = Mr * amu  # Mass of background air
    max_char = 5
    num_char_tuple = np.arange(-max_char, max_char + 1)  # Number of charge carried by a particle
    char_fracF = np.zeros(len(num_char_tuple))

    # Viscosity of air
    eta0 = 18.46 * 1e-6  # Pa s
    T0 = 25 + 273  # K
    S = 110  # for air
    eta = eta0 * (T / T0) ** (3 / 2) * (T0 + S) / (T + S)

    # calculation starts here
    mp = Mrp * amu  # Ion mass
    mn = Mrn * amu
    Dp = kB * T * Zp / e  # Diffusion coefficient
    Dn = kB * T * Zn / e
    cp = np.sqrt(8 * kB * T / np.pi / mp)  # Mean thermal speed of ion
    cn = np.sqrt(8 * kB * T / np.pi / mn)
    lambdap = 16 * np.sqrt(2) / 3 / np.pi * Dp / cp * np.sqrt(ma / (ma + mp))  # mean free path of ion
    lambdan = 16 * np.sqrt(2) / 3 / np.pi * Dn / cn * np.sqrt(ma / (ma + mn))  # mean free path of ion

    a_p = dp / 2  # Particle radius
    a_g = ((ma * T * kB) / (16 * np.pi**3 * eta**2)) ** (1 / 4)  # Gas radius
    a_i_p = -a_g + (3 * (1 + mp / ma)**0.5 * cp * kB * T / (8 * P * Dp))  # ion pos radius
    a_i_n = -a_g + (3 * (1 + mn / ma)**0.5 * cn * kB * T / (8 * P * Dn))  # ion neg radius
    
    # Radius of the limiting sphere
    delta_p = cal_delta(a_p, lambdap)
    delta_n = cal_delta(a_p, lambdan)

    bminp_tuple = np.array([cal_bmin(delta_p, q, a_p, a_i_p, epsp, T) for q in num_char_tuple])
    bminn_tuple = np.array([cal_bmin(delta_n, q, a_p, a_i_n, epsp, T) for q in num_char_tuple])
    alpha_p_tuple = (bminp_tuple / delta_p) ** 2
    alpha_n_tuple = (bminn_tuple / delta_n) ** 2

    beta_p_tuple = np.zeros(len(num_char_tuple))
    beta_n_tuple = np.zeros(len(num_char_tuple))
    for ii in range(len(num_char_tuple)):
        beta_p_tuple[ii] = cal_beta(delta_p, num_char_tuple[ii], a_p, a_i_p, Dp, cp, alpha_p_tuple[ii], epsp, T)
        beta_n_tuple[ii] = cal_beta(delta_n, num_char_tuple[ii], a_p, a_i_n, Dn, cn, alpha_n_tuple[ii], epsp, T)

    # calculate charge fraction, a stupid iteration approach
    char_frac_tuple = np.zeros(len(num_char_tuple))
    char_frac_tuple[max_char] = 1  # all particles are neutral at the beginning (index max_char corresponds to charge 0)

    # tmax = 1e13/min(Np, Nn); # ISO15200:2009(E)
    tmax = 1e13 / min(Np, Nn)
    dt = tmax / 100
    t_span = (0.0, tmax)
    
    params = (beta_p_tuple, beta_n_tuple, num_char_tuple, Np, Nn)
    
    # Solve the ODE
    sol = solve_ivp(
        fun=lambda t, y: cal_char_frac(t, y, params),
        t_span=t_span,
        y0=char_frac_tuple,
        method='RK45',
        t_eval=np.arange(0, tmax + dt, dt),
        rtol=1e-9,
        atol=1e-14
    )

    char_fracF[:] = sol.y[:, -1]
    return char_fracF, beta_p_tuple, beta_n_tuple


if __name__ == "__main__":
    # Example usage
    d_tuple = np.array([0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.009,
                        0.010, 0.020, 0.030, 0.040, 0.050, 0.060, 0.070, 0.080, 0.090,
                        0.100, 0.200, 0.300, 0.400, 0.500, 0.600, 0.700, 0.800, 0.900,
                        1.000, 2.000, 3.00])
    d_tuple = 2 * d_tuple
    d_tuple = d_tuple * 1e-6

    Zp = 1.2e-4
    Zn = 1.35e-4
    Mrp = 150
    Mrn = 90

    charFracFn = [calChargeFracF(dp, Zp, Zn, Mrp, Mrn)[0][5] for dp in d_tuple]
    charFracFp = [calChargeFracF(dp, Zp, Zn, Mrp, Mrn)[0][7] for dp in d_tuple]

    print("Negative charge fractions:", charFracFn)
    print("Positive charge fractions:", charFracFp)