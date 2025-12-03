"""Simple .env reader for lightweight configuration.

Supports KEY=VALUE pairs. Only minimal parsing; ignores comments and empty lines.
"""
from pathlib import Path
from typing import Optional


def _load_dotenv(path: Optional[str] = None):
    p = Path(path or Path.cwd() / '.env')
    data = {}
    try:
        text = p.read_text()
    except Exception:
        return data
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        k, v = line.split('=', 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


_ENV_CACHE = _load_dotenv()


def get_str(key: str, default: Optional[str] = None) -> Optional[str]:
    return _ENV_CACHE.get(key, default)


def get_float(key: str, default: float = 0.0) -> float:
    v = _ENV_CACHE.get(key)
    if v is None:
        return float(default)
    try:
        return float(v)
    except Exception:
        return float(default)


def get_int(key: str, default: int = 0) -> int:
    v = _ENV_CACHE.get(key)
    if v is None:
        return int(default)
    try:
        # Support hex values like 0x25
        return int(v, 0)
    except Exception:
        return int(default)
