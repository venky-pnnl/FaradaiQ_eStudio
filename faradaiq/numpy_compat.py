"""Small NumPy compatibility helpers used by FaradaIQ Studio.

The managed FaradaIQ runtime pins NumPy 1.26.4 for compatibility with the
scientific stack.  ``numpy.trapezoid`` was introduced in NumPy 2.0, while
``numpy.trapz`` is the equivalent API in 1.x.  This helper keeps deterministic
integration working on both branches without mutating NumPy at import time.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def trapezoid(y: Any, x: Any = None, dx: float = 1.0, axis: int = -1):
    """Integrate with the composite trapezoidal rule across NumPy 1.x and 2.x.

    Preference order:
    1. ``numpy.trapezoid`` on NumPy 2.x.
    2. ``numpy.trapz`` on NumPy 1.x.
    3. A compact NumPy-only implementation as a final defensive fallback.
    """
    modern = getattr(np, "trapezoid", None)
    if callable(modern):
        return modern(y, x=x, dx=dx, axis=axis)

    legacy = getattr(np, "trapz", None)
    if callable(legacy):
        return legacy(y, x=x, dx=dx, axis=axis)

    values = np.asarray(y)
    if values.shape[axis] < 2:
        return np.sum(values, axis=axis) * 0.0

    if x is None:
        spacing = dx
    else:
        coords = np.asarray(x)
        if coords.ndim == 1:
            spacing = np.diff(coords)
            shape = [1] * values.ndim
            shape[axis] = spacing.shape[0]
            spacing = spacing.reshape(shape)
        else:
            spacing = np.diff(coords, axis=axis)

    left = [slice(None)] * values.ndim
    right = [slice(None)] * values.ndim
    left[axis] = slice(0, -1)
    right[axis] = slice(1, None)
    return np.sum((values[tuple(left)] + values[tuple(right)]) * 0.5 * spacing, axis=axis)
