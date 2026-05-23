import numpy as np
from .. import constants


# Wiedensohler coefficients indexed by [sign_index, abs_charge-1, polynomial_power]
# sign_index 0 = positive p(1) > 0, sign_index 1 = negative p(1) < 0
_ALFA = {
    'pos': np.array([
        [-2.3484,  0.6044,  0.4800,  0.0013, -0.1553,  0.0320],   # |p| = 1
        [-44.4756, 79.3772, -62.89, 26.4492, -5.748,   0.5049],    # |p| = 2
    ]),
    'neg': np.array([
        [-2.3197,  0.6175,  0.6201, -0.1105, -0.1260,  0.0297],   # |p| = 1
        [-26.3328, 35.9044, -21.4608,  7.0867, -1.3088,  0.1051],  # |p| = 2
    ]),
}


def varaus(dp, p, temp):
    """
    Charging efficiency (Wiedensohler for |p|<=2, Gunn for |p|>=3).

    dp   : 1-D array of particle diameters [m], shape (n_dp,)
    p    : 1-D array of charge numbers (integers), shape (n_p,)
    temp : scalar temperature [K]

    Returns coeff of shape (n_dp, n_p).
    """
    dp = np.atleast_1d(np.asarray(dp, dtype=float))
    p = np.atleast_1d(np.asarray(p, dtype=float))

    n_dp = len(dp)
    n_p = len(p)

    coeff = np.zeros((n_dp, n_p))

    # Determine sign from first element of p
    alfa = _ALFA['pos'] if p[0] > 0 else _ALFA['neg']
    sign_p = np.sign(p[0])

    ldp = np.log10(dp / 1e-9)  # log10(dp / nm)

    # Wiedensohler polynomial for |p| = 1 and 2
    n_wied = min(2, n_p)
    for charge_idx in range(n_wied):
        coefft = np.zeros(n_dp)
        for k in range(6):
            coefft += alfa[charge_idx, k] * ldp ** k
        coeff[:, charge_idx] = 10.0 ** coefft

    # For large particles (dp > 1 µm) use Gunn theory for |p| = 1, 2
    iil = dp > 1e-6
    if np.any(iil):
        for charge_idx in range(n_wied):
            i_abs = charge_idx + 1  # |p| value
            coe = (2.0 * np.pi * constants.eo * dp[iil] * constants.boltz * temp) / constants.e ** 2
            coeff[iil, charge_idx] = (
                (1.0 / np.sqrt(coe * 2.0 * np.pi))
                * np.exp(-(-sign_p * i_abs - coe * 0.1335) ** 2 / (2.0 * coe))
            )

    # Gunn theory for |p| >= 3
    for charge_idx in range(2, n_p):
        i_abs = charge_idx + 1  # |p| value
        coe = (2.0 * np.pi * constants.eo * dp * constants.boltz * temp) / constants.e ** 2
        coeff[:, charge_idx] = (
            (1.0 / np.sqrt(coe * 2.0 * np.pi))
            * np.exp(-(-sign_p * i_abs - coe * 0.1335) ** 2 / (2.0 * coe))
        )

    return coeff
