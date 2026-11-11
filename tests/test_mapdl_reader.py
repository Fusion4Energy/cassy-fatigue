from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from cassy_fatigue import cli


def names(*sequence):
    return patch.object(cli, "_has_reader", side_effect=list(sequence))


def test_reader_present_loads_nothing():
    with names(True), patch.object(cli.dpf, "load_library") as load:
        cli.ensure_mapdl_reader(Mock(), "/opt/ansys")
    load.assert_not_called()


def test_unlistable_server_is_not_checked():
    with names(None), patch.object(cli.dpf, "load_library") as load:
        cli.ensure_mapdl_reader(Mock(), "/opt/ansys")
    load.assert_not_called()


@pytest.mark.parametrize(
    ("windows", "plugin"),
    [
        (True, Path("Y:/v242/dpf/bin/winx64/mapdlOperatorsCore.dll")),
        (False, Path("Y:/v242/dpf/bin/linx64/libmapdlOperatorsCore.so")),
    ],
)
def test_missing_reader_loads_plugin(windows, plugin):
    server = Mock()
    with (
        names(False, True),
        patch.object(cli.windrive, "is_windows", return_value=windows),
        patch.object(cli.dpf, "load_library") as load,
    ):
        cli.ensure_mapdl_reader(server, "Y:/v242")
    load.assert_called_once_with(str(plugin), "mapdl_operators", server=server)


def test_plugin_load_failure_is_explained():
    with (
        names(False),
        patch.object(cli.dpf, "load_library", side_effect=RuntimeError("dll")),
        pytest.raises(RuntimeError, match="did not load its MAPDL plugin.*dll"),
    ):
        cli.ensure_mapdl_reader(Mock(), "/opt/ansys")


def test_reader_still_missing_after_load():
    with (
        names(False, False),
        patch.object(cli.dpf, "load_library"),
        pytest.raises(RuntimeError, match="still has no result_info_provider"),
    ):
        cli.ensure_mapdl_reader(Mock(), "/opt/ansys")


def test_failed_check_shuts_started_server_down():
    server = Mock(ip="h", port=1)
    with (
        patch.object(cli.dpf, "start_local_server", return_value=server),
        patch.object(cli, "ensure_mapdl_reader", side_effect=RuntimeError("no reader")),
        pytest.raises(RuntimeError, match="no reader"),
        cli.dpf_server(None, None, None),
    ):
        pass
    server.shutdown.assert_called_once()


def test_has_reader_distinguishes_missing_from_unknown():
    missing = cli.dpf_errors.DPFServerException("Source operator not found")
    with patch.object(cli.dpf, "Operator", side_effect=missing):
        assert cli._has_reader(Mock()) is False
    with patch.object(cli.dpf, "Operator", side_effect=TypeError("mock")):
        assert cli._has_reader(Mock()) is None
    with patch.object(cli.dpf, "Operator"):
        assert cli._has_reader(Mock()) is True
