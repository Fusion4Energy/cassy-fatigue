import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import pyvista as pv
import yaml
from test_config import configuration

from cassy_fatigue.config import Config
from cassy_fatigue.kernel import EventResult, InvalidCode, MaterialSource, RegionResult
from cassy_fatigue.output import project_nodes, write_outputs
from cassy_fatigue.reader import MaterialRegion


def regions():
    results = []
    for material_id, nodes, a, b in [
        (1, [1, 2], [0.8, 0.5], [0.1, 0.2]),
        (2, [2, 3], [0.1, 0.4], [0.8, 0.1]),
    ]:
        coords = np.array([[float(node), 0, 0] for node in nodes])
        region = MaterialRegion(material_id, np.array(nodes), coords)
        events = []
        for event_id, values in (("pulse", a), ("secondary", b)):
            fields = {
                name: np.ones(2)
                for name in (
                    "delta_sigma_eq_Pa",
                    "delta_primary_eq_Pa",
                    "N",
                    "T_degC",
                    "de1",
                    "de2",
                    "de3",
                    "de4",
                    "de_total",
                )
            }
            fields["Vj"] = np.array(values)
            events.append(EventResult(event_id, fields, np.zeros(2, dtype=np.uint32)))
        results.append(
            RegionResult(
                region,
                f"material {material_id}",
                tuple(events),
                np.array(a) + np.array(b),
                np.zeros(2, dtype=np.uint32),
            )
        )
    return tuple(results)


def test_interface_breakdown_uses_material_governing_total():
    projection = project_nodes(np.array([1, 2, 3, 4]), regions())
    np.testing.assert_allclose(projection["U_total"][:3], [0.9, 0.9, 0.5])
    assert projection["events"]["pulse"]["Vj"][1] == 0.1
    assert projection["events"]["secondary"]["Vj"][1] == 0.8
    assert projection["winner_region"][1] == 1
    np.testing.assert_allclose(
        projection["U_total"][:3],
        projection["events"]["pulse"]["Vj"][:3] + projection["events"]["secondary"]["Vj"][:3],
    )
    assert np.isnan(projection["U_total"][3])
    assert projection["invalid_code"][3] == InvalidCode.MISSING_STRESS


def test_interface_does_not_hide_invalid_material():
    results = regions()
    results[0].events[0].invalid[1] = InvalidCode.NONFINITE_RESULT
    results[0].events[0].fields["Vj"][1] = np.nan
    results[0].invalid[1] = InvalidCode.NONFINITE_RESULT
    results[0].usage[1] = np.nan
    projection = project_nodes(np.array([1, 2, 3]), results)
    assert np.isnan(projection["U_total"][1])
    assert np.isnan(projection["events"]["pulse"]["Vj"][1])
    assert projection["events"]["secondary"]["Vj"][1] == 0.8


def test_invalid_delta_does_not_discard_other_delta_diagnostics():
    results = regions()[:1]
    results[0].events[0].invalid[0] = InvalidCode.NONFINITE_RESULT
    results[0].events[0].fields["Vj"][0] = np.nan
    results[0].invalid[0] = InvalidCode.NONFINITE_RESULT
    results[0].usage[0] = np.nan
    projection = project_nodes(np.array([1, 2]), results)
    assert np.isnan(projection["U_total"][0])
    assert np.isnan(projection["events"]["pulse"]["Vj"][0])
    assert projection["events"]["secondary"]["Vj"][0] == 0.1
    assert projection["event_invalid"]["secondary"][0] == 0


def test_projection_rejects_empty_regions_and_duplicate_ids():
    with pytest.raises(ValueError):
        project_nodes(np.array([1]), ())
    with pytest.raises(ValueError):
        project_nodes(np.array([1, 1, 2, 3]), regions())


def output_inputs(tmp_path):
    config = Config.from_dict(configuration(), tmp_path / "config.yaml")
    node_ids = np.array([1, 2, 3])
    points = np.array([[1.0, 0, 0], [2.0, 0, 0], [3.0, 0, 0]])
    grid = pv.UnstructuredGrid({pv.CellType.VERTEX: np.arange(3).reshape(-1, 1)}, points)
    grid.point_data["node_id"] = node_ids
    reader = SimpleNamespace(
        node_ids=node_ids,
        coordinates=points,
        grid=grid,
        units={"struct": {"length": {"unit": "m", "source": "result"}}},
    )
    material_path = tmp_path / "material.yaml"
    material_path.write_text("test: material\n", encoding="utf-8")
    sources = {
        1: MaterialSource(material_path, hashlib.sha256(material_path.read_bytes()).hexdigest())
    }
    return config, reader, sources


def test_vtu_echo_hashes_breakdown_and_overwrite(tmp_path):
    config, reader, sources = output_inputs(tmp_path)
    paths = write_outputs(config, reader, regions(), sources, tmp_path / "results")
    assert sorted(path.name for path in (tmp_path / "results").iterdir()) == [
        "configuration.yaml",
        "fatigue.vtu",
        "traceability.json",
    ]
    mesh = pv.read(paths["vtu"])
    assert mesh.n_points == 3
    np.testing.assert_allclose(mesh["U_total"], [0.9, 0.9, 0.5])
    assert mesh["material_id_U"][1] == 2
    assert mesh["material_id_pulse"][1] == 2
    np.testing.assert_allclose(mesh["U_total"], mesh["Vj_pulse"] + mesh["Vj_secondary"])
    assert "de_total_secondary" in mesh.point_data
    assert mesh["Vj_pulse"][1] == 0.1 and mesh["Vj_secondary"][1] == 0.8
    trace = json.loads(paths["traceability"].read_text(encoding="utf-8"))
    assert trace["materials"]["1"]["sha256"] == sources[1].sha256
    assert trace["cassy_source"]
    assert Path(trace["config_source"]) == config.source
    with pytest.raises(FileExistsError):
        write_outputs(config, reader, regions(), sources, tmp_path / "results")


def test_failed_write_leaves_no_success_outputs(tmp_path, monkeypatch):
    config, reader, sources = output_inputs(tmp_path)

    def fail(*args, **kwargs):
        raise ValueError("Traceability failure")

    monkeypatch.setattr("cassy_fatigue.output.json.dumps", fail)
    output = tmp_path / "results"
    with pytest.raises(ValueError, match="Traceability failure"):
        write_outputs(config, reader, regions(), sources, output)
    assert list(output.iterdir()) == []


def test_projection_rejects_inconsistent_events_and_unknown_nodes():
    results = regions()
    reordered = results[1].__class__(**{**results[1].__dict__, "events": results[1].events[::-1]})
    with pytest.raises(ValueError, match="inconsistent event"):
        project_nodes(np.array([1, 2, 3]), (results[0], reordered))
    with pytest.raises(RuntimeError, match="outside"):
        project_nodes(np.array([1, 2]), results)


def test_declared_units_are_echoed_and_traced(tmp_path):
    raw = configuration()
    raw["analyses"]["struct"] = {"file": "model.rst", "units": {"length": "mm", "stress": "MPa"}}
    config = Config.from_dict(raw, tmp_path / "config.yaml")
    _, reader, sources = output_inputs(tmp_path)
    reader.units = {"struct": {"length": {"unit": "mm", "source": "configuration"}}}
    paths = write_outputs(config, reader, regions(), sources, tmp_path / "results")
    echo = yaml.safe_load(paths["configuration"].read_text(encoding="utf-8"))
    assert echo["analyses"]["struct"] == {
        "file": str(tmp_path / "model.rst"),
        "units": {"length": "mm", "stress": "MPa"},
    }
    assert Config.from_dict(echo, tmp_path / "echo.yaml").analysis_units == config.analysis_units
    trace = json.loads(paths["traceability"].read_text(encoding="utf-8"))
    assert trace["analysis_units"]["struct"]["length"] == {"unit": "mm", "source": "configuration"}
