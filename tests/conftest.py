"""Shared live-DPF fixture for opt-in integration tests."""

import os

import pytest

from cassy_fatigue.cli import dpf_server


@pytest.fixture(scope="session")
def dpf_live_server():
    """One DPF server for the whole session.

    With ``DPF_IP``/``DPF_PORT`` set, connect to that existing server and leave it running.
    Otherwise start a local server (Ansys found by DPF, e.g. via ``AWP_ROOT242``) and shut
    it down when the session ends, also on failures.
    """
    if os.environ.get("CASSY_DPF_INTEGRATION") != "1":
        pytest.skip("Set CASSY_DPF_INTEGRATION=1 to run live DPF integration")
    port = os.environ.get("DPF_PORT")
    with dpf_server(os.environ.get("DPF_IP"), int(port) if port else None, None) as (server, _):
        yield server
