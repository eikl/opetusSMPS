"""Physical constants used by the inversion code."""
from types import SimpleNamespace

constants = SimpleNamespace(
    e=1.602176634e-19,        # elementary charge [C]
    Boltzmann=1.380649e-23,   # Boltzmann constant [J/K]
    R=8.314462618,            # molar gas constant [J/(mol·K)]
    Avogadro=6.02214076e23,   # Avogadro number [1/mol]
    epsilon_0=8.8541878128e-12,  # vacuum permittivity [F/m]
)