from pathlib import Path
from unittest.mock import patch

import pytest

from benchmarks import dpf_smoke, mapdl_fixture, phase0_reader_checks


@pytest.mark.parametrize(
    "check",
    [
        lambda: dpf_smoke.run_benchmark(None),
        lambda: mapdl_fixture.verify(Path("unused.rst"), None),
        lambda: phase0_reader_checks.check_load_step_substep_mapping(Path("unused.rst"), None),
        lambda: phase0_reader_checks.check_temperature_units_and_conversion(
            Path("unused.rst"), None
        ),
        lambda: phase0_reader_checks.check_pyvista_mesh_access(Path("unused.rst"), None),
    ],
)
def test_missing_server_does_not_create_model(check):
    with patch.object(dpf_smoke.dpf, "Model") as model:
        with pytest.raises(ValueError, match="automatic startup is disabled"):
            check()
        model.assert_not_called()


def test_repeated_checks_reuse_existing_server():
    server = object()
    with (
        patch("sys.argv", ["dpf_smoke.py", "--repeat", "2"]),
        patch.object(dpf_smoke.dpf, "connect_to_server", return_value=server) as connect,
        patch.object(dpf_smoke, "run_benchmark", return_value={}) as benchmark,
    ):
        dpf_smoke.main()
    connect.assert_called_once()
    config = connect.call_args.kwargs["config"]
    assert config.protocol == "gRPC"
    assert config.legacy is True
    assert config.grpc_mode == "insecure"
    assert benchmark.call_count == 2
    for call in benchmark.call_args_list:
        assert call.args == (server,)
