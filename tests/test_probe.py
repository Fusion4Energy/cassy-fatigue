"""Result metadata probe: offline set mapping and a live check on the tracked fixture."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from cassy_fatigue.probe import _sets, probe_result
from cassy_fatigue.reader import ReaderError

FIXTURE = Path(__file__).resolve().parents[1] / "benchmarks" / "fixtures" / "two_materials.rst"


class FakeFrequencies:
    def __init__(self, steps):
        self.data = np.concatenate([np.asarray(times, dtype=float) for times in steps])
        self.scoping = SimpleNamespace(
            location="TimeFreq_steps", ids=list(range(1, len(steps) + 1))
        )
        self._steps = steps

    def get_entity_data(self, index):
        return np.asarray(self._steps[index])


def test_sets_are_one_based_with_load_steps_and_substeps():
    sets = _sets(SimpleNamespace(time_frequencies=FakeFrequencies([[0.5, 1.0], [2.0]])))
    assert [(s.set_id, s.time, s.load_step, s.substep) for s in sets] == [
        (1, 0.5, 1, 1),
        (2, 1.0, 1, 2),
        (3, 2.0, 2, 1),
    ]
    assert sets[2].label("s") == "set 3: t=2 s, LS2/SS1"


def test_sets_without_step_scoping_still_list_times():
    frequencies = SimpleNamespace(
        data=np.array([1.0, 2.0]), scoping=SimpleNamespace(location="Other", ids=[])
    )
    sets = _sets(SimpleNamespace(time_frequencies=frequencies))
    assert [(s.set_id, s.load_step) for s in sets] == [(1, None), (2, None)]
    assert sets[0].label("s") == "set 1: t=1 s"


def test_probe_requires_server_and_existing_result(tmp_path):
    with pytest.raises(ValueError, match="automatic startup is disabled"):
        probe_result(FIXTURE, None)
    with pytest.raises(ReaderError, match="not found"):
        probe_result(tmp_path / "missing.rst", object())
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(ReaderError, match="expected an .rst"):
        probe_result(tmp_path / "file.txt", object())


def test_probe_live_fixture(dpf_live_server):
    info = probe_result(FIXTURE, dpf_live_server)
    assert info.kind == "rst"
    assert info.time_unit == "s"
    assert [(s.set_id, s.time, s.load_step, s.substep) for s in info.sets] == [
        (1, 1.0, 1, 1),
        (2, 2.0, 2, 1),
    ]
    assert info.material_ids == (1, 2)
    assert {(n.name, n.location, n.size) for n in info.named_selections} == {
        ("INTERFACE", "Nodal", 6),
        ("MATERIAL_1", "Elemental", 2),
        ("MATERIAL_2", "Elemental", 2),
    }
    assert (info.n_nodes, info.n_elements) == (18, 4)
    assert info.solid_only and info.has_stress and info.has_temperature


def test_uppercase_extension_probe_and_run(tmp_path, dpf_live_server):
    """``.RST`` (common on Windows): DPF has no reader for the key ``RST``."""
    import shutil
    from copy import deepcopy

    import pyvista as pv

    from cassy_fatigue.config import Config
    from cassy_fatigue.kernel import load_materials
    from cassy_fatigue.pipeline import run_config

    upper = tmp_path / "TWO_MATERIALS.RST"
    shutil.copyfile(FIXTURE, upper)
    info = probe_result(upper, dpf_live_server)
    assert info.kind == "rst" and info.n_nodes == 18 and info.has_temperature
    config = Config.read(FIXTURE.parents[1] / "two_state.yaml")
    raw = deepcopy(config.raw)
    raw["analyses"]["struct"] = str(upper)
    config = Config.from_dict(raw, config.source)
    materials, sources = load_materials(config)
    paths = run_config(config, materials, sources, dpf_live_server, "live", tmp_path / "out")
    u_total = pv.read(paths["vtu"]).point_data["U_total"]
    assert np.nanmax(u_total) == pytest.approx(0.001551, abs=5e-7)
