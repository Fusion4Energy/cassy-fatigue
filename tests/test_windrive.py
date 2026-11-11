import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from cassy_fatigue import cli, windrive

UNC = r"\\f4eda.local\STOR\ANSYS202x\ANSYS2024R2\v242"


def done(code=0, stderr=""):
    return subprocess.CompletedProcess([], code, stdout="", stderr=stderr)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (UNC, (r"\\f4eda.local\STOR", r"ANSYS202x\ANSYS2024R2\v242")),
        ("//server/share/v242", (r"\\server\share", "v242")),
        (r"\\server\share", (r"\\server\share", ".")),
        (r"C:\Program Files\ANSYS Inc\v242", None),
        ("/opt/ansys", None),
        (r"\\?\C:\ansys", None),
        (r"\\server", None),
    ],
)
def test_unc_share(path, expected):
    assert windrive.unc_share(path) == expected


def test_not_windows_or_not_unc_is_unchanged():
    with patch.object(windrive, "_net_use") as net:
        with (
            patch.object(windrive, "is_windows", return_value=False),
            windrive.drive_letter_path(UNC) as path,
        ):
            assert path == UNC
        with patch.object(windrive, "is_windows", return_value=True):
            for local in (None, r"C:\ansys\v242"):
                with windrive.drive_letter_path(local) as path:
                    assert path == local
    net.assert_not_called()


def test_maps_free_letter_and_always_unmaps():
    calls = []

    def net(*arguments):
        calls.append(arguments)
        # Y: looks free but is a disconnected persistent mapping.
        return done(2, "System error 85") if arguments[0] == "Y:" and len(calls) == 1 else done()

    with (
        patch.object(windrive, "is_windows", return_value=True),
        patch.object(windrive, "drive_in_use", side_effect=lambda letter: letter == "Z"),
        patch.object(windrive, "_net_use", side_effect=net),
        pytest.raises(RuntimeError, match="boom"),
        windrive.drive_letter_path(UNC) as path,
    ):
        assert path == r"X:\ANSYS202x\ANSYS2024R2\v242"
        raise RuntimeError("boom")
    assert calls == [
        ("Y:", r"\\f4eda.local\STOR", "/persistent:no"),
        ("X:", r"\\f4eda.local\STOR", "/persistent:no"),
        ("X:", "/delete", "/y"),
    ]


def test_no_letter_available_explains_workaround():
    with (
        patch.object(windrive, "is_windows", return_value=True),
        patch.object(windrive, "drive_in_use", return_value=False),
        patch.object(windrive, "_net_use", return_value=done(2, "Access is denied.")),
        pytest.raises(RuntimeError, match="Access is denied.*connect to it"),
        windrive.drive_letter_path(UNC),
    ):
        pass


@pytest.mark.parametrize("explicit", [True, False])
def test_local_server_started_from_mapped_drive(explicit):
    server = Mock(ip="127.0.0.1", port=50054)
    calls = []
    with (
        patch.object(windrive, "is_windows", return_value=True),
        patch.object(windrive, "drive_in_use", return_value=False),
        patch.object(windrive, "_net_use", side_effect=lambda *a: calls.append(a) or done()),
        patch.object(cli.dpf_misc, "get_ansys_path", return_value=UNC) as resolve,
        patch.object(cli.dpf, "start_local_server", return_value=server) as start,
    ):
        with cli.dpf_server(None, None, Path(UNC) if explicit else None):
            assert calls == [("Z:", r"\\f4eda.local\STOR", "/persistent:no")]
        assert start.call_args.kwargs["ansys_path"] == r"Z:\ANSYS202x\ANSYS2024R2\v242"
        server.shutdown.assert_called_once()
        assert calls[-1] == ("Z:", "/delete", "/y")
        assert resolve.called is not explicit


def test_failed_start_still_unmaps():
    calls = []
    with (
        patch.object(windrive, "is_windows", return_value=True),
        patch.object(windrive, "drive_in_use", return_value=False),
        patch.object(windrive, "_net_use", side_effect=lambda *a: calls.append(a) or done()),
        patch.object(cli.dpf, "start_local_server", side_effect=RuntimeError("no licence")),
        pytest.raises(RuntimeError, match="no licence"),
        cli.dpf_server(None, None, Path(UNC)),
    ):
        pass
    assert calls[-1] == ("Z:", "/delete", "/y")


def test_linux_default_path_is_left_to_dpf():
    with (
        patch.object(windrive, "is_windows", return_value=False),
        patch.object(cli.dpf_misc, "get_ansys_path") as resolve,
        patch.object(cli.dpf, "start_local_server", return_value=Mock(ip="h", port=1)) as start,
        cli.dpf_server(None, None, None),
    ):
        pass
    resolve.assert_not_called()
    assert start.call_args.kwargs["ansys_path"] is None


def test_awp_root_points_at_mapped_drive_only_while_starting(monkeypatch):
    monkeypatch.setenv("AWP_ROOT242", UNC + "\\")
    monkeypatch.setenv("AWP_ROOT251", r"C:\Ansys\v251")
    seen = {}
    with (
        patch.object(windrive, "is_windows", return_value=True),
        patch.object(windrive, "drive_in_use", return_value=False),
        patch.object(windrive, "_net_use", side_effect=lambda *a: done()),
        patch.object(
            cli.dpf,
            "start_local_server",
            side_effect=lambda **k: seen.update(windrive.os.environ) or Mock(ip="h", port=1),
        ),
        cli.dpf_server(None, None, Path(UNC)),
    ):
        assert windrive.os.environ["AWP_ROOT242"] == UNC + "\\"
    assert seen["AWP_ROOT242"] == r"Z:\ANSYS202x\ANSYS2024R2\v242"
    assert seen["AWP_ROOT251"] == r"C:\Ansys\v251"


def test_unmapped_path_leaves_environment(monkeypatch):
    monkeypatch.setenv("AWP_ROOT242", "/opt/ansys")
    with windrive.ansys_root_environment("/opt/ansys", "/opt/ansys"):
        assert windrive.os.environ["AWP_ROOT242"] == "/opt/ansys"
