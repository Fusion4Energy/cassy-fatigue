"""Opt-in live checks (CASSY_DPF_INTEGRATION=1); ordinary pytest runs never contact DPF."""

import json
import subprocess
import sys
from pathlib import Path


def test_phase0_all_reader_checks_pass(dpf_live_server):
    root = Path(__file__).resolve().parent.parent
    fixture = root / "benchmarks" / "fixtures" / "two_materials.rst"
    assert fixture.is_file(), f"Tracked fixture not found: {fixture}"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "benchmarks" / "phase0_reader_checks.py"),
            str(fixture),
            "--check",
            "all",
            "--dpf-ip",
            str(dpf_live_server.ip),
            "--dpf-port",
            str(dpf_live_server.port),
        ],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    checks = json.loads(result.stdout)
    assert checks["load_step_substep_mapping"]["sets"] == [
        {"set_id": 1, "load_step": 1, "substep": 1, "time": 1.0},
        {"set_id": 2, "load_step": 2, "substep": 1, "time": 2.0},
    ]
    temperatures = checks["temperature_units_and_conversion"]["temperature_checks"]
    assert [field["temperature_degC"] for field in temperatures] == [20.0, 120.0]
    assert all(field["nodal_conversion"]["shape"] == [18] for field in temperatures)
    grid = checks["pyvista_mesh_access"]
    assert (grid["grid_type"], grid["n_points"], grid["n_cells"]) == (
        "UnstructuredGrid",
        18,
        4,
    )
    assert grid["coordinates_match"] and grid["connectivity_match"]
