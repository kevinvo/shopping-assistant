from __future__ import annotations

import sys
import types

__all__ = ["install_distutils_shim"]


def _strtobool(val: str) -> int:
    """
    Convert string representations of truth to ``True`` (1) or ``False`` (0).

    This mirrors ``distutils.util.strtobool`` so that third-party packages such
    as PyAthena continue to work after the stdlib ``distutils`` module was
    removed in Python 3.12 (the runtime used by Lambda).
    """

    value = val.lower()
    if value in {"y", "yes", "t", "true", "on", "1"}:
        return 1
    if value in {"n", "no", "f", "false", "off", "0"}:
        return 0
    raise ValueError(f"invalid truth value {val!r}")


def install_distutils_shim() -> None:
    """
    Ensure ``distutils.util`` can be imported even though the stdlib package has
    been removed.

    If the real module is available we leave it untouched. Otherwise we inject a
    minimal replacement into ``sys.modules`` that provides the single function
    required by our dependencies.
    """

    try:
        import distutils.util  # type: ignore[import]
    except ModuleNotFoundError:
        distutils_module = types.ModuleType("distutils")
        util_module = types.ModuleType("distutils.util")
        util_module.strtobool = _strtobool  # type: ignore[attr-defined]
        distutils_module.util = util_module  # type: ignore[attr-defined]

        sys.modules["distutils"] = distutils_module
        sys.modules["distutils.util"] = util_module
    else:
        sys.modules.setdefault("distutils.util", distutils.util)  # type: ignore[attr-defined]
