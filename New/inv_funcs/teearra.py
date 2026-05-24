import numpy as np
from scipy.special import erf
from .cunn import cunn
from .visc import visc
import scipy.constants as sc



def teearra(p, dp, t, press, voltage, pituus, arkaksi, aryksi, qa, qc, qm, qs):
    # Transfer functions according to Stolzenburg:
    # Triangle functions + correction for diffusion
    # p : 1-D array of charge numbers, shape (n_p,)
    # dp: 1-D array of particle diameters [m], shape (n_dp,)

    dp = np.atleast_1d(dp)
    p = np.atleast_1d(p)

    beta = (qs + qa) / (qm + qc)
    delta = -(qs - qa) / (qs + qa)

    gammas = (aryksi / arkaksi) ** 2
    gkappa = pituus * arkaksi / (arkaksi ** 2 - aryksi ** 2)

    gammai = (0.25 * (1 - gammas ** 2) * (1 - gammas) ** 2
              + (5 / 18) * (1 - gammas ** 3) * (1 - gammas) * np.log(gammas)
              + (1 / 12) * (1 - gammas ** 4) * np.log(gammas) ** 2) / \
             ((1 - gammas)
              * (-0.5 * (1 + gammas) * np.log(gammas) - (1 - gammas)) ** 2)

    gabeta = (4.0 * (1 + beta) ** 2
              * (gammai + 1.0 / (2 * (1 + beta) * gkappa) ** 2)) / (1 - gammas)

    # Mobility of each particle: shape (n_dp,)
    mob = sc.elementary_charge * cunn(dp, t, press) / (3 * np.pi * visc(t) * dp)

    # zeta[i, j] = mob[i] * p[j] ; shape (n_dp, n_p)
    zeta = np.outer(mob, p)

    # Dimensionless penetration parameter; shape (n_dp, n_p)
    # Note: log(aryksi/arkaksi) < 0 because inner radius < outer radius
    zetap = 4.0 * voltage * np.pi * pituus * zeta / ((qm + qc) * np.log(aryksi / arkaksi))

    # Diffusion broadening parameter; shape (n_dp, n_p)
    rhota = np.zeros((len(dp), len(p)))
    diffusion_factor = gabeta * np.log(aryksi / arkaksi) * sc.Boltzmann * t / (sc.elementary_charge * voltage)
    for i in range(len(p)):
        rhota[:, i] = np.sqrt((zetap[:, i] / p[i]) * diffusion_factor)

    def epsilon(x):
        # epsilon.m: e = -x*(1 - erf(x)) + (1/sqrt(pi))*exp(-x^2)
        return -x * (1.0 - erf(x)) + (1.0 / np.sqrt(np.pi)) * np.exp(-x * x)

    sq2_rho = np.sqrt(2) * rhota

    teea1 = rhota / (np.sqrt(2) * beta * (1.0 - delta)) * (
        epsilon(np.abs(zetap - (1 + beta))       / sq2_rho) +
        epsilon(np.abs(zetap - (1 - beta))       / sq2_rho) -
        epsilon(np.abs(zetap - (1 + beta * delta)) / sq2_rho) -
        epsilon(np.abs(zetap - (1 - beta * delta)) / sq2_rho)
    )

    teea2 = 1.0 / (2 * beta * (1.0 - delta)) * (
        np.abs(zetap - (1 + beta)) +
        np.abs(zetap - (1 - beta)) -
        np.abs(zetap - (1 + beta * delta)) -
        np.abs(zetap - (1 - beta * delta))
    )

    return teea2 + teea1