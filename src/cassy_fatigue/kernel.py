"""Nodal fatigue using the actual CASSY design-code kernels."""

import hashlib
import logging
import os
from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path

import numpy as np
from cassy.auxiliary.custom_errors import MissingPropertyError
from cassy.designcodes.map import PATH_CODES
from cassy.general.material import FatigueInconelRCCMRx, Material, PropertyFactory
from cassy.runners.run_common import MATERIALS_PATH, build_material_library
from numpy.typing import NDArray

from cassy_fatigue.config import Config, ConfigurationError
from cassy_fatigue.progress import Progress
from cassy_fatigue.reader import DPFReader, FloatArray, MaterialRegion
from cassy_fatigue.states import build_state, event_temperature

logger = logging.getLogger(__name__)


class InvalidCode(IntFlag):
    """Bit flags for invalid nodes. Values 4 and 8 are retired and kept unused."""

    VALID = 0
    MISSING_STRESS = 1
    MISSING_TEMPERATURE = 2
    # CASSY's array kernel returned NaN/non-positive N: e.g. a property out of bounds or
    # a strain/stress above the fatigue curve. Nodes are not re-run individually.
    NONFINITE_RESULT = 16
    MISSING_PROPERTY = 32


@dataclass(frozen=True)
class EventConfig:
    T: float | FloatArray
    dpa: float | FloatArray
    welding_f: float | FloatArray


@dataclass(frozen=True)
class NodalEvent:
    config: EventConfig
    PQF: float | FloatArray
    tresca: float | FloatArray


@dataclass(frozen=True)
class EventResult:
    event_id: str
    fields: dict[str, FloatArray]
    invalid: NDArray[np.uint32]


@dataclass(frozen=True)
class RegionResult:
    region: MaterialRegion
    material_name: str
    events: tuple[EventResult, ...]
    usage: FloatArray
    invalid: NDArray[np.uint32]


@dataclass(frozen=True)
class MaterialSource:
    path: Path
    sha256: str


def require_array_support() -> None:
    prop = PropertyFactory.create_property(
        {"probe": {"type": "constant", "value": 1, "lower_bound": [0], "upper_bound": [1]}},
        "probe",
        "array support check",
    )
    try:
        value = prop(np.array([0.0, 1.0]))
    except ValueError as error:
        raise ConfigurationError(
            "CASSY is not array-aware: it cannot evaluate material properties for many "
            "nodes at once. Install a CASSY version with array support in this environment."
        ) from error
    if np.asarray(value).shape != (2,):
        raise ConfigurationError("CASSY must include array support; scalar fallback is disabled")


def material_support(material: Material, code: str) -> str | None:
    """Why ``material`` cannot be used with ``code`` by the nodal kernel, or None if it can."""
    curve = getattr(material, "N", None)
    if curve is None or getattr(curve, "ftype", None) not in ("strain", "stress"):
        return "no supported fatigue curve type"
    if isinstance(curve, FatigueInconelRCCMRx) or getattr(curve, "mean_stress", False):
        return (
            "fatigue curve needs arguments not supplied by the nodal kernel (mean stress/Inconel)"
        )
    if code != "SDC-IC" and curve.ftype != "strain":
        return f"{code} supports strain-based curves only"
    return None


def list_materials(code: str, library: Path | None = None) -> list[tuple[str, str | None]]:
    """All CASSY materials (bundled, ./additional_materials, ``library``) with support reasons."""
    require_array_support()
    if library is not None and not Path(library).is_dir():
        raise ConfigurationError(f"Material library directory not found: {library}")
    materials = build_material_library(library)
    return [(name, material_support(materials[name], code)) for name in sorted(materials)]


def load_materials(config: Config) -> tuple[dict[int, Material], dict[int, MaterialSource]]:
    require_array_support()
    if config.material_library is not None and not config.material_library.is_dir():
        raise ConfigurationError(f"Material library directory not found: {config.material_library}")
    # Match the folder/override order used by CASSY's build_material_library.
    folders = [Path(str(MATERIALS_PATH)), Path.cwd() / "additional_materials"]
    if config.material_library is not None:
        folders.append(config.material_library)
    sources = {}
    for folder in folders:
        if folder.is_dir():
            for name in os.listdir(folder):
                if name.endswith((".yaml", ".yml")):
                    sources[name.split(".")[0]] = (folder / name).resolve()
    before = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sources.items()}
    library = build_material_library(config.material_library)
    selected = {}
    hashes = {}
    for material_id, name in config.materials.items():
        if name not in library or name not in sources:
            raise ConfigurationError(
                f"Unknown CASSY material {name!r} for material ID {material_id}"
            )
        material = library[name]
        reason = material_support(material, config.code)
        if reason is not None:
            raise ConfigurationError(f"Material {name!r}: {reason}")
        selected[material_id] = material
        path = sources[name]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != before[name]:
            raise ConfigurationError(f"Material file changed while loading: {path}")
        hashes[material_id] = MaterialSource(path, digest)
    return selected, hashes


def von_mises(stress: FloatArray) -> FloatArray:
    values = np.asarray(stress, dtype=float)
    if values.ndim != 2 or values.shape[1] != 6:
        raise ValueError("Stress must have shape (nodes, 6) in XX, YY, ZZ, XY, YZ, XZ order")
    xx, yy, zz, xy, yz, xz = values.T
    return np.sqrt(
        0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) + 3 * (xy**2 + yz**2 + xz**2)
    )


def _numeric(value, size: int, name: str) -> FloatArray:
    if value is None:
        return np.full(size, np.nan)
    array = np.asarray(value, dtype=float)
    if array.shape == ():
        return np.full(size, float(array))
    if array.shape != (size,):
        raise ValueError(f"CASSY result {name} has shape {array.shape}, expected {(size,)}")
    return array.copy()


def assess_event(
    code_name: str,
    material: Material,
    event_id: str,
    ncycles: float,
    temperature: FloatArray,
    dpa: FloatArray,
    welding_f: FloatArray,
    delta: FloatArray,
    delta_primary: FloatArray,
) -> EventResult:
    size = len(temperature)
    if not np.isfinite(ncycles) or ncycles < 0:
        raise ValueError("ncycles must be finite and non-negative")
    if any(values.shape != (size,) for values in (temperature, dpa, welding_f)):
        raise ValueError("Temperature, dpa and welding factors must have shape (nodes,)")
    if not np.isfinite(dpa).all() or np.any(dpa < 0):
        raise ValueError("dpa must be finite and non-negative")
    if not np.isfinite(welding_f).all() or np.any(welding_f < 1):
        raise ValueError("welding factors must be finite and >= 1")
    if delta.shape != (size, 6) or delta_primary.shape != (size, 6):
        raise ValueError("Stress differences must have shape (nodes, 6)")
    equivalent = von_mises(delta)
    primary = von_mises(delta_primary)
    invalid = np.zeros(size, dtype=np.uint32)
    invalid[~np.isfinite(delta).all(axis=1) | ~np.isfinite(delta_primary).all(axis=1)] |= int(
        InvalidCode.MISSING_STRESS
    )
    invalid[~np.isfinite(temperature)] |= int(InvalidCode.MISSING_TEMPERATURE)
    good = invalid == 0
    fields = {
        "delta_sigma_eq_Pa": equivalent,
        "delta_primary_eq_Pa": primary,
        "T_degC": temperature.copy(),
        "dpa": dpa.copy(),
        "welding_f": welding_f.copy(),
    }
    numeric_names = {
        "de1": "de1",
        "de2": "de2",
        "de3": "de3",
        "de4": "de4",
        "de tot": "de_total",
        "N": "N",
    }
    for name in numeric_names.values():
        fields[name] = np.full(size, np.nan)
    if np.any(good):
        event = NodalEvent(
            EventConfig(temperature[good], dpa[good], welding_f[good]),
            equivalent[good],
            primary[good],
        )
        try:
            assessment = PATH_CODES[code_name].computeVj(event, material)
        except MissingPropertyError as error:
            invalid[good] |= int(InvalidCode.MISSING_PROPERTY)
            logger.error("Event %s, material %s: %s", event_id, material.name, error)
        else:
            for cassy_name, output_name in numeric_names.items():
                fields[output_name][good] = _numeric(
                    assessment[cassy_name], np.count_nonzero(good), cassy_name
                )
            # CASSY's array kernel masks out-of-bounds/above-curve entries as NaN; such nodes
            # are flagged and left NaN rather than re-run one by one.
            invalid[good & (~np.isfinite(fields["N"]) | (fields["N"] <= 0))] |= int(
                InvalidCode.NONFINITE_RESULT
            )
    usage = np.full(size, np.nan)
    valid = invalid == 0
    usage[valid] = ncycles / fields["N"][valid]
    fields["Vj"] = usage
    if np.any(~valid):
        logger.warning(
            "Event %s, material %s: %d invalid nodes",
            event_id,
            material.name,
            np.count_nonzero(~valid),
        )
    return EventResult(event_id, fields, invalid)


def evaluate(
    config: Config,
    reader: DPFReader,
    materials: dict[int, Material],
    progress: Progress | None = None,
) -> tuple[RegionResult, ...]:
    progress = progress or Progress()
    selections = {}
    used_nodes = np.empty(0, dtype=np.int64)
    for region in config.regions:
        nodes = np.unique(
            np.asarray(reader.named_selection(region.named_selection), dtype=np.int64)
        )
        overlap = np.intersect1d(used_nodes, nodes, assume_unique=True)
        if len(overlap):
            raise ConfigurationError(
                f"Region {region.named_selection!r} overlaps another region at {len(overlap)} nodes"
            )
        used_nodes = np.union1d(used_nodes, nodes)
        selections[region.named_selection] = nodes
    results = []
    for material_id, region in reader.regions.items():
        size = len(region.node_ids)
        dpa = np.zeros(size)
        factors = np.ones(size)
        for metadata in config.regions:
            selected = np.isin(region.node_ids, selections[metadata.named_selection])
            dpa[selected] = metadata.dpa
            factors[selected] = metadata.welding_f
        events = []
        for event in config.fatigue_events:
            state = build_state(reader, config, event, material_id)
            temperature = event_temperature(reader, config, event, material_id)
            # Reads above are progress steps of their own; this step is the CASSY assessment.
            with progress.step(f"Assessing event {event.id}, material {material_id}"):
                events.append(
                    assess_event(
                        config.code,
                        materials[material_id],
                        event.id,
                        event.ncycles,
                        temperature,
                        dpa,
                        factors,
                        state.total,
                        state.primary,
                    )
                )
        usage = np.sum(np.stack([event.fields["Vj"] for event in events]), axis=0)
        invalid = np.bitwise_or.reduce(np.stack([event.invalid for event in events]), axis=0)
        results.append(
            RegionResult(region, config.materials[material_id], tuple(events), usage, invalid)
        )
    return tuple(results)
