from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from cassy_fatigue.progress import Progress
from cassy_fatigue.reader import (
    DPFReader,
    MaterialRegion,
    ReaderError,
    align,
    stress_to_pa,
    temperature_to_celsius,
)


@pytest.mark.parametrize("unit,factor", [("Pa", 1), ("kPa", 1e3), ("MPa", 1e6), ("GPa", 1e9)])
def test_stress_units(unit, factor):
    np.testing.assert_array_equal(stress_to_pa(np.array([1.0, 2.0]), unit), [factor, 2 * factor])


@pytest.mark.parametrize(
    "unit,values",
    [
        ("degC", [0.0, 100.0]),
        ("K", [273.15, 373.15]),
        ("degF", [32.0, 212.0]),
    ],
)
def test_temperature_units(unit, values):
    np.testing.assert_allclose(temperature_to_celsius(np.array(values), unit), [0, 100], atol=1e-12)


def test_unknown_units_do_not_fall_back():
    with pytest.raises(ReaderError):
        stress_to_pa(np.ones(1), "psi")
    with pytest.raises(ReaderError):
        temperature_to_celsius(np.ones(1), "")


def test_align_by_ids_preserves_inputs_and_marks_missing():
    values = np.array([[10.0, 20.0], [30.0, 40.0]])
    result = align(np.array([1, 2, 3]), [3, 1], values)
    np.testing.assert_array_equal(result[[0, 2]], [[30, 40], [10, 20]])
    assert np.isnan(result[1]).all()
    assert not result.flags.writeable
    np.testing.assert_array_equal(values, [[10, 20], [30, 40]])
    with pytest.raises(ReaderError):
        align(np.array([1, 2]), [1, 1], values)
    with pytest.raises(ReaderError):
        align(np.array([1, 2]), [1, 3], values)


def test_reader_requires_existing_server():
    with pytest.raises(ValueError, match="automatic startup is disabled"):
        DPFReader(None, None)


def test_temperature_sets_match_time_not_set_number():
    reader = object.__new__(DPFReader)
    reader.progress = Progress()
    reader.config = SimpleNamespace(temperature_sources={"struct": "therm"}, analysis_units={})
    reader.units = {}
    structural = SimpleNamespace(
        n_sets=2,
        time_frequencies=SimpleNamespace(unit="s"),
        get_frequency=lambda cumulative_index: [2.0, 5.0][cumulative_index],
    )
    thermal = SimpleNamespace(
        n_sets=3,
        time_frequencies=SimpleNamespace(
            unit="s",
            data=np.array([1.0, 2.0, 5.0]),
        ),
    )
    reader.models = {
        name: SimpleNamespace(metadata=SimpleNamespace(time_freq_support=support))
        for name, support in (("struct", structural), ("therm", thermal))
    }
    assert reader.temperature_set("struct", 2) == ("therm", 3)
    thermal.time_frequencies.data = np.array([2.0, 5.0, 5.0])
    with pytest.raises(ReaderError, match="exactly one"):
        reader.temperature_set("struct", 2)
    with pytest.raises(ReaderError, match="no result set"):
        reader.temperature_set("struct", 3)


def test_thermal_nodal_kelvin_conversion_alignment_and_cache():
    reader = object.__new__(DPFReader)
    reader.progress = Progress()
    reader.config = SimpleNamespace(analyses={"therm": Path("model.rth")}, analysis_units={})
    reader.units = {}
    reader.server = object()
    reader.regions = {1: MaterialRegion(1, np.array([1, 2, 3]), np.zeros((3, 3)))}
    reader._temperature_cache = {}
    reader._set_time = Mock(return_value=1.0)
    field = SimpleNamespace(
        location="Nodal",
        component_count=1,
        unit="K",
        data=np.array([373.15, 273.15]),
        scoping=SimpleNamespace(ids=np.array([3, 1])),
    )
    operator = Mock()
    operator.outputs.fields_container.return_value = [field]
    factory = Mock(return_value=operator)
    reader.models = {
        "therm": SimpleNamespace(
            metadata=SimpleNamespace(meshed_region=object()),
            results=SimpleNamespace(temperature=factory),
        )
    }
    with patch("cassy_fatigue.reader.dpf.Scoping", return_value=object()):
        result = reader.temperature("therm", 1)
        assert reader.temperature("therm", 1) is result
    factory.assert_called_once()
    np.testing.assert_allclose(result[1], [0, np.nan, 100], atol=1e-12, equal_nan=True)
    operator.inputs.requested_location.connect.assert_called_once_with("Nodal")


def test_bfe_temperature_uses_assignment_scoping_not_solver_material_id():
    reader = object.__new__(DPFReader)
    reader.progress = Progress()
    reader.config = SimpleNamespace(analyses={"struct": Path("model.rst")}, analysis_units={})
    reader.units = {}
    reader.server = object()
    reader.regions = {1: MaterialRegion(1, np.array([3, 4]), np.zeros((2, 3)), "COPPER")}
    reader._temperature_cache = {}
    reader._set_time = Mock(return_value=1.0)
    scoping = object()
    reader._scopings = {"struct": {1: scoping}}
    field = SimpleNamespace(
        location="ElementalNodal",
        component_count=1,
        unit="degC",
        data=np.array([20.0, 120.0]),
        scoping=SimpleNamespace(ids=np.array([3, 4])),
    )
    operator = Mock()
    operator.outputs.fields_container.return_value = [field]
    mesh = Mock()
    mesh.property_field.side_effect = AssertionError("Must use the assignment scoping")
    reader.models = {
        "struct": SimpleNamespace(
            metadata=SimpleNamespace(meshed_region=mesh),
            results=SimpleNamespace(structural_temperature=Mock(return_value=operator)),
        )
    }
    average = Mock()
    average.outputs.field.return_value = field
    with patch(
        "cassy_fatigue.reader.dpf.operators.averaging.elemental_nodal_to_nodal",
        return_value=average,
    ):
        result = reader.temperature("struct", 1)
    operator.inputs.mesh_scoping.connect.assert_called_once_with(scoping)
    np.testing.assert_array_equal(result[1], [20.0, 120.0])


def test_index_of_and_same_ids():
    from cassy_fatigue.reader import index_of, same_ids

    np.testing.assert_array_equal(index_of(np.array([30, 10, 20]), [20, 30, 10]), [2, 0, 1])
    with pytest.raises(ReaderError, match="outside"):
        index_of(np.array([1, 2]), [3])
    with pytest.raises(ReaderError, match="not unique"):
        index_of(np.array([1, 1]), [1])
    assert same_ids([3, 1, 2], [1, 2, 3]) and not same_ids([1, 2], [1, 3])


def element_mesh(element_ids, materials, connectivity, order=None):
    """Fake DPF mesh: CSR connectivity as node indices, properties scoped by element ID."""
    order = list(range(len(element_ids))) if order is None else order
    ids = [element_ids[i] for i in order]
    blocks = [connectivity[i] for i in order]
    offsets = np.cumsum([0] + [len(block) for block in blocks[:-1]])
    field = SimpleNamespace(
        data=np.concatenate(blocks), entity_data_offsets=offsets, scoping=SimpleNamespace(ids=ids)
    )
    prop = lambda values: SimpleNamespace(
        data=np.asarray(values), scoping=SimpleNamespace(ids=element_ids)
    )
    return SimpleNamespace(
        nodes=SimpleNamespace(scoping=SimpleNamespace(ids=np.array([101, 102, 103, 104, 105]))),
        elements=SimpleNamespace(connectivities_field=field, element_types_field=prop([5, 5])),
        property_field=lambda name: prop(materials),
    )


def test_element_signature_is_order_independent_and_detects_changes():
    signature = DPFReader._element_signature
    base = signature(element_mesh([10, 20], [1, 2], [[0, 1, 2], [2, 3, 4]]))
    same = lambda mesh: all(np.array_equal(a, b) for a, b in zip(signature(mesh), base))
    assert same(element_mesh([10, 20], [1, 2], [[0, 1, 2], [2, 3, 4]], order=[1, 0]))
    assert not same(element_mesh([10, 20], [1, 2], [[0, 1, 2], [2, 3, 0]]))
    assert not same(element_mesh([10, 20], [1, 1], [[0, 1, 2], [2, 3, 4]]))
    assert not same(element_mesh([10, 20], [1, 2], [[0, 1, 2], [2, 3]]))
    np.testing.assert_array_equal(base[4], [101, 102, 103, 103, 104, 105])
    broken = element_mesh([10, 20], [1, 2], [[0, 1, 2], [2, 3, 4]])
    broken.property_field = lambda name: SimpleNamespace(
        data=np.array([1]), scoping=SimpleNamespace(ids=[10])
    )
    with pytest.raises(ReaderError, match="outside"):
        signature(broken)


def unit_reader(declared=None):
    from cassy_fatigue.config import AnalysisUnits

    reader = object.__new__(DPFReader)
    reader.config = SimpleNamespace(
        analysis_units={} if declared is None else {"struct": AnalysisUnits(**declared)}
    )
    reader.units = {}
    reader.models = {
        "struct": SimpleNamespace(
            metadata=SimpleNamespace(result_info=SimpleNamespace(unit_system="User defined"))
        )
    }
    return reader


def test_recorded_units_take_precedence_and_declarations_fill_gaps():
    reader = unit_reader({"length": "mm", "stress": "MPa"})
    assert reader.unit("struct", "length", "") == "mm"
    assert reader.unit("struct", "stress", "MPa") == "MPa"
    assert reader.units["struct"] == {
        "length": {"unit": "mm", "source": "configuration"},
        "stress": {"unit": "MPa", "source": "result"},
    }
    assert unit_reader().unit("struct", "length", "m") == "m"


def test_missing_or_contradicting_units_are_errors():
    with pytest.raises(
        ReaderError,
        match=r"records no length unit \(result unit system: "
        r"'User defined'\).*analyses\.struct\.units\.length",
    ):
        unit_reader().unit("struct", "length", "")
    with pytest.raises(
        ReaderError, match="records stress unit 'Pa' but the configuration declares 'MPa'"
    ):
        unit_reader({"stress": "MPa"}).unit("struct", "stress", "Pa")


def test_declared_time_unit_allows_time_mapping():
    reader = unit_reader({"time": "s"})
    reader.progress = Progress()
    reader.config.temperature_sources = {"struct": "struct"}
    reader.models["struct"].metadata.time_freq_support = SimpleNamespace(
        n_sets=1,
        time_frequencies=SimpleNamespace(unit=""),
        get_frequency=lambda cumulative_index: 1.0,
    )
    assert reader.temperature_set("struct", 1) == ("struct", 1)
    reader.config.analysis_units = {}
    with pytest.raises(ReaderError, match="records no time unit"):
        reader.temperature_set("struct", 1)


def typed_mesh(ids, kinds, materials=None):
    field = lambda values: SimpleNamespace(
        data=np.asarray(values), scoping=SimpleNamespace(ids=ids)
    )
    return SimpleNamespace(
        elements=SimpleNamespace(element_types_field=field(kinds)),
        property_field=lambda name: field(materials),
    )


def test_solid_elements_skip_shells_beams_and_points():
    from ansys.dpf.core.elements import element_types

    from cassy_fatigue.reader import solid_elements

    kinds = [
        element_types.Hex8.value,
        element_types.Quad4.value,
        element_types.Tet10.value,
        element_types.Line2.value,
        element_types.Quad4.value,
        element_types.Point1.value,
    ]
    ids, skipped = solid_elements(typed_mesh([60, 50, 40, 30, 20, 10], kinds))
    np.testing.assert_array_equal(ids, [40, 60])
    assert skipped == {"Quad4": 2, "Line2": 1, "Point1": 1}
    ids, skipped = solid_elements(typed_mesh([1, 2], [element_types.Hex8.value] * 2))
    np.testing.assert_array_equal(ids, [1, 2])
    assert skipped == {}


def test_material_id_scopings_drop_non_solid_elements(monkeypatch):
    reader = object.__new__(DPFReader)
    reader.server = object()
    reader.solid_element_ids = np.array([10, 11])
    reader.skipped_elements = {"Quad4": 3}
    reader._scopings = {}
    # mat 1: solids 10, 11 and shell 12; mat 2: shells 20, 21 only.
    labels = [{"mat": 1}, {"mat": 2}]
    containers = [SimpleNamespace(ids=[10, 11, 12]), SimpleNamespace(ids=[20, 21])]
    split = type(
        "Split",
        (),
        {
            "__len__": lambda self: 2,
            "__getitem__": lambda self, i: containers[i],
            "get_label_space": lambda self, i: labels[i],
        },
    )()
    monkeypatch.setattr(
        "cassy_fatigue.reader.dpf.operators.scoping.split_on_property_type",
        lambda **kwargs: SimpleNamespace(outputs=SimpleNamespace(mesh_scoping=lambda: split)),
    )
    monkeypatch.setattr(
        "cassy_fatigue.reader.dpf.Scoping", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    reader.node_ids = np.array([1, 2])
    reader.coordinates = np.zeros((2, 3))
    reader.units = {}
    reader.config = SimpleNamespace(analysis_units={})
    mesh = SimpleNamespace(
        unit="m",
        nodes=SimpleNamespace(
            scoping=SimpleNamespace(ids=reader.node_ids),
            coordinates_field=SimpleNamespace(data=np.zeros((2, 3))),
        ),
    )
    reader._check_mesh(
        "struct", SimpleNamespace(metadata=SimpleNamespace(meshed_region=mesh)), {"struct"}, None
    )
    assert list(reader._scopings["struct"]) == [1]
    assert reader._scopings["struct"][1].ids == [10, 11]


def test_solid_grid_drops_shells_and_shell_only_nodes_live(dpf_live_server):
    from ansys.dpf import core as dpf

    from cassy_fatigue.reader import solid_elements, solid_grid

    server = dpf_live_server
    mesh = dpf.MeshedRegion(num_nodes=9, num_elements=2, server=server)
    corners = [
        [0, 0, 0],
        [1, 0, 0],
        [1, 1, 0],
        [0, 1, 0],
        [0, 0, 1],
        [1, 0, 1],
        [1, 1, 1],
        [0, 1, 1],
    ]
    for index, xyz in enumerate([*corners, [2, 0, 0]]):  # node 9: shell-only
        mesh.nodes.add_node(index + 1, xyz)
    mesh.elements.add_solid_element(1, list(range(8)))
    mesh.elements.add_shell_element(2, [1, 8, 5, 2])  # a shell sharing a hex face edge
    solid, skipped = solid_elements(mesh)
    np.testing.assert_array_equal(solid, [1])
    assert skipped == {"Quad4": 1}
    grid = solid_grid(mesh, solid, True, 1e-3, server)
    assert (grid.n_cells, grid.n_points) == (1, 8)
    np.testing.assert_array_equal(np.sort(grid.point_data["node_id"]), np.arange(1, 9))
    np.testing.assert_array_equal(grid.cell_data["element_id"], [1])
    ids = list(grid.point_data["node_id"])
    np.testing.assert_allclose(grid.points[ids.index(7)], [1e-3, 1e-3, 1e-3])
