from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import yaml
from test_config import configuration

from cassy_fatigue import cli
from cassy_fatigue import pipeline as pipeline_module


def write_config(tmp_path, delta=False):
    path = tmp_path / "config.yaml"
    raw = configuration()
    if delta:
        raw["fatigue_events"].append({"id": "secondary", "ncycles": 10, "loads": ["hot"]})
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def no_endpoint_env(monkeypatch):
    monkeypatch.delenv("DPF_IP", raising=False)
    monkeypatch.delenv("DPF_PORT", raising=False)


@contextmanager
def pipeline(evaluate=None):
    with (
        patch.object(cli, "load_materials", return_value=({}, {})),
        patch.object(pipeline_module, "DPFReader") as reader,
        patch.object(pipeline_module, "evaluate", side_effect=evaluate, return_value=()),
        patch.object(pipeline_module, "write_outputs", return_value={}) as write,
    ):
        yield reader, write


def check_legacy(call):
    config = call.kwargs["config"]
    assert (config.protocol, config.legacy, config.grpc_mode) == ("gRPC", True, "insecure")
    assert call.kwargs["as_global"] is False


@pytest.mark.parametrize("delta", [False, True])
def test_explicit_endpoint_connects_and_leaves_server_running(tmp_path, delta):
    server = Mock()
    with (
        pipeline() as (reader, write),
        patch.object(cli.dpf, "connect_to_server", return_value=server) as connect,
        patch.object(cli.dpf, "start_local_server") as start,
    ):
        assert cli.main(["run", str(write_config(tmp_path, delta)), "--dpf-port", "50060"]) == 0
    start.assert_not_called()
    connect.assert_called_once()
    assert connect.call_args.kwargs["port"] == 50060
    assert connect.call_args.kwargs["ip"] == "127.0.0.1"
    check_legacy(connect.call_args)
    assert reader.call_args.args[1] is server
    server.shutdown.assert_not_called()
    assert "existing server" in write.call_args.args[-1]


def test_environment_endpoint_selects_connect_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("DPF_IP", "10.0.0.5")
    with (
        pipeline(),
        patch.object(cli.dpf, "connect_to_server") as connect,
        patch.object(cli.dpf, "start_local_server") as start,
    ):
        assert cli.main(["run", str(write_config(tmp_path))]) == 0
    start.assert_not_called()
    assert (connect.call_args.kwargs["ip"], connect.call_args.kwargs["port"]) == ("10.0.0.5", 50054)


def test_no_endpoint_starts_local_server_and_shuts_it_down(tmp_path):
    server = Mock(ip="127.0.0.1", port=50055)
    with (
        pipeline() as (reader, write),
        patch.object(cli.dpf, "connect_to_server") as connect,
        patch.object(cli.dpf, "start_local_server", return_value=server) as start,
    ):
        assert cli.main(["run", str(write_config(tmp_path)), "--ansys-path", "/opt/ansys"]) == 0
    connect.assert_not_called()
    start.assert_called_once()
    check_legacy(start.call_args)
    assert start.call_args.kwargs["ansys_path"] == str(Path("/opt/ansys"))
    assert start.call_args.kwargs["use_docker_by_default"] is False
    assert reader.call_args.args[1] is server
    server.shutdown.assert_called_once()
    assert write.call_args.args[-1].startswith("127.0.0.1:50055 (started")


@pytest.mark.parametrize("error", [RuntimeError("DPF read failed"), KeyboardInterrupt()])
def test_started_server_is_shut_down_when_processing_fails(tmp_path, error):
    server = Mock(ip="127.0.0.1", port=50054)

    def fail(*args):
        raise error

    with (
        pipeline(evaluate=fail),
        patch.object(cli.dpf, "start_local_server", return_value=server),
    ):
        if isinstance(error, KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                cli.main(["run", str(write_config(tmp_path))])
        else:
            assert cli.main(["run", str(write_config(tmp_path))]) == 1
    server.shutdown.assert_called_once()


def test_shutdown_failure_does_not_mask_success(tmp_path, caplog):
    server = Mock(ip="127.0.0.1", port=50054)
    server.shutdown.side_effect = RuntimeError("already gone")
    with pipeline(), patch.object(cli.dpf, "start_local_server", return_value=server):
        assert cli.main(["run", str(write_config(tmp_path))]) == 0
    assert "Could not shut down" in caplog.text


@pytest.mark.parametrize(
    "args", [["--dpf-port", "0"], ["--dpf-port", "50054", "--ansys-path", "/opt"]]
)
def test_invalid_endpoint_options(tmp_path, args):
    with pytest.raises(SystemExit):
        cli.main(["run", str(write_config(tmp_path)), *args])


def test_invalid_environment_port_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DPF_PORT", "not-a-port")
    with pytest.raises(SystemExit):
        cli.main(["run", str(write_config(tmp_path))])


def test_invalid_config_fails_before_connect(tmp_path, caplog):
    path = tmp_path / "config.yaml"
    path.write_text("code: not-a-code\n", encoding="utf-8")
    with (
        patch.object(cli.dpf, "connect_to_server") as connect,
        patch.object(cli.dpf, "start_local_server") as start,
    ):
        assert cli.main(["run", str(path)]) == 1
    connect.assert_not_called()
    start.assert_not_called()
    assert "missing keys" in caplog.text


def test_invalid_event_fails_before_connect(tmp_path, caplog):
    raw = configuration()
    raw["fatigue_events"] = [
        {
            "id": "pulse",
            "ncycles": 100,
            "loads": ["missing"],
        }
    ]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with (
        patch.object(cli.dpf, "connect_to_server") as connect,
        patch.object(cli.dpf, "start_local_server") as start,
    ):
        assert cli.main(["run", str(path)]) == 1
    connect.assert_not_called()
    start.assert_not_called()
    assert "unknown load" in caplog.text
