"""InkIt version — the ONE source of truth.

Read by app.py (window title, About), InkIt.spec (Windows exe version
resource) and the smoke tests. Bump only here. Semantic Versioning:
MAJOR.MINOR.PATCH[-beta.N].
"""
from __future__ import annotations

import re

__version__ = "0.9.0-beta.1"

APP_NAME = "InkIt"

_m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$", __version__)
if not _m:
    raise ValueError(f"Invalid semantic version: {__version__!r}")
MAJOR, MINOR, PATCH = int(_m.group(1)), int(_m.group(2)), int(_m.group(3))
PRERELEASE = _m.group(4) or ""
IS_BETA = PRERELEASE.startswith("beta")
# Build number for the 4-part Windows FileVersion: beta.N -> N, final release -> 0
BUILD = int(PRERELEASE.split(".")[-1]) if IS_BETA and PRERELEASE.split(".")[-1].isdigit() else 0
VERSION_TUPLE = (MAJOR, MINOR, PATCH, BUILD)


def display_version() -> str:
    """'Beta 0.9.0-beta.1' for betas, plain '1.0.0' otherwise."""
    return f"Beta {__version__}" if IS_BETA else __version__


def title_base() -> str:
    """Window-title prefix, e.g. 'InkIt Beta 0.9.0-beta.1'."""
    return f"{APP_NAME} {display_version()}"
