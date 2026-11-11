from copy import deepcopy

import pytest
from cassy.auxiliary.types import LoadTerm

from cassy_fatigue.config import Config, ConfigurationError, FatigueEvent, Load


def configuration():
    return {
        "code": "SDC-IC",
        "analyses": {"struct": "model.rst"},
        "materials": {1: "test"},
        "loads": {
            "cold": {"classification": "P", "sets": ["-struct:1"]},
            "hot": {"classification": "Q", "sets": ["+struct:2"]},
        },
        "fatigue_events": [{"id": "pulse", "ncycles": 100, "loads": ["hot", "cold"]}],
    }


def test_config_paths_loads_and_events(tmp_path):
    config = Config.from_dict(configuration(), tmp_path / "config.yaml")
    assert config.analyses["struct"] == tmp_path / "model.rst"
    assert config.loads["cold"] == Load("P", (LoadTerm("-", "struct", 1),), 1.0)
    assert config.fatigue_events[0] == FatigueEvent("pulse", 100, ("hot", "cold"))
    assert config.event_terms(config.fatigue_events[0]) == (
        LoadTerm("+", "struct", 2),
        LoadTerm("-", "struct", 1),
    )
    assert config.materials == {1: "test"}


def test_load_combines_sets_of_several_analyses_with_scale(tmp_path):
    raw = configuration()
    raw["analyses"]["other"] = "other.rst"
    raw["loads"]["thermal"] = {
        "classification": "Q",
        "sets": ["+other:3", " - struct : 2"],
        "scale": 0.5,
    }
    raw["fatigue_events"] = [
        {"id": "pulse", "ncycles": 100, "loads": ["thermal"]},
        {"id": "secondary", "ncycles": 10, "loads": ["hot", "cold"]},
    ]
    config = Config.from_dict(raw, tmp_path / "config.yaml")
    load = config.loads["thermal"]
    assert load.terms == (LoadTerm("+", "other", 3), LoadTerm("-", "struct", 2))
    assert load.scale == 0.5 and load.analyses == ("other", "struct")
    assert [(event.id, event.ncycles) for event in config.fatigue_events] == [
        ("pulse", 100),
        ("secondary", 10),
    ]
    # Each run reads its own temperatures, so +other:3 never takes struct's.
    assert config.temperature_sources == {"other": "other", "struct": "struct"}


def test_declared_temperature_source(tmp_path):
    raw = configuration()
    raw["analyses"] = {
        "struct": {"file": "model.rst", "temperature": "therm"},
        "other": "other.rst",
        "therm": "therm.rth",
        "unused": "unused.rst",
    }
    raw["loads"]["thermal"] = {"classification": "Q", "sets": ["+other:3", "-struct:2"]}
    config = Config.from_dict(raw, tmp_path / "config.yaml")
    assert config.temperature_sources == {"struct": "therm", "other": "other"}


@pytest.mark.parametrize(
    "analyses,match",
    [
        ({"struct": {"file": "model.rst", "temperature": "missing"}}, "unknown analysis"),
        ({"struct": {"file": "model.rst", "temperature": ""}}, "non-empty"),
        (
            {"struct": "model.rst", "therm": {"file": "t.rth", "temperature": "struct"}},
            "only a structural .rst",
        ),
    ],
)
def test_invalid_declared_temperature_source(analyses, match, tmp_path):
    raw = configuration()
    raw["analyses"] = analyses
    with pytest.raises(ConfigurationError, match=match):
        Config.from_dict(raw, tmp_path / "config.yaml")


def test_legacy_temperature_source(tmp_path):
    raw = configuration()
    raw["analyses"]["therm"] = "therm.rth"
    raw["temperature"] = {"source": "therm"}
    assert Config.from_dict(raw, tmp_path / "c.yaml").temperature_sources == {"struct": "therm"}
    raw["temperature"] = {"source": "struct"}
    assert Config.from_dict(raw, tmp_path / "c.yaml").temperature_sources == {"struct": "struct"}
    raw["analyses"]["struct"] = {"file": "model.rst", "temperature": "therm"}
    with pytest.raises(ConfigurationError, match="contradicts"):
        Config.from_dict(raw, tmp_path / "c.yaml")
    raw["temperature"] = {"source": "missing"}
    raw["analyses"]["struct"] = "model.rst"
    with pytest.raises(ConfigurationError, match="Unknown temperature source"):
        Config.from_dict(raw, tmp_path / "c.yaml")


def test_legacy_temperature_source_rejected_with_several_analyses(tmp_path):
    """The silent case: B's set would take A's temperature at the same time."""
    raw = configuration()
    raw["analyses"]["other"] = "other.rst"
    raw["temperature"] = {"source": "struct"}
    raw["loads"]["diff"] = {"classification": "Q", "sets": ["+struct:3", "-other:3"]}
    with pytest.raises(ConfigurationError, match="ambiguous with several structural analyses"):
        Config.from_dict(raw, tmp_path / "config.yaml")


@pytest.mark.parametrize(
    "term",
    [
        "struct:1",
        "+struct",
        "+struct:x",
        "+missing:1",
        "+struct:0",
        "+thermal.rth:1",
        1,
    ],
)
def test_invalid_set_term(term, tmp_path):
    raw = configuration()
    raw["analyses"]["thermal.rth"] = "thermal.rth"
    raw["loads"]["cold"]["sets"] = [term]
    with pytest.raises(ConfigurationError):
        Config.from_dict(raw, tmp_path / "config.yaml")


def test_repeated_set_in_one_load_is_rejected(tmp_path):
    raw = configuration()
    raw["loads"]["cold"]["sets"] = ["+struct:1", "-struct:1"]
    with pytest.raises(ConfigurationError, match="more than once"):
        Config.from_dict(raw, tmp_path / "config.yaml")


@pytest.mark.parametrize("loads", [[], "hot", ["+hot"], ["missing"], ["hot", "hot"], None])
def test_invalid_event_loads(loads, tmp_path):
    raw = configuration()
    raw["fatigue_events"] = [{"id": "pulse", "ncycles": 100, "loads": loads}]
    with pytest.raises(ConfigurationError):
        Config.from_dict(raw, tmp_path / "config.yaml")


@pytest.mark.parametrize(
    "old", [{"delta": ["+hot", "-cold"]}, {"state_a": ["+hot"], "state_b": ["+cold"]}]
)
def test_signed_event_forms_are_rejected_with_hint(old, tmp_path):
    raw = configuration()
    raw["fatigue_events"] = [{"id": "pulse", "ncycles": 100, **old}]
    with pytest.raises(ConfigurationError, match="sum of loads"):
        Config.from_dict(raw, tmp_path / "config.yaml")


def test_single_set_load_form_is_rejected_with_hint(tmp_path):
    raw = configuration()
    raw["loads"]["cold"] = {"analysis": "struct", "set": 1, "classification": "P"}
    with pytest.raises(ConfigurationError, match="sets: \\[\\+struct:2"):
        Config.from_dict(raw, tmp_path / "config.yaml")


def test_named_material_assignments_are_normalized_and_echo_preserved(tmp_path):
    raw = configuration()
    raw["materials"] = {"STEEL_BODY": "steel", "COPPER_BODY": "copper"}
    config = Config.from_dict(raw, tmp_path / "config.yaml")
    assert config.materials == {1: "steel", 2: "copper"}
    assert config.material_selections == {1: "STEEL_BODY", 2: "COPPER_BODY"}
    assert config.raw["materials"] == raw["materials"]


@pytest.mark.parametrize(
    "materials",
    [
        {1: "steel", "BODY": "copper"},
        {"": "steel"},
        {"  ": "steel"},
        {"1": "steel"},
        {0: "steel"},
        {False: "steel"},
    ],
)
def test_invalid_or_mixed_material_assignment_modes(materials, tmp_path):
    raw = configuration()
    raw["materials"] = materials
    with pytest.raises(ConfigurationError):
        Config.from_dict(raw, tmp_path / "config.yaml")


@pytest.mark.parametrize(
    "key,value",
    [
        ("code", "EN 13445"),
        ("materials", {True: "test"}),
        ("materials", {"1": "test"}),
        ("materials", {}),
        ("loads", {}),
        ("fatigue_events", []),
        ("temperature", {"source": "missing"}),
        ("temperature", {"source": "struct", "typo": 1}),
        ("analyses", {"struct": "model.txt"}),
        ("regions", "INTERFACE"),
    ],
)
def test_invalid_top_level(key, value, tmp_path):
    raw = configuration()
    raw[key] = value
    with pytest.raises(ConfigurationError):
        Config.from_dict(raw, tmp_path / "config.yaml")


@pytest.mark.parametrize(
    "key,value",
    [
        ("sets", []),
        ("sets", "+struct:1"),
        ("classification", "inertial"),
        ("scale", 0),
        ("scale", float("inf")),
        ("scale", True),
        ("typo", 1),
    ],
)
def test_invalid_load(key, value, tmp_path):
    raw = configuration()
    raw["loads"]["cold"][key] = value
    with pytest.raises(ConfigurationError):
        Config.from_dict(raw, tmp_path / "config.yaml")


@pytest.mark.parametrize(
    "key,value",
    [
        ("ncycles", -1),
        ("ncycles", float("nan")),
        ("ncycles", True),
        ("id", "../pulse"),
        ("typo", 1),
    ],
)
def test_invalid_event(key, value, tmp_path):
    raw = configuration()
    raw["fatigue_events"][0][key] = value
    with pytest.raises(ConfigurationError):
        Config.from_dict(raw, tmp_path / "config.yaml")


def test_unknown_duplicate_empty_and_region_validation(tmp_path):
    for change in ("unknown", "duplicate", "empty", "region"):
        raw = configuration()
        if change == "unknown":
            raw["unsupported"] = 1
        elif change == "duplicate":
            raw["fatigue_events"].append(deepcopy(raw["fatigue_events"][0]))
        elif change == "empty":
            raw["fatigue_events"][0]["loads"] = []
        else:
            raw["regions"] = [{"named_selection": "INTERFACE", "welding_f": 0.5}]
        with pytest.raises(ConfigurationError):
            Config.from_dict(raw, tmp_path / "config.yaml")
    source = tmp_path / "duplicate.yaml"
    source.write_text("code: SDC-IC\ncode: RCC-MRx\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Duplicate YAML key"):
        Config.read(source)


def test_analysis_units_are_optional_fallbacks(tmp_path):
    from cassy_fatigue.config import AnalysisUnits

    raw = configuration()
    raw["analyses"]["struct"] = {
        "file": "model.rst",
        "units": {"length": "mm", "stress": "MPa", "temperature": "degC"},
    }
    config = Config.from_dict(raw, tmp_path / "config.yaml")
    assert config.analyses["struct"] == tmp_path / "model.rst"
    assert config.analysis_units == {"struct": AnalysisUnits("mm", "MPa", "degC")}
    assert config.analysis_units["struct"].declared() == {
        "length": "mm",
        "stress": "MPa",
        "temperature": "degC",
    }
    raw["analyses"]["struct"] = {"file": "model.rst"}
    assert Config.from_dict(raw, tmp_path / "config.yaml").analysis_units == {}
    assert Config.from_dict(configuration(), tmp_path / "config.yaml").analysis_units == {}


@pytest.mark.parametrize(
    "entry,match",
    [
        ({"file": "model.rst", "units": {"length": "furlong"}}, "length must be one of"),
        ({"file": "model.rst", "units": {"pressure": "MPa"}}, "unknown keys"),
        ({"file": "model.rst", "units": {"time": "ms"}}, "time must be one of"),
        ({"path": "model.rst"}, "missing keys"),
        ({"file": "model.rst", "units": "mm"}, "must be a mapping"),
    ],
)
def test_analysis_units_are_validated(tmp_path, entry, match):
    raw = configuration()
    raw["analyses"]["struct"] = entry
    with pytest.raises(ConfigurationError, match=match):
        Config.from_dict(raw, tmp_path / "config.yaml")
