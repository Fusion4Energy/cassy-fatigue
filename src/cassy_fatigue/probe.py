"""Read-only metadata of a result file, to pre-fill configuration choices.

The probe only lists what a configuration can refer to. It does not replace the mesh,
unit and material checks of :class:`cassy_fatigue.reader.DPFReader`, which still run at
evaluation time.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cassy_fatigue.reader import ReaderError, index_of, open_model, solid_elements


@dataclass(frozen=True)
class ResultSet:
    set_id: int  # one-based DPF result-set ID, as used by ``loads.<name>.set``
    time: float
    load_step: int | None
    substep: int | None

    def label(self, time_unit: str) -> str:
        step = "" if self.load_step is None else f", LS{self.load_step}/SS{self.substep}"
        return f"set {self.set_id}: t={self.time:g} {time_unit}{step}"


@dataclass(frozen=True)
class NamedSelection:
    name: str
    location: str  # "Nodal" or "Elemental" (DPF location names)
    size: int


@dataclass(frozen=True)
class ResultInfo:
    path: Path
    kind: str  # "rst" or "rth"
    analysis_type: str
    unit_system: str
    length_unit: str  # "" if the result records no unit system
    time_unit: str
    sets: tuple[ResultSet, ...]
    material_ids: tuple[int, ...]  # of solid elements only (others are not assessed)
    named_selections: tuple[NamedSelection, ...]
    n_nodes: int
    n_elements: int
    n_solid_elements: int
    skipped_elements: dict[str, int]  # non-solid elements per DPF element type name
    has_temperature: bool
    has_stress: bool

    @property
    def solid_only(self) -> bool:
        return not self.skipped_elements

    def set_label(self, set_id: int) -> str:
        for item in self.sets:
            if item.set_id == set_id:
                return item.label(self.time_unit)
        return f"set {set_id} (not in result)"


def _sets(support) -> tuple[ResultSet, ...]:
    frequencies = support.time_frequencies
    times = np.asarray(frequencies.data, dtype=float).ravel()
    steps: list[tuple[int | None, int | None]] = []
    # The time field is scoped on load steps; each step holds its substep times.
    if frequencies.scoping.location == "TimeFreq_steps":
        for index, step in enumerate(frequencies.scoping.ids):
            count = len(np.atleast_1d(frequencies.get_entity_data(index)))
            steps.extend((int(step), substep) for substep in range(1, count + 1))
    if len(steps) != len(times):
        steps = [(None, None)] * len(times)
    # DPF cumulative indices are zero-based; result-set IDs are one-based.
    return tuple(
        ResultSet(index + 1, float(time), *step)
        for index, (time, step) in enumerate(zip(times, steps, strict=True))
    )


def probe_result(path: Path, server) -> ResultInfo:
    """Metadata of one ``.rst``/``.rth`` on an existing, caller-owned DPF server."""
    if server is None:
        raise ValueError("An existing DPF server is required; automatic startup is disabled")
    path = Path(path).resolve()  # the DPF server does not resolve relative paths
    if not path.is_file():
        raise ReaderError(f"Analysis file not found: {path}")
    kind = path.suffix.lower().lstrip(".")
    if kind not in ("rst", "rth"):
        raise ReaderError(f"{path.name}: expected an .rst or .rth file")
    model = open_model(path, server)
    metadata = model.metadata
    info = metadata.result_info
    results = {result.name for result in info.available_results}
    mesh = metadata.meshed_region
    solid, skipped = solid_elements(mesh)
    mat = mesh.property_field("mat")
    materials = np.asarray(mat.data, dtype=np.int64).ravel()
    if skipped and len(solid):
        materials = materials[index_of(mat.scoping.ids, solid, "element material IDs")]
    materials = np.unique(materials)
    selections = []
    for name in metadata.available_named_selections:
        scoping = metadata.named_selection(name)
        selections.append(NamedSelection(name, scoping.location, len(scoping.ids)))
    support = metadata.time_freq_support
    return ResultInfo(
        path=path,
        kind=kind,
        analysis_type=str(info.analysis_type),
        unit_system=str(info.unit_system),
        length_unit=str(mesh.unit or ""),
        time_unit=str(support.time_frequencies.unit),
        sets=_sets(support),
        material_ids=tuple(int(value) for value in materials if value > 0),
        named_selections=tuple(selections),
        n_nodes=int(mesh.nodes.n_nodes),
        n_elements=int(mesh.elements.n_elements),
        n_solid_elements=len(solid),
        skipped_elements=skipped,
        has_temperature=("structural_temperature" if kind == "rst" else "temperature") in results,
        has_stress="stress" in results,
    )
