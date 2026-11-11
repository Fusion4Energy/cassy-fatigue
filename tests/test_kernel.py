import numpy as np
import pytest
import yaml
from cassy.designcodes.map import PATH_CODES
from cassy.general.material import Material, PropertyFactory
from test_config import configuration

from cassy_fatigue.config import Config, ConfigurationError
from cassy_fatigue.kernel import (
    EventConfig,
    InvalidCode,
    NodalEvent,
    assess_event,
    evaluate,
    load_materials,
    require_array_support,
    von_mises,
)
from cassy_fatigue.reader import MaterialRegion
from cassy_fatigue.states import build_state, event_temperature


@pytest.fixture
def material(tmp_path):
    def constant(value):
        return {"type": "constant", "value": value}

    data = {
        "properties": {
            "Poisson Ratio": constant(0.3),
            "Young Modulus": constant(200e9),
            "Min Yield Strength": constant(200e6),
            "Min Tensile Strength": constant(500e6),
            "K": constant(800),
            "m": constant(0.2),
            "Keps": constant(1),
            "Kmu": constant(1),
            "Fatigue": {
                "type": "fatigue",
                "ftype": "strain",
                "values": {
                    "T": [20, 200],
                    "N_cycles": [100, 1000, 10000],
                    "y": [[0.002, 0.0018], [0.001, 0.0009], [0.0005, 0.0004]],
                },
            },
        }
    }
    path = tmp_path / "test.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return Material(path)


def test_von_mises_components_hydrostatic_and_shape():
    values = np.array(
        [
            [4.0, 0, 0, 0, 0, 0],
            [7, 7, 7, 0, 0, 0],
            [0, 0, 0, 3, 0, 0],
            [0, 0, 0, 0, 3, 0],
            [0, 0, 0, 0, 0, 3],
        ]
    )
    np.testing.assert_allclose(
        von_mises(values), [4, 0, 3 * np.sqrt(3), 3 * np.sqrt(3), 3 * np.sqrt(3)]
    )
    with pytest.raises(ValueError):
        von_mises(np.ones((6, 2)))


def test_scalar_only_cassy_is_rejected_not_emulated(monkeypatch):
    monkeypatch.setattr(PropertyFactory, "create_property", lambda *args: lambda values: 1.0)
    with pytest.raises(ConfigurationError, match="scalar fallback is disabled"):
        require_array_support()


def test_unknown_material_fails_before_dpf(tmp_path):
    config = Config.from_dict(configuration(), tmp_path / "config.yaml")
    with pytest.raises(ConfigurationError, match="Unknown CASSY material"):
        load_materials(config)


@pytest.mark.parametrize("code", ["SDC-IC", "RCC-MRx", "RCC-MR"])
def test_kernel_matches_actual_scalar_cassy(code, material):
    require_array_support()
    rng = np.random.default_rng(17)
    delta = rng.normal(size=(20, 6)) * 2e7
    primary = delta * 0.1
    temperature = np.linspace(20, 200, 20)
    factor = np.linspace(1, 1.5, 20)
    dpa = np.zeros(20)
    result = assess_event(code, material, "pulse", 100, temperature, dpa, factor, delta, primary)
    assert not result.invalid.any()
    for index in range(20):
        scalar = PATH_CODES[code].computeVj(
            NodalEvent(
                EventConfig(float(temperature[index]), 0.0, float(factor[index])),
                float(von_mises(delta[index : index + 1])[0]),
                float(von_mises(primary[index : index + 1])[0]),
            ),
            material,
        )
        for original, output in [
            ("de1", "de1"),
            ("de2", "de2"),
            ("de3", "de3"),
            ("de4", "de4"),
            ("de tot", "de_total"),
            ("N", "N"),
        ]:
            np.testing.assert_allclose(result.fields[output][index], scalar[original], rtol=1e-12)
        assert result.fields["Vj"][index] == pytest.approx(100 / scalar["N"], rel=1e-12)


def test_one_node_hand_calculation(material):
    delta = np.array([[200e6, 0, 0, 0, 0, 0]])
    result = assess_event(
        "SDC-IC",
        material,
        "pulse",
        100,
        np.array([20.0]),
        np.zeros(1),
        np.ones(1),
        delta,
        np.zeros((1, 6)),
    )
    de1 = 2 / 3 * 1.3 * 200e6 / 200e9
    N = 10 ** (3 + np.log10(de1 / 0.001) / np.log10(0.0005 / 0.001))
    assert result.fields["de1"][0] == pytest.approx(de1)
    assert result.fields["de2"][0] == result.fields["de3"][0] == result.fields["de4"][0] == 0
    assert result.fields["N"][0] == pytest.approx(N)
    assert result.fields["Vj"][0] == pytest.approx(100 / N)


def test_failed_nodes_are_flagged_nan_without_scalar_fallback(material, monkeypatch):
    delta = np.zeros((5, 6))
    delta[:, 0] = [100e6, 100e6, 100e6, 100e6, 1e9]
    delta[1, 1] = np.nan
    temperature = np.array([20.0, 20.0, np.nan, 500.0, 20.0])
    code = PATH_CODES["SDC-IC"]
    calls = []
    original = code.computeVj

    def array_only(event, mat):
        calls.append(np.ndim(event.PQF))
        return original(event, mat)

    monkeypatch.setattr(code, "computeVj", array_only)
    result = assess_event(
        "SDC-IC",
        material,
        "pulse",
        100,
        temperature,
        np.zeros(5),
        np.ones(5),
        delta,
        np.zeros((5, 6)),
    )
    np.testing.assert_array_equal(
        result.invalid,
        [
            0,
            InvalidCode.MISSING_STRESS,
            InvalidCode.MISSING_TEMPERATURE,
            InvalidCode.NONFINITE_RESULT,
            InvalidCode.NONFINITE_RESULT,
        ],
    )
    assert np.isfinite(result.fields["Vj"][0])
    assert np.isnan(result.fields["Vj"][1:]).all()
    assert np.isnan(result.fields["N"][1:]).all()
    assert calls == [1]  # one vectorized call; failed nodes are not re-run as scalars


class FakeReader:
    def __init__(self):
        self.regions = {1: MaterialRegion(1, np.array([3, 1]), np.zeros((2, 3)))}
        self.values = {1: np.full((2, 6), 2.0), 2: np.full((2, 6), 5.0)}

    def stress(self, analysis, set_id):
        return {1: self.values[set_id]}

    def temperature_set(self, analysis, set_id):
        return analysis, set_id

    def temperature(self, source, set_id):
        return {1: np.array([20.0, np.nan]) if set_id == 1 else np.array([120.0, 120.0])}

    def named_selection(self, name):
        return np.array([1])


def test_event_sums_loads_primary_only_and_temperature_envelope(tmp_path):
    raw = configuration()
    raw["loads"]["thermal"] = {
        "classification": "Q",
        "sets": ["+struct:2", "-struct:1"],
        "scale": 2,
    }
    raw["fatigue_events"] = [{"id": "pulse", "ncycles": 1, "loads": ["thermal", "cold"]}]
    config = Config.from_dict(raw, tmp_path / "config.yaml")
    reader = FakeReader()
    event = config.fatigue_events[0]
    state = build_state(reader, config, event, 1)
    # thermal = 2 * (5 - 2) = 6 (Q); cold = -2 (P); the event is their sum.
    np.testing.assert_array_equal(state.total, np.full((2, 6), 4))
    np.testing.assert_array_equal(state.primary, np.full((2, 6), -2))
    np.testing.assert_array_equal(reader.values[1], np.full((2, 6), 2))
    T = event_temperature(reader, config, event, 1)
    assert T[0] == 120 and np.isnan(T[1])


def test_region_parameters_and_overlap(tmp_path, material):
    raw = configuration()
    raw["regions"] = [{"named_selection": "weld", "welding_f": 1.5, "dpa": 1}]
    config = Config.from_dict(raw, tmp_path / "config.yaml")
    result = evaluate(config, FakeReader(), {1: material})[0]
    np.testing.assert_array_equal(result.events[0].fields["dpa"], [0, 1])
    np.testing.assert_array_equal(result.events[0].fields["welding_f"], [1, 1.5])
    raw["regions"].append({"named_selection": "overlapping", "dpa": 1})
    with pytest.raises(ConfigurationError, match="overlaps"):
        evaluate(Config.from_dict(raw, tmp_path / "config.yaml"), FakeReader(), {1: material})


@pytest.mark.parametrize("code", ["SDC-IC", "RCC-MRx", "RCC-MR"])
def test_multiple_events_scalar_usage_and_total(tmp_path, material, code):
    raw = configuration()
    raw["code"] = code
    raw["fatigue_events"] = [
        {"id": "pulse", "ncycles": 100, "loads": ["hot", "cold"]},
        {"id": "secondary", "ncycles": 10, "loads": ["hot"]},
    ]
    reader = FakeReader()
    reader.values = {
        1: np.array([[100e6, 0, 0, 0, 0, 0], [50e6, 0, 0, 0, 0, 0]]),
        2: np.array([[200e6, 0, 0, 0, 0, 0], [100e6, 0, 0, 0, 0, 0]]),
    }
    reader.temperature = lambda source, set_id: {1: np.full(2, 120.0)}
    config = Config.from_dict(raw, tmp_path / "config.yaml")
    result = evaluate(config, reader, {1: material})[0]
    assert not result.invalid.any()
    assert [event.event_id for event in result.events] == ["pulse", "secondary"]
    for event, cycles, delta, primary in (
        (result.events[0], 100, reader.values[2] - reader.values[1], -reader.values[1]),
        (result.events[1], 10, reader.values[2], np.zeros((2, 6))),
    ):
        for index in range(2):
            scalar = PATH_CODES[code].computeVj(
                NodalEvent(
                    EventConfig(120.0, 0.0, 1.0),
                    float(von_mises(delta[index : index + 1])[0]),
                    float(von_mises(primary[index : index + 1])[0]),
                ),
                material,
            )
            assert event.fields["N"][index] == pytest.approx(scalar["N"], rel=1e-12)
            assert event.fields["Vj"][index] == pytest.approx(cycles / scalar["N"], rel=1e-12)
    np.testing.assert_allclose(
        result.usage, result.events[0].fields["Vj"] + result.events[1].fields["Vj"]
    )
