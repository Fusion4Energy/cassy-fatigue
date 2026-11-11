"""Conservative interface projection and traceable VTU outputs."""

import hashlib
import inspect
import json
import os
from importlib.metadata import version
from pathlib import Path

import numpy as np
import yaml
from cassy.designcodes.map import PATH_CODES
from cassy.general import material as material_module

from cassy_fatigue.config import Config
from cassy_fatigue.kernel import InvalidCode, MaterialSource, RegionResult
from cassy_fatigue.reader import DPFReader, index_of


def project_nodes(node_ids, results: tuple[RegionResult, ...]) -> dict:
    if not results:
        raise ValueError("No assessed material regions to project")
    if np.ndim(node_ids) != 1 or len(np.unique(node_ids)) != len(node_ids):
        raise ValueError("Projected node IDs must be a unique one-dimensional array")
    event_ids = [event.event_id for event in results[0].events]
    if any([event.event_id for event in result.events] != event_ids for result in results):
        raise ValueError("Material regions have inconsistent event IDs/order")
    size = len(node_ids)
    total = np.full(size, -np.inf)
    invalid = np.zeros(size, dtype=np.uint32)
    winner_region = np.full(size, -1, dtype=int)
    seen = np.zeros(size, dtype=bool)
    event_fields = {}
    event_invalid = {}
    event_material = {}
    for event_id in event_ids:
        fields = next(event.fields for event in results[0].events if event.event_id == event_id)
        event_fields[event_id] = {name: np.full(size, np.nan) for name in fields}
        event_invalid[event_id] = np.zeros(size, dtype=np.uint32)
        event_material[event_id] = np.full(size, -1, dtype=np.int64)
    for region_index, result in enumerate(results):
        positions = index_of(node_ids, result.region.node_ids, "material region node IDs")
        seen[positions] = True
        invalid[positions] |= result.invalid
        usage = np.where(np.isnan(result.usage), -np.inf, result.usage)
        governs = (winner_region[positions] < 0) | (usage > total[positions])
        chosen = positions[governs]
        total[chosen] = usage[governs]
        winner_region[chosen] = region_index
        for event in result.events:
            event_invalid[event.event_id][positions] |= event.invalid
            target = event_fields[event.event_id]
            for name, values in event.fields.items():
                target[name][chosen] = values[governs]
            event_material[event.event_id][chosen] = result.region.material_id
    invalid[~seen] |= int(InvalidCode.MISSING_STRESS)
    total[(invalid != 0) | ~seen] = np.nan
    winner_region[invalid != 0] = -1
    for event_id, fields in event_fields.items():
        event_invalid[event_id][~seen] |= int(InvalidCode.MISSING_STRESS)
        bad = event_invalid[event_id] != 0
        for values in fields.values():
            values[bad] = np.nan
        event_material[event_id][bad] = -1
    return {
        "U_total": total,
        "invalid_code": invalid,
        "winner_region": winner_region,
        "events": event_fields,
        "event_invalid": event_invalid,
        "event_material": event_material,
    }


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_outputs(
    config: Config,
    reader: DPFReader,
    results: tuple[RegionResult, ...],
    materials: dict[int, MaterialSource],
    output_dir: Path,
    overwrite: bool = False,
    endpoint: str = "caller-supplied server",
) -> dict[str, Path]:
    if not results:
        raise ValueError("No assessed material regions to export")
    output_dir = output_dir.resolve()
    paths = {
        name: output_dir / filename
        for name, filename in {
            "vtu": "fatigue.vtu",
            "configuration": "configuration.yaml",
            "traceability": "traceability.json",
        }.items()
    }
    if not overwrite and any(path.exists() for path in paths.values()):
        raise FileExistsError(
            f"Output files already exist in {output_dir}; use --overwrite explicitly"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = {
        name: path.with_name(f".{path.stem}.tmp{path.suffix}") for name, path in paths.items()
    }
    if any(path.exists() for path in temporary.values()):
        raise FileExistsError(f"Temporary output files already exist in {output_dir}")
    grid = reader.grid  # solid elements only
    node_ids = np.asarray(grid.point_data["node_id"], dtype=np.int64)
    projection = project_nodes(node_ids, results)
    grid.point_data["U_total"] = projection["U_total"]
    grid.point_data["invalid_code"] = projection["invalid_code"]
    governing_material = np.full(len(node_ids), -1, dtype=np.int64)
    for index, result in enumerate(results):
        governing_material[projection["winner_region"] == index] = result.region.material_id
    grid.point_data["material_id_U"] = governing_material
    for event_id, fields in projection["events"].items():
        for name, values in fields.items():
            grid.point_data[f"{name}_{event_id}"] = values
        grid.point_data[f"invalid_code_{event_id}"] = projection["event_invalid"][event_id]
        grid.point_data[f"material_id_{event_id}"] = projection["event_material"][event_id]
    echo = dict(config.raw)
    echo.pop("temperature", None)  # folded into analyses.<name>.temperature below
    echo["analyses"] = {}
    for name, path in config.analyses.items():
        entry = {"file": str(path)}
        if name in config.analysis_units:
            entry["units"] = config.analysis_units[name].declared()
        if config.temperature_sources.get(name, name) != name:
            entry["temperature"] = config.temperature_sources[name]
        echo["analyses"][name] = entry if len(entry) > 1 else str(path)
    if config.material_library is not None:
        echo["material_library"] = str(config.material_library)
    source_files = [
        Path(material_module.__file__),
        Path(inspect.getfile(type(PATH_CODES[config.code]))),
    ]
    provenance = {
        "cassy_fatigue_version": version("cassy-fatigue"),
        "cassy_distribution_version": version("cassy-f4e"),
        "dpf_core_version": version("ansys-dpf-core"),
        "config_source": str(config.source),
        "endpoint": endpoint,
        "units": {"stress": "Pa", "temperature": "degC", "coordinates": "m"},
        # Source units per analysis and whether the result recorded them or the
        # configuration declared them (only for quantities that were read).
        "analysis_units": reader.units,
        # Analysis whose temperatures each structural analysis's sets use.
        "temperature_sources": dict(config.temperature_sources),
        "materials": {
            str(key): {"path": str(value.path), "sha256": value.sha256}
            for key, value in materials.items()
        },
        "material_assignment": {
            "mode": "named_selection" if config.material_selections else "solver_material_id",
            "regions": {
                str(key): {"material": name, "named_selection": config.material_selections.get(key)}
                for key, name in config.materials.items()
            },
        },
        "cassy_source": {str(path): _hash(path) for path in source_files},
        "tool_source": {path.name: _hash(path) for path in Path(__file__).parent.glob("*.py")},
        # Non-solid elements (shell, beam, point, contact, 2D) per DPF element type; they
        # are left out of the assessment and of fatigue.vtu.
        "skipped_elements": dict(getattr(reader, "skipped_elements", {})),
        "invalid_codes": {name: int(code) for name, code in InvalidCode.__members__.items()},
        "interface_policy": "Maximum material-local U; all event Vj values and diagnostics use "
        "the material governing U. Any invalid material makes the "
        "corresponding projected result NaN.",
        "limitations": [
            "Solid elements only: non-solid elements are skipped (no shell/beam layer inference)",
            "Volumetric load treatment; no inertial upper-limit VM",
            "Analyst-defined events (sums of P/Q loads) only; no automatic cycle extraction",
            "Thermal .rth alignment requires a unique matching physical time and identical nodes",
        ],
    }
    try:
        grid.save(temporary["vtu"])
        temporary["configuration"].write_text(
            yaml.safe_dump(echo, sort_keys=False), encoding="utf-8"
        )
        temporary["traceability"].write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        for name, path in paths.items():
            os.replace(temporary[name], path)
    finally:
        for path in temporary.values():
            path.unlink(missing_ok=True)
    return paths
