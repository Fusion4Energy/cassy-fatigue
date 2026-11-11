"""Command-line nodal fatigue workflow."""

import argparse
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import yaml
from ansys.dpf import core as dpf
from ansys.dpf.core import errors as dpf_errors
from ansys.dpf.core import misc as dpf_misc
from cassy.auxiliary.custom_errors import ConfigError

from cassy_fatigue import windrive
from cassy_fatigue.config import Config
from cassy_fatigue.kernel import load_materials
from cassy_fatigue.pipeline import run_config

logger = logging.getLogger(__name__)

DEFAULT_DPF_IP = "127.0.0.1"
DEFAULT_DPF_PORT = 50054


def server_config() -> dpf.ServerConfig:
    """Plaintext legacy gRPC, required by Ansys 2024 R2 DPF servers. Local use only."""
    return dpf.ServerConfig(protocol="gRPC", legacy=True, grpc_mode="insecure")


@contextmanager
def dpf_server(
    ip: str | None, port: int | None, ansys_path: Path | None
) -> Iterator[tuple[object, str]]:
    """Connect to an existing server if an endpoint is given, else start one and shut it down.

    Yields ``(server, endpoint_description)``. A connected server is left running; a
    server started here is always shut down on exit, including on errors.
    """
    if ip is not None or port is not None:
        ip, port = ip or DEFAULT_DPF_IP, port or DEFAULT_DPF_PORT
        logger.info("Connecting to existing DPF server at %s:%s", ip, port)
        server = dpf.connect_to_server(ip=ip, port=port, as_global=False, config=server_config())
        yield server, f"{ip}:{port} (existing server)"
        return
    logger.info("No DPF endpoint given; starting a local DPF server")
    # On Windows a network-share (UNC) installation is started via a temporary drive letter,
    # kept until the server is shut down (cmd.exe cannot run DPF's .bat from a UNC folder).
    install = resolve_ansys_path(ansys_path)
    with windrive.drive_letter_path(install) as launch_path:
        start = time.perf_counter()
        # The server process inherits AWP_ROOTxxx; point it at the mapped drive as well.
        with windrive.ansys_root_environment(install, launch_path):
            server = dpf.start_local_server(
                ip=DEFAULT_DPF_IP,
                port=DEFAULT_DPF_PORT,
                ansys_path=launch_path,
                as_global=False,
                config=server_config(),
                use_docker_by_default=False,
                use_pypim_by_default=False,
            )
        endpoint = f"{server.ip}:{server.port}"
        logger.info("Started DPF server at %s in %.1f s", endpoint, time.perf_counter() - start)
        try:
            ensure_mapdl_reader(server, launch_path or getattr(server, "ansys_path", None))
            yield server, f"{endpoint} (started and shut down by cassy-fatigue)"
        finally:
            try:
                server.shutdown()
                logger.info("Shut down DPF server at %s", endpoint)
            except Exception as error:  # noqa: BLE001 - never mask the processing result/error.
                logger.warning("Could not shut down DPF server at %s: %s", endpoint, error)


MAPDL_READER = "result_info_provider"  # provided by the mapdlOperatorsCore plugin


def _has_reader(server) -> bool | None:
    """Whether ``server`` can create the MAPDL reader; ``None`` if that cannot be told.

    Creating the operator is the call a read makes (listing operators is not supported by
    every server type).
    """
    try:
        dpf.Operator(MAPDL_READER, server=server)
    except dpf_errors.DPFServerException as error:
        logger.debug("Cannot create %s: %s", MAPDL_READER, error)
        return False
    except Exception as error:  # noqa: BLE001 - not a DPF answer; leave it to the read
        logger.warning("Could not check the DPF server for %s: %s", MAPDL_READER, error)
        return None
    return True


def mapdl_plugin_path(ansys_path: str | Path) -> Path:
    if windrive.is_windows():
        return Path(ansys_path) / "dpf" / "bin" / "winx64" / "mapdlOperatorsCore.dll"
    return Path(ansys_path) / "dpf" / "bin" / "linx64" / "libmapdlOperatorsCore.so"


def ensure_mapdl_reader(server, ansys_path: str | Path | None) -> None:
    """Fail early (or load the plugin) if a started server cannot read ``.rst``/``.rth``.

    A server whose MAPDL plugin did not load starts normally, and only the first read
    fails with 'Source operator "result_info_provider" not found'.
    """
    if _has_reader(server) is not False:
        return
    if ansys_path is None:
        raise RuntimeError(
            f"The started DPF server has no MAPDL result reader ({MAPDL_READER}): its "
            "mapdlOperatorsCore plugin did not load. Pass --ansys-path or start DPF yourself."
        )
    plugin = mapdl_plugin_path(ansys_path)
    logger.warning(
        "The started DPF server has no MAPDL result reader (%s); loading %s", MAPDL_READER, plugin
    )
    try:
        dpf.load_library(str(plugin), "mapdl_operators", server=server)
    except Exception as error:
        raise RuntimeError(
            f"The started DPF server did not load its MAPDL plugin and loading {plugin} "
            f"failed: {error}"
        ) from error
    if _has_reader(server) is False:
        raise RuntimeError(f"Loaded {plugin}, but the DPF server still has no {MAPDL_READER}")
    logger.info("Loaded the MAPDL plugin %s", plugin)


def resolve_ansys_path(ansys_path: Path | None) -> str | None:
    """The installation DPF would use; resolved only on Windows to detect UNC shares."""
    if ansys_path is not None:
        return str(ansys_path)
    if not windrive.is_windows():
        return None
    try:
        return str(dpf_misc.get_ansys_path(None))
    except Exception:  # noqa: BLE001 - let start_local_server report the missing install
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cassy-fatigue")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run",
        help="Assess fatigue events (sums of P/Q loads) and total usage",
        description="Without --dpf-ip/--dpf-port (or DPF_IP/DPF_PORT), a local DPF server is "
        "started for the run and shut down afterwards.",
    )
    run.add_argument("config", type=Path)
    run.add_argument("--output", type=Path)
    run.add_argument("--overwrite", action="store_true")
    run.add_argument(
        "--dpf-ip",
        default=os.environ.get("DPF_IP"),
        help="connect to an existing DPF server at this address",
    )
    # argparse applies ``type`` to string defaults, so DPF_PORT is validated like the option.
    run.add_argument(
        "--dpf-port",
        type=int,
        default=os.environ.get("DPF_PORT"),
        help=f"connect to an existing DPF server on this port (default {DEFAULT_DPF_PORT})",
    )
    run.add_argument(
        "--ansys-path",
        type=Path,
        help="Ansys installation used to start a local server (default: found by DPF)",
    )
    gui = commands.add_parser(
        "gui", help="Open the configuration editor and results viewer (needs the gui extra)"
    )
    gui.add_argument(
        "config", type=Path, nargs="?", help="configuration YAML to edit, or a fatigue.vtu to view"
    )
    args = parser.parse_args(argv)
    if args.command == "gui":
        return gui_main([] if args.config is None else [str(args.config)])
    if args.dpf_port is not None and not 1 <= args.dpf_port <= 65535:
        parser.error("--dpf-port must be between 1 and 65535")
    connecting = args.dpf_ip is not None or args.dpf_port is not None
    if connecting and args.ansys_path is not None:
        parser.error("--ansys-path only applies when starting a server; omit --dpf-ip/--dpf-port")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        # Validate everything that does not need DPF before connecting or starting a server.
        config = Config.read(args.config)
        materials, sources = load_materials(config)
        with dpf_server(args.dpf_ip, args.dpf_port, args.ansys_path) as (server, endpoint):
            paths = run_config(
                config, materials, sources, server, endpoint, args.output, args.overwrite
            )
    except (ValueError, RuntimeError, OSError, ConfigError, yaml.YAMLError) as error:
        logger.error("%s", error)
        return 1
    for name, path in paths.items():
        logger.info("%s: %s", name, path)
    return 0


def gui_main(argv: list[str] | None = None) -> int:
    """Start the PySide6 editor, with a clear message if the optional extra is missing."""
    try:
        from cassy_fatigue.gui.app import main as run_gui
    except ImportError as error:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
        logger.error("The GUI needs PySide6: pip install 'cassy-fatigue[gui]' (%s)", error)
        return 1
    return run_gui(argv)


if __name__ == "__main__":
    raise SystemExit(main())
