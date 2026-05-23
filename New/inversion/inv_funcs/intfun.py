import numpy as np
from .ltubefl import ltubefl
from .cpc_loss import cpc_loss1, cpc_loss2
from .dmps_loss import dmps_loss1, dmps_loss2
from .teearra import teearra
from .varaus import varaus
from .gunn_woessner_modified import gunn_woessner_modified
from .calChargeFracF import calChargeFracF


def intfun(dp, t, press, p, volt, pituus, arkaksi, aryksi, qa, qc, qm, qs,
           pipelength, pipeflow, lsys, Zp, Zn, Mrp, Mrn, Np, Nn,
           charging_efficiency, summed):
    """
    Main function for transfer function calculations.

    dp   : scalar or array, integration variable on log10 scale
    Returns a scalar when dp is scalar (as required by scipy.integrate.quad),
    or a 1-D array when dp is an array (for vectorised callers).
    """
    scalar_input = np.ndim(dp) == 0
    dp = np.atleast_1d(10.0 ** np.asarray(dp, dtype=float))

    # Laminar flow tube losses
    tubeloss = ltubefl(dp, pipelength, pipeflow, t, press)

    if lsys == 1:
        cpcloss = cpc_loss1(dp, t, press)
    else:
        cpcloss = cpc_loss2(dp, t, press)

    if lsys == 1:
        dmaloss = dmps_loss1(dp, qa, t, press)
    else:
        dmaloss = dmps_loss2(dp, qa, t, press)

    totalloss = np.atleast_1d(tubeloss * cpcloss * dmaloss)

    # Transfer-function triangles; shape (n_dp, n_p)
    tr = teearra(p, dp, t, press, volt, pituus, arkaksi, aryksi, qa, qc, qm, qs)

    # Charging efficiency; shape (n_dp, n_p)
    p = np.atleast_1d(p)
    if charging_efficiency == 'wiedensohler':
        if summed == 1:
            charge = varaus(dp, -p, t) + varaus(dp, p, t)
        else:
            charge = varaus(dp, p, t)

    elif charging_efficiency == 'gunn woessner mod':
        charge = np.zeros((len(dp), len(p)))
        for i in range(len(p)):
            charge[:, i] = gunn_woessner_modified(
                p[i], dp, t, Zp, Zn, Mrp, Mrn, Np, Nn, summed)

    elif charging_efficiency == 'fuchs':
        charge = np.zeros((len(dp), len(p)))
        tmp = np.zeros((len(dp), 2 * len(p) + 1))
        for i in range(len(dp)):
            tmp = calChargeFracF(dp[i], len(p), Zp, Zn, Mrp, Mrn, Np, Nn, 1000, t, press)
        if p[0] < 0:
            charge = tmp[:, 0:len(p)]
        else:
            charge = tmp[:, len(p):]
    else:
        raise ValueError(
            'charging_efficiency not recognised. Most probably a spelling mistake.')

    # Sum over charges, then multiply by losses; res shape (n_dp,)
    res = np.sum(tr * charge, axis=1) * totalloss

    if scalar_input:
        return float(res[0])
    return res
