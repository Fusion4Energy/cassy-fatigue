"""Nodal fatigue post-processing for Ansys results using CASSY rules."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cassy-fatigue")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0+unknown"
