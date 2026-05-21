"""A file with a cyclic import for cycle detection tests.

app.py -> cycle_a.py -> cycle_b.py -> cycle_a.py
"""

from .cycle_b import forward_to_a
import os as _os


def app_main():
    result = forward_to_a()
    return _os.getcwd()
