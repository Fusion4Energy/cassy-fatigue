import hashlib
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv
from cassy.designcodes.map import PATH_CODES

from cassy_fatigue.config import Config
from cassy_fatigue.kernel import EventConfig, NodalEvent, evaluate, load_materials, von_mises
from cassy_fatigue.output import write_outputs
from cassy_fatigue.reader import DPFReader


@pytest.mark.parametrize("multiple_analyses", [False, True])
@pytest.mark.parametrize("named_materials", [False, True])
def test_saved_fixture_scalar_parity_material_interface_and_outputs(
    tmp_path, multiple_analyses, named_materials, dpf_live_server
):
    root = Path(__file__).resolve().parent.parent
    config = Config.read(root / "benchmarks" / "two_state.yaml")
    if multiple_analyses:
        raw = deepcopy(config.raw)
        raw["analyses"]["other"] = raw["analyses"]["struct"]
        raw["loads"]["hot"]["sets"] = ["+other:2"]
        config = Config.from_dict(raw, config.source)
    if named_materials:
        raw = deepcopy(config.raw)
        raw["materials"] = {
            f"MATERIAL_{key}": name for key, name in reversed(list(config.materials.items()))
        }
        raw["analyses"]["temperature"] = raw["analyses"]["struct"]
        # A temperature-only .rst read through a declared, time-matched source.
        for name in ("struct", "other"):
            if name in raw["analyses"]:
                raw["analyses"][name] = {
                    "file": raw["analyses"][name],
                    "temperature": "temperature",
                }
        config = Config.from_dict(raw, config.source)
    server = dpf_live_server
    reader = DPFReader(config, server)
    materials, sources = load_materials(config)
    assert len(reader.regions) == 2
    interface = set(reader.named_selection("INTERFACE"))
    assert len(interface) == 6
    assert set(reader.regions[1].node_ids) & set(reader.regions[2].node_ids) == interface
    steel_id, copper_id = (2, 1) if named_materials else (1, 2)
    assert set(reader.named_selection("MATERIAL_1")) == set(reader.regions[steel_id].node_ids)
    first = reader.stress("struct", 1)
    assert reader.stress("struct", 1) is first
    with pytest.raises(TypeError):
        first[1] = np.zeros_like(first[1])
    assert not first[1].flags.writeable
    np.testing.assert_allclose(
        first[steel_id], np.broadcast_to([200e6, 0, 0, 0, 0, 0], first[steel_id].shape), atol=1
    )
    np.testing.assert_allclose(
        first[copper_id], np.broadcast_to([100e6, 0, 0, 0, 0, 0], first[copper_id].shape), atol=1
    )
    results = evaluate(config, reader, materials)
    assert len(reader._stress_cache) == len(reader._temperature_cache) == 2
    for result in results:
        assert not result.invalid.any()
        np.testing.assert_allclose(result.usage, sum(event.fields["Vj"] for event in result.events))
        material_id = result.region.material_id
        cold = reader.stress("struct", 1)[material_id]
        hot = reader.stress("struct", 2)[material_id]
        for index, node_id in enumerate(result.region.node_ids):
            factor = 1.5 if node_id in interface else 1.0
            scalar = PATH_CODES["SDC-IC"].computeVj(
                NodalEvent(
                    EventConfig(120.0, 0.0, factor),
                    float(von_mises((hot - cold)[index : index + 1])[0]),
                    float(von_mises((-cold)[index : index + 1])[0]),
                ),
                materials[material_id],
            )
            np.testing.assert_allclose(result.events[0].fields["N"][index], scalar["N"], rtol=1e-12)
            np.testing.assert_allclose(
                result.events[0].fields["Vj"][index], 100 / scalar["N"], rtol=1e-12
            )
    paths = write_outputs(config, reader, results, sources, tmp_path / "output")
    grid = pv.read(paths["vtu"])
    assert (grid.n_points, grid.n_cells) == (18, 4)
    assert np.isfinite(grid["U_total"]).all()
    np.testing.assert_allclose(grid["U_total"], grid["Vj_pulse"] + grid["Vj_secondary"])
    assert not np.any(grid["invalid_code"])
    assert set(np.unique(grid["material_id_U"])) <= set(config.materials)
    trace = json.loads(paths["traceability"].read_text(encoding="utf-8"))
    assert trace["material_assignment"]["mode"] == (
        "named_selection" if named_materials else "solver_material_id"
    )
    for material_id, source in sources.items():
        assert (
            trace["materials"][str(material_id)]["sha256"]
            == hashlib.sha256(source.path.read_bytes()).hexdigest()
        )
