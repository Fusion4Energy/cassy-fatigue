"""Check the saved two-material fixture using an existing DPF server."""

import argparse
import json
import os
from pathlib import Path

import numpy as np
from ansys.dpf import core as dpf


def _model(result: Path, server):
    if server is None:
        raise ValueError("An existing DPF server is required; automatic startup is disabled")
    return dpf.Model(str(result), server=server)


def check_load_step_substep_mapping(result: Path, server) -> dict[str, object]:
    """Verify the fixture's two load steps, each with one substep."""
    model = _model(result, server)
    support = model.metadata.time_freq_support
    assert support.n_sets == 2
    np.testing.assert_allclose(support.time_frequencies.data, [1.0, 2.0])
    sets = []
    for step in range(2):
        # DPF support indices are zero-based; result time scoping is one-based.
        index = support.get_cumulative_index(step=step, substep=0)
        assert index == step
        time = support.get_frequency(step=step, substep=0)
        np.testing.assert_allclose(time, step + 1.0)
        np.testing.assert_allclose(support.get_frequency(cumulative_index=index), time)
        sets.append(
            {
                "set_id": int(index) + 1,
                "load_step": step + 1,
                "substep": 1,
                "time": float(time),
            }
        )
    return {"n_sets": support.n_sets, "sets": sets}


def check_temperature_units_and_conversion(result: Path, server) -> dict[str, object]:
    """Check every BFE temperature before and after nodal averaging."""
    model = _model(result, server)
    mesh = model.metadata.meshed_region
    assert model.metadata.time_freq_support.n_sets == 2
    unit_system = str(model.metadata.result_info.unit_system)
    assert "degC" in unit_system
    checks = []
    for set_id, temperature in enumerate((20.0, 120.0), start=1):
        operator = model.results.structural_temperature()
        operator.inputs.time_scoping.connect([set_id])
        operator.inputs.requested_location.connect(dpf.locations.elemental_nodal)
        fields = operator.outputs.fields_container()
        assert len(fields) == 1, f"Expected one temperature field for set {set_id}"
        field = fields[0]
        assert field.location == dpf.locations.elemental_nodal
        assert field.unit == "degC"
        assert np.asarray(field.data).shape == (32,)
        np.testing.assert_allclose(field.data, temperature, rtol=0, atol=1e-6)
        nodal = dpf.operators.averaging.elemental_nodal_to_nodal(
            field=field,
            mesh=mesh,
            server=server,
        ).outputs.field()
        assert nodal.location == dpf.locations.nodal
        assert nodal.unit == "degC"
        assert np.asarray(nodal.data).shape == (18,)
        assert set(nodal.scoping.ids) == set(mesh.nodes.scoping.ids)
        np.testing.assert_allclose(nodal.data, temperature, rtol=0, atol=1e-6)
        checks.append(
            {
                "set_id": set_id,
                "location": field.location,
                "unit": field.unit,
                "shape": list(field.data.shape),
                "temperature_degC": temperature,
                "nodal_conversion": {
                    "location": nodal.location,
                    "unit": nodal.unit,
                    "shape": list(nodal.data.shape),
                },
            }
        )
    return {"unit_system": unit_system, "temperature_checks": checks}


def check_pyvista_mesh_access(result: Path, server) -> dict[str, object]:
    """Check the actual PyVista grid, coordinates, and element connectivity."""
    model = _model(result, server)
    import pyvista as pv

    mesh = model.metadata.meshed_region
    # mesh.grid imports all VTK modules and stalls in this environment.
    # This fixture contains only eight-node hexahedra.
    coordinates = np.asarray(mesh.nodes.coordinates_field.data)
    connectivity = [np.asarray(element.connectivity) for element in mesh.elements]
    assert all(nodes.shape == (8,) for nodes in connectivity)
    cells = np.concatenate([np.concatenate(([8], nodes)) for nodes in connectivity])
    grid = pv.UnstructuredGrid(
        cells,
        np.full(len(connectivity), pv.CellType.HEXAHEDRON),
        coordinates,
    )
    assert isinstance(grid, pv.UnstructuredGrid)
    assert grid.n_points == mesh.nodes.n_nodes == 18
    assert grid.n_cells == mesh.elements.n_elements == 4
    np.testing.assert_allclose(grid.points, mesh.nodes.coordinates_field.data)
    np.testing.assert_allclose(grid.bounds, [0, 1, 0, 2, 0, 1])
    np.testing.assert_array_equal(grid.celltypes, np.full(4, pv.CellType.HEXAHEDRON))
    for index in range(grid.n_cells):
        np.testing.assert_array_equal(
            grid.get_cell(index).point_ids,
            mesh.elements[index].connectivity,
        )
    return {
        "pyvista_version": pv.__version__,
        "conversion_method": "explicit fixture hexahedra; native mesh.grid import stalls",
        "grid_type": type(grid).__name__,
        "n_points": grid.n_points,
        "n_cells": grid.n_cells,
        "bounds": list(grid.bounds),
        "cell_types": grid.celltypes.tolist(),
        "coordinates_match": True,
        "connectivity_match": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, help="Path to the saved two_materials.rst fixture")
    parser.add_argument("--dpf-ip", default=os.environ.get("DPF_IP", "127.0.0.1"))
    parser.add_argument("--dpf-port", type=int, default=int(os.environ.get("DPF_PORT", "50054")))
    parser.add_argument(
        "--check", choices=["all", "mapping", "temperature", "pyvista"], default="all"
    )
    args = parser.parse_args()
    result = args.result.resolve()
    if not result.is_file():
        parser.error(f"Result file not found: {result}")
    server = dpf.connect_to_server(
        ip=args.dpf_ip,
        port=args.dpf_port,
        as_global=False,
        config=dpf.ServerConfig(protocol="gRPC", legacy=True, grpc_mode="insecure"),
    )
    results = {"result_file": str(result)}
    if args.check in ("all", "mapping"):
        results["load_step_substep_mapping"] = check_load_step_substep_mapping(result, server)
    if args.check in ("all", "temperature"):
        results["temperature_units_and_conversion"] = check_temperature_units_and_conversion(
            result,
            server,
        )
    if args.check in ("all", "pyvista"):
        results["pyvista_mesh_access"] = check_pyvista_mesh_access(result, server)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
