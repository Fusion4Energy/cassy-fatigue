"""Qt-free GUI model: YAML round trips, path handling, renames and the material catalogue."""

from dataclasses import fields
from pathlib import Path

import pytest
import yaml

from cassy_fatigue.config import Config, ConfigurationError
from cassy_fatigue.gui import model
from cassy_fatigue.kernel import list_materials

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"


def same_config(a: Config, b: Config) -> bool:
    return all(
        getattr(a, f.name) == getattr(b, f.name)
        for f in fields(Config)
        if f.name not in ("raw", "source")
    )


@pytest.mark.parametrize("name", ["two_state.yaml", "named_materials.yaml"])
def test_round_trip_keeps_configuration(name):
    path = BENCHMARKS / name
    project, notices = model.load_project(path)
    assert notices == []
    assert Path(project["analyses"]["struct"]).is_absolute()
    result = model.validate(project, path)
    assert isinstance(result, Config), result
    assert same_config(result, Config.read(path))
    assert model.to_raw(project, path)["analyses"] == {"struct": "fixtures/two_materials.rst"}


def test_save_elsewhere_rewrites_relative_paths(tmp_path):
    project, _ = model.load_project(BENCHMARKS / "two_state.yaml")
    target = tmp_path / "sub" / "copy.yaml"
    target.parent.mkdir()
    model.save_project(project, target)
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    # Relative when possible; across Windows drives (e.g. CI: checkout on D:, temp on C:)
    # no relative path exists and the absolute path is kept.
    same_drive = Path(tmp_path).anchor.lower() == BENCHMARKS.resolve().anchor.lower()
    assert Path(raw["analyses"]["struct"]).is_absolute() is not same_drive
    assert same_config(Config.read(target), Config.read(BENCHMARKS / "two_state.yaml"))
    assert not list(target.parent.glob(".*.tmp"))


@pytest.mark.parametrize(
    "events",
    [
        [
            {"id": "pulse", "ncycles": 100, "delta": ["+hot", "-cold"]},
            {"id": "secondary", "ncycles": 10, "delta": ["+hot"]},
        ],
        [
            {"id": "pulse", "ncycles": 100, "state_a": ["+hot"], "state_b": ["+cold"]},
            {"id": "secondary", "ncycles": 10, "state_a": ["+hot"], "state_b": []},
        ],
    ],
)
def test_former_signed_format_converts_exactly(tmp_path, events):
    raw = yaml.safe_load((BENCHMARKS / "two_state.yaml").read_text(encoding="utf-8"))
    raw["analyses"]["struct"] = str(BENCHMARKS / "fixtures" / "two_materials.rst")
    raw["loads"] = {
        "cold": {"analysis": "struct", "set": 1, "classification": "P"},
        "hot": {"analysis": "struct", "set": 2, "classification": "Q"},
    }
    raw["fatigue_events"] = events
    path = tmp_path / "former.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="replaced by a signed set list"):
        Config.read(path)
    project, notices = model.load_project(path)
    assert len(notices) == 5  # two loads, one negated load, two events
    assert project["loads"]["cold_neg"] == {"classification": "P", "sets": ["-struct:1"]}
    assert [event["loads"] for event in project["fatigue_events"]] == [["hot", "cold_neg"], ["hot"]]
    converted = model.validate(project, path)
    assert isinstance(converted, Config)
    # Same stress ranges as the shipped example, which already uses the new form.
    example = Config.read(BENCHMARKS / "two_state.yaml")
    for new, ref in zip(converted.fatigue_events, example.fatigue_events, strict=True):
        assert converted.event_terms(new) == example.event_terms(ref)
        assert [converted.loads[n].classification for n in new.loads] == [
            example.loads[n].classification for n in ref.loads
        ]


def test_invalid_project_reports_error_and_can_be_saved_as_draft(tmp_path):
    project = model.new_project()
    assert "analyses" in model.validate(project, tmp_path / "draft.yaml")
    model.save_project(project, tmp_path / "draft.yaml")
    reloaded, _ = model.load_project(tmp_path / "draft.yaml")
    assert reloaded["code"] == "SDC-IC"
    assert reloaded["materials"] == {} and reloaded["loads"] == {}


def test_load_rejects_duplicate_and_unknown_keys(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("code: SDC-IC\ncode: RCC-MR\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Duplicate"):
        model.load_project(path)
    path.write_text("code: SDC-IC\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Unknown"):
        model.load_project(path)


def test_rename_and_remove_load_update_events():
    project, _ = model.load_project(BENCHMARKS / "two_state.yaml")
    model.rename_load(project, "hot", "thermal")
    assert list(project["loads"]) == ["thermal", "cold_removed"]
    assert [event["loads"] for event in project["fatigue_events"]] == [
        ["thermal", "cold_removed"],
        ["thermal"],
    ]
    with pytest.raises(ConfigurationError, match="already exists"):
        model.rename_load(project, "thermal", "cold_removed")
    model.remove_load(project, "cold_removed")
    assert [event["loads"] for event in project["fatigue_events"]] == [["thermal"], ["thermal"]]


def test_rename_and_remove_analysis():
    project, _ = model.load_project(BENCHMARKS / "two_state.yaml")
    project["analyses"]["therm"] = "/x/therm.rth"
    model.set_analysis_temperature(project, "struct", "therm")
    model.rename_analysis(project, "struct", "mech")
    assert project["analysis_temperature"] == {"mech": "therm"}
    assert [load["sets"] for load in project["loads"].values()] == [["+mech:2"], ["-mech:1"]]
    model.rename_analysis(project, "therm", "thermal")
    assert project["analysis_temperature"] == {"mech": "thermal"}
    # Removing a source keeps the reference so validation reports it (no silent switch).
    model.remove_analysis(project, "thermal")
    assert project["analysis_temperature"] == {"mech": "thermal"}
    assert "unknown analysis 'thermal'" in model.validate(project, BENCHMARKS / "x.yaml")
    model.remove_analysis(project, "mech")
    assert project["analysis_temperature"] == {}


def test_per_analysis_temperature_round_trip(tmp_path):
    project, _ = model.load_project(BENCHMARKS / "two_state.yaml")
    project["analyses"]["therm"] = str(tmp_path / "therm.rth")
    assert model.temperature_text(project, "struct") == "own"
    model.set_analysis_temperature(project, "struct", "therm")
    assert model.temperature_text(project, "struct") == "from therm"
    path = tmp_path / "config.yaml"
    raw = model.to_raw(project, path)
    assert raw["analyses"]["struct"]["temperature"] == "therm" and "temperature" not in raw
    config = model.validate(project, path)
    assert isinstance(config, Config) and config.temperature_sources == {"struct": "therm"}
    model.save_project(project, path)
    assert model.load_project(path)[0]["analysis_temperature"] == {"struct": "therm"}
    model.set_analysis_temperature(project, "struct", "struct")
    assert project["analysis_temperature"] == {}


@pytest.mark.parametrize(
    "source,expected",
    [
        ("a", {"b": "a"}),  # the old global source applied to every structural run
        ("therm", {"a": "therm", "b": "therm"}),
    ],
)
def test_legacy_temperature_source_converted_per_analysis(tmp_path, source, expected):
    path = tmp_path / "old.yaml"
    path.write_text(
        "code: SDC-IC\nanalyses: {a: a.rst, b: b.rst, therm: t.rth}\n"
        f"temperature: {{source: {source}}}\nmaterials: {{1: x}}\n"
        "loads: {l: {classification: Q, sets: [+a:3, -b:3]}}\n"
        "fatigue_events: [{id: e, ncycles: 1, loads: [l]}]\n",
        encoding="utf-8",
    )
    project, notices = model.load_project(path)
    assert project["analysis_temperature"] == expected
    assert "temperature" not in project and any("temperature.source" in n for n in notices)
    assert "temperature" not in model.to_raw(project, path)


def test_unique_name():
    assert model.unique_name("load", []) == "load"
    assert model.unique_name("load", ["load", "load_2"]) == "load_3"


def test_material_catalogue_reasons_per_code():
    sdc = dict(list_materials("SDC-IC"))
    mrx = dict(list_materials("RCC-MRx"))
    assert sdc["SS316L(N)-IG_SDC-IC"] is None
    assert sdc["XM-19 SS_SDC-IC"] is None  # stress-based curve, allowed for SDC-IC only
    assert "strain-based" in mrx["XM-19 SS_SDC-IC"]
    assert "mean stress" in sdc["Inconel 718 RCC-MRx"]
    with pytest.raises(ConfigurationError, match="not found"):
        list_materials("SDC-IC", Path("/nonexistent/materials"))


def test_declared_units_round_trip_rename_and_remove(tmp_path):
    project, _ = model.load_project(BENCHMARKS / "two_state.yaml")
    path = BENCHMARKS / "two_state.yaml"
    assert model.units_text(project, "struct") == "from result"
    model.set_analysis_units(project, "struct", {"length": "mm", "stress": "MPa", "time": None})
    assert model.to_raw(project, path)["analyses"] == {
        "struct": {"file": "fixtures/two_materials.rst", "units": {"length": "mm", "stress": "MPa"}}
    }
    assert model.units_text(project, "struct") == "length mm, stress MPa"
    config = model.validate(project, path)
    assert isinstance(config, Config) and config.analysis_units["struct"].length == "mm"
    target = tmp_path / "units.yaml"
    model.save_project(project, target)
    reloaded, _ = model.load_project(target)
    assert reloaded["analysis_units"] == {"struct": {"length": "mm", "stress": "MPa"}}
    assert Path(reloaded["analyses"]["struct"]).is_absolute()
    model.rename_analysis(reloaded, "struct", "main")
    assert reloaded["analysis_units"] == {"main": {"length": "mm", "stress": "MPa"}}
    model.set_analysis_units(reloaded, "main", {"length": None})
    assert reloaded["analysis_units"] == {}
    model.set_analysis_units(reloaded, "main", {"length": "mm"})
    model.remove_analysis(reloaded, "main")
    assert reloaded["analysis_units"] == {}
