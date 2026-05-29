"""Python file in mixed project that imports a C header."""

from . import config as _cfg

print(f"Loaded config from {_cfg}")
