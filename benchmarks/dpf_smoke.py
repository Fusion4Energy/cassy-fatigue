import argparse
import json
import os
from pathlib import Path
from time import perf_counter

from ansys.dpf import core as dpf
from ansys.dpf.core import examples


def run_benchmark(server) -> dict[str, object]:
    """Check the example result using an existing, caller-owned DPF server."""
    if server is None:
        raise ValueError("An existing DPF server is required; automatic startup is disabled")
    result_path = Path(examples.download_transient_result())
    model = dpf.Model(str(result_path), server=server)
    mesh = model.metadata.meshed_region

    stress = model.results.stress()
    stress.inputs.requested_location.connect(dpf.locations.elemental_nodal)
    stress_fields = stress.outputs.fields_container()
    temperature_fields = model.results.structural_temperature().outputs.fields_container()

    if not stress_fields:
        raise RuntimeError("The example result contains no stress fields")
    if not temperature_fields:
        raise RuntimeError("The example result contains no structural temperature fields")
    if "mat" not in mesh.available_property_fields:
        raise RuntimeError("The example result contains no material property field")

    mesh.property_field("mat")
    stress_field = stress_fields[0]
    temperature_field = temperature_fields[0]
    summary = {
        "result_file": str(result_path),
        "unit_system": str(model.metadata.result_info.unit_system),
        "nodes": mesh.nodes.n_nodes,
        "elements": mesh.elements.n_elements,
        "sets": model.metadata.time_freq_support.n_sets,
        "stress_location": str(stress_field.location),
        "stress_shape": list(stress_field.data.shape),
        "temperature_location": str(temperature_field.location),
        "temperature_shape": list(temperature_field.data.shape),
        "material_property_fields": mesh.available_property_fields,
        "named_selections": mesh.available_named_selections,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--dpf-ip", default=os.environ.get("DPF_IP", "127.0.0.1"))
    parser.add_argument("--dpf-port", type=int, default=int(os.environ.get("DPF_PORT", "50054")))
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    server = dpf.connect_to_server(
        ip=args.dpf_ip,
        port=args.dpf_port,
        as_global=False,
        config=dpf.ServerConfig(protocol="gRPC", legacy=True, grpc_mode="insecure"),
    )
    durations = []
    summary = None
    for _ in range(args.repeat):
        started = perf_counter()
        summary = run_benchmark(server)
        durations.append(perf_counter() - started)

    print(json.dumps({"runs": args.repeat, "seconds": durations, "result": summary}, indent=2))


if __name__ == "__main__":
    main()
