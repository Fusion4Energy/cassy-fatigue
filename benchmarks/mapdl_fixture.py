"""Generate and verify a small two-material, two-state MAPDL result."""

import argparse
import json
import os
from pathlib import Path

import numpy as np
from ansys.dpf import core as dpf
from ansys.mapdl.core import Mapdl

MATERIALS = {1: (200e9, 1e-5), 2: (100e9, 5e-6)}
# Git-tracked copy of the generated result used by tests and examples.
TRACKED_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "two_materials.rst"
STATES = ((0.001, 20.0), (0.002, 120.0))


def generate(output: Path, mapdl) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    result = output / "two_materials.rst"
    if result.exists():
        raise FileExistsError(f"Refusing to overwrite existing fixture: {result}")
    try:
        mapdl.finish()
        mapdl.cwd(str(output))
        mapdl.filname("two_materials")
        mapdl.clear()
        mapdl.prep7()
        mapdl.run("/UNITS,SI")
        mapdl.et(1, 185)
        for material_id, (young, expansion) in MATERIALS.items():
            mapdl.mp("EX", material_id, young)
            mapdl.mp("PRXY", material_id, 0.0)
            mapdl.mp("ALPX", material_id, expansion)
        node_ids = {}
        for z_index in range(2):
            for y_index in range(3):
                for x_index in range(3):
                    node_id = len(node_ids) + 1
                    node_ids[x_index, y_index, z_index] = node_id
                    mapdl.n(node_id, x_index / 2, y_index, z_index)
        for y_index in range(2):
            mapdl.mat(y_index + 1)
            for x_index in range(2):
                corners = (
                    (x_index, y_index, 0),
                    (x_index + 1, y_index, 0),
                    (x_index + 1, y_index + 1, 0),
                    (x_index, y_index + 1, 0),
                    (x_index, y_index, 1),
                    (x_index + 1, y_index, 1),
                    (x_index + 1, y_index + 1, 1),
                    (x_index, y_index + 1, 1),
                )
                mapdl.e(*(node_ids[corner] for corner in corners))
        mapdl.nsel("S", "LOC", "Y", 1)
        mapdl.cm("INTERFACE", "NODE")
        for material_id in MATERIALS:
            mapdl.esel("S", "MAT", "", material_id)
            mapdl.cm(f"MATERIAL_{material_id}", "ELEM")
        mapdl.allsel()
        mapdl.d("ALL", "UY", 0)
        mapdl.d("ALL", "UZ", 0)
        mapdl.nsel("S", "LOC", "X", 0)
        mapdl.d("ALL", "UX", 0)
        mapdl.allsel()
        mapdl.finish()
        mapdl.slashsolu()
        mapdl.antype("STATIC")
        mapdl.tref(20)
        mapdl.outres("ALL", "ALL")
        for set_id, (strain, temperature) in enumerate(STATES, start=1):
            mapdl.nsel("S", "LOC", "X", 1)
            mapdl.d("ALL", "UX", strain)
            mapdl.allsel()
            mapdl.bfe("ALL", "TEMP", 1, temperature, temperature, temperature, temperature)
            mapdl.bfe("ALL", "TEMP", 5, temperature, temperature, temperature, temperature)
            mapdl.time(set_id)
            mapdl.nsubst(1)
            mapdl.solve()
        mapdl.finish()
    finally:
        mapdl.finish()
    if not result.is_file():
        raise RuntimeError(f"MAPDL did not produce {result}")
    return result


def verify(result: Path, server) -> dict[str, object]:
    if server is None:
        raise ValueError("An existing DPF server is required; automatic startup is disabled")
    model = dpf.Model(str(result), server=server)
    mesh = model.metadata.meshed_region
    assert mesh.nodes.n_nodes == 18
    assert mesh.elements.n_elements == 4
    assert model.metadata.time_freq_support.n_sets == 2
    assert "INTERFACE" in model.metadata.available_named_selections
    assert len(model.metadata.named_selection("INTERFACE").ids) == 6
    materials = mesh.property_field("mat")
    assert set(np.asarray(materials.data).ravel()) == set(MATERIALS)
    assert "degC" in model.metadata.result_info.unit_system
    checks = []
    for set_id, (strain, temperature) in enumerate(STATES, start=1):
        for material_id, (young, expansion) in MATERIALS.items():
            elements = materials.scoping.ids[np.asarray(materials.data).ravel() == material_id]
            scoping = dpf.Scoping(
                ids=elements.tolist(), location=dpf.locations.elemental, server=server
            )
            stress = model.results.stress()
            stress.inputs.time_scoping.connect([set_id])
            stress.inputs.mesh_scoping.connect(scoping)
            stress.inputs.requested_location.connect(dpf.locations.elemental_nodal)
            fields = stress.outputs.fields_container()
            thermal_strain = expansion * (temperature - 20)
            expected = young * np.array(
                [strain - thermal_strain, -thermal_strain, -thermal_strain, 0, 0, 0]
            )
            assert len(fields) > 0
            for field in fields:
                assert field.location == dpf.locations.elemental_nodal
                assert field.unit == "Pa"
                values = np.asarray(field.data)
                np.testing.assert_allclose(
                    values, np.broadcast_to(expected, values.shape), rtol=1e-6, atol=1
                )
                nodal = dpf.operators.averaging.elemental_nodal_to_nodal(
                    field=field,
                    mesh=mesh,
                    server=server,
                ).outputs.field()
                nodal_values = np.asarray(nodal.data)
                np.testing.assert_allclose(
                    nodal_values, np.broadcast_to(expected, nodal_values.shape), rtol=1e-6, atol=1
                )
                assert set(model.metadata.named_selection("INTERFACE").ids).issubset(
                    set(nodal.scoping.ids)
                )
            temperatures = model.results.structural_temperature()
            temperatures.inputs.time_scoping.connect([set_id])
            temperatures.inputs.mesh_scoping.connect(scoping)
            temp_fields = temperatures.outputs.fields_container()
            assert len(temp_fields) > 0
            for field in temp_fields:
                np.testing.assert_allclose(field.data, temperature, atol=1e-6)
            checks.append(
                {
                    "set": set_id,
                    "material": material_id,
                    "stress_Pa": expected.tolist(),
                    "temperature_degC": temperature,
                }
            )
    return {"nodes": 18, "elements": 4, "sets": 2, "checks": checks}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("benchmarks/generated/two_materials"))
    parser.add_argument("--ip", default=os.environ.get("PYMAPDL_IP", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PYMAPDL_PORT", "50052")))
    parser.add_argument(
        "--allow-clear",
        action="store_true",
        help="Allow replacing the connected MAPDL model with this fixture",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help=f"Verify an existing result (default: tracked {TRACKED_FIXTURE.name})",
    )
    parser.add_argument(
        "--result", type=Path, default=TRACKED_FIXTURE, help="Result checked by --verify-only"
    )
    parser.add_argument("--dpf-ip", default=os.environ.get("DPF_IP", "127.0.0.1"))
    parser.add_argument("--dpf-port", type=int, default=int(os.environ.get("DPF_PORT", "50054")))
    args = parser.parse_args()
    output = args.output.resolve()
    if not args.verify_only and not args.allow_clear:
        parser.error("Generation replaces the connected model; explicitly supply --allow-clear")
    server = dpf.connect_to_server(
        ip=args.dpf_ip,
        port=args.dpf_port,
        as_global=False,
        config=dpf.ServerConfig(protocol="gRPC", legacy=True, grpc_mode="insecure"),
    )
    if args.verify_only:
        result = args.result.resolve()
    else:
        mapdl = Mapdl(ip=args.ip, port=args.port, cleanup_on_exit=False)
        result = generate(output, mapdl)
    summary = verify(result, server)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
