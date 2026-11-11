"""One DPF server per GUI session, with the same ownership policy as the CLI.

Connecting to an endpoint never stops that server; a server started here is shut down
by :meth:`DPFSession.close` (called on window close, with an ``atexit`` safety net).
"""

from __future__ import annotations

import atexit
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from cassy_fatigue.cli import DEFAULT_DPF_IP, DEFAULT_DPF_PORT, dpf_server


@dataclass(frozen=True)
class ServerSettings:
    connect: bool = False  # False: start a local server; True: connect to ip:port
    ip: str = DEFAULT_DPF_IP
    port: int = DEFAULT_DPF_PORT
    ansys_path: Path | None = None

    def arguments(self) -> tuple[str | None, int | None, Path | None]:
        if self.connect:
            return self.ip, self.port, None
        return None, None, self.ansys_path


class DPFSession:
    def __init__(self, settings: ServerSettings | None = None):
        self._settings = settings or ServerSettings()
        self._stack: ExitStack | None = None
        self._server = None
        self.endpoint = ""
        self._lock = threading.RLock()
        atexit.register(self.close)

    @property
    def settings(self) -> ServerSettings:
        return self._settings

    @property
    def active(self) -> bool:
        return self._server is not None

    def configure(self, settings: ServerSettings) -> None:
        """Change settings; an open session is closed and reopened lazily on next use."""
        with self._lock:
            if settings != self._settings:
                self.close()
                self._settings = settings

    def server(self):
        """The session's server, connecting or starting it on first use."""
        with self._lock:
            if self._server is None:
                stack = ExitStack()
                try:
                    self._server, self.endpoint = stack.enter_context(
                        dpf_server(*self._settings.arguments())
                    )
                except BaseException:
                    stack.close()
                    raise
                self._stack = stack
            return self._server

    def close(self) -> None:
        with self._lock:
            stack, self._stack, self._server, self.endpoint = self._stack, None, None, ""
            if stack is not None:
                stack.close()
