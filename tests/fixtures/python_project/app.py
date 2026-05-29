"""A file with a cyclic import for cycle detection tests.

app.py -> cycle_a.py -> cycle_b.py -> cycle_a.py
"""

import os as _os

from .cycle_b import forward_to_a


def app_main():
    _ = forward_to_a()
    return _os.getcwd()
