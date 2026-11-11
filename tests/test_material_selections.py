from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_config import configuration

from cassy_fatigue.config import Config
from cassy_fatigue.progress import Progress
from cassy_fatigue.reader import DPFReader, ReaderError


def reader_for(selections, monkeypatch):
    reader = object.__new__(DPFReader)
    reader.progress = Progress()
    raw = configuration()
    raw["materials"] = {name: "test" for name in selections}
    reader.config = Config.from_dict(raw, Path("config.yaml"))
    reader.base_analysis = "struct"
    reader.server = object()
    reader.node_ids = np.arange(1, 6)
    reader.solid_element_ids = np.array([10, 20])
    reader.skipped_elements = {}
    field = SimpleNamespace(
        data=np.array([0, 1, 2, 2, 3, 4]),
        entity_data_offsets=np.array([0, 3]),
        scoping=SimpleNamespace(ids=[10, 20]),
    )
    reader.mesh = SimpleNamespace(
        nodes=SimpleNamespace(scoping=SimpleNamespace(ids=reader.node_ids)),
        elements=SimpleNamespace(scoping=SimpleNamespace(ids=[10, 20]), connectivities_field=field),
    )
    metadata = SimpleNamespace(
        available_named_selections=list(selections),
        named_selection=lambda name: selections[name],
    )
    reader.models = {"struct": SimpleNamespace(metadata=metadata)}
    monkeypatch.setattr(
        "cassy_fatigue.reader.dpf.Scoping", lambda **kwargs: SimpleNamespace(**kwargs)
    )
    return reader


def scope(location, ids):
    return SimpleNamespace(location=location, ids=ids)


@pytest.mark.parametrize("nodal", [False, True])
def test_complete_partitions_accept_shared_interface_nodes(nodal, monkeypatch):
    selections = {
        "LEFT": scope("Nodal", [1, 2, 3]) if nodal else scope("Elemental", [10]),
        "RIGHT": scope("Nodal", [3, 4, 5]) if nodal else scope("Elemental", [20]),
    }
    reader = reader_for(selections, monkeypatch)
    result = reader._material_selection_scopings()
    assert result[1].ids == [10] and result[2].ids == [20]
    assert result[1].location == result[2].location == "Elemental"
    assert result[1].server is reader.server


def test_one_selection_can_cover_multiple_solver_material_regions(monkeypatch):
    reader = reader_for({"BODY": scope("Elemental", [10, 20])}, monkeypatch)
    assert reader._material_selection_scopings()[1].ids == [10, 20]


@pytest.mark.parametrize(
    "selections,match",
    [
        ({"LEFT": scope("Elemental", [10])}, "1 solid elements unassigned"),
        ({"LEFT": scope("Elemental", [10]), "RIGHT": scope("Elemental", [10, 20])}, "overlaps"),
        ({"BODY": scope("Elemental", [])}, "no complete elements"),
        ({"BODY": scope("Nodal", [3])}, "no complete elements"),
        ({"BODY": scope("Elemental", [999])}, "unknown elements"),
        ({"BODY": scope("Nodal", [999])}, "unknown nodes"),
        ({"BODY": scope("TimeFreq", [1])}, "unsupported location"),
    ],
)
def test_invalid_material_selection_is_explicit(selections, match, monkeypatch):
    reader = reader_for(selections, monkeypatch)
    with pytest.raises(ReaderError, match=match):
        reader._material_selection_scopings()


def test_missing_named_selection_is_explicit(monkeypatch):
    reader = reader_for({"BODY": scope("Elemental", [10, 20])}, monkeypatch)
    reader.models["struct"].metadata.available_named_selections = []
    with pytest.raises(ReaderError, match="not found"):
        reader._material_selection_scopings()


def test_non_solid_elements_are_left_out_of_selection_partitions(monkeypatch):
    # Element 20 is a shell: selections need not cover it, and it is dropped from them.
    reader = reader_for({"BODY": scope("Elemental", [10, 20])}, monkeypatch)
    reader.solid_element_ids = np.array([10])
    assert reader._material_selection_scopings()[1].ids == [10]
    reader = reader_for({"BODY": scope("Elemental", [10])}, monkeypatch)
    reader.solid_element_ids = np.array([10])
    assert reader._material_selection_scopings()[1].ids == [10]
    reader = reader_for(
        {"LEFT": scope("Elemental", [10]), "SHELL": scope("Nodal", [3, 4, 5])}, monkeypatch
    )
    reader.solid_element_ids = np.array([10])
    with pytest.raises(ReaderError, match="'SHELL' contains no solid elements"):
        reader._material_selection_scopings()
