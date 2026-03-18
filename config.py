"""INI-based configuration reader.

Reads settings from config.ini using configparser.  All keys across all
sections are flattened into a single lookup so that existing callers can
continue using flat key names (e.g. ``get_str('CPC_TYPE')``).

Exported helpers (unchanged API):
    get_str(key, default)  -> Optional[str]
    get_float(key, default) -> float
    get_int(key, default)  -> int
"""
import configparser
from pathlib import Path
from typing import Optional


def _load_config(path: Optional[str] = None) -> dict[str, str]:
    p = Path(path or Path(__file__).resolve().parent / 'config.ini')
    cp = configparser.ConfigParser()
    cp.read(str(p))
    # Flatten all sections into one dict; later sections override earlier ones
    # on key collision, matching the old .env "last value wins" behaviour.
    data: dict[str, str] = {}
    for section in cp.sections():
        for key, value in cp.items(section):
            data[key.upper()] = value
    return data


_CFG_CACHE = _load_config()


def reload() -> None:
    """Re-read config.ini and refresh the in-memory cache."""
    global _CFG_CACHE
    _CFG_CACHE = _load_config()


def get_str(key: str, default: Optional[str] = None) -> Optional[str]:
    return _CFG_CACHE.get(key, default)


def get_float(key: str, default: float = 0.0) -> float:
    v = _CFG_CACHE.get(key)
    if v is None:
        return float(default)
    try:
        return float(v)
    except Exception:
        return float(default)


def get_int(key: str, default: int = 0) -> int:
    v = _CFG_CACHE.get(key)
    if v is None:
        return int(default)
    try:
        # Support hex values like 0x25
        return int(v, 0)
    except Exception:
        return int(default)
