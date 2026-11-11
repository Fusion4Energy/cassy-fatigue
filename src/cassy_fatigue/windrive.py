"""Temporary drive letters for Ansys installations on Windows network shares.

DPF starts its server by changing into ``<ansys>\\aisol\\bin\\winx64`` and running
``Ans.Dpf.Grpc.bat``. ``cmd.exe`` refuses a UNC working directory: it prints the path and a
"UNC paths are not supported" warning on stderr, which DPF reports as a startup error.
Mapping the share to a drive letter (what ``pushd`` does) avoids that. The mapping is
removed again when the context exits, including on errors.
"""

from __future__ import annotations

import logging
import os
import string
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PureWindowsPath

logger = logging.getLogger(__name__)

# Search from Z: downwards, like ``pushd``; A:/B: (floppy) and C: are never used.
CANDIDATE_LETTERS = tuple(reversed(string.ascii_uppercase[3:]))


def is_windows() -> bool:
    return os.name == "nt"


def unc_share(path: str) -> tuple[str, str] | None:
    """Split a UNC path into ``(\\\\server\\share, remainder)``; ``None`` if it is not UNC."""
    text = str(path).replace("/", "\\")
    if not text.startswith("\\\\") or text.startswith(("\\\\?\\", "\\\\.\\")):
        return None
    windows = PureWindowsPath(text)
    share = windows.drive
    if share.count("\\") != 3 or share.endswith("\\"):  # need \\server\share
        return None
    return share, str(windows.relative_to(windows.anchor))


def drive_in_use(letter: str) -> bool:
    return os.path.exists(f"{letter}:\\")


def _net_use(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["net", "use", *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _same_path(first: str, second: str) -> bool:
    def norm(path):
        return str(path).replace("/", "\\").rstrip("\\").casefold()

    return norm(first) == norm(second)


@contextmanager
def ansys_root_environment(original: str | None, mapped: str | None) -> Iterator[None]:
    """Temporarily set every ``AWP_ROOTxxx`` naming ``original`` to ``mapped``.

    The DPF server inherits the environment; left on the UNC path it may resolve its
    plugins there instead of on the mapped drive. Restored on exit (child processes
    keep the value they were started with).
    """
    if original is None or mapped is None or _same_path(original, mapped):
        yield
        return
    changed = {
        key: value
        for key, value in os.environ.items()
        if key.upper().startswith("AWP_ROOT") and _same_path(value, original)
    }
    for key in changed:
        os.environ[key] = mapped
        logger.info("Starting DPF with %s=%s", key, mapped)
    try:
        yield
    finally:
        os.environ.update(changed)


@contextmanager
def drive_letter_path(path: str | None) -> Iterator[str | None]:
    """Yield ``path`` with a UNC share replaced by a temporary drive letter (Windows only).

    Any other path (or ``None``, or any path on another OS) is yielded unchanged.
    """
    split = None if path is None or not is_windows() else unc_share(path)
    if split is None:
        yield path
        return
    share, remainder = split
    errors = []
    for letter in CANDIDATE_LETTERS:
        if drive_in_use(letter):
            continue
        result = _net_use(f"{letter}:", share, "/persistent:no")
        if result.returncode == 0:
            break
        # A disconnected persistent mapping looks free but is taken: try the next letter.
        errors.append(f"{letter}: {(result.stderr or result.stdout).strip()}")
    else:
        detail = "; ".join(errors[-3:]) or "no free drive letter"
        raise RuntimeError(
            f"Cannot map {share} to a drive letter to start DPF from {path} ({detail}). "
            "Map the share manually (net use X: <share>) and use the X: path as Ansys "
            "installation, or start DPF yourself and connect to it."
        )
    mapped = str(PureWindowsPath(f"{letter}:\\", remainder))
    logger.info("Mapped %s to %s: to start DPF from %s", share, letter, mapped)
    try:
        yield mapped
    finally:
        result = _net_use(f"{letter}:", "/delete", "/y")
        if result.returncode == 0:
            logger.info("Removed temporary drive %s: (%s)", letter, share)
        else:
            logger.warning(
                "Could not remove temporary drive %s: (%s): %s",
                letter,
                share,
                (result.stderr or result.stdout).strip(),
            )
