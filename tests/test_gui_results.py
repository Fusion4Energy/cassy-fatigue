"""Results-viewer logic on a synthetic fatigue.vtu (no Qt, no DPF; off-screen rendering)."""

import json

import numpy as np
import pytest
import pyvista as pv

from cassy_fatigue.gui import results as rs
from cassy_fatigue.gui.results import ClipState, ResultSet, ViewState


def write_result(tmp_path, traceability=True):
    """3 x 1 x 1 hexahedra along x (16 nodes); node at x=3,y=0,z=0 is invalid in event b."""
    grid = pv.ImageData(dimensions=(4, 2, 2)).cast_to_unstructured_grid()
    x = grid.points[:, 0]
    n = grid.n_points
    grid.point_data["node_id"] = np.arange(101, 101 + n)
    vj_a = 1e-4 * (1 + x)
    vj_b = 1e-3 * (1 + x)
    bad = (x == 3) & (grid.points[:, 1] == 0) & (grid.points[:, 2] == 0)
    vj_b[bad] = np.nan
    grid.point_data["U_total"] = np.where(bad, np.nan, vj_a + vj_b)
    grid.point_data["invalid_code"] = np.where(bad, 16, 0).astype(np.uint32)
    grid.point_data["material_id_U"] = np.where(bad, -1, np.where(x < 1.5, 1, 2)).astype(np.int64)
    for event, vj in (("a", vj_a), ("b", vj_b)):
        grid.point_data[f"N_{event}"] = 1000 / vj
        grid.point_data[f"Vj_{event}"] = vj
        grid.point_data[f"T_degC_{event}"] = 20 + 100 * x
        grid.point_data[f"invalid_code_{event}"] = np.where(bad & (event == "b"), 16, 0).astype(
            np.uint32
        )
        grid.point_data[f"material_id_{event}"] = grid.point_data["material_id_U"]
    grid.cell_data["element_id"] = np.arange(1, 4)
    path = tmp_path / "fatigue.vtu"
    grid.save(path)
    if traceability:
        (tmp_path / "traceability.json").write_text(
            json.dumps(
                {
                    "invalid_codes": {"VALID": 0, "MISSING_STRESS": 1, "NONFINITE_RESULT": 16},
                    "material_assignment": {
                        "regions": {
                            "1": {"material": "Steel", "named_selection": None},
                            "2": {"material": "Copper", "named_selection": "MAT_CU"},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
    return path


@pytest.fixture
def result(tmp_path):
    return ResultSet.load(write_result(tmp_path))


def test_fields_events_and_summary(result):
    assert result.events == ["a", "b"]
    quantities = result.quantities()
    assert quantities[:3] == ["U_total", "invalid_code", "material_id_U"]
    assert quantities.count("invalid_code") == 1  # whole-node and per-event share one entry
    assert {"Vj", "N", "T_degC", "material_id"} <= set(quantities)
    assert "node_id" not in quantities and "_node_index" not in quantities
    assert result.field_name("U_total") == "U_total"
    assert result.field_name("invalid_code") == "invalid_code"
    assert result.field_name("invalid_code", "b") == "invalid_code_b"
    assert result.field_name("Vj") == "Vj_a"  # first event by default
    with pytest.raises(ValueError, match="No field"):
        result.field_name("Vj", "missing")
    assert "16 nodes, 3 cells, 2 event(s), 1 invalid node(s)" in result.summary()


def test_rejects_non_results(tmp_path):
    path = tmp_path / "plain.vtu"
    pv.ImageData(dimensions=(2, 2, 2)).cast_to_unstructured_grid().save(path)
    with pytest.raises(ValueError, match="not a cassy-fatigue result"):
        ResultSet.load(path)


def test_default_scene_uses_log_range_of_finite_values(result):
    scene = result.build_scene(ViewState())
    assert scene.field == "U_total" and scene.log
    finite = result.values("U_total")[np.isfinite(result.values("U_total"))]
    assert scene.clim == pytest.approx((finite.min(), finite.max()))
    assert scene.context is None and scene.visible_nodes.all()
    assert scene.surface.n_points == result.grid.n_points
    assert np.isnan(scene.surface.point_data["_display"]).sum() == 1  # NaN kept (magenta)
    categorical = result.build_scene(ViewState(quantity="material_id_U", log=True))
    assert not categorical.log
    with pytest.raises(ValueError, match="positive minimum"):
        result.build_scene(ViewState(log=True, clim=(0.0, 1.0)))
    with pytest.raises(ValueError, match="below the minimum"):
        result.build_scene(ViewState(clim=(1.0, 0.5), log=False))


def test_threshold_keeps_cells_with_any_node_in_range(result):
    # Vj_b = 1e-3 (1 + x): only nodes at x >= 2 exceed 2.5e-3, so the cells [1, 2] and
    # [2, 3] are kept (any node in range) and the cell [0, 1] is dropped.
    scene = result.build_scene(ViewState(quantity="Vj", event="b", threshold=(2.5e-3, 1.0)))
    x = result.grid.points[:, 0]
    np.testing.assert_array_equal(scene.visible_nodes, x >= 1)
    assert scene.context is not None  # transparent context shown when filtered
    assert scene.surface.points[:, 0].min() == pytest.approx(1.0)
    empty = result.build_scene(ViewState(threshold=(10.0, 20.0)))
    assert empty.surface.n_points == 0 and not empty.visible_nodes.any()
    assert "No cells" in empty.message


def test_clip_is_capped_and_tracks_visible_nodes(result):
    scene = result.build_scene(ViewState(clip=ClipState("x", 1.5, keep_above=False)))
    x = result.grid.points[:, 0]
    np.testing.assert_array_equal(scene.visible_nodes, x <= 1.5)
    points = scene.surface.points
    assert points[:, 0].max() == pytest.approx(1.5)
    cut = np.isclose(points[:, 0], 1.5)
    assert cut.sum() >= 4  # the cut face is part of the surface (capped)
    above = result.build_scene(ViewState(clip=ClipState("x", 1.5, keep_above=True)))
    np.testing.assert_array_equal(above.visible_nodes, x >= 1.5)
    whole = result.build_scene(ViewState(clip=ClipState("x", 1.5, whole_cells=True)))
    # Whole cells: the cut cell is kept entirely, so no interpolated points.
    assert set(np.round(whole.surface.points[:, 0], 9)) <= {0.0, 1.0, 2.0}
    assert whole.visible_nodes.sum() == np.count_nonzero(x <= 2)


def test_hotspots_order_visibility_and_invalid(result):
    total = result.values("U_total")
    top = result.hotspots("U_total", 3, "highest")
    assert all(np.isfinite(total[top]))
    assert list(total[top]) == sorted(total[top], reverse=True)
    assert top == sorted(top, key=lambda i: (-total[i], i))  # ties: lower index first
    low = result.hotspots("N_b", 2, result.default_mode("N"))
    assert result.default_mode("N") == "lowest"
    assert result.values("N_b")[low[0]] == np.nanmin(result.values("N_b"))
    assert result.hotspots("U_total", 10, "invalid") == [int(np.flatnonzero(np.isnan(total))[0])]
    assert result.hotspots("Vj_a", 10, "invalid") == []  # event a has no invalid nodes
    visible = result.grid.points[:, 0] <= 1
    assert all(visible[i] for i in result.hotspots("U_total", 50, "highest", visible))
    assert result.hotspots("U_total", 0) == []
    with pytest.raises(ValueError, match="mode"):
        result.hotspots("U_total", 1, "worst")


def test_picking_and_node_report(result):
    visible = result.grid.points[:, 0] <= 1
    assert result.grid.points[result.nearest_node((2.9, 0, 0))][0] == 3
    assert result.grid.points[result.nearest_node((2.9, 0, 0), visible)][0] == 1
    assert result.nearest_node((0, 0, 0), np.zeros(result.grid.n_points, bool)) is None
    bad = int(np.flatnonzero(np.isnan(result.values("U_total")))[0])
    report = result.node_report(bad)
    assert (
        report["node_id"] == result.node_id(bad) and result.index_of_node(report["node_id"]) == bad
    )
    assert report["invalid_flags"] == ["NONFINITE_RESULT"]
    assert report["material_U"] == "none (invalid)"
    assert (
        report["events"]["b"]["invalid_code"] == 16 and report["events"]["a"]["invalid_code"] == 0
    )
    assert np.isnan(report["U_total"])
    text = result.format_report(bad)
    assert "INVALID 16: NONFINITE_RESULT" in text and "U_total = NaN" in text
    assert result.material_label(2) == "2 (Copper, MAT_CU)"
    with pytest.raises(ValueError, match="No node"):
        result.index_of_node(1)


def test_flags_without_traceability(tmp_path):
    result = ResultSet.load(write_result(tmp_path, traceability=False))
    assert result.flag_names() is None
    assert rs.decode_flags(17) == ["MISSING_STRESS", "NONFINITE_RESULT"]
    assert rs.decode_flags(64) == ["UNKNOWN_64"]
    assert result.material_label(1) == "1"


def test_view_state_round_trip_and_validation():
    state = ViewState(
        quantity="Vj",
        event="b",
        log=True,
        clim=(1e-5, 1e-2),
        threshold=(1e-3, 1),
        clip=ClipState("z", 0.25, keep_above=True, whole_cells=True),
        edges=True,
    )
    assert ViewState.from_dict(json.loads(json.dumps(state.to_dict()))) == state
    with pytest.raises(ValueError, match="Unknown view settings"):
        ViewState.from_dict({"quantity": "U_total", "colour": "red"})


def test_screenshot_is_reproducible_from_its_view_file(result, tmp_path):
    state = ViewState(quantity="T_degC", event="a", clip=ClipState("x", 1.5))
    png = rs.save_screenshot(result, state, None, tmp_path / "shot", (320, 200), marker=0)
    assert png.suffix == ".png" and png.stat().st_size > 0
    loaded, camera, data = rs.load_view(rs.view_file(png))
    assert loaded == state and data["source_sha256"] == result.sha256
    assert data["image_size"] == [320, 200] and data["marker_node_id"] == 101
    again = rs.save_screenshot(result, loaded, camera, tmp_path / "again.png", (320, 200), marker=0)
    first = pv.read(png).point_data["PNGImage"]
    second = pv.read(again).point_data["PNGImage"]
    np.testing.assert_array_equal(first, second)
    (tmp_path / "bad.view.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="not a saved view"):
        rs.load_view(tmp_path / "bad.view.json")


def test_draw_keeps_camera_and_replaces_actors(result):
    plotter = pv.Plotter(off_screen=True, window_size=[200, 150])
    try:
        rs.draw(plotter, result.build_scene(ViewState()), ViewState())
        plotter.view_xy()
        camera = plotter.camera_position
        state = ViewState(threshold=(0.0, 1.0))
        rs.draw(plotter, result.build_scene(state), state, marker=3, results=result)
        assert plotter.camera_position == camera
        assert {"result", "context", "marker"} <= set(plotter.actors)
        rs.draw(plotter, result.build_scene(ViewState()), ViewState())
        assert "marker" not in plotter.actors and "context" not in plotter.actors
        rs.draw(plotter, result.build_scene(ViewState(quantity="N")), ViewState(quantity="N"))
        assert list(plotter.scalar_bars.keys()) == ["N (allowable cycles) — a"]
    finally:
        plotter.close()


def test_export_csv(result, tmp_path):
    rows = result.hotspots("U_total", 2)
    result.export_csv(rows, "U_total", tmp_path / "hot.csv")
    lines = (tmp_path / "hot.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("rank,node_id,U_total,U_total,invalid_code,material_id_U")
    assert len(lines) == 3 and lines[1].split(",")[1] == str(result.node_id(rows[0]))
