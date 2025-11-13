"""
Lightweight replacement for :mod:`distutils.util` providing only ``strtobool``.

The stdlib ``distutils`` module was deprecated and eventually removed from the
Python runtime used in AWS Lambda. Some third-party libraries (e.g. PyAthena)
still import ``distutils.util.strtobool``. Instead of pulling the entire legacy
package back into the deployment artefact, we provide the single helper they
require.
"""

from __future__ import annotations

__all__ = ["strtobool"]


def strtobool(val: str) -> int:
    """
    Convert a string representation of truth to ``True`` (1) or ``False`` (0).

    Mirrors the behaviour of ``distutils.util.strtobool`` so that dependencies
    expecting that function continue to work without bundling the full
    ``distutils`` package.
    """

    val_lower = val.lower()
    if val_lower in {"y", "yes", "t", "true", "on", "1"}:
        return 1
    if val_lower in {"n", "no", "f", "false", "off", "0"}:
        return 0
    raise ValueError(f"invalid truth value {val!r}")
