"""DPF reads using only an explicitly supplied, caller-owned server."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias

import numpy as np
import pyvista as pv
from ansys.dpf import core as dpf
from ansys.dpf.core.elements import element_types
from ansys.dpf.core.server_types import AnyServerType
from numpy.typing import NDArray

from cassy_fatigue.config import LENGTH_UNITS, STRESS_UNITS, UNIT_CHOICES, Config
from cassy_fatigue.progress import Progress

logger = logging.getLogger(__name__)

FloatArray: TypeAlias = NDArray[np.float64]
IntArray: TypeAlias = NDArray[np.int64]


class ReaderError(RuntimeError):
    pass


def open_model(path, server) -> dpf.Model:
    """``dpf.Model`` with the result key from the lower-cased extension.

    DPF takes the key from the extension verbatim, and has no reader for ``RST``/``RTH``
    (uppercase, common on Windows): reads then fail with 'Source operator
    "result_info_provider" not found'.
    """
    sources = dpf.DataSources(server=server)
    sources.set_result_file_path(str(path), key=str(path).rsplit(".", 1)[-1].lower())
    return dpf.Model(sources, server=server)


def stress_to_pa(values: FloatArray, unit: str) -> FloatArray:
    if unit not in STRESS_UNITS:
        raise ReaderError(f"Unsupported stress unit {unit!r}; refusing to infer units")
    return np.asarray(values, dtype=float) * STRESS_UNITS[unit]


def temperature_to_celsius(values: FloatArray, unit: str) -> FloatArray:
    values = np.asarray(values, dtype=float)
    if unit in ("degC", "C"):
        return values.copy()
    if unit == "K":
        return values - 273.15
    if unit == "degF":
        return (values - 32) * 5 / 9
    raise ReaderError(f"Unsupported temperature unit {unit!r}; refusing to infer units")


def length_factor(unit: str) -> float:
    if unit not in LENGTH_UNITS:
        raise ReaderError(f"Unsupported mesh length unit {unit!r}; refusing to infer units")
    return LENGTH_UNITS[unit]


def index_of(target_ids, ids, context: str = "IDs") -> IntArray:
    """Positions of ``ids`` in the unique ``target_ids`` array, without Python-level loops."""
    target_ids = np.asarray(target_ids, dtype=np.int64)
    ids = np.asarray(ids, dtype=np.int64)
    order = np.argsort(target_ids, kind="stable")
    sorted_ids = target_ids[order]
    if np.any(sorted_ids[1:] == sorted_ids[:-1]):
        raise ReaderError(f"Target {context} are not unique")
    positions = np.searchsorted(sorted_ids, ids)
    if np.any(positions >= len(sorted_ids)) or not np.array_equal(sorted_ids[positions], ids):
        raise ReaderError(f"{context} contain entries outside the target scoping")
    return order[positions]


def same_ids(a, b) -> bool:
    """True if two ID arrays contain the same unique set of IDs."""
    a, b = np.unique(np.asarray(a, dtype=np.int64)), np.unique(np.asarray(b, dtype=np.int64))
    return np.array_equal(a, b)


def align(node_ids: IntArray, field_ids, values: FloatArray) -> FloatArray:
    """Align by IDs, leaving explicitly missing nodal results as NaN."""
    field_ids = np.asarray(field_ids, dtype=np.int64)
    values = np.asarray(values, dtype=float)
    if values.ndim == 0 or values.shape[0] != len(field_ids):
        raise ReaderError("Nodal result has an inconsistent data shape")
    if len(np.unique(field_ids)) != len(field_ids):
        raise ReaderError("Nodal result has inconsistent or duplicate node IDs")
    positions = index_of(node_ids, field_ids, "nodal field node IDs")
    result = np.full((len(node_ids), *values.shape[1:]), np.nan)
    result[positions] = values
    result.setflags(write=False)
    return result


def element_type_name(kind: int) -> str:
    try:
        return element_types(int(kind)).name
    except ValueError:
        return f"type {int(kind)}"


def solid_elements(mesh) -> tuple[IntArray, dict[str, int]]:
    """Sorted IDs of the solid elements and the count of skipped elements per type name."""
    field = mesh.elements.element_types_field
    kinds = np.asarray(field.data, dtype=np.int64).ravel()
    ids = np.asarray(field.scoping.ids, dtype=np.int64)
    if len(kinds) != len(ids):
        raise ReaderError("Element type field has an inconsistent data shape")
    unique, counts = np.unique(kinds, return_counts=True)
    solid = [int(kind) for kind in unique if element_types.shape(int(kind)) == "solid"]
    skipped = {
        element_type_name(kind): int(count)
        for kind, count in zip(unique, counts, strict=True)
        if int(kind) not in solid
    }
    return np.sort(ids[np.isin(kinds, solid)]), skipped


def _connectivity(mesh) -> tuple[IntArray, IntArray, IntArray]:
    """Element IDs, CSR offsets (n_elements + 1) and node-index connectivity."""
    field = mesh.elements.connectivities_field
    connectivity = np.asarray(field.data, dtype=np.int64)
    offsets = np.append(np.asarray(field.entity_data_offsets, dtype=np.int64), len(connectivity))
    return np.asarray(field.scoping.ids, dtype=np.int64), offsets, connectivity


@dataclass(frozen=True)
class MaterialRegion:
    material_id: int
    node_ids: IntArray
    coordinates: FloatArray
    named_selection: str | None = None


class DPFReader:
    def __init__(
        self, config: Config, server: AnyServerType | None, progress: Progress | None = None
    ):
        if server is None:
            raise ValueError("An existing DPF server is required; automatic startup is disabled")
        self.config = config
        self.server = server
        self.progress = progress or Progress()
        self.units: dict[str, dict[str, dict[str, str]]] = {}
        self.models = {}
        for name, path in config.analyses.items():
            if not path.is_file():
                raise ReaderError(f"Analysis file not found: {path}")
            self.models[name] = open_model(path, server)
        self.base_analysis = next(iter(config.loads.values())).terms[0].analysis
        self._check_temperature_results()
        with self.progress.step(
            f"Reading mesh of {self.base_analysis} ({config.analyses[self.base_analysis].name})"
        ):
            self._read_base_mesh()
        self._check_meshes()

    def _check_temperature_results(self) -> None:
        """Every temperature source must contain temperatures (BFE in an .rst)."""
        for analysis, source in self.config.temperature_sources.items():
            thermal = self.config.analyses[source].suffix.lower() == ".rth"
            result = "temperature" if thermal else "structural_temperature"
            available = {
                entry.name for entry in self.models[source].metadata.result_info.available_results
            }
            if result not in available:
                what = "nodal temperatures" if thermal else "BFE (structural) temperatures"
                hint = (
                    ""
                    if source != analysis
                    else f"; if they come from another analysis, declare "
                    f"analyses.{analysis}: {{file: ..., temperature: <analysis>}}"
                )
                raise ReaderError(
                    f"Analysis {source} (temperature source of {analysis}) contains no {what}{hint}"
                )

    @staticmethod
    def open_steps(config: Config) -> int:
        """Progress steps of ``__init__``: base mesh, one per other analysis, element checks."""
        return len(config.analyses) + 1

    def unit(self, analysis: str, quantity: str, reported) -> str:
        """The unit of ``quantity`` in ``analysis``: as recorded in the result, else declared.

        A declared unit (``analyses.<name>.units``) only fills in a unit the result does
        not record; one that contradicts a recorded unit is an error. Each resolved unit
        and its origin is kept in ``self.units`` for the traceability report.
        """
        reported = str(reported or "").strip()
        units = self.config.analysis_units.get(analysis)
        declared = getattr(units, quantity) if units is not None else None
        if reported:
            if declared and declared != reported:
                raise ReaderError(
                    f"Analysis {analysis} records {quantity} unit {reported!r} but the "
                    f"configuration declares {declared!r}; the recorded unit takes precedence, "
                    f"so remove or correct analyses.{analysis}.units.{quantity}"
                )
            unit, origin = reported, "result"
        elif declared:
            unit, origin = declared, "configuration"
        else:
            raise ReaderError(
                f"Analysis {analysis} records no {quantity} unit{self._unit_system(analysis)}; "
                f"refusing to infer units. Declare it as analyses.{analysis}.units.{quantity} "
                f"(one of {', '.join(UNIT_CHOICES[quantity])}) or re-solve with /UNITS"
            )
        self.units.setdefault(analysis, {})[quantity] = {"unit": unit, "source": origin}
        return unit

    def _unit_system(self, analysis: str) -> str:
        try:
            return (
                f" (result unit system: {self.models[analysis].metadata.result_info.unit_system!r})"
            )
        except Exception:  # noqa: BLE001 - diagnostic only
            return ""

    def _read_base_mesh(self) -> None:
        self.mesh = self.models[self.base_analysis].metadata.meshed_region
        self.node_ids = np.asarray(self.mesh.nodes.scoping.ids, dtype=np.int64)
        self.coordinates = np.asarray(self.mesh.nodes.coordinates_field.data, dtype=float)
        self.coordinates = self.coordinates * length_factor(
            self.unit(self.base_analysis, "length", self.mesh.unit)
        )
        if (
            len(np.unique(self.node_ids)) != len(self.node_ids)
            or not np.isfinite(self.coordinates).all()
        ):
            raise ReaderError("Structural mesh has duplicate node IDs or invalid coordinates")
        # Only solid elements are assessed; shell/beam/point/contact elements are skipped
        # (their layers are not inferred) and their nodes only get solid-element values.
        self.solid_element_ids, self.skipped_elements = solid_elements(self.mesh)
        if not len(self.solid_element_ids):
            raise ReaderError(
                "The mesh contains no solid elements; shell/beam layers are not inferred"
            )
        if self.skipped_elements:
            logger.warning(
                "Skipping %d non-solid elements (%s); only solid elements are assessed",
                sum(self.skipped_elements.values()),
                ", ".join(f"{name}: {count}" for name, count in self.skipped_elements.items()),
            )

    def _check_meshes(self) -> None:
        config = self.config
        self._scopings = {}
        self._stress_cache: dict[tuple[str, int], Mapping[int, FloatArray]] = {}
        self._temperature_cache: dict[tuple[str, int], Mapping[int, FloatArray]] = {}
        self._grid: pv.UnstructuredGrid | None = None
        structural = {name for load in config.loads.values() for name in load.analyses}
        # A structural temperature source is averaged per material element scoping, so its
        # elements/material IDs must match the stress mesh in both assignment modes.
        structural |= {
            source
            for source in config.temperature_sources.values()
            if config.analyses[source].suffix.lower() == ".rst"
        }
        assignments = self._material_selection_scopings() if config.material_selections else None
        for name, model in self.models.items():
            if name == self.base_analysis:
                self._check_mesh(name, model, structural, assignments)
            else:
                with self.progress.step(
                    f"Reading and checking mesh of {name} ({config.analyses[name].name})"
                ):
                    self._check_mesh(name, model, structural, assignments)
        with self.progress.step("Building material regions and checking element connectivity"):
            self._build_regions(structural)

    def _check_mesh(self, name, model, structural, assignments) -> None:
        server = self.server
        mesh = model.metadata.meshed_region
        ids = np.asarray(mesh.nodes.scoping.ids, dtype=np.int64)
        coordinates = np.asarray(mesh.nodes.coordinates_field.data) * length_factor(
            self.unit(name, "length", mesh.unit)
        )
        if len(ids) != len(self.node_ids) or not same_ids(ids, self.node_ids):
            raise ReaderError(
                f"Analysis {name} does not have the same node IDs as {self.base_analysis}"
            )
        aligned = align(self.node_ids, ids, coordinates)
        if not np.allclose(aligned, self.coordinates, rtol=1e-9, atol=1e-12):
            raise ReaderError(f"Analysis {name} has incompatible mesh coordinates")
        if name in structural:
            # Element types/IDs of every structural analysis are compared with the base
            # mesh in _build_regions, so the base solid element IDs apply to all of them.
            if assignments is not None:
                self._scopings[name] = assignments
            else:
                scopings = dpf.operators.scoping.split_on_property_type(
                    mesh=mesh,
                    requested_location=dpf.locations.elemental,
                    label1="mat",
                    server=server,
                ).outputs.mesh_scoping()
                self._scopings[name] = {}
                for index in range(len(scopings)):
                    scoping = scopings[index]
                    if self.skipped_elements:
                        ids = np.asarray(scoping.ids, dtype=np.int64)
                        ids = ids[np.isin(ids, self.solid_element_ids)]
                        if not len(ids):
                            continue  # a material of non-solid elements only
                        scoping = dpf.Scoping(
                            ids=ids.tolist(), location=dpf.locations.elemental, server=server
                        )
                    self._scopings[name][int(scopings.get_label_space(index)["mat"])] = scoping

    def _build_regions(self, structural) -> None:
        config, server = self.config, self.server
        self.regions = {}
        for material_id, scoping in self._scopings[self.base_analysis].items():
            nodal = dpf.operators.scoping.transpose(
                mesh_scoping=scoping,
                meshed_region=self.mesh,
                server=server,
            ).outputs.mesh_scoping_as_scoping()
            ids = np.sort(np.asarray(nodal.ids, dtype=np.int64))
            coordinates = self.coordinates[index_of(self.node_ids, ids, "material node IDs")]
            self.regions[material_id] = MaterialRegion(
                material_id,
                ids,
                coordinates,
                config.material_selections.get(material_id),
            )
        if set(config.materials) != set(self.regions):
            hint = (
                " (material IDs of non-solid elements only are skipped)"
                if self.skipped_elements
                else ""
            )
            raise ReaderError(
                f"Configured material IDs {sorted(config.materials)} do not match "
                f"solid-element mesh IDs {sorted(self.regions)}{hint}"
            )
        base_elements = self._element_signature(self.mesh)
        for name in sorted(structural - {self.base_analysis}):
            elements = self._element_signature(self.models[name].metadata.meshed_region)
            if not all(np.array_equal(a, b) for a, b in zip(elements, base_elements, strict=True)):
                raise ReaderError(
                    f"Structural analysis {name} has incompatible element connectivity/material IDs"
                )

    @staticmethod
    def _element_signature(mesh) -> tuple[IntArray, ...]:
        """Element IDs, material IDs, type IDs, node counts and node-ID connectivity, by element ID."""
        element_ids, offsets, connectivity = _connectivity(mesh)
        counts = np.diff(offsets)
        order = np.argsort(element_ids, kind="stable")
        sorted_ids = element_ids[order]

        def by_element(field, name):
            values = np.asarray(field.data, dtype=np.int64).ravel()
            if len(values) != len(field.scoping.ids):
                raise ReaderError(f"Element {name} field has an inconsistent data shape")
            return values[index_of(field.scoping.ids, sorted_ids, f"element {name} IDs")]

        # Gather each element's connectivity in element-ID order (vectorized CSR reorder).
        lengths = counts[order]
        starts = offsets[:-1][order]
        gather = np.repeat(starts - np.cumsum(lengths) + lengths, lengths) + np.arange(
            lengths.sum()
        )
        node_ids = np.asarray(mesh.nodes.scoping.ids, dtype=np.int64)
        return (
            sorted_ids,
            by_element(mesh.property_field("mat"), "material"),
            by_element(mesh.elements.element_types_field, "type"),
            lengths,
            node_ids[connectivity[gather]],
        )

    def _material_selection_scopings(self) -> dict:
        metadata = self.models[self.base_analysis].metadata
        all_elements = np.unique(np.asarray(self.mesh.elements.scoping.ids, dtype=np.int64))
        solid = self.solid_element_ids  # sorted and unique
        mesh_nodes = np.asarray(self.mesh.nodes.scoping.ids, dtype=np.int64)
        connectivity = None
        assigned = np.zeros(len(solid), dtype=bool)
        result = {}
        for material_id, name in self.config.material_selections.items():
            if name not in metadata.available_named_selections:
                raise ReaderError(f"Material named selection {name!r} was not found")
            selection = metadata.named_selection(name)
            ids = np.unique(np.asarray(selection.ids, dtype=np.int64))
            if selection.location == dpf.locations.elemental:
                if not np.isin(ids, all_elements).all():
                    raise ReaderError(f"Material selection {name!r} contains unknown elements")
                elements = ids
            elif selection.location == dpf.locations.nodal:
                if not np.isin(ids, self.node_ids).all():
                    raise ReaderError(f"Material selection {name!r} contains unknown nodes")
                if connectivity is None:
                    connectivity = _connectivity(self.mesh)
                element_ids, offsets, nodes = connectivity
                selected = np.isin(mesh_nodes, ids)[nodes]
                counts = np.add.reduceat(selected.astype(np.int64), offsets[:-1])
                elements = np.unique(element_ids[counts == np.diff(offsets)])
            else:
                raise ReaderError(
                    f"Material selection {name!r} has unsupported location {selection.location}"
                )
            if not len(elements):
                raise ReaderError(f"Material selection {name!r} contains no complete elements")
            elements = elements[np.isin(elements, solid)]  # non-solid elements are skipped
            if not len(elements):
                raise ReaderError(f"Material selection {name!r} contains no solid elements")
            positions = np.searchsorted(solid, elements)
            overlap = np.count_nonzero(assigned[positions])
            if overlap:
                raise ReaderError(
                    f"Material selection {name!r} overlaps another assignment at {overlap} elements"
                )
            assigned[positions] = True
            result[material_id] = dpf.Scoping(
                ids=elements.tolist(),
                location=dpf.locations.elemental,
                server=self.server,
            )
        missing = np.count_nonzero(~assigned)
        if missing:
            raise ReaderError(
                f"Material named selections leave {missing} solid elements unassigned"
            )
        return result

    def _set_time(self, analysis: str, set_id: int) -> float:
        support = self.models[analysis].metadata.time_freq_support
        if not 1 <= set_id <= support.n_sets:
            raise ReaderError(f"Analysis {analysis} has no result set {set_id}")
        if self.unit(analysis, "time", support.time_frequencies.unit) != "s":
            raise ReaderError(f"Analysis {analysis} is not a time-domain result in seconds")
        time = float(support.get_frequency(cumulative_index=set_id - 1))
        if not np.isfinite(time):
            raise ReaderError(f"Analysis {analysis} set {set_id} has an invalid time")
        return time

    def temperature_set(self, analysis: str, set_id: int) -> tuple[str, int]:
        """``(source, set)`` holding the temperature of stress set ``analysis:set_id``.

        The source is the analysis's own result unless ``analyses.<name>.temperature``
        names another one; there the set is matched by physical time, never by number.
        """
        time = self._set_time(analysis, set_id)
        source = self.config.temperature_sources[analysis]
        if source == analysis:
            return source, set_id
        support = self.models[source].metadata.time_freq_support
        if self.unit(source, "time", support.time_frequencies.unit) != "s":
            raise ReaderError(f"Temperature source {source} is not a time-domain result in seconds")
        matches = np.flatnonzero(
            np.isclose(support.time_frequencies.data, time, rtol=1e-10, atol=1e-12)
        )
        if len(matches) != 1:
            raise ReaderError(
                f"Temperature source {source} of {analysis} must have exactly "
                f"one set at time {time}; found {len(matches)}"
            )
        return source, int(matches[0]) + 1

    @staticmethod
    def _single_field(fields, context: str):
        if len(fields) != 1:
            raise ReaderError(
                f"{context}: expected one field, got {len(fields)}; layer/shape partitions are not inferred"
            )
        return fields[0]

    def stress(self, analysis: str, set_id: int) -> Mapping[int, FloatArray]:
        key = (analysis, set_id)
        if key not in self._stress_cache:
            self._set_time(analysis, set_id)
            model = self.models[analysis]
            result = {}
            for material_id, scoping in self._scopings[analysis].items():
                with self.progress.step(
                    f"Reading stress {analysis} set {set_id}, material {material_id}"
                ):
                    operator = model.results.stress()
                    operator.inputs.time_scoping.connect([set_id])
                    operator.inputs.mesh_scoping.connect(scoping)
                    operator.inputs.requested_location.connect(dpf.locations.elemental_nodal)
                    operator.inputs.bool_rotate_to_global.connect(True)
                    field = self._single_field(
                        operator.outputs.fields_container(),
                        f"Stress {analysis}/{set_id}/material {material_id}",
                    )
                    if (
                        field.location != dpf.locations.elemental_nodal
                        or field.component_count != 6
                    ):
                        raise ReaderError(
                            "Stress must be an ElementalNodal six-component global Cartesian tensor"
                        )
                    nodal = dpf.operators.averaging.elemental_nodal_to_nodal(
                        field=field,
                        mesh=model.metadata.meshed_region,
                        server=self.server,
                    ).outputs.field()
                    result[material_id] = align(
                        self.regions[material_id].node_ids,
                        nodal.scoping.ids,
                        stress_to_pa(nodal.data, self.unit(analysis, "stress", nodal.unit)),
                    )
            self._stress_cache[key] = MappingProxyType(result)
        return self._stress_cache[key]

    def temperature(self, source: str, set_id: int) -> Mapping[int, FloatArray]:
        key = (source, set_id)
        if key not in self._temperature_cache:
            self._set_time(source, set_id)
            model = self.models[source]
            mesh = model.metadata.meshed_region
            thermal = self.config.analyses[source].suffix.lower() == ".rth"
            result = {}
            for material_id, region in self.regions.items():
                with self.progress.step(
                    f"Reading temperature {source} set {set_id}, material {material_id}"
                ):
                    if thermal:
                        operator = model.results.temperature()
                        scoping = dpf.Scoping(
                            ids=region.node_ids.tolist(),
                            location=dpf.locations.nodal,
                            server=self.server,
                        )
                        location = dpf.locations.nodal
                    else:
                        operator = model.results.structural_temperature()
                        # Every .rst temperature source is mesh-checked and has material scopings.
                        scoping = self._scopings[source][material_id]
                        location = dpf.locations.elemental_nodal
                    operator.inputs.time_scoping.connect([set_id])
                    operator.inputs.mesh_scoping.connect(scoping)
                    operator.inputs.requested_location.connect(location)
                    field = self._single_field(
                        operator.outputs.fields_container(),
                        f"Temperature {source}/{set_id}/material {material_id}",
                    )
                    if field.location != location or field.component_count != 1:
                        raise ReaderError(
                            "Temperature result has an unexpected location/component count"
                        )
                    if not thermal:
                        field = dpf.operators.averaging.elemental_nodal_to_nodal(
                            field=field,
                            mesh=mesh,
                            server=self.server,
                        ).outputs.field()
                    unit = self.unit(source, "temperature", field.unit)
                    result[material_id] = align(
                        region.node_ids, field.scoping.ids, temperature_to_celsius(field.data, unit)
                    )
            self._temperature_cache[key] = MappingProxyType(result)
        return self._temperature_cache[key]

    def named_selection(self, name: str) -> IntArray:
        model = self.models[self.base_analysis]
        if name not in model.metadata.available_named_selections:
            raise ReaderError(f"Named selection {name!r} was not found")
        scoping = model.metadata.named_selection(name)
        if scoping.location == dpf.locations.nodal:
            ids = np.asarray(scoping.ids, dtype=np.int64)
        elif scoping.location == dpf.locations.elemental:
            nodal = dpf.operators.scoping.transpose(
                mesh_scoping=scoping,
                meshed_region=self.mesh,
                server=self.server,
            ).outputs.mesh_scoping_as_scoping()
            ids = np.asarray(nodal.ids, dtype=np.int64)
        else:
            raise ReaderError(
                f"Named selection {name!r} has unsupported location {scoping.location}"
            )
        if len(ids) == 0 or not np.isin(ids, self.node_ids).all():
            raise ReaderError(f"Named selection {name!r} is empty or contains unknown nodes")
        return ids

    @property
    def grid(self) -> pv.UnstructuredGrid:
        """A deep copy of the cached solid-element grid, so callers can attach point data.

        Non-solid elements, and nodes used only by them, are not part of the grid.
        """
        if self._grid is None:
            factor = length_factor(self.unit(self.base_analysis, "length", self.mesh.unit))
            self._grid = solid_grid(
                self.mesh, self.solid_element_ids, bool(self.skipped_elements), factor, self.server
            )
        return self._grid.copy(deep=True)


def solid_grid(
    mesh, solid_element_ids, has_skipped: bool, factor: float, server
) -> pv.UnstructuredGrid:
    """PyVista grid of the solid elements of ``mesh``, with coordinates in metres."""
    if has_skipped:
        mesh = dpf.operators.mesh.from_scoping(
            scoping=dpf.Scoping(
                ids=np.asarray(solid_element_ids).tolist(),
                location=dpf.locations.elemental,
                server=server,
            ),
            inclusive=0,
            mesh=mesh,
            server=server,
        ).outputs.mesh()
    # Call the server operator directly to avoid mesh.grid's full VTK import.
    operator = dpf.operators.mesh.mesh_to_pyvista(
        mesh=mesh,
        as_linear=False,
        vtk_updated=True,
        server=server,
    )
    nodes = np.asarray(operator.outputs.nodes().data, dtype=float)
    grid = pv.UnstructuredGrid(
        np.asarray(operator.outputs.cells()), np.asarray(operator.outputs.cell_types()), nodes
    )
    node_ids = np.asarray(mesh.nodes.scoping.ids, dtype=np.int64)
    element_ids = np.asarray(mesh.elements.scoping.ids, dtype=np.int64)
    if (
        grid.n_points != len(node_ids)
        or grid.n_cells != len(element_ids)
        or not same_ids(element_ids, solid_element_ids)
    ):
        raise ReaderError("DPF-to-PyVista conversion changed the mesh counts")
    coordinates = np.asarray(mesh.nodes.coordinates_field.data, dtype=float) * factor
    if not np.allclose(nodes * factor, coordinates):
        raise ReaderError("DPF-to-PyVista conversion changed the node ordering")
    grid.points = coordinates
    grid.point_data["node_id"] = node_ids
    grid.cell_data["element_id"] = element_ids
    return grid
